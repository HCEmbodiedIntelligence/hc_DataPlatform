"""P19 retention, legal-hold, and streaming redacted-export workflows."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Protocol, cast
from urllib.parse import quote
from uuid import uuid4

from hc_data_platform.core.context import select_request_scope
from hc_data_platform.core.errors import problem
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.scope import ScopeGuard

from .models import (
    AuditExportArtifact,
    AuditExportDownloadAuthorization,
    AuditExportJob,
    AuditExportProgress,
    AuditLegalHold,
    AuditRetentionPolicy,
    AuditScope,
)
from .service import AuditProjectionService

Clock = Callable[[], datetime]
_EXPORT_WINDOW = timedelta(days=3660)
_DOWNLOAD_TTL = timedelta(minutes=15)


def _now(clock: Clock) -> datetime:
    value = clock()
    if value.tzinfo is None:
        raise RuntimeError("audit governance clock must be timezone-aware")
    return value.astimezone(timezone.utc)


def _region(value: str | None) -> str:
    return value or ""


def _policy_etag(scope: AuditScope, version: int, standard: int, security: int) -> str:
    value = (
        f"{scope.organization_id}:{scope.project_id}:{scope.region_code}:"
        f"{version}:{standard}:{security}"
    )
    return f'"audit-retention:{hashlib.sha256(value.encode()).hexdigest()[:32]}"'


def _authorize(
    auth: AuthContext,
    scope: AuditScope,
    capability: str,
) -> None:
    if auth.organization_scope_triples:
        ScopeGuard.require(
            auth,
            scope.project_id,
            scope.region_code,
            scope.organization_id,
        )
        auth.require_capability(capability, scope.project_id, scope.organization_id)
    else:
        ScopeGuard.require(auth, scope.project_id, scope.region_code)
        auth.require_capability(capability, scope.project_id)
    select_request_scope(
        scope.project_id,
        scope.region_code,
        organization_id=scope.organization_id,
    )


class AuditGovernanceRepository(Protocol):
    def get_policy(self, scope: AuditScope) -> AuditRetentionPolicy | None: ...

    def put_policy(
        self,
        policy: AuditRetentionPolicy,
        *,
        if_match: str,
    ) -> AuditRetentionPolicy: ...

    def list_holds(self, scope: AuditScope) -> tuple[AuditLegalHold, ...]: ...

    def create_hold(self, hold: AuditLegalHold) -> AuditLegalHold: ...

    def release_hold(
        self,
        scope: AuditScope,
        hold_id: str,
        *,
        actor_id: str,
        released_at: datetime,
    ) -> AuditLegalHold | None: ...

    def create_export(
        self,
        job: AuditExportJob,
        *,
        idempotency_key: str,
        request_fingerprint: str,
        execution_event: DomainEventEnvelope,
    ) -> tuple[AuditExportJob, bool]: ...

    def get_export(self, scope: AuditScope, job_id: str) -> AuditExportJob | None: ...

    def save_export(
        self,
        job: AuditExportJob,
        *,
        expected_statuses: frozenset[str],
    ) -> bool: ...

    def requeue_export(
        self,
        job: AuditExportJob,
        *,
        expected_statuses: frozenset[str],
        execution_event: DomainEventEnvelope,
    ) -> bool: ...

    def append_audit(
        self,
        *,
        scope: AuditScope,
        actor_id: str,
        request_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        details: Mapping[str, object],
        occurred_at: datetime,
    ) -> None: ...


class AuditArtifactStore(Protocol):
    def store(self, job_id: str, chunks: Iterable[bytes]) -> AuditExportArtifact: ...

    def authorize(
        self,
        job_id: str,
        artifact: AuditExportArtifact,
        expires_seconds: int,
    ) -> str: ...


class InMemoryAuditArtifactStore:
    def __init__(self) -> None:
        self.artifacts: dict[str, bytes] = {}

    def store(self, job_id: str, chunks: Iterable[bytes]) -> AuditExportArtifact:
        content = b"".join(chunks)
        existing = self.artifacts.get(job_id)
        if existing is not None and existing != content:
            raise problem(
                status=409,
                code="AUDIT_EXPORT_ARTIFACT_IMMUTABLE",
                title="Audit export artifact is immutable",
                detail="The export job already owns different artifact bytes.",
            )
        self.artifacts[job_id] = content
        return AuditExportArtifact(
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=str(len(content)),
        )

    def authorize(
        self,
        job_id: str,
        artifact: AuditExportArtifact,
        expires_seconds: int,
    ) -> str:
        content = self.artifacts.get(job_id)
        if content is None or hashlib.sha256(content).hexdigest() != artifact.sha256:
            raise problem(
                status=409,
                code="AUDIT_EXPORT_ARTIFACT_INTEGRITY_FAILED",
                title="Audit export artifact integrity failed",
                detail="The stored export does not match its immutable digest.",
            )
        return f"memory://audit-exports/{quote(job_id, safe='')}?ttl={expires_seconds}"


class S3AuditArtifactStore:
    """Bounded-memory JSONL storage with fresh, browser-safe GET authorization."""

    def __init__(
        self,
        client: Any,
        bucket: str,
        *,
        prefix: str = "audit-exports",
        presign_client: Any | None = None,
    ) -> None:
        self._client = client
        self._presign = presign_client or client
        self._bucket = bucket
        self._prefix = prefix.strip("/")

    def _key(self, job_id: str) -> str:
        return f"{self._prefix}/{quote(job_id, safe='')}/events.jsonl"

    def store(self, job_id: str, chunks: Iterable[bytes]) -> AuditExportArtifact:
        digest = hashlib.sha256()
        size = 0
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as stream:
            for chunk in chunks:
                digest.update(chunk)
                size += len(chunk)
                stream.write(chunk)
            stream.seek(0)
            artifact = AuditExportArtifact(sha256=digest.hexdigest(), size_bytes=str(size))
            try:
                self._client.put_object(
                    Bucket=self._bucket,
                    Key=self._key(job_id),
                    Body=stream,
                    ContentType="application/x-ndjson",
                    Metadata={"sha256": artifact.sha256},
                    IfNoneMatch="*",
                )
            except Exception as exc:
                if not _is_precondition_failed(exc):
                    raise
                head = self._client.head_object(Bucket=self._bucket, Key=self._key(job_id))
                metadata = {
                    str(key).lower(): str(value)
                    for key, value in cast(
                        Mapping[object, object], head.get("Metadata", {})
                    ).items()
                }
                if (
                    metadata.get("sha256") != artifact.sha256
                    or str(head.get("ContentLength")) != artifact.size_bytes
                ):
                    raise problem(
                        status=409,
                        code="AUDIT_EXPORT_ARTIFACT_IMMUTABLE",
                        title="Audit export artifact is immutable",
                        detail="The export job already owns different artifact bytes.",
                    ) from exc
        return artifact

    def authorize(
        self,
        job_id: str,
        artifact: AuditExportArtifact,
        expires_seconds: int,
    ) -> str:
        head = self._client.head_object(Bucket=self._bucket, Key=self._key(job_id))
        metadata = {
            str(key).lower(): str(value)
            for key, value in cast(Mapping[object, object], head.get("Metadata", {})).items()
        }
        if (
            metadata.get("sha256") != artifact.sha256
            or str(head.get("ContentLength")) != artifact.size_bytes
        ):
            raise problem(
                status=409,
                code="AUDIT_EXPORT_ARTIFACT_INTEGRITY_FAILED",
                title="Audit export artifact integrity failed",
                detail="The stored export does not match its immutable digest and size.",
            )
        return str(
            self._presign.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": self._key(job_id),
                    "ResponseContentType": "application/x-ndjson",
                    "ResponseContentDisposition": f'attachment; filename="audit-{job_id}.jsonl"',
                    "ResponseCacheControl": "no-store",
                },
                ExpiresIn=expires_seconds,
                HttpMethod="GET",
            )
        )


class InMemoryAuditGovernanceRepository:
    def __init__(self) -> None:
        self._policies: dict[tuple[str, str, str], AuditRetentionPolicy] = {}
        self._holds: dict[str, AuditLegalHold] = {}
        self._exports: dict[str, AuditExportJob] = {}
        self._receipts: dict[tuple[str, str, str, str], tuple[str, str]] = {}
        self.execution_events: list[DomainEventEnvelope] = []
        self.audit_events: list[dict[str, object]] = []
        self._lock = RLock()

    @staticmethod
    def _scope_key(scope: AuditScope) -> tuple[str, str, str]:
        return scope.organization_id, scope.project_id, _region(scope.region_code)

    def get_policy(self, scope: AuditScope) -> AuditRetentionPolicy | None:
        with self._lock:
            return self._policies.get(self._scope_key(scope))

    def put_policy(self, policy: AuditRetentionPolicy, *, if_match: str) -> AuditRetentionPolicy:
        with self._lock:
            current = self._policies.get(self._scope_key(policy.scope))
            expected = current.etag if current else _policy_etag(policy.scope, 0, 365, 2555)
            if if_match != expected:
                raise _etag_conflict(expected)
            self._policies[self._scope_key(policy.scope)] = policy
            return policy

    def list_holds(self, scope: AuditScope) -> tuple[AuditLegalHold, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (hold for hold in self._holds.values() if hold.scope == scope),
                    key=lambda hold: (hold.created_at, hold.hold_id),
                    reverse=True,
                )
            )

    def create_hold(self, hold: AuditLegalHold) -> AuditLegalHold:
        with self._lock:
            self._holds[hold.hold_id] = hold
            return hold

    def release_hold(
        self,
        scope: AuditScope,
        hold_id: str,
        *,
        actor_id: str,
        released_at: datetime,
    ) -> AuditLegalHold | None:
        with self._lock:
            hold = self._holds.get(hold_id)
            if hold is None or hold.scope != scope:
                return None
            if hold.status == "RELEASED":
                return hold
            released = hold.model_copy(
                update={
                    "status": "RELEASED",
                    "released_by": actor_id,
                    "released_at": released_at,
                }
            )
            self._holds[hold_id] = released
            return released

    def create_export(
        self,
        job: AuditExportJob,
        *,
        idempotency_key: str,
        request_fingerprint: str,
        execution_event: DomainEventEnvelope,
    ) -> tuple[AuditExportJob, bool]:
        receipt_key = (*self._scope_key(job.scope), idempotency_key)
        with self._lock:
            receipt = self._receipts.get(receipt_key)
            if receipt is not None:
                fingerprint, job_id = receipt
                if fingerprint != request_fingerprint:
                    raise _idempotency_conflict()
                return self._exports[job_id], False
            self._exports[job.job_id] = job
            self._receipts[receipt_key] = (request_fingerprint, job.job_id)
            self.execution_events.append(execution_event)
            return job, True

    def get_export(self, scope: AuditScope, job_id: str) -> AuditExportJob | None:
        with self._lock:
            job = self._exports.get(job_id)
            return job if job is not None and job.scope == scope else None

    def save_export(
        self,
        job: AuditExportJob,
        *,
        expected_statuses: frozenset[str],
    ) -> bool:
        with self._lock:
            current = self._exports.get(job.job_id)
            if current is None or current.status not in expected_statuses:
                return False
            self._exports[job.job_id] = job
            return True

    def requeue_export(
        self,
        job: AuditExportJob,
        *,
        expected_statuses: frozenset[str],
        execution_event: DomainEventEnvelope,
    ) -> bool:
        with self._lock:
            current = self._exports.get(job.job_id)
            if current is None or current.status not in expected_statuses:
                return False
            self._exports[job.job_id] = job
            self.execution_events.append(execution_event)
            return True

    def append_audit(self, **event: Any) -> None:
        with self._lock:
            self.audit_events.append(dict(event))


class AuditGovernanceService:
    def __init__(
        self,
        repository: AuditGovernanceRepository,
        projection: AuditProjectionService,
        artifact_store: AuditArtifactStore,
        *,
        clock: Clock = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._repository = repository
        self._projection = projection
        self._artifacts = artifact_store
        self._clock = clock

    @classmethod
    def in_memory(cls, projection: AuditProjectionService) -> AuditGovernanceService:
        return cls(
            InMemoryAuditGovernanceRepository(),
            projection,
            InMemoryAuditArtifactStore(),
        )

    def get_policy(self, *, auth: AuthContext, scope: AuditScope) -> AuditRetentionPolicy:
        _authorize(auth, scope, "audit.read")
        return self._repository.get_policy(scope) or self._default_policy(scope)

    def put_policy(
        self,
        *,
        auth: AuthContext,
        scope: AuditScope,
        standard_days: int,
        security_days: int,
        if_match: str,
        request_id: str,
    ) -> AuditRetentionPolicy:
        _authorize(auth, scope, "audit.export")
        current = self._repository.get_policy(scope) or self._default_policy(scope)
        now = _now(self._clock)
        policy = AuditRetentionPolicy(
            scope=scope,
            policy_version=current.policy_version + 1,
            standard_days=standard_days,
            security_days=security_days,
            etag=_policy_etag(
                scope,
                current.policy_version + 1,
                standard_days,
                security_days,
            ),
            updated_by=auth.subject_id,
            updated_at=now,
        )
        saved = self._repository.put_policy(policy, if_match=if_match)
        self._audit(
            scope,
            auth.subject_id,
            request_id,
            "audit.retention.updated",
            "AUDIT_RETENTION_POLICY",
            scope.project_id,
            {"policy_version": saved.policy_version},
            now,
        )
        return saved

    def list_holds(self, *, auth: AuthContext, scope: AuditScope) -> tuple[AuditLegalHold, ...]:
        _authorize(auth, scope, "audit.read")
        return self._repository.list_holds(scope)

    def create_hold(
        self,
        *,
        auth: AuthContext,
        scope: AuditScope,
        reason: str,
        occurred_from: datetime,
        occurred_to: datetime,
        request_id: str,
    ) -> AuditLegalHold:
        _authorize(auth, scope, "audit.export")
        start, end = _time_window(occurred_from, occurred_to)
        now = _now(self._clock)
        hold = self._repository.create_hold(
            AuditLegalHold(
                hold_id=str(uuid4()),
                scope=scope,
                reason=reason,
                occurred_from=start,
                occurred_to=end,
                status="ACTIVE",
                created_by=auth.subject_id,
                created_at=now,
            )
        )
        self._audit(
            scope,
            auth.subject_id,
            request_id,
            "audit.legal_hold.created",
            "AUDIT_LEGAL_HOLD",
            hold.hold_id,
            {"occurred_from": start.isoformat(), "occurred_to": end.isoformat()},
            now,
        )
        return hold

    def release_hold(
        self,
        *,
        auth: AuthContext,
        scope: AuditScope,
        hold_id: str,
        request_id: str,
    ) -> AuditLegalHold:
        _authorize(auth, scope, "audit.export")
        now = _now(self._clock)
        hold = self._repository.release_hold(
            scope,
            hold_id,
            actor_id=auth.subject_id,
            released_at=now,
        )
        if hold is None:
            raise _not_found("AUDIT_LEGAL_HOLD_NOT_FOUND", "Audit legal hold not found")
        self._audit(
            scope,
            auth.subject_id,
            request_id,
            "audit.legal_hold.released",
            "AUDIT_LEGAL_HOLD",
            hold_id,
            {},
            now,
        )
        return hold

    def create_export(
        self,
        *,
        auth: AuthContext,
        scope: AuditScope,
        occurred_from: datetime,
        occurred_to: datetime,
        idempotency_key: str,
        request_id: str,
    ) -> AuditExportJob:
        _authorize(auth, scope, "audit.export")
        start, end = _time_window(occurred_from, occurred_to)
        if end - start > _EXPORT_WINDOW:
            raise problem(
                status=422,
                code="AUDIT_EXPORT_RANGE_TOO_LARGE",
                title="Audit export range too large",
                detail="An audit export may cover at most ten years.",
            )
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "organization_id": scope.organization_id,
                    "project_id": scope.project_id,
                    "region_code": scope.region_code,
                    "occurred_from": start.isoformat(),
                    "occurred_to": end.isoformat(),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        now = _now(self._clock)
        candidate = AuditExportJob(
            job_id=str(uuid4()),
            scope=scope,
            status="QUEUED",
            progress=AuditExportProgress(exported_event_count=0, scanned_page_count=0),
            occurred_from=start,
            occurred_to=end,
            created_by=auth.subject_id,
            created_at=now,
            updated_at=now,
        )
        execution_event = DomainEventEnvelope(
            event_type=AuditExportOutboxHandler.EVENT_TYPE,
            aggregate_type="AUDIT_EXPORT",
            aggregate_id=candidate.job_id,
            organization_id=scope.organization_id,
            project_id=scope.project_id,
            region_code=scope.region_code,
            occurred_at=now,
            trace_id=request_id,
            payload={"organization_id": scope.organization_id, "job_id": candidate.job_id},
        )
        job, was_created = self._repository.create_export(
            candidate,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            execution_event=execution_event,
        )
        if was_created:
            self._audit(
                scope,
                auth.subject_id,
                request_id,
                "audit.export.queued",
                "AUDIT_EXPORT",
                job.job_id,
                {},
                now,
            )
        return job

    def get_export(self, *, auth: AuthContext, scope: AuditScope, job_id: str) -> AuditExportJob:
        _authorize(auth, scope, "audit.export")
        job = self._repository.get_export(scope, job_id)
        if job is None:
            raise _not_found("AUDIT_EXPORT_NOT_FOUND", "Audit export not found")
        return job

    def run_export(
        self,
        *,
        auth: AuthContext,
        scope: AuditScope,
        job_id: str,
        request_id: str,
    ) -> None:
        _authorize(auth, scope, "audit.export")
        job = self.get_export(auth=auth, scope=scope, job_id=job_id)
        # A process can die after persisting RUNNING but before acknowledging
        # its core outbox claim. A reclaimed envelope therefore resumes the
        # same job. The artifact store uses an immutable job-id key, making
        # repeated materialization safe and conflict-detecting.
        if job.status not in {"QUEUED", "RUNNING"}:
            return
        if job.status == "QUEUED":
            running = job.model_copy(update={"status": "RUNNING", "updated_at": _now(self._clock)})
            if not self._repository.save_export(running, expected_statuses=frozenset({"QUEUED"})):
                return
        else:
            running = job
        try:
            integrity = self._projection.integrity(
                auth=auth,
                organization_id=scope.organization_id,
                project_id=scope.project_id,
                region_code=scope.region_code,
                request_id=request_id,
            ).data
            if integrity.status != "PASSED":
                raise _ExportFailure(
                    "AUDIT_INTEGRITY_FAILED",
                    "The audit chain failed verification; no export was materialized.",
                )
            exported = 0
            pages = 0

            def chunks() -> Iterable[bytes]:
                nonlocal exported, pages, running
                for page in self._projection.iter_redacted_export_pages(
                    auth=auth,
                    organization_id=scope.organization_id,
                    project_id=scope.project_id,
                    region_code=scope.region_code,
                    occurred_from=job.occurred_from,
                    occurred_to=job.occurred_to,
                ):
                    latest = self._repository.get_export(scope, job_id)
                    if latest is None or latest.status == "CANCELLED":
                        raise _ExportCancelled
                    pages += 1
                    for event in page:
                        exported += 1
                        yield (
                            event.model_dump_json(by_alias=True, exclude_none=False) + "\n"
                        ).encode()
                    running = running.model_copy(
                        update={
                            "progress": AuditExportProgress(
                                exported_event_count=exported,
                                scanned_page_count=pages,
                            ),
                            "updated_at": _now(self._clock),
                        }
                    )
                    if not self._repository.save_export(
                        running, expected_statuses=frozenset({"RUNNING"})
                    ):
                        raise _ExportCancelled

            artifact = self._artifacts.store(job_id, chunks())
            completed = running.model_copy(
                update={
                    "status": "SUCCEEDED",
                    "progress": AuditExportProgress(
                        exported_event_count=exported,
                        scanned_page_count=pages,
                    ),
                    "artifact": artifact,
                    "updated_at": _now(self._clock),
                }
            )
            if self._repository.save_export(completed, expected_statuses=frozenset({"RUNNING"})):
                self._audit(
                    scope,
                    auth.subject_id,
                    request_id,
                    "audit.export.completed",
                    "AUDIT_EXPORT",
                    job_id,
                    {"event_count": exported, "artifact_sha256": artifact.sha256},
                    completed.updated_at,
                )
        except _ExportCancelled:
            return
        except Exception as exc:
            code = exc.code if isinstance(exc, _ExportFailure) else "AUDIT_EXPORT_FAILED"
            message = str(exc)[:512] if isinstance(exc, _ExportFailure) else "Audit export failed."
            failed = running.model_copy(
                update={
                    "status": "FAILED",
                    "error_code": code,
                    "error_message": message,
                    "updated_at": _now(self._clock),
                }
            )
            self._repository.save_export(failed, expected_statuses=frozenset({"RUNNING"}))

    def cancel_export(
        self, *, auth: AuthContext, scope: AuditScope, job_id: str, request_id: str
    ) -> AuditExportJob:
        job = self.get_export(auth=auth, scope=scope, job_id=job_id)
        if job.status not in {"QUEUED", "RUNNING"}:
            raise problem(
                status=409,
                code="AUDIT_EXPORT_CANCEL_NOT_ALLOWED",
                title="Audit export cannot be cancelled",
                detail="Only queued or running exports can be cancelled.",
            )
        now = _now(self._clock)
        cancelled = job.model_copy(update={"status": "CANCELLED", "updated_at": now})
        if not self._repository.save_export(
            cancelled, expected_statuses=frozenset({"QUEUED", "RUNNING"})
        ):
            raise problem(
                status=409,
                code="AUDIT_EXPORT_STATE_CHANGED",
                title="Audit export state changed",
                detail="Refresh the export before trying again.",
            )
        self._audit(
            scope,
            auth.subject_id,
            request_id,
            "audit.export.cancelled",
            "AUDIT_EXPORT",
            job_id,
            {},
            now,
        )
        return cancelled

    def retry_export(
        self, *, auth: AuthContext, scope: AuditScope, job_id: str, request_id: str
    ) -> AuditExportJob:
        job = self.get_export(auth=auth, scope=scope, job_id=job_id)
        if job.status not in {"FAILED", "CANCELLED"}:
            raise problem(
                status=409,
                code="AUDIT_EXPORT_RETRY_NOT_ALLOWED",
                title="Audit export cannot be retried",
                detail="Only failed or cancelled exports can be retried.",
            )
        now = _now(self._clock)
        queued = job.model_copy(
            update={
                "status": "QUEUED",
                "progress": AuditExportProgress(exported_event_count=0, scanned_page_count=0),
                "artifact": None,
                "error_code": None,
                "error_message": None,
                "updated_at": now,
            }
        )
        execution_event = DomainEventEnvelope(
            event_type=AuditExportOutboxHandler.EVENT_TYPE,
            aggregate_type="AUDIT_EXPORT",
            aggregate_id=queued.job_id,
            organization_id=scope.organization_id,
            project_id=scope.project_id,
            region_code=scope.region_code,
            occurred_at=now,
            trace_id=request_id,
            payload={"organization_id": scope.organization_id, "job_id": queued.job_id},
        )
        if not self._repository.requeue_export(
            queued,
            expected_statuses=frozenset({"FAILED", "CANCELLED"}),
            execution_event=execution_event,
        ):
            raise problem(
                status=409,
                code="AUDIT_EXPORT_STATE_CHANGED",
                title="Audit export state changed",
                detail="Refresh the export before trying again.",
            )
        self._audit(
            scope,
            auth.subject_id,
            request_id,
            "audit.export.retried",
            "AUDIT_EXPORT",
            job_id,
            {},
            now,
        )
        return queued

    def authorize_download(
        self,
        *,
        auth: AuthContext,
        scope: AuditScope,
        job_id: str,
        request_id: str,
    ) -> AuditExportDownloadAuthorization:
        job = self.get_export(auth=auth, scope=scope, job_id=job_id)
        if job.status != "SUCCEEDED" or job.artifact is None:
            raise problem(
                status=409,
                code="AUDIT_EXPORT_NOT_READY",
                title="Audit export is not ready",
                detail="Only a successful export can receive a download authorization.",
            )
        now = _now(self._clock)
        seconds = int(_DOWNLOAD_TTL.total_seconds())
        url = self._artifacts.authorize(job_id, job.artifact, seconds)
        self._audit(
            scope,
            auth.subject_id,
            request_id,
            "audit.export.download_authorized",
            "AUDIT_EXPORT",
            job_id,
            {"artifact_sha256": job.artifact.sha256},
            now,
        )
        return AuditExportDownloadAuthorization(
            job_id=job_id,
            download_url=url,
            expires_at=now + _DOWNLOAD_TTL,
            artifact=job.artifact,
        )

    def _default_policy(self, scope: AuditScope) -> AuditRetentionPolicy:
        return AuditRetentionPolicy(
            scope=scope,
            policy_version=0 + 1,
            standard_days=365,
            security_days=2555,
            etag=_policy_etag(scope, 0, 365, 2555),
            updated_by="system",
            updated_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
        )

    def _audit(
        self,
        scope: AuditScope,
        actor_id: str,
        request_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        details: Mapping[str, object],
        occurred_at: datetime,
    ) -> None:
        self._repository.append_audit(
            scope=scope,
            actor_id=actor_id,
            request_id=request_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            occurred_at=occurred_at,
        )


class _ExportFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _ExportCancelled(RuntimeError):
    pass


def _time_window(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    if start.tzinfo is None or end.tzinfo is None:
        raise problem(
            status=422,
            code="AUDIT_TIMEZONE_REQUIRED",
            title="Timezone-aware range required",
            detail="Audit governance times must include a UTC offset.",
        )
    normalized = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    if normalized[0] >= normalized[1]:
        raise problem(
            status=422,
            code="AUDIT_TIME_RANGE_INVALID",
            title="Invalid audit time range",
            detail="The start time must be earlier than the end time.",
        )
    return normalized


def _etag_conflict(current: str) -> Exception:
    return problem(
        status=409,
        code="AUDIT_RETENTION_ETAG_MISMATCH",
        title="Audit retention policy changed",
        detail="Refresh the policy before updating it.",
        details={"current_etag": current},
    )


def _idempotency_conflict() -> Exception:
    return problem(
        status=409,
        code="IDEMPOTENCY_KEY_REUSED",
        title="Idempotency key reused",
        detail="The Idempotency-Key was already used for a different audit export.",
    )


def _is_precondition_failed(error: BaseException) -> bool:
    """Recognize S3 conditional-put failure without coupling to boto3 types."""
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return False
    details = response.get("Error")
    if not isinstance(details, Mapping):
        return False
    return str(details.get("Code", "")) in {"PreconditionFailed", "412"}


def _not_found(code: str, title: str) -> Exception:
    return problem(
        status=404,
        code=code,
        title=title,
        detail="The resource is not visible in the selected scope.",
    )


class AuditExportOutboxHandler:
    """Durable worker entry point for a single exact-scope audit export."""

    EVENT_TYPE = "audit.export.requested.v1"

    def __init__(self, service: AuditGovernanceService) -> None:
        self._service = service

    def __call__(self, event: DomainEventEnvelope) -> None:
        if event.event_type != self.EVENT_TYPE or event.region_code is None:
            raise ValueError("audit export outbox event has an invalid scope")
        region_code = event.region_code
        organization_id = _required_outbox_payload(event, "organization_id")
        job_id = _required_outbox_payload(event, "job_id")
        if event.aggregate_id != job_id:
            raise ValueError("audit export outbox aggregate does not match job")
        scope = AuditScope(
            organization_id=organization_id,
            project_id=event.project_id,
            region_code=region_code,
        )
        worker = AuthContext(
            subject_id="audit-export-dispatcher",
            project_ids=frozenset({scope.project_id}),
            region_codes=frozenset({region_code}),
            service_identity=True,
            capabilities=frozenset({"audit.read", "audit.export"}),
            scope_pairs=frozenset({(scope.project_id, region_code)}),
            organization_ids=frozenset({scope.organization_id}),
            organization_scope_triples=frozenset(
                {(scope.organization_id, scope.project_id, region_code)}
            ),
            organization_scoped_capabilities=frozenset(
                {
                    (scope.organization_id, scope.project_id, "audit.read"),
                    (scope.organization_id, scope.project_id, "audit.export"),
                }
            ),
        )
        self._service.run_export(
            auth=worker,
            scope=scope,
            job_id=job_id,
            request_id=event.trace_id or event.event_id,
        )


def _required_outbox_payload(event: DomainEventEnvelope, name: str) -> str:
    value = event.payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"audit export outbox event is missing {name}")
    return value


class PostgresAuditGovernanceRepository:
    """Durable governance state under request-selected PostgreSQL RLS."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._factory = connection_factory

    @staticmethod
    def _row(cursor: Any, raw: object) -> dict[str, object]:
        if isinstance(raw, Mapping):
            return {str(key): value for key, value in raw.items()}
        names = tuple(str(column[0]) for column in cursor.description)
        return dict(zip(names, cast(Sequence[object], raw), strict=True))

    @staticmethod
    def _scope(row: Mapping[str, object]) -> AuditScope:
        region = str(row["region_code"])
        return AuditScope(
            organization_id=str(row["organization_id"]),
            project_id=str(row["project_id"]),
            region_code=region or None,
        )

    def _one(self, query: str, params: Sequence[object]) -> dict[str, object] | None:
        connection = self._factory()
        cursor = connection.cursor()
        try:
            cursor.execute(query, params)
            raw = cursor.fetchone()
            return None if raw is None else self._row(cursor, raw)
        finally:
            cursor.close()
            connection.close()

    def get_policy(self, scope: AuditScope) -> AuditRetentionPolicy | None:
        row = self._one(
            """SELECT * FROM core.audit_retention_policies
               WHERE organization_id = %s AND project_id = %s AND region_code = %s""",
            (scope.organization_id, scope.project_id, _region(scope.region_code)),
        )
        return (
            None
            if row is None
            else AuditRetentionPolicy(
                scope=self._scope(row),
                policy_version=int(str(row["policy_version"])),
                standard_days=int(str(row["standard_days"])),
                security_days=int(str(row["security_days"])),
                etag=str(row["etag"]),
                updated_by=str(row["updated_by"]),
                updated_at=cast(datetime, row["updated_at"]),
            )
        )

    def put_policy(self, policy: AuditRetentionPolicy, *, if_match: str) -> AuditRetentionPolicy:
        connection = self._factory()
        cursor = connection.cursor()
        try:
            current = self.get_policy(policy.scope)
            expected = current.etag if current else _policy_etag(policy.scope, 0, 365, 2555)
            if expected != if_match:
                raise _etag_conflict(expected)
            cursor.execute(
                """INSERT INTO core.audit_retention_policies (
                       project_id, region_code, organization_id, policy_version,
                       standard_days, security_days, etag, updated_by, updated_at
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (organization_id, project_id, region_code) DO UPDATE SET
                       organization_id=EXCLUDED.organization_id,
                       policy_version=EXCLUDED.policy_version,
                       standard_days=EXCLUDED.standard_days,
                       security_days=EXCLUDED.security_days,
                       etag=EXCLUDED.etag, updated_by=EXCLUDED.updated_by,
                       updated_at=EXCLUDED.updated_at
                    WHERE core.audit_retention_policies.etag = %s""",
                (
                    policy.scope.project_id,
                    _region(policy.scope.region_code),
                    policy.scope.organization_id,
                    policy.policy_version,
                    policy.standard_days,
                    policy.security_days,
                    policy.etag,
                    policy.updated_by,
                    policy.updated_at,
                    if_match,
                ),
            )
            if cursor.rowcount != 1:
                raise _etag_conflict(expected)
            connection.commit()
            return policy
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def list_holds(self, scope: AuditScope) -> tuple[AuditLegalHold, ...]:
        connection = self._factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """SELECT *
                     FROM core.audit_legal_holds
                    WHERE organization_id = %s AND project_id = %s AND region_code = %s
                    ORDER BY created_at DESC, hold_id DESC""",
                (scope.organization_id, scope.project_id, _region(scope.region_code)),
            )
            rows = [self._row(cursor, raw) for raw in cursor.fetchall()]
            return tuple(self._hold(row) for row in rows)
        finally:
            cursor.close()
            connection.close()

    def _hold(self, row: Mapping[str, object]) -> AuditLegalHold:
        return AuditLegalHold(
            hold_id=str(row["hold_id"]),
            scope=self._scope(row),
            reason=str(row["reason"]),
            occurred_from=cast(datetime, row["occurred_from"]),
            occurred_to=cast(datetime, row["occurred_to"]),
            status=cast(Any, str(row["status"])),
            created_by=str(row["created_by"]),
            created_at=cast(datetime, row["created_at"]),
            released_by=None if row.get("released_by") is None else str(row["released_by"]),
            released_at=cast(datetime | None, row.get("released_at")),
        )

    def create_hold(self, hold: AuditLegalHold) -> AuditLegalHold:
        connection = self._factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """INSERT INTO core.audit_legal_holds (
                       hold_id, project_id, region_code, organization_id, reason,
                       occurred_from, occurred_to, status, created_by, created_at
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,'ACTIVE',%s,%s)""",
                (
                    hold.hold_id,
                    hold.scope.project_id,
                    _region(hold.scope.region_code),
                    hold.scope.organization_id,
                    hold.reason,
                    hold.occurred_from,
                    hold.occurred_to,
                    hold.created_by,
                    hold.created_at,
                ),
            )
            connection.commit()
            return hold
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def release_hold(
        self, scope: AuditScope, hold_id: str, *, actor_id: str, released_at: datetime
    ) -> AuditLegalHold | None:
        connection = self._factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """UPDATE core.audit_legal_holds
                      SET status = 'RELEASED', released_by = %s, released_at = %s
                    WHERE hold_id = %s AND organization_id = %s
                      AND project_id = %s AND region_code = %s
                      AND status = 'ACTIVE'
                    RETURNING *""",
                (
                    actor_id,
                    released_at,
                    hold_id,
                    scope.organization_id,
                    scope.project_id,
                    _region(scope.region_code),
                ),
            )
            raw = cursor.fetchone()
            if raw is None:
                connection.rollback()
                existing = self._one(
                    """SELECT * FROM core.audit_legal_holds
                        WHERE hold_id = %s AND organization_id = %s
                          AND project_id = %s AND region_code = %s""",
                    (
                        hold_id,
                        scope.organization_id,
                        scope.project_id,
                        _region(scope.region_code),
                    ),
                )
                return None if existing is None else self._hold(existing)
            row = self._row(cursor, raw)
            connection.commit()
            return self._hold(row)
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def _job(self, row: Mapping[str, object]) -> AuditExportJob:
        artifact = None
        if row.get("artifact_sha256") is not None:
            artifact = AuditExportArtifact(
                sha256=str(row["artifact_sha256"]), size_bytes=str(row["artifact_size_bytes"])
            )
        return AuditExportJob(
            job_id=str(row["job_id"]),
            scope=self._scope(row),
            status=cast(Any, str(row["status"])),
            progress=AuditExportProgress(
                exported_event_count=int(str(row["exported_event_count"])),
                scanned_page_count=int(str(row["scanned_page_count"])),
            ),
            occurred_from=cast(datetime, row["occurred_from"]),
            occurred_to=cast(datetime, row["occurred_to"]),
            created_by=str(row["created_by"]),
            created_at=cast(datetime, row["created_at"]),
            updated_at=cast(datetime, row["updated_at"]),
            artifact=artifact,
            error_code=None if row.get("error_code") is None else str(row["error_code"]),
            error_message=None if row.get("error_message") is None else str(row["error_message"]),
        )

    def create_export(
        self,
        job: AuditExportJob,
        *,
        idempotency_key: str,
        request_fingerprint: str,
        execution_event: DomainEventEnvelope,
    ) -> tuple[AuditExportJob, bool]:
        connection = self._factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """INSERT INTO core.audit_export_jobs (
                       job_id, project_id, region_code, organization_id,
                       idempotency_key, request_fingerprint, status,
                       occurred_from, occurred_to, created_by, created_at, updated_at
                   ) VALUES (%s,%s,%s,%s,%s,%s,'QUEUED',%s,%s,%s,%s,%s)
                   ON CONFLICT (organization_id, project_id, region_code, idempotency_key)
                   DO NOTHING
                   RETURNING *""",
                (
                    job.job_id,
                    job.scope.project_id,
                    _region(job.scope.region_code),
                    job.scope.organization_id,
                    idempotency_key,
                    request_fingerprint,
                    job.occurred_from,
                    job.occurred_to,
                    job.created_by,
                    job.created_at,
                    job.updated_at,
                ),
            )
            raw = cursor.fetchone()
            if raw is None:
                cursor.execute(
                    """SELECT * FROM core.audit_export_jobs
                        WHERE organization_id = %s AND project_id = %s AND region_code = %s
                          AND idempotency_key = %s""",
                    (
                        job.scope.organization_id,
                        job.scope.project_id,
                        _region(job.scope.region_code),
                        idempotency_key,
                    ),
                )
                raw = cursor.fetchone()
                row = self._row(cursor, raw)
                if str(row["request_fingerprint"]) != request_fingerprint:
                    raise _idempotency_conflict()
                connection.commit()
                return self._job(row), False
            else:
                row = self._row(cursor, raw)
            self._insert_outbox(cursor, execution_event)
            connection.commit()
            return self._job(row), True
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def get_export(self, scope: AuditScope, job_id: str) -> AuditExportJob | None:
        row = self._one(
            """SELECT * FROM core.audit_export_jobs
                WHERE job_id = %s AND organization_id = %s
                  AND project_id = %s AND region_code = %s""",
            (
                job_id,
                scope.organization_id,
                scope.project_id,
                _region(scope.region_code),
            ),
        )
        return None if row is None else self._job(row)

    def save_export(self, job: AuditExportJob, *, expected_statuses: frozenset[str]) -> bool:
        connection = self._factory()
        cursor = connection.cursor()
        try:
            artifact_key = f"audit-exports/{job.job_id}/events.jsonl" if job.artifact else None
            cursor.execute(
                """UPDATE core.audit_export_jobs
                      SET status = %s,
                          exported_event_count = %s,
                          scanned_page_count = %s,
                          artifact_key = %s,
                          artifact_sha256 = %s,
                          artifact_size_bytes = %s,
                          error_code = %s,
                          error_message = %s,
                          updated_at = %s
                    WHERE job_id = %s AND organization_id = %s
                      AND project_id = %s AND region_code = %s
                      AND status = ANY(%s)""",
                (
                    job.status,
                    job.progress.exported_event_count,
                    job.progress.scanned_page_count,
                    artifact_key,
                    None if job.artifact is None else job.artifact.sha256,
                    None if job.artifact is None else int(job.artifact.size_bytes),
                    job.error_code,
                    job.error_message,
                    job.updated_at,
                    job.job_id,
                    job.scope.organization_id,
                    job.scope.project_id,
                    _region(job.scope.region_code),
                    list(expected_statuses),
                ),
            )
            changed = int(cursor.rowcount) == 1
            connection.commit()
            return changed
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def _insert_outbox(cursor: Any, event: DomainEventEnvelope) -> None:
        if event.organization_id is None:
            raise ValueError("audit outbox event requires organization identity")
        cursor.execute(
            """INSERT INTO core.outbox_events (
                   event_id, organization_id, project_id, region_code,
                   event_type, envelope, occurred_at
               ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)""",
            (
                event.event_id,
                event.organization_id,
                event.project_id,
                event.region_code,
                event.event_type,
                event.model_dump_json(),
                event.occurred_at,
            ),
        )

    def requeue_export(
        self,
        job: AuditExportJob,
        *,
        expected_statuses: frozenset[str],
        execution_event: DomainEventEnvelope,
    ) -> bool:
        connection = self._factory()
        cursor = connection.cursor()
        try:
            artifact_key = None
            cursor.execute(
                """UPDATE core.audit_export_jobs
                      SET status = %s,
                          exported_event_count = %s,
                          scanned_page_count = %s,
                          artifact_key = %s,
                          artifact_sha256 = %s,
                          artifact_size_bytes = %s,
                          error_code = %s,
                          error_message = %s,
                          updated_at = %s
                    WHERE job_id = %s AND organization_id = %s
                      AND project_id = %s AND region_code = %s
                      AND status = ANY(%s)""",
                (
                    job.status,
                    job.progress.exported_event_count,
                    job.progress.scanned_page_count,
                    artifact_key,
                    None,
                    None,
                    job.error_code,
                    job.error_message,
                    job.updated_at,
                    job.job_id,
                    job.scope.organization_id,
                    job.scope.project_id,
                    _region(job.scope.region_code),
                    list(expected_statuses),
                ),
            )
            changed = int(cursor.rowcount) == 1
            if changed:
                self._insert_outbox(cursor, execution_event)
            connection.commit()
            return changed
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def append_audit(
        self,
        *,
        scope: AuditScope,
        actor_id: str,
        request_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        details: Mapping[str, object],
        occurred_at: datetime,
    ) -> None:
        connection = self._factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """INSERT INTO core.audit_events (
                       audit_id, organization_id, project_id, region_code, actor_id, action,
                       resource_type, resource_id, request_id, details, occurred_at
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                (
                    str(uuid4()),
                    scope.organization_id,
                    scope.project_id,
                    scope.region_code,
                    actor_id,
                    action,
                    resource_type,
                    resource_id,
                    request_id,
                    json.dumps(dict(details), sort_keys=True),
                    occurred_at,
                ),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()
