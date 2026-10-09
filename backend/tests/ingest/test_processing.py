from __future__ import annotations

from datetime import datetime, timezone

import pytest

from hc_data_platform.core.errors import ProblemException, problem
from hc_data_platform.ingest.models import (
    IngestTriggerStatus,
    IngestWorkflowLocator,
    UploadSession,
    UploadStatus,
)
from hc_data_platform.ingest.processing import (
    project_upload_processing_status,
    read_upload_processing_status,
)
from hc_data_platform.workflow.models import JobRecord, JobStatus
from hc_data_platform.workflow.names import INGEST_ROLLOUT_WORKFLOW

NOW = datetime(2026, 8, 21, 4, tzinfo=timezone.utc)
SOURCE_SHA256 = "a" * 64


def _session() -> UploadSession:
    return UploadSession(
        session_id="52faee8f-f489-4c19-8b54-38a51cf46899",
        project_id="project-a",
        region_code="cn-test",
        rollout_id="rollout-a",
        data_package_id="package-a",
        object_key="raw/v1/project=project-a/package-a/recording.mcap",
        expected_sha256=SOURCE_SHA256,
        expected_size=1024,
        expected_crc64=1,
        manifest_fingerprint="b" * 64,
        status=UploadStatus.RAW_COMMITTED,
        workflow=IngestWorkflowLocator(
            event_id="event-a",
            workflow_id="ingest-rollout:v1:project-a:cn-test%2Frollout-a",
            status=IngestTriggerStatus.DISPATCHED,
            attempts=1,
            updated_at=NOW,
        ),
    )


def _job(*, status: JobStatus = JobStatus.SUCCEEDED) -> JobRecord:
    session = _session()
    return JobRecord(
        job_id=session.workflow.workflow_id,
        workflow_id=session.workflow.workflow_id,
        workflow_run_id="run-a",
        job_type=INGEST_ROLLOUT_WORKFLOW,
        project_id=session.project_id,
        resource_id=session.rollout_id,
        status=status,
        stage="completed" if status is JobStatus.SUCCEEDED else "alignment",
        attempt=2,
        result={
            "alignment": {"frequency_hz": 30.0, "ignored": "not projected"},
            "derived": {
                "project_id": session.project_id,
                "dataset_id": "dataset_ingest_a",
                "rollout_id": session.rollout_id,
                "source_sha256": session.expected_sha256,
                "converter_version": "converter-v1",
                "dataset_version": 4,
                "lance_version": 7,
                "step_count": 300,
                "content_hash": "c" * 64,
            },
            "viewer_target": {
                "schema_version": "dataset-ingest-viewer-target/v1",
                "dataset_id": "dataset_ingest_a",
                "version_id": "version_lance_4",
                "episode_id": "episode_ingest_a",
                "revision_id": "revision_ingest_a",
            },
            "annotation_task": {"task_id": "annotation-a", "status": "CREATED"},
            "raw_secret": "must-not-cross-the-upload-api",
        },
        created_at=NOW,
        updated_at=NOW,
    )


class _MissingJobStatus:
    async def get(self, _job_id: str) -> JobRecord:
        raise problem(
            status=404,
            code="JOB_NOT_FOUND",
            title="Job not found",
            detail="The requested workflow job does not exist.",
        )


@pytest.mark.asyncio
async def test_missing_temporal_execution_projects_the_durable_dispatch_as_pending() -> None:
    result = await read_upload_processing_status(_session(), _MissingJobStatus())

    assert result.status.value == "PENDING"
    assert result.stage == "workflow_starting"
    assert result.aligned_media is None
    assert result.attempt == 1


def test_success_projects_only_the_lineage_checked_media_target() -> None:
    result = project_upload_processing_status(_session(), _job())

    assert result.model_dump(mode="json") == {
        "schema_version": "upload-processing-status/v1",
        "session_id": "52faee8f-f489-4c19-8b54-38a51cf46899",
        "rollout_id": "rollout-a",
        "workflow_id": "ingest-rollout:v1:project-a:cn-test%2Frollout-a",
        "status": "SUCCEEDED",
        "stage": "completed",
        "attempt": 2,
        "episode": None,
        "aligned_media": {
            "schema_version": "upload-aligned-media-target/v1",
            "project_id": "project-a",
            "dataset_id": "dataset_ingest_a",
            "rollout_id": "rollout-a",
            "dataset_version": 4,
            "annotation_task_id": "annotation-a",
            "fps": 30,
            "start_step": 0,
            "end_step": 300,
        },
        "viewer": {
            "schema_version": "dataset-ingest-viewer-target/v1",
            "dataset_id": "dataset_ingest_a",
            "version_id": "version_lance_4",
            "episode_id": "episode_ingest_a",
            "revision_id": "revision_ingest_a",
        },
        "error_code": None,
        "updated_at": "2026-08-21T04:00:00Z",
    }


@pytest.mark.parametrize(
    ("job_update", "expected_code"),
    [
        ({"project_id": "project-b"}, "UPLOAD_PROCESSING_IDENTITY_INVALID"),
        (
            {
                "result": {
                    **(_job().result or {}),
                    "derived": {
                        **((_job().result or {})["derived"]),
                        "source_sha256": "d" * 64,
                    },
                }
            },
            "UPLOAD_PROCESSING_LINEAGE_INVALID",
        ),
    ],
)
def test_processing_projection_fails_closed_on_cross_upload_facts(
    job_update: dict[str, object],
    expected_code: str,
) -> None:
    with pytest.raises(ProblemException) as raised:
        project_upload_processing_status(_session(), _job().model_copy(update=job_update))

    assert raised.value.problem.status == 500
    assert raised.value.problem.code == expected_code


def _episode_job():
    from hc_data_platform.episode_store.catalog_models import EpisodeDerivedReady
    from hc_data_platform.episode_store.models import DataVersionRef, EpisodeIdentity

    job = _job()
    legacy = job.result["derived"]
    data = DataVersionRef(
        identity=EpisodeIdentity(
            organization_id="org-a",
            project_id="project-a",
            region_code="cn-test",
            dataset_id=legacy["dataset_id"],
            episode_id="episode_ingest_a",
            rollout_id="rollout-a",
        ),
        data_version_id="dv_" + "1" * 64,
        version=1,
        content_hash="2" * 64,
        schema_snapshot_id="schema-a",
        package_prefix="private/path",
        frame_count=300,
    )
    derived = EpisodeDerivedReady(
        **{k: v for k, v in legacy.items() if k != "lance_version"},
        data=data,
        collection_version_id="cv_" + "3" * 64,
    )
    return job.model_copy(
        update={
            "result": {
                **job.result,
                "derived": derived.model_dump(mode="json"),
                "alignment": {"frequency_hz": 25},
                "viewer_target": {
                    **job.result["viewer_target"],
                    "version_id": "version_" + derived.collection_version_id,
                },
            }
        }
    )


def test_episode_status_uses_fixed_viewer_and_actual_frequency_without_storage_locators():
    result = project_upload_processing_status(_session(), _episode_job())
    assert result.aligned_media is None
    assert result.episode.fps == 25 and result.episode.frame_count == 300
    assert result.episode.annotation_task_id == "annotation-a"
    assert result.episode.content_hash == "2" * 64
    assert result.viewer.version_id == "version_cv_" + "3" * 64
    assert "private/path" not in result.model_dump_json()


@pytest.mark.parametrize(
    "field,value",
    [
        ("region_code", "other"),
        ("episode_id", "episode_other"),
        ("project_id", "other"),
        ("rollout_id", "other"),
    ],
)
def test_episode_status_rejects_inconsistent_identity(field, value):
    import copy

    job = _episode_job()
    result = copy.deepcopy(job.result)
    result["derived"]["data"]["identity"][field] = value
    with pytest.raises(ProblemException):
        project_upload_processing_status(_session(), job.model_copy(update={"result": result}))
