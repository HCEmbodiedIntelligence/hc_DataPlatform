from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.publishing.audit import InMemoryExportAuditRecorder
from hc_data_platform.publishing.memory import (
    InMemoryAnnotationSnapshot,
    InMemoryArtifactSink,
    InMemoryCatalogSnapshot,
    InMemoryExportSource,
    InMemoryLanceSnapshotExporter,
    InMemoryPublishedManifestRepository,
)
from hc_data_platform.publishing.models import (
    ApprovedAnnotationSnapshotV1,
    CatalogRolloutSnapshotV1,
    DerivedStatus,
    ExportStepV1,
    PublishedDatasetManifestV1,
    PublishedRolloutV1,
    QualityStatus,
    StepRangeV1,
)
from hc_data_platform.publishing.router import router as publishing_router
from hc_data_platform.publishing.service import DatasetPublisher, ExportCoordinator
from hc_data_platform.security import AuthContext
from hc_data_platform.workflow.models import JobRecord, JobStatus
from hc_data_platform.workflow.router import router as workflow_router
from hc_data_platform.workflow.service import InMemoryWorkflowLauncher

HASH = "a" * 64


class PendingInMemoryWorkflowLauncher(InMemoryWorkflowLauncher):
    """Exercise the API cancellation path without running an export inline."""

    def start(
        self,
        *,
        workflow_id: str,
        job_type: str,
        project_id: str,
        resource_id: str,
        runner: object,
    ) -> JobRecord:
        del runner
        with self._lock:
            existing_id = self._by_workflow.get(workflow_id)
            if existing_id is not None:
                return self._jobs[existing_id]
            now = datetime.now(timezone.utc)
            job = JobRecord(
                workflow_id=workflow_id,
                job_type=job_type,
                project_id=project_id,
                resource_id=resource_id,
                status=JobStatus.RUNNING,
                stage="export",
                attempt=1,
                created_at=now,
                updated_at=now,
            )
            self._jobs[job.job_id] = job
            self._by_workflow[workflow_id] = job.job_id
            return job


def _manifest() -> PublishedDatasetManifestV1:
    return PublishedDatasetManifestV1(
        project_id="project-a",
        dataset_id="dataset-a",
        dataset_version="v1",
        base_lance_version="lance-v1",
        created_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        content_hash=HASH,
        annotations_uri="published/project-a/dataset-a/v1/annotations.lance",
        annotations_content_sha256="b" * 64,
        training_manifest_uri="published/project-a/dataset-a/v1/training-manifest.json",
        training_manifest_content_sha256="c" * 64,
        rollouts=(
            PublishedRolloutV1(
                rollout_id="rollout-a",
                source_mcap_sha256="d" * 64,
                base_lance_version="lance-v1",
                annotation_revision=2,
                quality_profile_version="quality-v1",
                alignment_profile_version="alignment-v1",
                alignment_frequency_hz=30,
                converter_version="converter-v1",
                total_steps=1,
                included_step_ranges=(StepRangeV1(start_step=0, end_step=1),),
            ),
        ),
    )


def _step() -> ExportStepV1:
    return ExportStepV1(
        rollout_id="rollout-a",
        step_index=0,
        timestamp_ns=0,
        modalities={"camera.front": "frame-0", "action": [0.0]},
        source_timestamps_ns={"camera.front": (0,), "action": (0,)},
        time_error_ns={"camera.front": 0, "action": 0},
        valid={"camera.front": True, "action": True},
        repeated={"camera.front": False, "action": False},
    )


@pytest.fixture
def export_api(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, dict[str, AuthContext | None], InMemoryExportAuditRecorder]]:
    import hc_data_platform.publishing.router as router_module
    import hc_data_platform.workflow.router as workflow_module

    sink = InMemoryArtifactSink()
    manifests = InMemoryPublishedManifestRepository()
    manifests.create_immutable(_manifest())
    publisher = DatasetPublisher(
        catalog=InMemoryCatalogSnapshot(
            (
                CatalogRolloutSnapshotV1(
                    rollout_id="rollout-a",
                    source_mcap_sha256="d" * 64,
                    total_steps=1,
                    quality_status=QualityStatus.PASS,
                    derived_status=DerivedStatus.DERIVED_READY,
                    quality_profile_version="quality-v1",
                    converter_version="converter-v1",
                ),
            )
        ),
        annotations=InMemoryAnnotationSnapshot(
            (
                ApprovedAnnotationSnapshotV1(
                    rollout_id="rollout-a",
                    annotation_revision=2,
                ),
            )
        ),
        repository=manifests,
        artifact_sink=sink,
    )
    coordinator = ExportCoordinator(
        source=InMemoryExportSource([_step()]),
        sink=sink,
        exporters=[InMemoryLanceSnapshotExporter()],
    )
    launcher = InMemoryWorkflowLauncher()
    audit = InMemoryExportAuditRecorder()
    monkeypatch.setattr(router_module, "_publisher", publisher)
    monkeypatch.setattr(router_module, "_export_coordinator", coordinator)
    monkeypatch.setattr(router_module, "_audit_recorder", audit)
    monkeypatch.setattr(router_module, "get_launcher", lambda: launcher)
    monkeypatch.setattr(workflow_module, "get_launcher", lambda: launcher)

    app = FastAPI()
    app.include_router(publishing_router)
    app.include_router(workflow_router)
    current: dict[str, AuthContext | None] = {"auth": None}

    @app.middleware("http")
    async def install_verified_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.auth_context = current["auth"]
        return await call_next(request)

    @app.exception_handler(ProblemException)
    async def problem_handler(_: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.problem.status,
            content=exc.problem.model_dump(mode="json", exclude_none=True),
            media_type="application/problem+json",
        )

    with TestClient(app) as client:
        yield client, current, audit


def _publisher_auth(project_id: str = "project-a") -> AuthContext:
    return AuthContext(
        subject_id="publisher-a",
        project_ids=frozenset({project_id}),
        region_codes=frozenset(),
        roles=frozenset({"publisher"}),
    )


def _export_url(job_id: str | None = None) -> str:
    base = "/api/v1/datasets/dataset-a/versions/v1/exports"
    return base if job_id is None else f"{base}/{job_id}"


def test_first_export_materializes_missing_ready_lance_manifest(
    export_api: tuple[TestClient, dict[str, AuthContext | None], InMemoryExportAuditRecorder],
) -> None:
    client, current, _audit = export_api
    current["auth"] = _publisher_auth()

    created = client.post(
        "/api/v1/datasets/dataset-a/versions/version_lance_1/exports",
        json={"project_id": "project-a", "format": "lance_snapshot"},
        headers={"Idempotency-Key": "first-ready-export"},
    )

    assert created.status_code == 202
    assert created.json()["status"] == "SUCCEEDED"
    assert created.json()["dataset_version"] == "version_lance_1"
    assert created.json()["result"]["media_type"] == "application/zip"


def test_missing_non_lance_publication_is_not_inferred(
    export_api: tuple[TestClient, dict[str, AuthContext | None], InMemoryExportAuditRecorder],
) -> None:
    client, current, _audit = export_api
    current["auth"] = _publisher_auth()

    response = client.post(
        "/api/v1/datasets/dataset-a/versions/custom-version/exports",
        json={"project_id": "project-a", "format": "lance_snapshot"},
        headers={"Idempotency-Key": "unknown-version-export"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "DATASET_VERSION_NOT_FOUND"


def test_export_job_is_idempotent_redacts_worker_locators_and_reauthorizes_download(
    export_api: tuple[TestClient, dict[str, AuthContext | None], InMemoryExportAuditRecorder],
) -> None:
    client, current, audit = export_api
    anonymous = client.post(
        _export_url(),
        json={"project_id": "project-a", "format": "lance_snapshot"},
        headers={"Idempotency-Key": "export-1"},
    )
    assert anonymous.status_code == 401

    current["auth"] = _publisher_auth()
    created = client.post(
        _export_url(),
        json={"project_id": "project-a", "format": "lance_snapshot"},
        headers={"Idempotency-Key": "export-1"},
    )
    assert created.status_code == 202
    assert created.headers["cache-control"] == "no-store"
    assert created.headers["location"].endswith(created.json()["job_id"])
    assert created.json()["status"] == "SUCCEEDED"
    assert created.json()["progress"] == {
        "phase": "completed",
        "completed_phases": 3,
        "total_phases": 3,
    }
    assert "artifact_uri" not in created.text
    assert "download_uri" not in created.text
    job_id = created.json()["job_id"]

    replay = client.post(
        _export_url(),
        json={"project_id": "project-a", "format": "lance_snapshot"},
        headers={"Idempotency-Key": "export-1"},
    )
    assert replay.status_code == 202
    assert replay.json()["job_id"] == job_id

    status = client.get(_export_url(job_id), params={"project_id": "project-a"})
    assert status.status_code == 200
    assert status.json()["result"]["row_count"] == 1
    assert "artifact_uri" not in status.text
    assert "download_uri" not in status.text

    generic = client.get(f"/api/v1/jobs/{job_id}")
    assert generic.status_code == 200
    assert "artifact_uri" not in generic.text
    assert "download_uri" not in generic.text

    download = client.get(f"{_export_url(job_id)}/download", params={"project_id": "project-a"})
    assert download.status_code == 200
    assert download.headers["cache-control"] == "no-store"
    assert download.json()["download_url"].startswith("memory://download/")
    assert download.json()["media_type"] == "application/zip"
    assert ".zip" in download.json()["download_url"]
    assert len(audit.events) == 1
    event = audit.events[0]
    assert event.job_id == job_id
    assert event.actor_id == "publisher-a"
    assert event.artifact_content_hash == download.json()["artifact_content_hash"]
    assert "memory://" not in repr(event)

    repeated_download = client.get(
        f"{_export_url(job_id)}/download", params={"project_id": "project-a"}
    )
    assert repeated_download.status_code == 200
    assert len(audit.events) == 2
    assert all(item.job_id == job_id for item in audit.events)

    current["auth"] = _publisher_auth("project-b")
    denied = client.get(f"{_export_url(job_id)}/download", params={"project_id": "project-a"})
    assert denied.status_code == 403
    assert len(audit.events) == 2


def test_failed_export_can_be_retried_but_success_cannot(
    export_api: tuple[TestClient, dict[str, AuthContext | None], InMemoryExportAuditRecorder],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, current, _ = export_api
    import hc_data_platform.publishing.router as router_module

    current["auth"] = _publisher_auth()
    unavailable = ExportCoordinator(
        source=InMemoryExportSource([_step()]),
        sink=InMemoryArtifactSink(),
        exporters=[],
    )
    monkeypatch.setattr(router_module, "_export_coordinator", unavailable)
    failed = client.post(
        _export_url(),
        json={"project_id": "project-a", "format": "lance_snapshot"},
        headers={"Idempotency-Key": "initial-failure"},
    )
    assert failed.status_code == 202
    assert failed.json()["status"] == "FAILED"
    assert failed.json()["error_code"] == "EXPORTER_UNAVAILABLE"
    assert "501" not in failed.text

    sink = InMemoryArtifactSink()
    monkeypatch.setattr(
        router_module,
        "_export_coordinator",
        ExportCoordinator(
            source=InMemoryExportSource([_step()]),
            sink=sink,
            exporters=[InMemoryLanceSnapshotExporter()],
        ),
    )
    retry = client.post(
        f"{_export_url(failed.json()['job_id'])}:retry",
        params={"project_id": "project-a"},
        headers={"Idempotency-Key": "retry-1"},
    )
    assert retry.status_code == 202
    assert retry.json()["status"] == "SUCCEEDED"
    assert retry.json()["job_id"] != failed.json()["job_id"]

    rejected = client.post(
        f"{_export_url(retry.json()['job_id'])}:retry",
        params={"project_id": "project-a"},
        headers={"Idempotency-Key": "retry-2"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "EXPORT_RETRY_NOT_ALLOWED"


def test_running_export_cancel_is_idempotent_and_cancelled_job_can_retry(
    export_api: tuple[TestClient, dict[str, AuthContext | None], InMemoryExportAuditRecorder],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, current, _ = export_api
    import hc_data_platform.publishing.router as router_module
    import hc_data_platform.workflow.router as workflow_module

    launcher = PendingInMemoryWorkflowLauncher()
    monkeypatch.setattr(router_module, "get_launcher", lambda: launcher)
    monkeypatch.setattr(workflow_module, "get_launcher", lambda: launcher)
    current["auth"] = _publisher_auth()
    created = client.post(
        _export_url(),
        json={"project_id": "project-a", "format": "lance_snapshot"},
        headers={"Idempotency-Key": "cancel-1"},
    )
    assert created.status_code == 202
    assert created.json()["status"] == "RUNNING"
    assert created.json()["progress"] == {
        "phase": "materializing",
        "completed_phases": 0,
        "total_phases": 3,
    }
    job_id = created.json()["job_id"]

    cancelled = client.post(
        f"{_export_url(job_id)}:cancel",
        params={"project_id": "project-a"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["cancellation_requested"] is True
    assert cancelled.headers["cache-control"] == "no-store"

    repeated_cancel = client.post(
        f"{_export_url(job_id)}:cancel",
        params={"project_id": "project-a"},
    )
    assert repeated_cancel.status_code == 200
    assert repeated_cancel.json() == cancelled.json()

    retry = client.post(
        f"{_export_url(job_id)}:retry",
        params={"project_id": "project-a"},
        headers={"Idempotency-Key": "cancel-retry-1"},
    )
    assert retry.status_code == 202
    assert retry.json()["status"] == "RUNNING"
    assert retry.json()["job_id"] != job_id
