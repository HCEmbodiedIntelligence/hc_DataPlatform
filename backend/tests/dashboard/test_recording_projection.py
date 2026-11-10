from datetime import datetime, timezone

import pytest

from hc_data_platform.collection_tasks.captures import capture_counts, capture_from_row
from hc_data_platform.dashboard.models import TaskPackageMainState, TaskProcessingStage
from hc_data_platform.dashboard.recording_projection import (
    RecordingEpisodeWorkflow,
    merge_recording_facts,
    recording_package,
)
from hc_data_platform.dashboard.repository import TaskStatusTaskFact
from hc_data_platform.dashboard.service import _data_issues, _package_main_state, _stage_counts
from hc_data_platform.quality.models import AutoQualityProblemV1
from tests.quality.test_router import _report

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


def capture(*, status="READY_FOR_SLICING", episodes=(), state="COMMITTED", quality=None):
    return capture_from_row(
        (
            "u",
            "t",
            "robot",
            "dataset",
            "rec",
            state,
            NOW,
            NOW,
            "2026-10-10T00:00:00+00:00",
            "2026-10-10T00:00:02+00:00",
            status,
            "READY",
            None,
            episodes,
            "CONTINUOUS",
            "rollout",
            "pkg",
            None,
            None,
            {"status": quality} if quality else None,
            None,
        )
    )


@pytest.mark.parametrize(("state", "quality", "bucket"), [
    ("PENDING_QC", None, "waiting"),
    ("QC_RUNNING", None, "running"),
    ("QC_FAILED", "REJECT", "isolated"),
    ("READY_FOR_SLICING", "PASS", "succeeded"),
])
def test_unsliced_recordings_already_participate_in_quality(state, quality, bucket):
    item = capture(status=state, quality=quality)
    fact = recording_package(item)
    stages = {stage.stage: stage for stage in _stage_counts((fact,))}
    assert getattr(stages[TaskProcessingStage.AUTOMATIC_VALIDATION], bucket) == 1
    assert item.episode_count == 0
    assert not fact.lance_ready
    if state == "READY_FOR_SLICING":
        assert _package_main_state(fact) is TaskPackageMainState.AWAITING_SLICE
    if state == "QC_FAILED":
        assert capture_counts((item,)).failed_count == 1
        assert _data_issues((fact,))[0].category == "QUALITY"


def test_receipt_is_not_admission_and_qc_failure_is_visible_in_existing_dashboard():
    item = capture()
    fact = recording_package(item)
    assert _package_main_state(fact) == TaskPackageMainState.AWAITING_SLICE
    assert not fact.lance_ready
    stages = {stage.stage: stage for stage in _stage_counts((fact,))}
    assert stages[TaskProcessingStage.RAW_RECEIPT].succeeded == 1
    assert stages[TaskProcessingStage.STANDARDIZATION].succeeded == 0
    failed = capture(
        status="SLICED",
        episodes=[{"status": "QC_FAILED", "quality": "REJECT", "error": "QC_REJECTED"}] * 2,
    )
    counts = capture_counts((failed,))
    assert counts.received_count == 1 and counts.failed_episode_count == 2
    issues = _data_issues((recording_package(failed),))
    assert len(issues) == 1 and issues[0].recording_id == "rec"
    assert issues[0].category == "QUALITY"


def test_dashboard_reuses_package_identity_and_filters_task_scope():
    item = capture()
    task = TaskStatusTaskFact(task_id="t", task_code="1", name="Task", lifecycle="ACTIVE")
    tasks, packages = merge_recording_facts((task,), (), (item,), {}, None)
    assert tasks[0].received_count == 1 and len(packages) == 1
    tasks2, packages2 = merge_recording_facts(tasks, packages, (item,), {}, None)
    assert tasks2[0].received_count == 1 and len(packages2) == 1
    assert merge_recording_facts((), (), (item,), {}, None) == ((), ())


def test_outer_recording_gate_can_reject_even_when_inner_quality_report_passes():
    document = {
        "status": "REJECT",
        "episode_id": "episode2",
        "finding_codes": ["OPENARM_CAPTURE_FRAME_INVALID"],
        "quality_report": _report().model_dump(mode="json"),
    }
    problem = AutoQualityProblemV1.from_recording_report(
        document, report_sha256="a" * 64, recording_id="rec", data_package_id="pkg", updated_at=NOW
    )
    assert problem.status == "REJECT" and problem.recording_episode_id == "episode2"
    assert problem.finding_codes == ("OPENARM_CAPTURE_FRAME_INVALID",)
    with pytest.raises(ValueError):
        AutoQualityProblemV1.from_recording_report(
            {**document, "status": "PASS"},
            report_sha256="a" * 64,
            recording_id="rec",
            data_package_id="pkg",
            updated_at=NOW,
        )


def test_dispatch_block_is_visible_without_fabricating_a_qc_failure():
    item = capture(
        status="SLICED",
        episodes=[
            {
                "status": "DISPATCH_BLOCKED",
                "quality": None,
                "error": "RECORDING_DATASET_SCHEMA_MISMATCH",
            }
        ],
    )
    assert item.stage == "BLOCKED" and item.qc_outcome is None
    assert item.failed_episode_count == 0 and item.blocked_episode_count == 1
    assert capture_counts((item,)).blocked_count == 1
    issue = _data_issues((recording_package(item),))[0]
    assert issue.category == "TECHNICAL"
    assert issue.reason_code == "RECORDING_DATASET_SCHEMA_MISMATCH"


def test_discarded_episodes_leave_failure_counts_and_keep_siblings():
    dropped = {"status": "DISCARDED", "quality": "REJECT", "error": None}
    failed = {"status": "QC_FAILED", "quality": "REJECT", "error": "QC_REJECTED"}
    one_remaining = capture(status="SLICED", episodes=[dropped, failed])
    assert one_remaining.failed_episode_count == 1
    assert capture_counts((one_remaining,)).failed_episode_count == 1
    all_removed = capture(status="SLICED", episodes=[dropped, dropped])
    assert all_removed.stage == "DISCARDED"
    assert all_removed.failed_episode_count == 0
    assert not _data_issues((recording_package(all_removed),))


@pytest.mark.parametrize(
    ("status", "review", "main", "annotation_bucket", "review_bucket"),
    [
        ("DRAFT", None, "ANNOTATING", "running", "not_started"),
        ("SUBMITTED", None, "REVIEW_PENDING", "succeeded", "waiting"),
        ("APPROVED", "APPROVE", "PUBLISH_PENDING", "succeeded", "succeeded"),
        ("NEEDS_REVISION", "NEEDS_REVISION", "ANNOTATING", "running", "blocked"),
        ("REJECTED", "REJECT", "ANNOTATING", "running", "blocked"),
    ],
)
def test_recording_uses_bound_episode_annotation_status(
    status, review, main, annotation_bucket, review_bucket
):
    item = capture(status="SLICED", episodes=[{"status": "READY", "quality": "PASS"}])
    fact = recording_package(
        item, episodes=(RecordingEpisodeWorkflow("annotation-a", status, review),)
    )
    assert fact.annotation_task_id == "annotation-a"
    assert _package_main_state(fact).value == main
    stages = {stage.stage: stage for stage in _stage_counts((fact,))}
    assert getattr(stages[TaskProcessingStage.ANNOTATION], annotation_bucket) == 1
    assert getattr(stages[TaskProcessingStage.REVIEW], review_bucket) == 1
    assert stages[TaskProcessingStage.RAW_RECEIPT].succeeded == 1
    assert stages[TaskProcessingStage.STANDARDIZATION].succeeded == 1


@pytest.mark.parametrize(
    ("unfinished", "main"),
    [
        (RecordingEpisodeWorkflow(), "STANDARDIZED_READY"),
        (RecordingEpisodeWorkflow("draft", "DRAFT"), "ANNOTATING"),
        (RecordingEpisodeWorkflow("submitted", "SUBMITTED"), "REVIEW_PENDING"),
        (RecordingEpisodeWorkflow("returned", "NEEDS_REVISION", "NEEDS_REVISION"), "ANNOTATING"),
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_completed_sibling_cannot_hide_an_unfinished_recording_slice(unfinished, main, reverse):
    item = capture(status="SLICED", episodes=[{"status": "READY", "quality": "PASS"}] * 2)
    episodes = (RecordingEpisodeWorkflow("complete", "APPROVED", "APPROVE", True), unfinished)
    fact = recording_package(item, episodes=episodes[::-1] if reverse else episodes)
    assert fact.annotation_task_id == unfinished.annotation_task_id
    assert _package_main_state(fact).value == main
    assert not fact.published
    stages = {stage.stage: stage for stage in _stage_counts((fact,))}
    assert stages[TaskProcessingStage.REVIEW].succeeded == 0
    assert stages[TaskProcessingStage.RAW_RECEIPT].succeeded == 1


def test_recording_publication_requires_all_slices_and_keeps_one_package():
    item = capture(status="SLICED", episodes=[{"status": "READY", "quality": "PASS"}] * 2)
    episodes = (
        RecordingEpisodeWorkflow("annotation-a", "APPROVED", "APPROVE", True),
        RecordingEpisodeWorkflow("annotation-b", "APPROVED", "APPROVE", False),
    )
    fact = recording_package(item, episodes=episodes)
    assert _package_main_state(fact) == TaskPackageMainState.PUBLISH_PENDING
    complete = recording_package(item, episodes=(episodes[0], episodes[0]))
    assert _package_main_state(complete) == TaskPackageMainState.PUBLISHED
    stages = {stage.stage: stage for stage in _stage_counts((complete,))}
    assert stages[TaskProcessingStage.ANNOTATION].succeeded == 1
    assert stages[TaskProcessingStage.REVIEW].succeeded == 1
    assert stages[TaskProcessingStage.PUBLICATION].succeeded == 1


@pytest.mark.parametrize("selected_id", [None, "task-a", "task-b"])
def test_recording_annotation_mapping_keeps_tasks_separate_even_in_a_shared_dataset(selected_id):
    captures = tuple(
        capture(status="SLICED", episodes=[{"status": "READY", "quality": "PASS"}]).model_copy(
            update={
                "collection_task_id": f"task-{suffix}",
                "recording_id": f"rec-{suffix}",
                "data_package_id": f"pkg-{suffix}",
            }
        )
        for suffix in ("a", "b")
    )
    tasks = tuple(
        TaskStatusTaskFact(
            task_id=item.collection_task_id,
            task_code=str(index),
            name="same name",
            lifecycle="ACTIVE",
        )
        for index, item in enumerate(captures)
    )
    workflows = {
        "rec-a": (RecordingEpisodeWorkflow("annotation-a", "APPROVED", "APPROVE"),),
        "rec-b": (RecordingEpisodeWorkflow("annotation-b", "DRAFT"),),
    }
    _, packages = merge_recording_facts(tasks, (), captures, {}, selected_id, workflows=workflows)
    assert len(packages) == (2 if selected_id is None else 1)
    for fact in packages:
        suffix = fact.task_id[-1]
        assert fact.annotation_task_id == f"annotation-{suffix}"
        assert fact.annotation_status == ("APPROVED" if suffix == "a" else "DRAFT")
        assert selected_id is None or fact.task_id == selected_id

    _, refreshed = merge_recording_facts(
        tasks, packages, captures, {}, selected_id, workflows=workflows
    )
    assert refreshed == packages

def test_recording_commit_failure_reports_ingestion_stage_for_package_and_episode():
    code = "EPISODE_PACKAGE_COMMIT_FAILED"
    item = capture(status="SLICED", episodes=[{"status": "FAILED", "quality": "PASS", "error": code}])
    episode = RecordingEpisodeWorkflow(processing_status="FAILED", error_code=code, failure_stage="episode_commit")
    fact = recording_package(item, episodes=(episode,))
    assert _package_main_state(fact) == TaskPackageMainState.LANCE_FAILED
    assert _package_main_state(fact.episode_facts[0]) == TaskPackageMainState.LANCE_FAILED
    assert _data_issues((fact,))[0].reason_code == code


@pytest.mark.parametrize("error", [
    'duplicate key value violates unique constraint "recording_upload_assets_object_key_key" DETAIL',
    "MCAP_RECORDING_ASSET_CONFLICT",
])
def test_recording_asset_conflict_is_not_reported_as_bad_raw_or_failed_qc(error):
    item = capture().model_copy(update={
        "stage": "FAILED", "recording_id": None, "error_code": error, "qc_outcome": None,
    })
    issue, = _data_issues((recording_package(item),))
    assert (issue.category, issue.label, issue.reason_code, issue.qc_status) == (
        "TECHNICAL", "录制登记失败", "MCAP_RECORDING_ASSET_CONFLICT", None,
    )
    assert "质检尚未开始" in issue.description
