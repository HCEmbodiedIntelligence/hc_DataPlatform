"""Stage queues are entered through real prerequisites, not future work."""

from dataclasses import replace

import pytest

from hc_data_platform.dashboard.models import TaskProcessingStage
from hc_data_platform.dashboard.recording_projection import (
    RecordingEpisodeWorkflow,
    recording_package,
)
from hc_data_platform.dashboard.repository import (
    InMemoryDashboardRepository,
    TaskStatusProjectionFacts,
)
from hc_data_platform.dashboard.service import DashboardService, _data_issues, _stage_counts
from tests.dashboard.test_recording_projection import capture
from tests.dashboard.test_task_status_projection import NOW, auth, package, status, task


@pytest.mark.parametrize(
    ("changes", "stage", "bucket"),
    [
        ({"raw_committed": False, "upload_status": "REGISTERED"}, "PACKAGE_UPLOAD", "waiting"),
        ({"raw_committed": False, "upload_status": "UPLOADING"}, "PACKAGE_UPLOAD", "running"),
        ({"raw_committed": False, "upload_status": "FAILED"}, "PACKAGE_UPLOAD", "failed"),
        ({"raw_committed": False, "upload_status": "PAUSED"}, "PACKAGE_UPLOAD", "blocked"),
        ({"raw_committed": False, "upload_status": "CANCELLED"}, "PACKAGE_UPLOAD", "blocked"),
        (
            {"raw_committed": False, "upload_status": "MULTIPART_COMPLETED"},
            "RAW_RECEIPT",
            "waiting",
        ),
        ({}, "AUTOMATIC_VALIDATION", "waiting"),
        (
            {"workflow_status": "PENDING", "workflow_stage": "verification"},
            "AUTOMATIC_VALIDATION",
            "waiting",
        ),
        (
            {"workflow_status": "RUNNING", "workflow_stage": "verification"},
            "AUTOMATIC_VALIDATION",
            "running",
        ),
        (
            {"verification_status": "RAW_VERIFIED", "workflow_stage": "awaiting_slice"},
            "AUTOMATIC_VALIDATION",
            "waiting",
        ),
        ({"verification_status": "REJECTED"}, "AUTOMATIC_VALIDATION", "failed"),
        (
            {
                "source_episode_status": "FAILED",
                "source_processing_status": "FAILED",
                "verification_reason_code": "LEROBOT_SOURCE_INVALID",
            },
            "AUTOMATIC_VALIDATION",
            "failed",
        ),
        (
            {
                "workflow_status": "TECHNICAL_FAILED",
                "workflow_stage": "verification",
                "verification_status": "RAW_VERIFIED",
            },
            "AUTOMATIC_VALIDATION",
            "failed",
        ),
        (
            {"source_episode_status": "PENDING", "source_processing_status": "RUNNING"},
            "AUTOMATIC_VALIDATION",
            "waiting",
        ),
        ({"qc_status": "PASS"}, "STANDARDIZATION", "waiting"),
        (
            {"qc_status": "PASS", "workflow_status": "PENDING", "workflow_stage": "alignment"},
            "STANDARDIZATION",
            "waiting",
        ),
        (
            {"qc_status": "PASS", "workflow_status": "RUNNING", "workflow_stage": "alignment"},
            "STANDARDIZATION",
            "running",
        ),
        (
            {
                "qc_status": "PASS",
                "workflow_status": "TECHNICAL_FAILED",
                "workflow_stage": "alignment",
            },
            "STANDARDIZATION",
            "failed",
        ),
        (
            {"qc_status": "PASS", "technical_state_available": False},
            "STANDARDIZATION",
            "unavailable",
        ),
        ({"qc_status": "PASS", "lance_ready": True}, "ANNOTATION", "waiting"),
        (
            {
                "qc_status": "PASS",
                "lance_ready": True,
                "annotation_task_id": "a",
                "annotation_status": "DRAFT",
            },
            "ANNOTATION",
            "running",
        ),
        (
            {
                "qc_status": "PASS",
                "lance_ready": True,
                "annotation_task_id": "a",
                "annotation_status": "SUBMITTED",
            },
            "REVIEW",
            "waiting",
        ),
        (
            {
                "qc_status": "PASS",
                "lance_ready": True,
                "annotation_task_id": "a",
                "annotation_status": "APPROVED",
            },
            "PUBLICATION",
            "waiting",
        ),
    ],
)
def test_only_the_reached_stage_has_active_work(changes, stage, bucket):
    result = status(package(**changes))
    stages = {item.stage.value: item for item in result.stages}
    reached = stages[stage]
    assert getattr(reached, bucket) == 1
    assert result.current_stage.value == stage
    later = False
    for item in result.stages:
        if later:
            assert item.not_started == 1, item
            assert item.waiting == item.running == item.failed == item.blocked == 0
        if item.stage.value == stage:
            later = True


def test_task_14_cohort_counts_approved_draft_and_parse_failure_separately():
    base = package(qc_status="PASS", lance_ready=True, annotation_task_id="a")
    facts = (
        replace(base, annotation_status="APPROVED"),
        replace(base, rollout_id="draft", data_package_id="draft", annotation_status="DRAFT"),
        package(
            rollout_id="failed",
            data_package_id="failed",
            source_episode_status="FAILED",
            source_processing_status="FAILED",
            verification_reason_code="LEROBOT_SOURCE_INVALID",
        ),
        package(rollout_id="discarded", data_package_id="discarded", resolution_status="DISCARDED"),
        package(rollout_id="duplicate", data_package_id="duplicate", duplicate_of_rollout_id="a"),
    )
    service = DashboardService(
        InMemoryDashboardRepository(
            task_status_projection=TaskStatusProjectionFacts(
                tasks=(task(registered=5, received=5),),
                packages=facts,
            )
        ),
        clock=lambda: NOW,
    )
    result = service.task_status(auth=auth(), project_id="project-a", region_code="cn-east")
    stages = {item.stage.value: item for item in result.pipeline.stages}
    assert stages["AUTOMATIC_VALIDATION"].failed == 1
    assert stages["AUTOMATIC_VALIDATION"].waiting == result.pipeline.qc.waiting == 0
    assert stages["STANDARDIZATION"].waiting == stages["ANNOTATION"].waiting == 0
    assert stages["REVIEW"].waiting == 0
    assert stages["REVIEW"].succeeded == 1
    assert stages["REVIEW"].not_started == 2
    assert stages["PUBLICATION"].waiting == 1
    assert stages["PUBLICATION"].not_started == 2
    assert result.pipeline.issues[0].reason_code == "LEROBOT_SOURCE_INVALID"
    assert result.selected.stages == result.pipeline.stages


@pytest.mark.parametrize("quality", ["RISK", "REJECT"])
def test_quality_problems_never_create_downstream_queues(quality):
    result = status(package(verification_status="RAW_VERIFIED", qc_status=quality))
    assert result.current_stage is TaskProcessingStage.AUTOMATIC_VALIDATION
    assert all(item.isolated == 1 for item in result.stages[4:])
    assert all(item.waiting == item.running == 0 for item in result.stages[4:])


@pytest.mark.parametrize("annotation_status", ["DRAFT", "SUBMITTED", "APPROVED"])
def test_latest_annotation_status_wins_over_old_review_decision(annotation_status):
    result = status(
        package(
            qc_status="PASS",
            lance_ready=True,
            annotation_task_id="a",
            annotation_status=annotation_status,
            review_decision="NEEDS_REVISION",
        )
    )
    review = result.stages[6]
    assert review.blocked == 0
    assert review.waiting == int(annotation_status == "SUBMITTED")
    assert review.succeeded == int(annotation_status == "APPROVED")


def test_published_cohort_has_no_invented_next_step():
    result = status(
        package(
            qc_status="PASS",
            lance_ready=True,
            annotation_task_id="a",
            annotation_status="APPROVED",
            published=True,
        )
    )
    assert result.current_stage is TaskProcessingStage.PUBLICATION
    assert result.next_step == "当前没有待处理项。"
    assert all(item.waiting == item.running == 0 for item in result.stages)


@pytest.mark.parametrize(
    "sibling",
    [
        RecordingEpisodeWorkflow("draft", "DRAFT"),
        RecordingEpisodeWorkflow("returned", "NEEDS_REVISION", "NEEDS_REVISION"),
        RecordingEpisodeWorkflow(processing_status="PENDING_QC", qc_status=None),
        RecordingEpisodeWorkflow(processing_status="QC_FAILED", qc_status="REJECT"),
    ],
)
def test_submitted_slice_remains_visible_when_sibling_has_not_reached_review(sibling):
    item = capture(
        status="SLICED",
        episodes=[
            {"status": "READY", "quality": "PASS"},
            {"status": sibling.processing_status, "quality": sibling.qc_status},
        ],
    )
    fact = recording_package(
        item,
        episodes=(
            sibling,
            RecordingEpisodeWorkflow("submitted", "SUBMITTED"),
        ),
    )
    stages = _stage_counts((fact,))
    assert stages[6].waiting == 1
    assert stages[6].succeeded == 0
    assert stages[7].waiting == 0


@pytest.mark.parametrize(
    ("processing", "quality", "stage", "bucket"),
    [
        ("PENDING_QC", None, 3, "waiting"),
        ("QC_RUNNING", None, 3, "running"),
        ("PENDING_ALIGNMENT", "PASS", 4, "waiting"),
        ("ALIGNING", "PASS", 4, "running"),
        ("FAILED", None, 3, "failed"),
        ("FAILED", "PASS", 4, "failed"),
    ],
)
def test_recording_waiting_running_and_failure_follow_slice_worker_state(
    processing,
    quality,
    stage,
    bucket,
):
    item = capture(status="SLICED", episodes=[{"status": processing, "quality": quality}])
    fact = recording_package(
        item,
        episodes=(
            RecordingEpisodeWorkflow(
                processing_status=processing,
                qc_status=quality,
                error_code="PROCESSING_ERROR" if processing == "FAILED" else None,
            ),
        ),
    )
    stages = _stage_counts((fact,))
    assert getattr(stages[stage], bucket) == 1
    assert all(item.not_started == 1 for item in stages[stage + 1 :])


def test_source_parse_error_is_a_real_problem_not_a_waiting_item():
    fact = package(
        source_episode_status="FAILED",
        source_processing_status="FAILED",
        verification_reason_code="LEROBOT_SOURCE_INVALID",
    )
    (issue,) = _data_issues((fact,))
    assert issue.stage is TaskProcessingStage.AUTOMATIC_VALIDATION
    assert issue.category == "TECHNICAL"
    assert issue.reason_code == "LEROBOT_SOURCE_INVALID"
