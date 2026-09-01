from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from hc_data_platform.audit_projection.governance import (
    AuditExportOutboxHandler,
    AuditGovernanceService,
    InMemoryAuditArtifactStore,
    InMemoryAuditGovernanceRepository,
    S3AuditArtifactStore,
)
from hc_data_platform.audit_projection.models import AuditScope
from hc_data_platform.audit_projection.repository import (
    AuditEventRecord,
    InMemoryAuditProjectionRepository,
)
from hc_data_platform.audit_projection.service import AuditProjectionService
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.security.auth import AuthContext

NOW = datetime(2026, 8, 21, 6, 0, tzinfo=timezone.utc)
SCOPE = AuditScope(
    organization_id="org-a",
    project_id="project-a",
    region_code="cn-hz",
)


def _auth(*capabilities: str) -> AuthContext:
    return AuthContext(
        subject_id="auditor-a",
        organization_ids=frozenset({SCOPE.organization_id}),
        project_ids=frozenset({"project-a"}),
        region_codes=frozenset({"cn-hz"}),
        scope_pairs=frozenset({("project-a", "cn-hz")}),
        scoped_capabilities=frozenset(("project-a", capability) for capability in capabilities),
        organization_scope_triples=frozenset(
            {(SCOPE.organization_id, SCOPE.project_id, SCOPE.region_code)}
        ),
        organization_scoped_capabilities=frozenset(
            (SCOPE.organization_id, SCOPE.project_id, capability) for capability in capabilities
        ),
    )


def _event(event_id: str, action: str, occurred_at: datetime) -> AuditEventRecord:
    return AuditEventRecord(
        audit_id=event_id,
        organization_id=SCOPE.organization_id,
        project_id="project-a",
        region_code="cn-hz",
        actor_id="actor-secret",
        action=action,
        resource_type="DATASET",
        resource_id=f"resource-{event_id}",
        request_id=f"request-{event_id}",
        before_hash=None,
        after_hash=None,
        occurred_at=occurred_at,
    )


def _service(
    *, integrity_status: str = "PASSED"
) -> tuple[
    AuditGovernanceService,
    InMemoryAuditGovernanceRepository,
    InMemoryAuditArtifactStore,
]:
    projection_repository = InMemoryAuditProjectionRepository(
        (
            _event("event-2", "dataset.updated", NOW - timedelta(hours=1)),
            _event("event-1", "access.denied", NOW - timedelta(hours=2)),
        ),
        integrity_status=integrity_status,  # type: ignore[arg-type]
    )
    projection = AuditProjectionService(
        projection_repository,
        clock=lambda: NOW,
    )
    repository = InMemoryAuditGovernanceRepository()
    artifacts = InMemoryAuditArtifactStore()
    return (
        AuditGovernanceService(repository, projection, artifacts, clock=lambda: NOW),
        repository,
        artifacts,
    )


def test_retention_policy_uses_etag_and_legal_hold_is_releasable() -> None:
    service, repository, _ = _service()
    reader = _auth("audit.read")
    governor = _auth("audit.read", "audit.export")

    default = service.get_policy(auth=reader, scope=SCOPE)
    updated = service.put_policy(
        auth=governor,
        scope=SCOPE,
        standard_days=730,
        security_days=2555,
        if_match=default.etag,
        request_id="request-policy",
    )
    assert updated.policy_version == 2
    assert updated.etag != default.etag

    with pytest.raises(ProblemException) as stale:
        service.put_policy(
            auth=governor,
            scope=SCOPE,
            standard_days=731,
            security_days=2555,
            if_match=default.etag,
            request_id="request-policy-stale",
        )
    assert stale.value.problem.code == "AUDIT_RETENTION_ETAG_MISMATCH"

    hold = service.create_hold(
        auth=governor,
        scope=SCOPE,
        reason="Regulatory investigation 2026-08",
        occurred_from=NOW - timedelta(days=7),
        occurred_to=NOW,
        request_id="request-hold",
    )
    assert hold.status == "ACTIVE"
    released = service.release_hold(
        auth=governor,
        scope=SCOPE,
        hold_id=hold.hold_id,
        request_id="request-release",
    )
    assert released.status == "RELEASED"
    assert released.released_by == "auditor-a"
    assert {event["action"] for event in repository.audit_events} >= {
        "audit.retention.updated",
        "audit.legal_hold.created",
        "audit.legal_hold.released",
    }


def test_export_streams_only_redacted_projection_and_reauthorizes_download() -> None:
    service, repository, artifacts = _service()
    auth = _auth("audit.read", "audit.export")
    job = service.create_export(
        auth=auth,
        scope=SCOPE,
        occurred_from=NOW - timedelta(days=1),
        occurred_to=NOW,
        idempotency_key="export-once",
        request_id="request-export",
    )

    assert len(repository.execution_events) == 1
    AuditExportOutboxHandler(service)(repository.execution_events[0])
    completed = service.get_export(auth=auth, scope=SCOPE, job_id=job.job_id)
    assert completed.status == "SUCCEEDED"
    assert completed.progress.exported_event_count == 2
    assert completed.artifact is not None

    lines = artifacts.artifacts[job.job_id].decode().splitlines()
    records = [json.loads(line) for line in lines]
    assert [record["event_id"] for record in records] == ["event-2", "event-1"]
    assert all("details" not in record for record in records)
    assert all(record["actor"]["principal_id"] is None for record in records)
    assert "actor-secret" not in artifacts.artifacts[job.job_id].decode()

    authorization = service.authorize_download(
        auth=auth,
        scope=SCOPE,
        job_id=job.job_id,
        request_id="request-download",
    )
    assert authorization.artifact.sha256 == completed.artifact.sha256
    assert authorization.download_url.startswith("memory://audit-exports/")
    assert repository.audit_events[-1]["action"] == "audit.export.download_authorized"


def test_reclaimed_outbox_delivery_resumes_a_running_export() -> None:
    service, repository, artifacts = _service()
    auth = _auth("audit.read", "audit.export")
    job = service.create_export(
        auth=auth,
        scope=SCOPE,
        occurred_from=NOW - timedelta(days=1),
        occurred_to=NOW,
        idempotency_key="resume-after-worker-crash",
        request_id="request-export",
    )
    crashed = job.model_copy(update={"status": "RUNNING", "updated_at": NOW})
    assert repository.save_export(crashed, expected_statuses=frozenset({"QUEUED"}))

    # A reclaimed core-outbox envelope must finish a job left RUNNING by a
    # process crash, while reusing the immutable job-owned artifact key.
    AuditExportOutboxHandler(service)(repository.execution_events[0])

    resumed = service.get_export(auth=auth, scope=SCOPE, job_id=job.job_id)
    assert resumed.status == "SUCCEEDED"
    assert job.job_id in artifacts.artifacts


class _S3PreconditionFailed(RuntimeError):
    response = {"Error": {"Code": "PreconditionFailed"}}


class _ImmutableS3:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, dict[str, str]]] = {}

    def put_object(self, **request: object) -> None:
        key = str(request["Key"])
        if key in self.objects:
            raise _S3PreconditionFailed
        body = request["Body"]
        assert hasattr(body, "read")
        content = body.read()  # type: ignore[union-attr]
        metadata = request["Metadata"]
        assert isinstance(metadata, dict)
        self.objects[key] = (content, metadata)

    def head_object(self, **request: object) -> dict[str, object]:
        content, metadata = self.objects[str(request["Key"])]
        return {"ContentLength": len(content), "Metadata": metadata}


def test_s3_export_artifact_reuses_matching_immutable_job_bytes() -> None:
    client = _ImmutableS3()
    store = S3AuditArtifactStore(client, "audit-evidence")

    first = store.store("job-reclaimed", (b"first\n", b"second\n"))
    resumed = store.store("job-reclaimed", (b"first\n", b"second\n"))
    assert resumed == first

    with pytest.raises(ProblemException) as changed:
        store.store("job-reclaimed", (b"changed\n",))
    assert changed.value.problem.code == "AUDIT_EXPORT_ARTIFACT_IMMUTABLE"


def test_integrity_failure_blocks_artifact_and_cancelled_job_can_retry() -> None:
    service, repository, artifacts = _service(integrity_status="FAILED")
    auth = _auth("audit.read", "audit.export")
    job = service.create_export(
        auth=auth,
        scope=SCOPE,
        occurred_from=NOW - timedelta(days=1),
        occurred_to=NOW,
        idempotency_key="integrity-failure",
        request_id="request-export",
    )
    AuditExportOutboxHandler(service)(repository.execution_events[0])
    failed = service.get_export(auth=auth, scope=SCOPE, job_id=job.job_id)
    assert failed.status == "FAILED"
    assert failed.error_code == "AUDIT_INTEGRITY_FAILED"
    assert job.job_id not in artifacts.artifacts

    queued = service.retry_export(
        auth=auth,
        scope=SCOPE,
        job_id=job.job_id,
        request_id="request-retry",
    )
    assert queued.status == "QUEUED"
    assert len(repository.execution_events) == 2
    cancelled = service.cancel_export(
        auth=auth,
        scope=SCOPE,
        job_id=job.job_id,
        request_id="request-cancel",
    )
    assert cancelled.status == "CANCELLED"
