from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Any, Protocol, cast

from .models import (
    AttemptOutcome,
    CredentialState,
    IdentityState,
    RobotCredentialSummary,
    RobotIngestAttempt,
    RobotIngestAuthContext,
    RobotIngestEpisodeResult,
    RobotIngestIdentity,
    RobotIngestStatistics,
    RobotIngestUpload,
    UploadState,
    UploadTarget,
)


@dataclass(frozen=True, slots=True)
class AuthenticationResult:
    context: RobotIngestAuthContext | None
    failure_code: str | None = None


class RobotIngestConflict(RuntimeError):
    pass


class RobotIngestRepository(Protocol):
    def create_identity(self, identity: RobotIngestIdentity) -> RobotIngestIdentity: ...

    def save_identity(self, identity: RobotIngestIdentity) -> RobotIngestIdentity: ...

    def get_identity(
        self, organization_id: str, ingest_identity_id: str
    ) -> RobotIngestIdentity | None: ...

    def list_identities(self, organization_id: str) -> tuple[RobotIngestIdentity, ...]: ...

    def issue_credential(
        self,
        *,
        identity: RobotIngestIdentity,
        summary: RobotCredentialSummary,
        token_digest: str,
        revoke_previous: bool,
    ) -> None: ...

    def revoke_credential(
        self, *, organization_id: str, ingest_identity_id: str, credential_id: str, now: datetime
    ) -> RobotCredentialSummary | None: ...

    def list_credentials(
        self, organization_id: str, ingest_identity_id: str
    ) -> tuple[RobotCredentialSummary, ...]: ...

    def authenticate(
        self, *, credential_id: str, token_digest: str, now: datetime
    ) -> AuthenticationResult: ...

    def resolve_upload_target(self, collection_task_id: str) -> UploadTarget | None: ...

    def resolve_collection_job(
        self,
        *,
        target: UploadTarget,
        robot_id: str,
        collection_job_id: str | None,
        generated_collection_job_id: str,
        now: datetime,
    ) -> str: ...

    def create_upload(self, upload: RobotIngestUpload) -> RobotIngestUpload: ...

    def save_upload(self, upload: RobotIngestUpload) -> RobotIngestUpload: ...

    def get_upload(
        self, organization_id: str, upload_id: str, ingest_identity_id: str
    ) -> RobotIngestUpload | None: ...

    def find_upload(
        self,
        *,
        organization_id: str,
        ingest_identity_id: str,
        collection_task_id: str,
        client_upload_id: str,
    ) -> RobotIngestUpload | None: ...

    def list_uploads(
        self,
        *,
        organization_id: str,
        project_id: str | None = None,
        region_code: str | None = None,
        robot_id: str | None = None,
        collection_task_id: str | None = None,
        source_format: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[RobotIngestUpload, ...]: ...

    def commit_raw_source(
        self,
        *,
        upload: RobotIngestUpload,
        raw_source_id: str,
        manifest_key: str,
        storage_prefix: str,
        content_hash: str,
        adapter_name: str,
        processing_status: str,
        now: datetime,
    ) -> RobotIngestUpload: ...

    def save_episode_results(
        self,
        *,
        upload: RobotIngestUpload,
        items: tuple[RobotIngestEpisodeResult, ...],
    ) -> tuple[RobotIngestEpisodeResult, ...]: ...

    def list_episode_results(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        upload_id: str,
    ) -> tuple[RobotIngestEpisodeResult, ...]: ...

    def append_attempt(self, attempt: RobotIngestAttempt) -> None: ...

    def list_attempts(
        self,
        *,
        organization_id: str,
        project_id: str | None = None,
        region_code: str | None = None,
        robot_id: str | None = None,
    ) -> tuple[RobotIngestAttempt, ...]: ...

    def statistics(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        robot_id: str,
        collection_task_id: str | None = None,
        source_format: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> RobotIngestStatistics: ...


@dataclass(frozen=True, slots=True, repr=False)
class _CredentialRecord:
    organization_id: str
    ingest_identity_id: str
    summary: RobotCredentialSummary
    token_digest: str


class InMemoryRobotIngestRepository:
    def __init__(self, *, targets: tuple[UploadTarget, ...] = ()) -> None:
        self._identities: dict[tuple[str, str], RobotIngestIdentity] = {}
        self._robot_identity: dict[tuple[str, str], str] = {}
        self._credentials: dict[str, _CredentialRecord] = {}
        self._targets: dict[str, UploadTarget] = {}
        self._jobs: dict[str, tuple[UploadTarget, str]] = {}
        self._uploads: dict[str, RobotIngestUpload] = {}
        self._upload_identity: dict[tuple[str, str, str], str] = {}
        self._attempts: list[RobotIngestAttempt] = []
        self._episode_results: dict[str, tuple[RobotIngestEpisodeResult, ...]] = {}
        self._lock = RLock()
        for target in targets:
            self.add_target(target)

    def add_target(self, target: UploadTarget) -> None:
        with self._lock:
            if target.collection_task_id in self._targets:
                raise RobotIngestConflict("collection_task_id must be globally unique")
            self._targets[target.collection_task_id] = target

    def create_identity(self, identity: RobotIngestIdentity) -> RobotIngestIdentity:
        key = (identity.organization_id, identity.ingest_identity_id)
        robot_key = (identity.organization_id, identity.robot_id)
        with self._lock:
            if key in self._identities or robot_key in self._robot_identity:
                raise RobotIngestConflict("robot ingest identity already exists")
            self._identities[key] = identity
            self._robot_identity[robot_key] = identity.ingest_identity_id
        return identity

    def save_identity(self, identity: RobotIngestIdentity) -> RobotIngestIdentity:
        key = (identity.organization_id, identity.ingest_identity_id)
        with self._lock:
            if key not in self._identities:
                raise KeyError(key)
            self._identities[key] = identity
        return identity

    def get_identity(
        self, organization_id: str, ingest_identity_id: str
    ) -> RobotIngestIdentity | None:
        with self._lock:
            return self._identities.get((organization_id, ingest_identity_id))

    def list_identities(self, organization_id: str) -> tuple[RobotIngestIdentity, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        identity
                        for (saved_org, _), identity in self._identities.items()
                        if saved_org == organization_id
                    ),
                    key=lambda item: (item.updated_at, item.ingest_identity_id),
                    reverse=True,
                )
            )

    def issue_credential(
        self,
        *,
        identity: RobotIngestIdentity,
        summary: RobotCredentialSummary,
        token_digest: str,
        revoke_previous: bool,
    ) -> None:
        with self._lock:
            if summary.credential_id in self._credentials:
                raise RobotIngestConflict("credential already exists")
            if revoke_previous:
                for key, record in tuple(self._credentials.items()):
                    if (
                        record.organization_id == identity.organization_id
                        and record.ingest_identity_id == identity.ingest_identity_id
                        and record.summary.state is CredentialState.ACTIVE
                    ):
                        self._credentials[key] = _CredentialRecord(
                            organization_id=record.organization_id,
                            ingest_identity_id=record.ingest_identity_id,
                            summary=record.summary.model_copy(
                                update={
                                    "state": CredentialState.REVOKED,
                                    "revoked_at": summary.issued_at,
                                }
                            ),
                            token_digest=record.token_digest,
                        )
            self._credentials[summary.credential_id] = _CredentialRecord(
                organization_id=identity.organization_id,
                ingest_identity_id=identity.ingest_identity_id,
                summary=summary,
                token_digest=token_digest,
            )
            self._identities[(identity.organization_id, identity.ingest_identity_id)] = identity

    def revoke_credential(
        self, *, organization_id: str, ingest_identity_id: str, credential_id: str, now: datetime
    ) -> RobotCredentialSummary | None:
        with self._lock:
            record = self._credentials.get(credential_id)
            if record is None or (
                record.organization_id,
                record.ingest_identity_id,
            ) != (organization_id, ingest_identity_id):
                return None
            if record.summary.state is CredentialState.REVOKED:
                return record.summary
            summary = record.summary.model_copy(
                update={"state": CredentialState.REVOKED, "revoked_at": now}
            )
            self._credentials[credential_id] = _CredentialRecord(
                organization_id=record.organization_id,
                ingest_identity_id=record.ingest_identity_id,
                summary=summary,
                token_digest=record.token_digest,
            )
            return summary

    def list_credentials(
        self, organization_id: str, ingest_identity_id: str
    ) -> tuple[RobotCredentialSummary, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        record.summary
                        for record in self._credentials.values()
                        if record.organization_id == organization_id
                        and record.ingest_identity_id == ingest_identity_id
                    ),
                    key=lambda item: item.credential_version,
                    reverse=True,
                )
            )

    def authenticate(
        self, *, credential_id: str, token_digest: str, now: datetime
    ) -> AuthenticationResult:
        import hmac

        with self._lock:
            record = self._credentials.get(credential_id)
            if record is None or not hmac.compare_digest(record.token_digest, token_digest):
                return AuthenticationResult(None, "ROBOT_CREDENTIAL_INVALID")
            summary = record.summary
            if summary.state is CredentialState.REVOKED:
                return AuthenticationResult(None, "ROBOT_CREDENTIAL_REVOKED")
            if summary.expires_at is not None and summary.expires_at <= now:
                return AuthenticationResult(None, "ROBOT_CREDENTIAL_EXPIRED")
            identity = self._identities.get((record.organization_id, record.ingest_identity_id))
            if identity is None or identity.state is IdentityState.DISABLED:
                return AuthenticationResult(None, "ROBOT_IDENTITY_DISABLED")
            authenticated_summary = summary.model_copy(update={"last_authenticated_at": now})
            self._credentials[credential_id] = _CredentialRecord(
                organization_id=record.organization_id,
                ingest_identity_id=record.ingest_identity_id,
                summary=authenticated_summary,
                token_digest=record.token_digest,
            )
            observed = identity.model_copy(
                update={"last_authenticated_at": now, "last_seen_at": now, "updated_at": now}
            )
            self._identities[(identity.organization_id, identity.ingest_identity_id)] = observed
            return AuthenticationResult(
                RobotIngestAuthContext(
                    organization_id=observed.organization_id,
                    authenticated_robot_id=observed.robot_id,
                    ingest_identity_id=observed.ingest_identity_id,
                    credential_id=summary.credential_id,
                    credential_version=summary.credential_version,
                    allowed_transports=observed.allowed_transports,
                    allowed_formats=observed.allowed_formats,
                    upload_policy=observed.upload_policy,
                )
            )

    def resolve_upload_target(self, collection_task_id: str) -> UploadTarget | None:
        with self._lock:
            return self._targets.get(collection_task_id)

    def resolve_collection_job(
        self,
        *,
        target: UploadTarget,
        robot_id: str,
        collection_job_id: str | None,
        generated_collection_job_id: str,
        now: datetime,
    ) -> str:
        del now
        with self._lock:
            if collection_job_id is not None:
                existing = self._jobs.get(collection_job_id)
                if existing is None or existing != (target, robot_id):
                    raise RobotIngestConflict("collection job does not match task and robot")
                return collection_job_id
            for job_id, value in self._jobs.items():
                if value == (target, robot_id):
                    return job_id
            self._jobs[generated_collection_job_id] = (target, robot_id)
            return generated_collection_job_id

    def create_upload(self, upload: RobotIngestUpload) -> RobotIngestUpload:
        key = (
            upload.ingest_identity_id,
            upload.target.collection_task_id,
            upload.client_upload_id,
        )
        with self._lock:
            if upload.upload_id in self._uploads or key in self._upload_identity:
                raise RobotIngestConflict("upload already exists")
            self._uploads[upload.upload_id] = upload
            self._upload_identity[key] = upload.upload_id
        return upload

    def save_upload(self, upload: RobotIngestUpload) -> RobotIngestUpload:
        with self._lock:
            if upload.upload_id not in self._uploads:
                raise KeyError(upload.upload_id)
            self._uploads[upload.upload_id] = upload
        return upload

    def get_upload(
        self, organization_id: str, upload_id: str, ingest_identity_id: str
    ) -> RobotIngestUpload | None:
        with self._lock:
            upload = self._uploads.get(upload_id)
            if (
                upload is None
                or upload.target.organization_id != organization_id
                or upload.ingest_identity_id != ingest_identity_id
            ):
                return None
            return upload

    def find_upload(
        self,
        *,
        organization_id: str,
        ingest_identity_id: str,
        collection_task_id: str,
        client_upload_id: str,
    ) -> RobotIngestUpload | None:
        with self._lock:
            upload_id = self._upload_identity.get(
                (ingest_identity_id, collection_task_id, client_upload_id)
            )
            upload = None if upload_id is None else self._uploads[upload_id]
            return (
                upload
                if upload is not None and upload.target.organization_id == organization_id
                else None
            )

    def list_uploads(
        self,
        *,
        organization_id: str,
        project_id: str | None = None,
        region_code: str | None = None,
        robot_id: str | None = None,
        collection_task_id: str | None = None,
        source_format: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[RobotIngestUpload, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        upload
                        for upload in self._uploads.values()
                        if upload.target.organization_id == organization_id
                        and (project_id is None or upload.target.project_id == project_id)
                        and (region_code is None or upload.target.region_code == region_code)
                        and (robot_id is None or upload.authenticated_robot_id == robot_id)
                        and (
                            collection_task_id is None
                            or upload.target.collection_task_id == collection_task_id
                        )
                        and (source_format is None or upload.source_format == source_format)
                        and (created_from is None or upload.created_at >= created_from)
                        and (created_to is None or upload.created_at < created_to)
                    ),
                    key=lambda item: (item.created_at, item.upload_id),
                    reverse=True,
                )
            )

    def commit_raw_source(
        self,
        *,
        upload: RobotIngestUpload,
        raw_source_id: str,
        manifest_key: str,
        storage_prefix: str,
        content_hash: str,
        adapter_name: str,
        processing_status: str,
        now: datetime,
    ) -> RobotIngestUpload:
        del manifest_key, storage_prefix, content_hash, adapter_name, processing_status
        with self._lock:
            current = self._uploads[upload.upload_id]
            if current.state is UploadState.COMMITTED:
                return current
            committed = upload.model_copy(
                update={
                    "raw_source_id": raw_source_id,
                    "state": UploadState.COMMITTED,
                    "committed_at": now,
                    "updated_at": now,
                }
            )
            self._uploads[upload.upload_id] = committed
            identity = self._identities[(upload.target.organization_id, upload.ingest_identity_id)]
            self._identities[(identity.organization_id, identity.ingest_identity_id)] = (
                identity.model_copy(update={"last_upload_at": now, "updated_at": now})
            )
            return committed

    def append_attempt(self, attempt: RobotIngestAttempt) -> None:
        with self._lock:
            self._attempts.append(attempt)

    def save_episode_results(
        self,
        *,
        upload: RobotIngestUpload,
        items: tuple[RobotIngestEpisodeResult, ...],
    ) -> tuple[RobotIngestEpisodeResult, ...]:
        if upload.raw_source_id is None:
            raise ValueError("Episode results require a committed Raw source")
        with self._lock:
            existing = self._episode_results.get(upload.upload_id, ())
            if existing and {item.episode_id: item.source_episode_index for item in existing} != {
                item.episode_id: item.source_episode_index for item in items
            }:
                raise RobotIngestConflict("Episode identity set is immutable")
            self._episode_results[upload.upload_id] = items
        return items

    def list_episode_results(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        upload_id: str,
    ) -> tuple[RobotIngestEpisodeResult, ...]:
        with self._lock:
            upload = self._uploads.get(upload_id)
            if upload is None or (
                upload.target.organization_id,
                upload.target.project_id,
                upload.target.region_code,
            ) != (organization_id, project_id, region_code):
                return ()
            return self._episode_results.get(upload_id, ())

    def list_attempts(
        self,
        *,
        organization_id: str,
        project_id: str | None = None,
        region_code: str | None = None,
        robot_id: str | None = None,
    ) -> tuple[RobotIngestAttempt, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        item
                        for item in self._attempts
                        if item.organization_id == organization_id
                        and (
                            project_id is None
                            or item.project_id is None
                            or item.project_id == project_id
                        )
                        and (
                            region_code is None
                            or item.region_code is None
                            or item.region_code == region_code
                        )
                        and (
                            robot_id is None
                            or item.authenticated_robot_id == robot_id
                            or item.request_robot_id == robot_id
                        )
                    ),
                    key=lambda item: (item.occurred_at, item.attempt_id),
                    reverse=True,
                )
            )

    def statistics(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        robot_id: str,
        collection_task_id: str | None = None,
        source_format: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> RobotIngestStatistics:
        uploads = self.list_uploads(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            robot_id=robot_id,
            collection_task_id=collection_task_id,
            source_format=source_format,
            created_from=created_from,
            created_to=created_to,
        )
        committed = [item for item in uploads if item.state is UploadState.COMMITTED]
        attempts = tuple(
            item
            for item in self.list_attempts(
                organization_id=organization_id,
                project_id=project_id,
                region_code=region_code,
                robot_id=robot_id,
            )
            if (collection_task_id is None or item.collection_task_id == collection_task_id)
            and (source_format is None or item.source_format == source_format)
            and (created_from is None or item.occurred_at >= created_from)
            and (created_to is None or item.occurred_at < created_to)
        )

        def episode_count(item: RobotIngestUpload) -> int:
            return (
                item.verified_episode_count
                if item.verified_episode_count is not None
                else item.derived_episode_count
            )

        def capture_duration_ns(item: RobotIngestUpload) -> int:
            # Cameras in one Raw capture run concurrently, so their durations must not
            # be summed. Prefer the longest trusted Adapter observation and fall back
            # to the manifest interval only until processing has produced one.
            verified = [
                metric.verified_duration_ns
                for metric in item.camera_verification
                if metric.verified_duration_ns is not None
            ]
            if verified:
                return max(verified)
            return int((item.capture_ended_at - item.capture_started_at).total_seconds() * 1e9)

        def explicit_qc(item: RobotIngestUpload) -> bool:
            return (
                item.qc_pass_episode_count
                + item.qc_risk_episode_count
                + item.qc_reject_episode_count
                > 0
            )

        pass_count = sum(
            item.qc_pass_episode_count if explicit_qc(item) else episode_count(item)
            for item in committed
            if item.quality_status.value == "PASS" or item.qc_pass_episode_count > 0
        )
        risk_count = sum(
            item.qc_risk_episode_count if explicit_qc(item) else episode_count(item)
            for item in committed
            if item.quality_status.value == "RISK" or item.qc_risk_episode_count > 0
        )
        reject_count = sum(
            item.qc_reject_episode_count if explicit_qc(item) else episode_count(item)
            for item in committed
            if item.quality_status.value == "REJECT" or item.qc_reject_episode_count > 0
        )
        evaluated = pass_count + risk_count + reject_count
        frame_count = sum(
            item.verified_frame_count
            or sum(metric.verified_frame_count or 0 for metric in item.camera_verification)
            for item in committed
        )
        return RobotIngestStatistics(
            robot_id=robot_id,
            upload_batch_count=len(uploads),
            committed_raw_count=len(committed),
            episode_count=sum(episode_count(item) for item in committed),
            frame_count=frame_count,
            sample_count=sum(item.verified_sample_count for item in committed),
            capture_duration_ns=sum(capture_duration_ns(item) for item in committed),
            raw_bytes=sum(item.total_bytes for item in committed),
            qc_pass_count=pass_count,
            qc_risk_count=risk_count,
            qc_reject_count=reject_count,
            technical_failure_count=sum(
                item.outcome in {AttemptOutcome.REJECTED, AttemptOutcome.FAILED}
                for item in attempts
            ),
            qualified_rate=None if evaluated == 0 else pass_count / evaluated,
            evaluated_episode_count=evaluated,
        )


class DbApiCursor(Protocol):
    description: Sequence[Sequence[Any]] | None

    def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    def fetchone(self) -> object | None: ...

    def fetchall(self) -> Sequence[object]: ...

    def close(self) -> None: ...


class DbApiConnection(Protocol):
    def cursor(self) -> DbApiCursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


def _row(cursor: DbApiCursor, raw: object) -> dict[str, object]:
    if isinstance(raw, Mapping):
        return {str(key): value for key, value in raw.items()}
    if cursor.description is None:
        raise RuntimeError("database cursor did not describe its result")
    return dict(
        zip(
            (str(column[0]) for column in cursor.description),
            cast(Sequence[object], raw),
            strict=True,
        )
    )


def _json(value: object) -> object:
    return json.loads(value) if isinstance(value, str) else value


def _document(model: BaseException | Any) -> str:
    if hasattr(model, "model_dump"):
        model = model.model_dump(mode="json")
    return json.dumps(model, sort_keys=True, separators=(",", ":"), default=str)


class PostgresRobotIngestRepository:
    """Scoped persistence plus two narrow SECURITY DEFINER bootstrap lookups."""

    def __init__(
        self,
        scoped_connection_factory: Callable[[], DbApiConnection],
        bootstrap_connection_factory: Callable[[], DbApiConnection],
    ) -> None:
        self._scoped = scoped_connection_factory
        self._bootstrap = bootstrap_connection_factory

    @classmethod
    def from_dsn(
        cls,
        dsn: str,
        scoped_connection_factory: Callable[[], DbApiConnection],
    ) -> PostgresRobotIngestRepository:
        from hc_data_platform.core.dbapi import normalize_postgres_dsn

        normalized = normalize_postgres_dsn(dsn)

        def bootstrap() -> DbApiConnection:
            import psycopg

            connection = psycopg.connect(normalized)
            connection.execute(
                """SELECT set_config('app.platform_admin', 'true', false),
                          set_config('app.organization_id', '', false),
                          set_config('app.project_id', '', false),
                          set_config('app.region_code', '', false)"""
            )
            return cast(DbApiConnection, connection)

        return cls(scoped_connection_factory, bootstrap)

    def _one(
        self, query: str, params: Sequence[object], *, bootstrap: bool = False
    ) -> dict[str, object] | None:
        connection = (self._bootstrap if bootstrap else self._scoped)()
        cursor = connection.cursor()
        try:
            cursor.execute(query, params)
            raw = cursor.fetchone()
            if bootstrap:
                # Robot authentication is a SECURITY DEFINER lookup that also records
                # last-authenticated/last-seen observations. Persist those writes;
                # read-only bootstrap resolvers are safe to commit as empty transactions.
                connection.commit()
            return None if raw is None else _row(cursor, raw)
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def create_identity(self, identity: RobotIngestIdentity) -> RobotIngestIdentity:
        connection = self._scoped()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO ingest.robot_ingest_identities (
                    organization_id, ingest_identity_id, robot_id, state,
                    credential_revision, identity_document, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                ON CONFLICT DO NOTHING RETURNING ingest_identity_id
                """,
                (
                    identity.organization_id,
                    identity.ingest_identity_id,
                    identity.robot_id,
                    identity.state.value,
                    identity.credential_revision,
                    _document(identity),
                    identity.created_at,
                    identity.updated_at,
                ),
            )
            if cursor.fetchone() is None:
                raise RobotIngestConflict("robot ingest identity already exists")
            connection.commit()
            return identity
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def save_identity(self, identity: RobotIngestIdentity) -> RobotIngestIdentity:
        connection = self._scoped()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE ingest.robot_ingest_identities
                   SET state = %s, credential_revision = %s, identity_document = %s::jsonb,
                       updated_at = %s, last_authenticated_at = %s, last_seen_at = %s,
                       last_upload_at = %s
                 WHERE organization_id = %s AND ingest_identity_id = %s
                """,
                (
                    identity.state.value,
                    identity.credential_revision,
                    _document(identity),
                    identity.updated_at,
                    identity.last_authenticated_at,
                    identity.last_seen_at,
                    identity.last_upload_at,
                    identity.organization_id,
                    identity.ingest_identity_id,
                ),
            )
            connection.commit()
            return identity
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def get_identity(
        self, organization_id: str, ingest_identity_id: str
    ) -> RobotIngestIdentity | None:
        row = self._one(
            """SELECT identity_document FROM ingest.robot_ingest_identities
                 WHERE organization_id = %s AND ingest_identity_id = %s""",
            (organization_id, ingest_identity_id),
        )
        return (
            None
            if row is None
            else RobotIngestIdentity.model_validate(_json(row["identity_document"]))
        )

    def list_identities(self, organization_id: str) -> tuple[RobotIngestIdentity, ...]:
        connection = self._scoped()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """SELECT identity_document FROM ingest.robot_ingest_identities
                     WHERE organization_id = %s ORDER BY updated_at DESC, ingest_identity_id""",
                (organization_id,),
            )
            return tuple(
                RobotIngestIdentity.model_validate(_json(_row(cursor, raw)["identity_document"]))
                for raw in cursor.fetchall()
            )
        finally:
            cursor.close()
            connection.close()

    def issue_credential(
        self,
        *,
        identity: RobotIngestIdentity,
        summary: RobotCredentialSummary,
        token_digest: str,
        revoke_previous: bool,
    ) -> None:
        connection = self._scoped()
        cursor = connection.cursor()
        try:
            if revoke_previous:
                cursor.execute(
                    """UPDATE ingest.robot_ingest_credentials
                          SET state = 'REVOKED', revoked_at = %s
                        WHERE organization_id = %s AND ingest_identity_id = %s
                          AND state = 'ACTIVE'""",
                    (summary.issued_at, identity.organization_id, identity.ingest_identity_id),
                )
            cursor.execute(
                """
                INSERT INTO ingest.robot_ingest_credentials (
                    credential_id, organization_id, ingest_identity_id, credential_version,
                    token_prefix, token_digest, state, issued_at, expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    summary.credential_id,
                    identity.organization_id,
                    identity.ingest_identity_id,
                    summary.credential_version,
                    summary.token_prefix,
                    token_digest,
                    summary.state.value,
                    summary.issued_at,
                    summary.expires_at,
                ),
            )
            cursor.execute(
                """UPDATE ingest.robot_ingest_identities
                      SET credential_revision = %s, identity_document = %s::jsonb,
                          updated_at = %s
                    WHERE organization_id = %s AND ingest_identity_id = %s""",
                (
                    identity.credential_revision,
                    _document(identity),
                    identity.updated_at,
                    identity.organization_id,
                    identity.ingest_identity_id,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def revoke_credential(
        self, *, organization_id: str, ingest_identity_id: str, credential_id: str, now: datetime
    ) -> RobotCredentialSummary | None:
        connection = self._scoped()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """UPDATE ingest.robot_ingest_credentials
                      SET state = 'REVOKED', revoked_at = COALESCE(revoked_at, %s)
                    WHERE organization_id = %s AND ingest_identity_id = %s
                      AND credential_id = %s
                RETURNING credential_id, credential_version, state, token_prefix, issued_at,
                          expires_at, revoked_at, last_authenticated_at""",
                (now, organization_id, ingest_identity_id, credential_id),
            )
            raw = cursor.fetchone()
            connection.commit()
            return None if raw is None else RobotCredentialSummary.model_validate(_row(cursor, raw))
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def list_credentials(
        self, organization_id: str, ingest_identity_id: str
    ) -> tuple[RobotCredentialSummary, ...]:
        connection = self._scoped()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """SELECT credential_id, credential_version, state, token_prefix, issued_at,
                          expires_at, revoked_at, last_authenticated_at
                     FROM ingest.robot_ingest_credentials
                    WHERE organization_id = %s AND ingest_identity_id = %s
                    ORDER BY credential_version DESC""",
                (organization_id, ingest_identity_id),
            )
            return tuple(
                RobotCredentialSummary.model_validate(_row(cursor, raw))
                for raw in cursor.fetchall()
            )
        finally:
            cursor.close()
            connection.close()

    def authenticate(
        self, *, credential_id: str, token_digest: str, now: datetime
    ) -> AuthenticationResult:
        row = self._one(
            "SELECT * FROM ingest.authenticate_robot_ingest_credential(%s, %s, %s)",
            (credential_id, token_digest, now),
            bootstrap=True,
        )
        if row is None or row.get("identity_document") is None:
            return AuthenticationResult(
                None, str(row.get("failure_code")) if row else "ROBOT_CREDENTIAL_INVALID"
            )
        identity = RobotIngestIdentity.model_validate(_json(row["identity_document"]))
        return AuthenticationResult(
            RobotIngestAuthContext(
                organization_id=identity.organization_id,
                authenticated_robot_id=identity.robot_id,
                ingest_identity_id=identity.ingest_identity_id,
                credential_id=str(row["credential_id"]),
                credential_version=cast(int, row["credential_version"]),
                allowed_transports=identity.allowed_transports,
                allowed_formats=identity.allowed_formats,
                upload_policy=identity.upload_policy,
            )
        )

    def resolve_upload_target(self, collection_task_id: str) -> UploadTarget | None:
        row = self._one(
            "SELECT * FROM collection_tasks.resolve_robot_upload_target(%s)",
            (collection_task_id,),
            bootstrap=True,
        )
        return (
            None
            if row is None
            else UploadTarget.model_validate(
                {**row, "processing_config": _json(row.get("processing_config") or {})}
            )
        )

    def resolve_collection_job(
        self,
        *,
        target: UploadTarget,
        robot_id: str,
        collection_job_id: str | None,
        generated_collection_job_id: str,
        now: datetime,
    ) -> str:
        connection = self._scoped()
        cursor = connection.cursor()
        try:
            if collection_job_id is not None:
                cursor.execute(
                    """SELECT task_id, robot_id, region_code FROM ingest.collection_jobs
                         WHERE organization_id = %s AND project_id = %s
                           AND collection_job_id = %s""",
                    (target.organization_id, target.project_id, collection_job_id),
                )
                raw = cursor.fetchone()
                if raw is None:
                    raise RobotIngestConflict("collection job does not match task and robot")
                values = _row(cursor, raw)
                if (
                    values["task_id"] != target.collection_task_id
                    or values["robot_id"] != robot_id
                    or values["region_code"] != target.region_code
                ):
                    raise RobotIngestConflict("collection job does not match task and robot")
                return collection_job_id
            cursor.execute(
                """SELECT collection_job_id FROM ingest.collection_jobs
                     WHERE organization_id = %s AND project_id = %s AND task_id = %s
                       AND region_code = %s AND robot_id = %s
                       AND status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')
                     ORDER BY updated_at DESC, collection_job_id LIMIT 1""",
                (
                    target.organization_id,
                    target.project_id,
                    target.collection_task_id,
                    target.region_code,
                    robot_id,
                ),
            )
            raw = cursor.fetchone()
            if raw is not None:
                return str(_row(cursor, raw)["collection_job_id"])
            cursor.execute(
                """INSERT INTO ingest.collection_jobs (
                       organization_id, project_id, region_code, task_id, collection_job_id,
                       robot_id, status, created_at, updated_at
                   ) VALUES (%s, %s, %s, %s, %s, %s, 'REGISTERED', %s, %s)
                   ON CONFLICT DO NOTHING RETURNING collection_job_id""",
                (
                    target.organization_id,
                    target.project_id,
                    target.region_code,
                    target.collection_task_id,
                    generated_collection_job_id,
                    robot_id,
                    now,
                    now,
                ),
            )
            connection.commit()
            return generated_collection_job_id
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def create_upload(self, upload: RobotIngestUpload) -> RobotIngestUpload:
        connection = self._scoped()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """INSERT INTO ingest.robot_ingest_uploads (
                       organization_id, project_id, region_code, upload_id, ingest_identity_id,
                       authenticated_robot_id, request_robot_id, credential_id,
                       credential_version, collection_task_id, collection_job_id, dataset_id,
                       client_upload_id, source_format, capture_mode, state, manifest_fingerprint,
                       upload_document, created_at, updated_at
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                             %s, %s, %s, %s::jsonb, %s, %s)
                   ON CONFLICT DO NOTHING RETURNING upload_id""",
                (
                    upload.target.organization_id,
                    upload.target.project_id,
                    upload.target.region_code,
                    upload.upload_id,
                    upload.ingest_identity_id,
                    upload.authenticated_robot_id,
                    upload.request_robot_id,
                    upload.credential_id,
                    upload.credential_version,
                    upload.target.collection_task_id,
                    upload.collection_job_id,
                    upload.target.dataset_id,
                    upload.client_upload_id,
                    upload.source_format,
                    upload.capture_mode.value,
                    upload.state.value,
                    upload.manifest_fingerprint,
                    _document(upload),
                    upload.created_at,
                    upload.updated_at,
                ),
            )
            if cursor.fetchone() is None:
                raise RobotIngestConflict("upload already exists")
            for asset in upload.assets:
                cursor.execute(
                    """INSERT INTO ingest.robot_ingest_assets (
                           organization_id, project_id, region_code, upload_id, asset_id,
                           object_key, multipart_upload_id, state, asset_document, created_at,
                           updated_at
                       ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)""",
                    (
                        upload.target.organization_id,
                        upload.target.project_id,
                        upload.target.region_code,
                        upload.upload_id,
                        asset.asset_id,
                        asset.object_key,
                        asset.multipart_upload_id,
                        asset.state.value,
                        _document(asset),
                        upload.created_at,
                        upload.updated_at,
                    ),
                )
            for camera in upload.cameras:
                cursor.execute(
                    """INSERT INTO ingest.robot_ingest_camera_metrics (
                           organization_id, project_id, region_code, upload_id, camera_id,
                           declared_metrics, verification_metrics, created_at, updated_at
                       ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, '{}'::jsonb, %s, %s)""",
                    (
                        upload.target.organization_id,
                        upload.target.project_id,
                        upload.target.region_code,
                        upload.upload_id,
                        camera.camera_id,
                        _document(camera),
                        upload.created_at,
                        upload.updated_at,
                    ),
                )
            connection.commit()
            return upload
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def save_upload(self, upload: RobotIngestUpload) -> RobotIngestUpload:
        connection = self._scoped()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """UPDATE ingest.robot_ingest_uploads
                      SET state = %s, upload_document = %s::jsonb, updated_at = %s,
                          committed_at = %s, raw_source_id = %s
                    WHERE organization_id = %s AND project_id = %s AND region_code = %s
                      AND upload_id = %s""",
                (
                    upload.state.value,
                    _document(upload),
                    upload.updated_at,
                    upload.committed_at,
                    upload.raw_source_id,
                    upload.target.organization_id,
                    upload.target.project_id,
                    upload.target.region_code,
                    upload.upload_id,
                ),
            )
            for asset in upload.assets:
                cursor.execute(
                    """UPDATE ingest.robot_ingest_assets
                          SET state = %s, asset_document = %s::jsonb, updated_at = %s
                        WHERE organization_id = %s AND project_id = %s AND region_code = %s
                          AND upload_id = %s AND asset_id = %s""",
                    (
                        asset.state.value,
                        _document(asset),
                        upload.updated_at,
                        upload.target.organization_id,
                        upload.target.project_id,
                        upload.target.region_code,
                        upload.upload_id,
                        asset.asset_id,
                    ),
                )
            for metric in upload.camera_verification:
                cursor.execute(
                    """UPDATE ingest.robot_ingest_camera_metrics
                          SET verification_metrics = %s::jsonb, updated_at = %s
                        WHERE organization_id = %s AND project_id = %s AND region_code = %s
                          AND upload_id = %s AND camera_id = %s""",
                    (
                        _document(metric),
                        upload.updated_at,
                        upload.target.organization_id,
                        upload.target.project_id,
                        upload.target.region_code,
                        upload.upload_id,
                        metric.camera_id,
                    ),
                )
            if upload.raw_source_id is not None:
                cursor.execute(
                    """UPDATE ingest.raw_sources
                          SET declared_episode_count = %s, verified_episode_count = %s,
                              derived_episode_count = %s, verified_frame_count = %s,
                              verified_sample_count = %s,
                              qc_pass_episode_count = %s, qc_risk_episode_count = %s,
                              qc_reject_episode_count = %s, processing_status = %s,
                              quality_status = %s,
                              updated_at = %s
                        WHERE organization_id = %s AND project_id = %s AND region_code = %s
                          AND raw_source_id = %s""",
                    (
                        upload.declared_episode_count,
                        upload.verified_episode_count,
                        upload.derived_episode_count,
                        upload.verified_frame_count,
                        upload.verified_sample_count,
                        upload.qc_pass_episode_count,
                        upload.qc_risk_episode_count,
                        upload.qc_reject_episode_count,
                        upload.processing_status.value,
                        upload.quality_status.value,
                        upload.updated_at,
                        upload.target.organization_id,
                        upload.target.project_id,
                        upload.target.region_code,
                        upload.raw_source_id,
                    ),
                )
                cursor.execute(
                    """UPDATE ingest.raw_ingest_jobs
                          SET status = CASE %s
                                  WHEN 'READY' THEN 'SUCCEEDED'
                                  WHEN 'PARTIALLY_FAILED' THEN 'PARTIALLY_FAILED'
                                  WHEN 'FAILED' THEN 'FAILED'
                                  WHEN 'PENDING' THEN 'PENDING'
                                  ELSE 'RUNNING'
                              END,
                              updated_at = %s
                        WHERE organization_id = %s AND project_id = %s AND region_code = %s
                          AND raw_source_id = %s""",
                    (
                        upload.processing_status.value,
                        upload.updated_at,
                        upload.target.organization_id,
                        upload.target.project_id,
                        upload.target.region_code,
                        upload.raw_source_id,
                    ),
                )
            connection.commit()
            return upload
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def save_episode_results(
        self,
        *,
        upload: RobotIngestUpload,
        items: tuple[RobotIngestEpisodeResult, ...],
    ) -> tuple[RobotIngestEpisodeResult, ...]:
        if upload.raw_source_id is None:
            raise ValueError("Episode results require a committed Raw source")
        connection = self._scoped()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """SELECT episode_id, source_episode_index
                     FROM ingest.raw_source_episodes
                    WHERE organization_id = %s AND project_id = %s AND region_code = %s
                      AND raw_source_id = %s""",
                (
                    upload.target.organization_id,
                    upload.target.project_id,
                    upload.target.region_code,
                    upload.raw_source_id,
                ),
            )
            existing_identity = {
                str(values["episode_id"]): int(cast(int, values["source_episode_index"]))
                for values in (_row(cursor, raw) for raw in cursor.fetchall())
            }
            incoming_identity = {item.episode_id: item.source_episode_index for item in items}
            if existing_identity and existing_identity != incoming_identity:
                raise RobotIngestConflict("Episode identity set is immutable")
            for item in items:
                cursor.execute(
                    """INSERT INTO ingest.raw_source_episodes (
                           organization_id, project_id, region_code, raw_source_id,
                           episode_id, source_episode_index, status, frame_count,
                           dataset_version, lance_version, sample_count, quality_status,
                           qc_report_id, created_at, updated_at
                       ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                 %s, %s, %s)
                       ON CONFLICT (
                           organization_id, project_id, region_code, raw_source_id, episode_id
                       ) DO UPDATE SET
                           status = EXCLUDED.status,
                           frame_count = EXCLUDED.frame_count,
                           dataset_version = EXCLUDED.dataset_version,
                           lance_version = EXCLUDED.lance_version,
                           sample_count = EXCLUDED.sample_count,
                           quality_status = EXCLUDED.quality_status,
                           qc_report_id = EXCLUDED.qc_report_id,
                           updated_at = EXCLUDED.updated_at""",
                    (
                        upload.target.organization_id,
                        upload.target.project_id,
                        upload.target.region_code,
                        upload.raw_source_id,
                        item.episode_id,
                        item.source_episode_index,
                        item.status,
                        item.frame_count,
                        item.dataset_version,
                        item.lance_version,
                        item.sample_count,
                        item.quality_status.value,
                        item.qc_report_id,
                        item.created_at,
                        item.updated_at,
                    ),
                )
            connection.commit()
            return items
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def list_episode_results(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        upload_id: str,
    ) -> tuple[RobotIngestEpisodeResult, ...]:
        connection = self._scoped()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """SELECT episode_id, source_episode_index, episode.status,
                          frame_count, sample_count, dataset_version, lance_version,
                          quality_status, qc_report_id,
                          episode.created_at, episode.updated_at
                     FROM ingest.robot_ingest_uploads upload
                     JOIN ingest.raw_source_episodes episode
                       ON episode.organization_id = upload.organization_id
                      AND episode.project_id = upload.project_id
                      AND episode.region_code = upload.region_code
                      AND episode.raw_source_id = upload.raw_source_id
                    WHERE upload.organization_id = %s AND upload.project_id = %s
                      AND upload.region_code = %s AND upload.upload_id = %s
                    ORDER BY source_episode_index, episode_id""",
                (organization_id, project_id, region_code, upload_id),
            )
            return tuple(
                RobotIngestEpisodeResult.model_validate(_row(cursor, raw))
                for raw in cursor.fetchall()
            )
        finally:
            cursor.close()
            connection.close()

    def get_upload(
        self, organization_id: str, upload_id: str, ingest_identity_id: str
    ) -> RobotIngestUpload | None:
        row = self._one(
            "SELECT upload_document FROM ingest.resolve_robot_ingest_upload(%s, %s, %s)",
            (organization_id, upload_id, ingest_identity_id),
            bootstrap=True,
        )
        return (
            None if row is None else RobotIngestUpload.model_validate(_json(row["upload_document"]))
        )

    def find_upload(
        self,
        *,
        organization_id: str,
        ingest_identity_id: str,
        collection_task_id: str,
        client_upload_id: str,
    ) -> RobotIngestUpload | None:
        row = self._one(
            """SELECT upload_document FROM ingest.robot_ingest_uploads
                 WHERE organization_id = %s AND ingest_identity_id = %s
                   AND collection_task_id = %s
                   AND client_upload_id = %s""",
            (organization_id, ingest_identity_id, collection_task_id, client_upload_id),
        )
        return (
            None if row is None else RobotIngestUpload.model_validate(_json(row["upload_document"]))
        )

    def list_uploads(
        self,
        *,
        organization_id: str,
        project_id: str | None = None,
        region_code: str | None = None,
        robot_id: str | None = None,
        collection_task_id: str | None = None,
        source_format: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> tuple[RobotIngestUpload, ...]:
        connection = self._scoped()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """SELECT upload_document FROM ingest.robot_ingest_uploads
                     WHERE organization_id = %s
                       AND (%s::text IS NULL OR project_id = %s)
                       AND (%s::text IS NULL OR region_code = %s)
                       AND (%s::text IS NULL OR authenticated_robot_id = %s)
                       AND (%s::text IS NULL OR collection_task_id = %s)
                       AND (%s::text IS NULL OR source_format = %s)
                       AND (%s::timestamptz IS NULL OR created_at >= %s)
                       AND (%s::timestamptz IS NULL OR created_at < %s)
                     ORDER BY created_at DESC, upload_id""",
                (
                    organization_id,
                    project_id,
                    project_id,
                    region_code,
                    region_code,
                    robot_id,
                    robot_id,
                    collection_task_id,
                    collection_task_id,
                    source_format,
                    source_format,
                    created_from,
                    created_from,
                    created_to,
                    created_to,
                ),
            )
            return tuple(
                RobotIngestUpload.model_validate(_json(_row(cursor, raw)["upload_document"]))
                for raw in cursor.fetchall()
            )
        finally:
            cursor.close()
            connection.close()

    def commit_raw_source(
        self,
        *,
        upload: RobotIngestUpload,
        raw_source_id: str,
        manifest_key: str,
        storage_prefix: str,
        content_hash: str,
        adapter_name: str,
        processing_status: str,
        now: datetime,
    ) -> RobotIngestUpload:
        current = self.get_upload(
            upload.target.organization_id,
            upload.upload_id,
            upload.ingest_identity_id,
        )
        if current is not None and current.state is UploadState.COMMITTED:
            return current
        committed = upload.model_copy(
            update={
                "raw_source_id": raw_source_id,
                "state": UploadState.COMMITTED,
                "committed_at": now,
                "updated_at": now,
            }
        )
        connection = self._scoped()
        cursor = connection.cursor()
        try:
            # Serialize competing commits with processing/retry projection writes.
            cursor.execute(
                """SELECT upload_document FROM ingest.robot_ingest_uploads
                WHERE organization_id=%s AND upload_id=%s FOR UPDATE""",
                (upload.target.organization_id, upload.upload_id),
            )
            locked = cursor.fetchone()
            if locked is not None:
                latest = RobotIngestUpload.model_validate(
                    _json(_row(cursor, locked)["upload_document"])
                )
                if latest.state is UploadState.COMMITTED:
                    connection.commit()
                    return latest
            cursor.execute(
                """INSERT INTO ingest.raw_sources (
                       organization_id, project_id, region_code, raw_source_id, upload_id,
                       dataset_id, collection_task_id, robot_id, source_format,
                       source_format_version, manifest_key, storage_prefix, content_hash,
                       file_count, total_bytes, raw_status, processing_status, created_at,
                       committed_at, updated_at, authenticated_robot_id, request_robot_id,
                       ingest_identity_id, credential_id, credential_version, capture_mode,
                       client_upload_id, upload_batch_count, raw_capture_count,
                       declared_episode_count, verified_episode_count, derived_episode_count,
                       verified_frame_count, verified_sample_count, qc_pass_episode_count,
                       qc_risk_episode_count, qc_reject_episode_count, quality_status
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                             %s, 'COMMITTED', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                             %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (organization_id, project_id, region_code, raw_source_id)
                   DO NOTHING""",
                (
                    upload.target.organization_id,
                    upload.target.project_id,
                    upload.target.region_code,
                    raw_source_id,
                    upload.upload_id,
                    upload.target.dataset_id,
                    upload.target.collection_task_id,
                    upload.authenticated_robot_id,
                    upload.source_format,
                    upload.source_format_version,
                    manifest_key,
                    storage_prefix,
                    content_hash,
                    len(upload.assets),
                    upload.total_bytes,
                    processing_status,
                    upload.created_at,
                    now,
                    now,
                    upload.authenticated_robot_id,
                    upload.request_robot_id,
                    upload.ingest_identity_id,
                    upload.credential_id,
                    upload.credential_version,
                    upload.capture_mode.value,
                    upload.client_upload_id,
                    upload.upload_batch_count,
                    upload.raw_capture_count,
                    upload.declared_episode_count,
                    upload.verified_episode_count,
                    upload.derived_episode_count,
                    upload.verified_frame_count,
                    upload.verified_sample_count,
                    upload.qc_pass_episode_count,
                    upload.qc_risk_episode_count,
                    upload.qc_reject_episode_count,
                    upload.quality_status.value,
                ),
            )
            cursor.execute(
                """INSERT INTO ingest.raw_ingest_jobs (
                       organization_id, project_id, region_code, job_id, raw_source_id,
                       job_type, adapter_name, status, created_at, updated_at
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'PENDING', %s, %s)
                   ON CONFLICT (organization_id, project_id, region_code, raw_source_id)
                   DO NOTHING""",
                (
                    upload.target.organization_id,
                    upload.target.project_id,
                    upload.target.region_code,
                    f"job-{raw_source_id}",
                    raw_source_id,
                    "CONTINUOUS_RECORDING_DISCOVERY"
                    if upload.capture_mode.value == "CONTINUOUS"
                    else (
                        "LEROBOT_IMPORT"
                        if upload.source_format == "LEROBOT_V3"
                        else "DIRECT_EPISODE_INGEST"
                    ),
                    adapter_name,
                    now,
                    now,
                ),
            )
            cursor.execute(
                """UPDATE ingest.robot_ingest_uploads
                      SET state = 'COMMITTED', raw_source_id = %s, upload_document = %s::jsonb,
                          committed_at = %s, updated_at = %s
                    WHERE upload_id = %s""",
                (raw_source_id, _document(committed), now, now, upload.upload_id),
            )
            cursor.execute(
                """UPDATE ingest.robot_ingest_identities
                      SET last_upload_at = %s, updated_at = %s,
                          identity_document = jsonb_set(
                              jsonb_set(identity_document, '{last_upload_at}', to_jsonb(%s::text)),
                              '{updated_at}', to_jsonb(%s::text)
                          )
                    WHERE organization_id = %s AND ingest_identity_id = %s""",
                (
                    now,
                    now,
                    now.isoformat(),
                    now.isoformat(),
                    upload.target.organization_id,
                    upload.ingest_identity_id,
                ),
            )
            from .processing_store import eligible, enqueue

            if eligible(committed):
                enqueue(cursor, committed)
            from . import recording_bridge
            if recording_bridge.eligible(committed):
                recording_bridge.enqueue(cursor,committed)
            connection.commit()
            return committed
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def append_attempt(self, attempt: RobotIngestAttempt) -> None:
        # Authentication and identity failures occur before a trusted project scope
        # exists. The narrow SECURITY DEFINER function accepts only the bounded audit
        # document. Read policies expose unscoped identity/auth failures only inside
        # the authenticated organization and project-bound facts only in their exact scope.
        connection = self._bootstrap()
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT ingest.record_robot_ingest_attempt(%s::jsonb)",
                (_document(attempt),),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def list_attempts(
        self,
        *,
        organization_id: str,
        project_id: str | None = None,
        region_code: str | None = None,
        robot_id: str | None = None,
    ) -> tuple[RobotIngestAttempt, ...]:
        connection = self._scoped()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """SELECT attempt_document FROM ingest.robot_ingest_attempts
                     WHERE organization_id = %s
                       AND (%s::text IS NULL OR project_id IS NULL OR project_id = %s)
                       AND (%s::text IS NULL OR region_code IS NULL OR region_code = %s)
                       AND (%s::text IS NULL
                            OR authenticated_robot_id = %s
                            OR request_robot_id = %s)
                     ORDER BY occurred_at DESC, attempt_id""",
                (
                    organization_id,
                    project_id,
                    project_id,
                    region_code,
                    region_code,
                    robot_id,
                    robot_id,
                    robot_id,
                ),
            )
            return tuple(
                RobotIngestAttempt.model_validate(_json(_row(cursor, raw)["attempt_document"]))
                for raw in cursor.fetchall()
            )
        finally:
            cursor.close()
            connection.close()

    def statistics(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        robot_id: str,
        collection_task_id: str | None = None,
        source_format: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> RobotIngestStatistics:
        # Keep the definition identical to the in-memory implementation and make it easy
        # to add task/time filters without embedding divergent SQL rate semantics.
        memory = InMemoryRobotIngestRepository()
        memory._uploads = {
            item.upload_id: item
            for item in self.list_uploads(
                organization_id=organization_id,
                project_id=project_id,
                region_code=region_code,
                robot_id=robot_id,
                collection_task_id=collection_task_id,
                source_format=source_format,
                created_from=created_from,
                created_to=created_to,
            )
        }
        memory._attempts = list(
            self.list_attempts(
                organization_id=organization_id,
                project_id=project_id,
                region_code=region_code,
                robot_id=robot_id,
            )
        )
        return memory.statistics(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            robot_id=robot_id,
            collection_task_id=collection_task_id,
            source_format=source_format,
            created_from=created_from,
            created_to=created_to,
        )
