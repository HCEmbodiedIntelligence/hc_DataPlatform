from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from hc_data_platform.dashboard.models import (
    DashboardSectionStatus,
    TaskAttainment,
    TaskPackageMainState,
    TaskProcessingStage,
)
from hc_data_platform.dashboard.repository import (
    InMemoryDashboardRepository,
    TaskStatusPackageFact,
    TaskStatusProjectionFacts,
    TaskStatusTaskFact,
)
from hc_data_platform.dashboard.service import DashboardService
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.capabilities import CAPABILITY_DASHBOARD_READ

NOW = datetime(2026, 8, 24, 8, tzinfo=timezone.utc)


def auth() -> AuthContext:
    return AuthContext(
        subject_id="operator-a",
        project_ids=frozenset({"project-a"}),
        region_codes=frozenset({"cn-east"}),
        roles=frozenset(),
        scope_pairs=frozenset({("project-a", "cn-east")}),
        scoped_capabilities=frozenset({("project-a", CAPABILITY_DASHBOARD_READ)}),
    )


def task(
    task_id: str = "task-a",
    *,
    lifecycle: str = "ACTIVE",
    registered: int = 1,
    received: int = 1,
    target: int | None = None,
    duration_target: float | None = None,
    confirmed_duration: float = 0,
) -> TaskStatusTaskFact:
    return TaskStatusTaskFact(
        task_id=task_id,
        task_code="00000001",
        name=f"Task {task_id}",
        lifecycle=lifecycle,
        target_package_count=target,
        target_duration_seconds=duration_target,
        registered_count=registered,
        received_count=received,
        confirmed_duration_seconds=confirmed_duration,
    )


def test_duration_target_uses_only_confirmed_device_saved_facts() -> None:
    in_progress = status(
        package(),
        task_fact=task(duration_target=60, confirmed_duration=0),
    )
    attained = status(
        package(),
        task_fact=task(duration_target=60, confirmed_duration=60),
    )

    assert in_progress.attainment is TaskAttainment.IN_PROGRESS
    assert in_progress.task.device_progress.confirmed_duration_seconds == 0
    assert attained.attainment is TaskAttainment.ATTAINED


def package(**changes: object) -> TaskStatusPackageFact:
    base = TaskStatusPackageFact(
        task_id="task-a",
        rollout_id="rollout-a",
        data_package_id="package-a",
        rollout_status="RAW_COMMITTED",
        upload_status="RAW_COMMITTED",
        raw_committed=True,
    )
    return replace(base, **changes)


def status(
    package_fact: TaskStatusPackageFact,
    *,
    task_fact: TaskStatusTaskFact | None = None,
) -> object:
    repository = InMemoryDashboardRepository(
        task_status_projection=TaskStatusProjectionFacts(
            tasks=(task_fact or task(),),
            packages=(package_fact,),
        )
    )
    return (
        DashboardService(repository, clock=lambda: NOW)
        .task_status(
            auth=auth(),
            project_id="project-a",
            region_code="cn-east",
        )
        .selected
    )


@pytest.mark.parametrize(
    ("fact", "expected"),
    (
        (
            package(rollout_status="REGISTERED", upload_status="REGISTERED", raw_committed=False),
            TaskPackageMainState.REGISTERED,
        ),
        (package(verification_status=None), TaskPackageMainState.RAW_RECEIVED),
        (
            package(
                verification_status="REJECTED",
                verification_reason_code="MCAP_FOOTER_MISSING",
            ),
            TaskPackageMainState.VALIDATION_FAILED,
        ),
        (
            package(verification_status="RAW_VERIFIED", qc_status="PASS"),
            TaskPackageMainState.STANDARDIZATION_WAITING,
        ),
        (
            package(verification_status="RAW_VERIFIED", qc_status="RISK"),
            TaskPackageMainState.QC_RISK,
        ),
        (
            package(verification_status="RAW_VERIFIED", qc_status="REJECT"),
            TaskPackageMainState.QC_REJECT,
        ),
        (
            package(
                verification_status="RAW_VERIFIED",
                qc_status="PASS",
                workflow_status="RUNNING",
                workflow_stage="alignment",
            ),
            TaskPackageMainState.ALIGNING,
        ),
        (
            package(
                verification_status="RAW_VERIFIED",
                qc_status="PASS",
                alignment_status="READY",
                workflow_status="RUNNING",
                workflow_stage="lance_commit",
            ),
            TaskPackageMainState.LANCE_WRITING,
        ),
        (
            package(
                verification_status="RAW_VERIFIED",
                qc_status="PASS",
                alignment_status="ABORTED",
                workflow_status="TECHNICAL_FAILED",
                workflow_stage="alignment",
                workflow_error_code="ALIGNMENT_IO_ERROR",
            ),
            TaskPackageMainState.ALIGNMENT_FAILED,
        ),
        (
            package(
                verification_status="RAW_VERIFIED",
                qc_status="PASS",
                alignment_status="READY",
                workflow_status="TECHNICAL_FAILED",
                workflow_stage="lance_commit",
                workflow_error_code="LANCE_WRITE_FAILED",
            ),
            TaskPackageMainState.LANCE_FAILED,
        ),
        (
            package(
                verification_status="RAW_VERIFIED",
                qc_status="PASS",
                alignment_status="READY",
                lance_ready=True,
            ),
            TaskPackageMainState.STANDARDIZED_READY,
        ),
    ),
)
def test_each_package_has_one_current_main_state(
    fact: TaskStatusPackageFact,
    expected: TaskPackageMainState,
) -> None:
    selected = status(fact)
    assert selected is not None
    assert selected.main_state_counts == {expected: 1}


def test_raw_structure_failure_is_not_qc_reject_and_has_diagnostic_action() -> None:
    selected = status(
        package(
            verification_status="REJECTED",
            verification_reason_code="MCAP_FOOTER_MISSING",
        )
    )
    assert selected is not None
    assert selected.qc.rejected == 0
    assert selected.qc.waiting == 0
    assert selected.blockers[0].category == "RAW_VALIDATION"
    assert selected.blockers[0].reason_code == "MCAP_FOOTER_MISSING"
    assert selected.actions[0].action == "VIEW_RAW_DIAGNOSTICS"


@pytest.mark.parametrize("outcome", ("RISK", "REJECT"))
def test_quality_findings_are_isolated_without_blocking_the_task(outcome: str) -> None:
    selected = status(package(verification_status="RAW_VERIFIED", qc_status=outcome))
    assert selected is not None
    assert selected.standardization.blocked_by_quality == 0
    assert selected.standardization.isolated_by_quality == 1
    assert selected.standardization.ready == 0
    assert selected.blocker_count == 0
    assert selected.blockers == ()
    assert selected.actions[0].action == "VIEW_QC_ANOMALIES"
    assert selected.actions[0].deep_link == "/manual/issues?source=AUTO_QC"
    assert all(action.action != "RETRY_TECHNICAL_PROCESSING" for action in selected.actions)


def test_historical_duplicate_source_episode_is_visible_and_isolated() -> None:
    selected = status(
        package(
            duplicate_of_rollout_id="rollout-canonical",
            verification_status="RAW_VERIFIED",
            qc_status="PASS",
            lance_ready=True,
            workflow_status="TECHNICAL_FAILED",
            workflow_stage="lance",
            workflow_error_code="DUPLICATE_HISTORY_FAILURE",
        )
    )
    assert selected is not None
    assert selected.qc.duplicate == 1
    assert selected.qc.passed == 0
    assert selected.standardization.ready == 0
    assert selected.standardization.isolated_by_quality == 1
    assert selected.blocker_count == 0
    assert selected.blockers == ()
    assert selected.actions[0].action == "VIEW_QC_ANOMALIES"
    downstream = {
        item.stage: item
        for item in selected.stages
        if item.stage
        in {
            TaskProcessingStage.AUTOMATIC_VALIDATION,
            TaskProcessingStage.STANDARDIZATION,
            TaskProcessingStage.ANNOTATION,
            TaskProcessingStage.REVIEW,
            TaskProcessingStage.PUBLICATION,
        }
    }
    assert all(item.isolated == 1 for item in downstream.values())


def test_qc_pass_with_alignment_failure_keeps_qc_pass_fact() -> None:
    selected = status(
        package(
            verification_status="RAW_VERIFIED",
            qc_status="PASS",
            alignment_status="ABORTED",
            workflow_status="TECHNICAL_FAILED",
            workflow_stage="alignment",
        )
    )
    assert selected is not None
    assert selected.qc.passed == 1
    assert selected.qc.rejected == 0
    assert selected.standardization.alignment_failed == 1
    assert selected.blockers[0].category == "TECHNICAL"


def test_multiple_tasks_require_explicit_selection_and_preserve_individual_cohorts() -> None:
    repository = InMemoryDashboardRepository(
        task_status_projection=TaskStatusProjectionFacts(
            tasks=(task("task-a"), task("task-b")),
            packages=(
                package(),
                package(task_id="task-b", rollout_id="rollout-b", data_package_id="package-b"),
            ),
        )
    )
    service = DashboardService(repository, clock=lambda: NOW)
    unselected = service.task_status(auth=auth(), project_id="project-a", region_code="cn-east")
    assert unselected.selected is None
    assert unselected.selected_task_id is None
    assert unselected.pipeline.task_count == 2
    assert unselected.pipeline.package_count == 2
    assert all(
        sum(
            (
                stage.waiting,
                stage.running,
                stage.succeeded,
                stage.risk,
                stage.isolated,
                stage.blocked,
                stage.failed,
                stage.unavailable,
            )
        )
        == 2
        for stage in unselected.pipeline.stages
    )
    assert unselected.pipeline.stages[0].succeeded == 2
    assert unselected.pipeline.stages[2].succeeded == 2

    selected = service.task_status(
        auth=auth(), project_id="project-a", region_code="cn-east", task_id="task-b"
    )
    assert selected.selected_task_id == "task-b"
    assert selected.selected is not None
    assert selected.pipeline.task_count == 1
    assert selected.pipeline.package_count == 1
    assert sum(selected.selected.main_state_counts.values()) == 1


def test_attainment_does_not_close_active_lifecycle() -> None:
    selected = status(
        package(verification_status="RAW_VERIFIED", qc_status="PASS"),
        task_fact=task(target=1, lifecycle="ACTIVE"),
    )
    assert selected is not None
    assert selected.attainment is TaskAttainment.ATTAINED
    assert selected.task.lifecycle.value == "ACTIVE"
    assert selected.actions[-1].action == "CLOSE_TASK"


def test_missing_fact_source_is_partial_not_a_fabricated_zero() -> None:
    repository = InMemoryDashboardRepository(
        task_status_projection=TaskStatusProjectionFacts(
            tasks=(task(),),
            packages=(package(verification_status="RAW_VERIFIED"),),
            unavailable_sources=("quality_rollout_summaries",),
        )
    )
    response = DashboardService(repository, clock=lambda: NOW).task_status(
        auth=auth(), project_id="project-a", region_code="cn-east"
    )
    assert response.section.status is DashboardSectionStatus.PARTIAL
    assert response.selected is not None
    assert response.selected.qc.unavailable == 1


def test_missing_durable_workflow_outcome_is_unknown_not_lance_success_or_failure() -> None:
    repository = InMemoryDashboardRepository(
        task_status_projection=TaskStatusProjectionFacts(
            tasks=(task(),),
            packages=(
                package(
                    verification_status="RAW_VERIFIED",
                    qc_status="PASS",
                    alignment_status="READY",
                    technical_state_available=False,
                ),
            ),
            unavailable_sources=("workflow_execution_state",),
        )
    )
    response = DashboardService(repository, clock=lambda: NOW).task_status(
        auth=auth(), project_id="project-a", region_code="cn-east"
    )
    assert response.section.status is DashboardSectionStatus.PARTIAL
    assert response.selected is not None
    assert response.selected.main_state_counts == {TaskPackageMainState.UNKNOWN: 1}
    assert response.selected.standardization.unavailable == 1
