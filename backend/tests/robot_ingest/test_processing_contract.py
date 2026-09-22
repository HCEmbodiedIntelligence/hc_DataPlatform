from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from hc_data_platform.robot_ingest.models import (
    RobotIngestUpload,
    RobotIngestUploadManifest,
    UploadTarget,
)
from hc_data_platform.robot_ingest.processing_contract import (
    EpisodeProgress,
    EpisodeReceipt,
    ProcessingDocument,
    processing_result,
)


def upload():
    now = datetime.now(timezone.utc)
    manifest = RobotIngestUploadManifest(
        client_upload_id=str(uuid4()),
        collection_task_id="task",
        robot_id="robot",
        capture_mode="PRESEGMENTED",
        source_format="LEROBOT_V3",
        source_format_version="v3.0",
        declared_episode_count=2,
        capture_started_at=now,
        capture_ended_at=now + timedelta(seconds=1),
        assets=[
            dict(
                asset_id="info",
                path="meta/info.json",
                size_bytes=2,
                sha256="0" * 64,
                crc64="0",
                media_type="application/json",
            )
        ],
    )
    return RobotIngestUpload(
        upload_id="upload",
        client_upload_id=manifest.client_upload_id,
        ingest_identity_id="identity",
        authenticated_robot_id="robot",
        request_robot_id="robot",
        credential_id="credential",
        credential_version=1,
        target=UploadTarget(
            collection_task_id="task",
            organization_id="org",
            project_id="project",
            dataset_id="dataset",
            region_code="region",
            task_status="ACTIVE",
        ),
        collection_job_id="job",
        capture_mode=manifest.capture_mode,
        source_format=manifest.source_format,
        source_format_version=manifest.source_format_version,
        capture_started_at=now,
        capture_ended_at=manifest.capture_ended_at,
        declared_episode_count=2,
        total_bytes=2,
        state="COMMITTED",
        manifest_fingerprint="0" * 64,
        manifest=manifest,
        assets=(),
        cameras=(),
        raw_source_id="raw",
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=1),
    )


@pytest.mark.parametrize(
    "quality,action",
    [("PASS", "open_episode"), ("RISK", "review_quality"), ("REJECT", "review_quality")],
)
def test_quality_decisions_are_terminal_but_not_technical_failures(quality, action):
    doc = ProcessingDocument(
        phase="DONE",
        episodes=tuple(
            EpisodeProgress(
                source_episode_id=f"source-{i}",
                source_episode_index=i,
                episode_id=f"ep-{i}",
                status="READY",
                quality_status=quality,
            )
            for i in range(2)
        ),
    )
    result = processing_result(upload(), doc)
    assert result.processing_status == "READY" and result.quality_status == quality
    assert result.terminal and result.poll_after_seconds == 0
    assert all(ep.next_action == action and not ep.retryable for ep in result.episodes)


def test_partial_failure_is_not_ready_and_active_batch_is_not_terminal():
    episodes = (
        EpisodeProgress(
            source_episode_id="source-0",
            source_episode_index=0,
            episode_id="ep-0",
            status="READY",
            quality_status="PASS",
        ),
        EpisodeProgress(
            source_episode_id="source-1",
            source_episode_index=1,
            episode_id="ep-1",
            status="FAILED",
            error_code="STORAGE_TIMEOUT",
            retryable=True,
        ),
    )
    result = processing_result(upload(), ProcessingDocument(phase="DONE", episodes=episodes))
    assert result.processing_status == "PARTIALLY_FAILED"
    assert result.episodes[1].next_action == "retry_processing"
    active = processing_result(upload(), ProcessingDocument(phase="PROCESSING", episodes=episodes))
    assert not active.terminal and active.poll_after_seconds == 5


def test_empty_discovery_failure_and_pending_polling():
    result = processing_result(
        upload(),
        ProcessingDocument(phase="DONE", error_code="ROBOT_PROCESSOR_UNAVAILABLE", retryable=True),
    )
    assert result.processing_status == "FAILED" and result.episodes == ()
    assert result.terminal and result.poll_after_seconds == 0
    assert not processing_result(upload(), ProcessingDocument()).terminal


def test_pass_requires_actual_publication_receipt_but_reject_does_not():
    with pytest.raises(ValidationError):
        EpisodeReceipt(quality_status="PASS", frame_count=1, sample_count=1, qc_report_id="qc")
    EpisodeReceipt(quality_status="REJECT", frame_count=1, sample_count=1, qc_report_id="qc")


def test_unexpected_worker_exception_does_not_leak_signed_urls():
    from temporalio.exceptions import ApplicationError
    from temporalio.testing import ActivityEnvironment

    from hc_data_platform.robot_ingest.processing_worker import RobotProcessingActivities

    class BrokenStore:
        def load(self, task):
            raise RuntimeError("https://store/private?X-Amz-Signature=secret")

    activities = RobotProcessingActivities(BrokenStore(), None)
    request = {
        "task": dict(
            organization_id="org",
            project_id="project",
            region_code="region",
            upload_id="upload",
            raw_source_id="raw",
            generation=0,
            workflow_id="workflow",
        )
    }
    with pytest.raises(ApplicationError) as raised:
        ActivityEnvironment().run(activities.discover, request)
    assert raised.value.type == "ROBOT_PROCESSING_TECHNICAL_FAILURE"
    assert raised.value.__suppress_context__
    assert "secret" not in str(raised.value)
