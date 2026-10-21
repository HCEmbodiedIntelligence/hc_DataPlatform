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
                receipt=EpisodeReceipt(
                    quality_status=quality,
                    frame_count=1,
                    sample_count=1,
                    dataset_version=1 if quality == "PASS" else None,
                    lance_version=1 if quality == "PASS" else None,
                    qc_report_id="qc",
                ),
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
            receipt=EpisodeReceipt(
                quality_status="PASS",
                frame_count=1,
                sample_count=1,
                dataset_version=1,
                lance_version=1,
                qc_report_id="qc",
            ),
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


def test_deleted_episode_is_history_and_does_not_surface_old_failure_or_quality():
    removed = EpisodeProgress(
        source_episode_id="source-0",
        source_episode_index=0,
        episode_id="ep-0",
        status="DISCARDED",
        quality_status="REJECT",
        error_code="OLD_FAILURE",
        retryable=True,
    )
    other = EpisodeProgress(
        source_episode_id="source-1",
        source_episode_index=1,
        episode_id="ep-1",
        status="FAILED",
        error_code="KEEP_THIS_FAILURE",
        retryable=True,
    )
    result = processing_result(
        upload(), ProcessingDocument(phase="DONE", episodes=(removed, other))
    )
    assert [ep.episode_id for ep in result.episodes] == ["ep-1"]
    assert result.episodes[0].error_code == "KEEP_THIS_FAILURE"
    assert result.quality_status == "PENDING"
    closed = processing_result(upload(), ProcessingDocument(phase="DONE", episodes=(removed,)))
    assert closed.terminal and closed.processing_status == "READY"
    assert closed.episodes == () and closed.quality_status == "PENDING"


def test_pass_requires_actual_publication_receipt_but_reject_does_not():
    with pytest.raises(ValidationError):
        EpisodeReceipt(quality_status="PASS", frame_count=1, sample_count=1, qc_report_id="qc")
    EpisodeReceipt(quality_status="REJECT", frame_count=1, sample_count=1, qc_report_id="qc")


def package_receipt():
    from hc_data_platform.episode_store.models import CommitReceipt, DataVersionRef, EpisodeIdentity

    return CommitReceipt(
        data=DataVersionRef(
            identity=EpisodeIdentity(
                organization_id="org",
                project_id="project",
                region_code="region",
                dataset_id="dataset",
                episode_id="episode_product",
                rollout_id="ep-0",
            ),
            data_version_id="dv_test",
            version=1,
            content_hash="1" * 64,
            schema_snapshot_id="schema",
            package_prefix="episodes/test",
            frame_count=2,
        ),
        idempotency_key="2" * 64,
        manifest_sha256="3" * 64,
    )


def test_new_receipt_uses_exact_package_and_reports_annotation_separately():
    receipt = EpisodeReceipt(
        quality_status="PASS",
        frame_count=3,
        sample_count=2,
        dataset_version=1,
        package_receipt=package_receipt(),
        qc_report_id="qc",
    )
    assert receipt.lance_version is None and receipt.data_ref.storage_format == "lerobot_v3"
    doc = ProcessingDocument(
        phase="DONE",
        episodes=(
            EpisodeProgress(
                source_episode_id="src",
                source_episode_index=0,
                episode_id="ep-0",
                status="READY",
                quality_status="PASS",
                receipt=receipt,
            ),
        ),
    )
    result = processing_result(upload().model_copy(update={"declared_episode_count": 1}), doc)
    assert result.quality_status == "PASS"
    assert set(result.episodes[0].model_dump()) == {
        "source_episode_id",
        "source_episode_index",
        "episode_id",
        "dataset_id",
        "status",
        "quality_status",
        "error_code",
        "retryable",
        "next_action",
        "data_readable",
        "annotation_status",
    }
    assert "data_ref" not in result.model_dump_json() and "lance" not in result.model_dump_json()
    assert "training" not in receipt.model_dump_json()
    assert result.episodes[0].data_readable is True
    assert result.episodes[0].annotation_status == "UNKNOWN"


@pytest.mark.parametrize(
    "change", [{"lance_version": 1}, {"dataset_version": None}, {"sample_count": 3}]
)
def test_package_receipt_rejects_mixed_storage_and_wrong_grid(change):
    values = dict(
        quality_status="PASS",
        frame_count=3,
        sample_count=2,
        dataset_version=1,
        package_receipt=package_receipt(),
        qc_report_id="qc",
    )
    with pytest.raises(ValidationError):
        EpisodeReceipt(**{**values, **change})


def test_ready_processing_document_requires_matching_receipt():
    values = dict(
        source_episode_id="src",
        source_episode_index=0,
        episode_id="ep-0",
        status="READY",
        quality_status="PASS",
    )
    with pytest.raises(ValidationError):
        EpisodeProgress(**values)
    receipt = EpisodeReceipt(
        quality_status="PASS",
        frame_count=2,
        sample_count=2,
        dataset_version=1,
        package_receipt=package_receipt(),
        qc_report_id="qc",
    )
    for change in ({"episode_id": "another"}, {"quality_status": "REJECT"}):
        with pytest.raises(ValidationError):
            EpisodeProgress(**{**values, "receipt": receipt, **change})


def test_unexpected_worker_exception_does_not_leak_signed_urls():
    from temporalio.exceptions import ApplicationError
    from temporalio.testing import ActivityEnvironment

    from hc_data_platform.robot_ingest.processing_worker import RobotProcessingActivities
    from hc_data_platform.workflow.execution import ActivityExecutionScope

    class BrokenStore:
        def load(self, task):
            raise RuntimeError("https://store/private?X-Amz-Signature=secret")

    activities = RobotProcessingActivities(
        BrokenStore(), None, execution_scope=ActivityExecutionScope(None)
    )
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
