from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any

import pytest

from hc_data_platform.collection_tasks.models import (
    CollectionTarget,
    CollectionTaskAttainmentStatus,
    CollectionTaskStatus,
    CollectionTaskTargetMetricStatus,
    CreateCollectionTask,
    QcOutcome,
    UpdateCollectionTask,
)
from hc_data_platform.collection_tasks.repository import InMemoryCollectionTaskRepository
from hc_data_platform.collection_tasks.service import CollectionTaskService
from hc_data_platform.core.errors import ProblemException

ORGANIZATION_ID = "org-a"


def command(*, name: str = "Bin picking") -> CreateCollectionTask:
    return CreateCollectionTask(
        name=name,
        type="COLLECTION",
        scenario="transparent-parts",
        description="Collect representative packages.",
        target=CollectionTarget(package_count=10),
    )


def create_task(
    service: CollectionTaskService,
    *,
    key: str = "create-1",
) -> tuple[str, str]:
    result = service.create(
        organization_id=ORGANIZATION_ID,
        project_id="project-a",
        command=command(),
        idempotency_key=key,
    )
    return result.record.collection_task_id, service.etag(result.record)


def assert_problem(captured: pytest.ExceptionInfo[ProblemException], code: str) -> None:
    assert captured.value.problem.code == code


def race_close(
    barrier: Barrier,
    service: CollectionTaskService,
    task_id: str,
    etag: str,
    iteration: int,
) -> str:
    barrier.wait()
    return service.close(
        organization_id=ORGANIZATION_ID,
        project_id="project-a",
        collection_task_id=task_id,
        if_match=etag,
        idempotency_key=f"close-{iteration}",
    ).record.status.value


def race_associate(
    barrier: Barrier,
    repository: InMemoryCollectionTaskRepository,
    task_id: str,
    iteration: int,
) -> str:
    barrier.wait()
    try:
        repository.record_received_package(
            organization_id=ORGANIZATION_ID,
            project_id="project-a",
            collection_task_id=task_id,
            package_id=f"package-{iteration}",
        )
        return "associated"
    except ProblemException as exc:
        assert exc.problem.code == "COLLECTION_TASK_CLOSED"
        return "closed"


def test_create_is_idempotent_and_exploratory_tasks_remain_explicitly_untargeted() -> None:
    service = CollectionTaskService()
    without_policy = CreateCollectionTask(
        name="Open policy",
        type="COLLECTION",
        scenario="general",
    )

    first = service.create(
        organization_id=ORGANIZATION_ID,
        project_id="project-a",
        command=without_policy,
        idempotency_key="same-key",
    )
    replay = service.create(
        organization_id=ORGANIZATION_ID,
        project_id="project-a",
        command=without_policy,
        idempotency_key="same-key",
    )

    assert replay.replayed is True
    assert first.record == replay.record
    assert first.record.status is CollectionTaskStatus.ACTIVE
    assert first.record.target is None
    assert first.record.quality_threshold is None
    assert first.record.task_code == "00000001"
    with pytest.raises(ValueError):
        CollectionTarget()
    with pytest.raises(ValueError):
        CollectionTarget(package_count=0)
    with pytest.raises(ValueError):
        CollectionTarget(duration_seconds=-1)

    with pytest.raises(ProblemException) as reused:
        service.create(
            organization_id=ORGANIZATION_ID,
            project_id="project-a",
            command=command(name="Different request"),
            idempotency_key="same-key",
        )
    assert_problem(reused, "IDEMPOTENCY_KEY_REUSED")


def test_tasks_can_share_an_existing_dataset_and_reassignment_locks_after_upload() -> None:
    first_dataset = "dataset_shared_collection"
    second_dataset = "dataset_other_collection"
    repository = InMemoryCollectionTaskRepository(
        assignable_datasets=(
            (ORGANIZATION_ID, "project-a", first_dataset),
            (ORGANIZATION_ID, "project-a", second_dataset),
        )
    )
    service = CollectionTaskService(repository)

    first = service.create(
        organization_id=ORGANIZATION_ID,
        project_id="project-a",
        command=command().model_copy(update={"dataset_id": first_dataset}),
        idempotency_key="shared-dataset-first",
    ).record
    second = service.create(
        organization_id=ORGANIZATION_ID,
        project_id="project-a",
        command=command(name="Second task").model_copy(update={"dataset_id": first_dataset}),
        idempotency_key="shared-dataset-second",
    ).record

    assert first.dataset_id == second.dataset_id == first_dataset
    reassigned = service.update(
        organization_id=ORGANIZATION_ID,
        project_id="project-a",
        collection_task_id=second.collection_task_id,
        command=UpdateCollectionTask(dataset_id=second_dataset),
        if_match=service.etag(second),
    )
    assert reassigned.dataset_id == second_dataset

    repository.record_received_package(
        organization_id=ORGANIZATION_ID,
        project_id="project-a",
        collection_task_id=second.collection_task_id,
        package_id="received-package",
    )
    with pytest.raises(ProblemException) as locked:
        service.update(
            organization_id=ORGANIZATION_ID,
            project_id="project-a",
            collection_task_id=second.collection_task_id,
            command=UpdateCollectionTask(dataset_id=first_dataset),
            if_match=service.etag(reassigned),
        )
    assert_problem(locked, "COLLECTION_TASK_DATASET_REASSIGNMENT_BLOCKED")


def test_dataset_assignment_rejects_unknown_scope_and_explicit_null() -> None:
    service = CollectionTaskService()
    with pytest.raises(ProblemException) as unavailable:
        service.create(
            organization_id=ORGANIZATION_ID,
            project_id="project-a",
            command=command().model_copy(update={"dataset_id": "dataset_unknown_scope"}),
            idempotency_key="unknown-dataset",
        )
    assert_problem(unavailable, "COLLECTION_TASK_DATASET_NOT_ASSIGNABLE")

    with pytest.raises(ValueError):
        CreateCollectionTask.model_validate(
            {
                "dataset_id": None,
                "name": "Invalid null",
                "type": "COLLECTION",
                "scenario": "general",
            }
        )
    with pytest.raises(ValueError):
        UpdateCollectionTask.model_validate({"dataset_id": None})


@pytest.mark.parametrize(
    ("initial_status", "action", "allowed", "final_status"),
    [
        (CollectionTaskStatus.ACTIVE, "update", True, CollectionTaskStatus.ACTIVE),
        (CollectionTaskStatus.ACTIVE, "close", True, CollectionTaskStatus.CLOSED),
        (CollectionTaskStatus.ACTIVE, "cancel", True, CollectionTaskStatus.CANCELLED),
        (CollectionTaskStatus.CLOSED, "update", False, CollectionTaskStatus.CLOSED),
        (CollectionTaskStatus.CLOSED, "close", True, CollectionTaskStatus.CLOSED),
    ],
)
def test_state_table(
    initial_status: CollectionTaskStatus,
    action: str,
    allowed: bool,
    final_status: CollectionTaskStatus,
) -> None:
    service = CollectionTaskService()
    task_id, active_etag = create_task(service)
    current_etag = active_etag
    if initial_status is CollectionTaskStatus.CLOSED:
        closed = service.close(
            organization_id=ORGANIZATION_ID,
            project_id="project-a",
            collection_task_id=task_id,
            if_match=active_etag,
            idempotency_key="initial-close",
        )
        current_etag = service.etag(closed.record)

    if action == "update":

        def invoke() -> Any:
            return service.update(
                organization_id=ORGANIZATION_ID,
                project_id="project-a",
                collection_task_id=task_id,
                command=UpdateCollectionTask(description="updated"),
                if_match=current_etag,
            )

    elif action == "close":

        def invoke() -> Any:
            return service.close(
                organization_id=ORGANIZATION_ID,
                project_id="project-a",
                collection_task_id=task_id,
                if_match=current_etag,
                idempotency_key="table-close",
            ).record

    else:

        def invoke() -> Any:
            return service.cancel(
                organization_id=ORGANIZATION_ID,
                project_id="project-a",
                collection_task_id=task_id,
                if_match=current_etag,
                idempotency_key="table-cancel",
            ).record

    if allowed:
        record = invoke()
        assert record.status is final_status
    else:
        with pytest.raises(ProblemException) as rejected:
            invoke()
        assert_problem(rejected, "COLLECTION_TASK_CLOSED")
        assert service.detail(ORGANIZATION_ID, "project-a", task_id).status is final_status


def test_update_requires_current_etag_and_only_changes_confirmed_fields() -> None:
    service = CollectionTaskService()
    task_id, etag = create_task(service)
    updated = service.update(
        organization_id=ORGANIZATION_ID,
        project_id="project-a",
        collection_task_id=task_id,
        command=UpdateCollectionTask(
            name="Updated name",
            target=CollectionTarget(duration_seconds=12.5),
            quality_threshold=0.92,
        ),
        if_match=etag,
    )
    assert updated.name == "Updated name"
    assert updated.target == CollectionTarget(duration_seconds=12.5)
    assert updated.quality_threshold == 0.92

    with pytest.raises(ProblemException) as stale:
        service.update(
            organization_id=ORGANIZATION_ID,
            project_id="project-a",
            collection_task_id=task_id,
            command=UpdateCollectionTask(description="stale"),
            if_match=etag,
        )
    assert_problem(stale, "ETAG_MISMATCH")

    with pytest.raises(ValueError):
        UpdateCollectionTask(name=None)


def test_close_cancel_and_reopen_are_explicit_idempotent_transitions() -> None:
    service = CollectionTaskService()
    task_id, etag = create_task(service)

    first = service.close(
        organization_id=ORGANIZATION_ID,
        project_id="project-a",
        collection_task_id=task_id,
        if_match=etag,
        idempotency_key="close-1",
    )
    replay = service.close(
        organization_id=ORGANIZATION_ID,
        project_id="project-a",
        collection_task_id=task_id,
        if_match=etag,
        idempotency_key="close-1",
    )

    assert first.record == replay.record
    assert replay.replayed is True
    assert first.record.status is CollectionTaskStatus.CLOSED
    reopened = service.reopen(
        organization_id=ORGANIZATION_ID,
        project_id="project-a",
        collection_task_id=task_id,
        if_match=service.etag(first.record),
        idempotency_key="reopen-1",
    )
    assert reopened.record.status is CollectionTaskStatus.ACTIVE
    assert (
        service.reopen(
            organization_id=ORGANIZATION_ID,
            project_id="project-a",
            collection_task_id=task_id,
            if_match=service.etag(first.record),
            idempotency_key="reopen-1",
        ).replayed
        is True
    )

    cancelled = service.cancel(
        organization_id=ORGANIZATION_ID,
        project_id="project-a",
        collection_task_id=task_id,
        if_match=service.etag(reopened.record),
        idempotency_key="cancel-1",
    )
    assert cancelled.record.status is CollectionTaskStatus.CANCELLED
    with pytest.raises(ProblemException) as cannot_edit_cancelled:
        service.update(
            organization_id=ORGANIZATION_ID,
            project_id="project-a",
            collection_task_id=task_id,
            command=UpdateCollectionTask(description="must reopen first"),
            if_match=service.etag(cancelled.record),
        )
    assert_problem(cannot_edit_cancelled, "COLLECTION_TASK_CANCELLED")


def test_progress_reports_target_attainment_without_automatic_state_change() -> None:
    repository = InMemoryCollectionTaskRepository()
    service = CollectionTaskService(repository)
    created = service.create(
        organization_id=ORGANIZATION_ID,
        project_id="project-a",
        command=CreateCollectionTask(
            name="Attainment",
            type="COLLECTION",
            scenario="integration",
            target=CollectionTarget(package_count=2, duration_seconds=20),
            quality_threshold=0.75,
        ),
        idempotency_key="attainment-create",
    ).record

    repository.record_received_package(
        organization_id=ORGANIZATION_ID,
        project_id="project-a",
        collection_task_id=created.collection_task_id,
        package_id="package-1",
        qc_outcome=QcOutcome.PASS,
        duration_seconds=10,
    )
    partial = service.progress(
        ORGANIZATION_ID,
        "project-a",
        created.collection_task_id,
        "cn-test",
    )
    assert partial.attainment.status is CollectionTaskAttainmentStatus.IN_PROGRESS
    assert partial.attainment.package_count is not None
    assert partial.attainment.package_count.progress == 0.5
    assert partial.attainment.duration_seconds is not None
    assert partial.attainment.duration_seconds.progress == 0.5

    repository.record_received_package(
        organization_id=ORGANIZATION_ID,
        project_id="project-a",
        collection_task_id=created.collection_task_id,
        package_id="package-2",
        duration_seconds=10,
    )
    pending_qc = service.progress(
        ORGANIZATION_ID,
        "project-a",
        created.collection_task_id,
        "cn-test",
    )
    assert pending_qc.attainment.status is CollectionTaskAttainmentStatus.IN_PROGRESS
    assert pending_qc.attainment.quality_status.value == "PENDING_QC"

    repository.record_received_package(
        organization_id=ORGANIZATION_ID,
        project_id="project-a",
        collection_task_id=created.collection_task_id,
        package_id="package-2",
        qc_outcome=QcOutcome.PASS,
    )
    attained = service.progress(
        ORGANIZATION_ID,
        "project-a",
        created.collection_task_id,
        "cn-test",
    )
    assert attained.attainment.status is CollectionTaskAttainmentStatus.ATTAINED
    assert attained.status is CollectionTaskStatus.ACTIVE

    repository.record_received_package(
        organization_id=ORGANIZATION_ID,
        project_id="project-a",
        collection_task_id=created.collection_task_id,
        package_id="package-3",
        qc_outcome=QcOutcome.PASS,
        duration_seconds=5,
    )
    exceeded = service.progress(
        ORGANIZATION_ID,
        "project-a",
        created.collection_task_id,
        "cn-test",
    )
    assert exceeded.attainment.status is CollectionTaskAttainmentStatus.EXCEEDED
    assert exceeded.attainment.package_count is not None
    assert exceeded.attainment.package_count.status is CollectionTaskTargetMetricStatus.EXCEEDED


def test_duration_target_stays_unknown_when_a_committed_manifest_lacks_time_range() -> None:
    repository = InMemoryCollectionTaskRepository()
    service = CollectionTaskService(repository)
    task_id = service.create(
        organization_id=ORGANIZATION_ID,
        project_id="project-a",
        command=CreateCollectionTask(
            name="Duration evidence",
            type="COLLECTION",
            scenario="integration",
            target=CollectionTarget(duration_seconds=60),
        ),
        idempotency_key="duration-create",
    ).record.collection_task_id
    repository.record_received_package(
        organization_id=ORGANIZATION_ID,
        project_id="project-a",
        collection_task_id=task_id,
        package_id="legacy-manifest-without-time-range",
    )

    progress = service.progress(ORGANIZATION_ID, "project-a", task_id, "cn-test")
    assert progress.captured_duration_seconds is None
    assert progress.duration_unknown_package_count == 1
    assert progress.attainment.duration_seconds is not None
    assert progress.attainment.duration_seconds.status is CollectionTaskTargetMetricStatus.UNKNOWN
    assert progress.attainment.status is CollectionTaskAttainmentStatus.IN_PROGRESS


def test_progress_counts_distinct_received_packages_and_latest_qc_facts() -> None:
    repository = InMemoryCollectionTaskRepository()
    service = CollectionTaskService(repository)
    task_id, _ = create_task(service)

    facts = {
        "package-1": QcOutcome.PASS,
        "package-2": QcOutcome.PASS,
        "package-3": QcOutcome.RISK,
        "package-4": QcOutcome.REJECT,
        "package-5": None,
    }
    for index, (package_id, outcome) in enumerate(facts.items()):
        repository.record_received_package(
            organization_id=ORGANIZATION_ID,
            project_id="project-a",
            collection_task_id=task_id,
            package_id=package_id,
            qc_outcome=outcome,
            device_ids=("device-a", f"device-{index}"),
            camera_ids=("camera-front",),
            topic_names=("/camera/front",),
        )
    # A latest-result update changes the outcome but never duplicates the package.
    repository.record_received_package(
        organization_id=ORGANIZATION_ID,
        project_id="project-a",
        collection_task_id=task_id,
        package_id="package-3",
        qc_outcome=QcOutcome.PASS,
    )

    progress = service.progress(ORGANIZATION_ID, "project-a", task_id, "cn-test")
    assert progress.received_package_count == 5
    assert progress.qc.evaluated_count == 4
    assert progress.qc.pass_count == 3
    assert progress.qc.risk_count == 0
    assert progress.qc.reject_count == 1
    assert progress.qc.pending_count == 1
    assert progress.qc.pass_rate.numerator == 3
    assert progress.qc.pass_rate.denominator == 4
    assert progress.qc.pass_rate.value == 0.75
    assert progress.observed_sources.device_ids == (
        "device-0",
        "device-1",
        "device-2",
        "device-3",
        "device-4",
        "device-a",
    )
    assert progress.observed_sources.camera_ids == ("camera-front",)
    assert progress.observed_sources.topic_names == ("/camera/front",)


def test_progress_rate_is_unknown_without_an_evaluated_denominator() -> None:
    repository = InMemoryCollectionTaskRepository()
    service = CollectionTaskService(repository)
    task_id, _ = create_task(service)
    repository.record_received_package(
        organization_id=ORGANIZATION_ID,
        project_id="project-a",
        collection_task_id=task_id,
        package_id="pending",
    )

    progress = service.progress(ORGANIZATION_ID, "project-a", task_id, "cn-test")
    assert progress.qc.pass_rate.numerator == 0
    assert progress.qc.pass_rate.denominator == 0
    assert progress.qc.pass_rate.value is None
    assert progress.qc.pending_count == 1


def test_packages_lists_every_received_package_with_its_current_gate() -> None:
    repository = InMemoryCollectionTaskRepository()
    service = CollectionTaskService(repository)
    task_id, _ = create_task(service)
    for package_id, outcome in (
        ("pending", None),
        ("passing", QcOutcome.PASS),
        ("risky", QcOutcome.RISK),
        ("rejected", QcOutcome.REJECT),
    ):
        repository.record_received_package(
            organization_id=ORGANIZATION_ID,
            project_id="project-a",
            collection_task_id=task_id,
            package_id=package_id,
            qc_outcome=outcome,
        )

    packages = service.packages(ORGANIZATION_ID, "project-a", task_id, "cn-test")
    assert len(packages.items) == 4
    assert {item.data_package_id: item.state.value for item in packages.items} == {
        "passing": "PROCESSING",
        "pending": "PENDING_QC",
        "rejected": "QUALITY_REJECTED",
        "risky": "QUALITY_RISK",
    }
    assert all(item.visualizable is False for item in packages.items)


def test_close_and_new_package_association_are_linearized() -> None:
    for iteration in range(100):
        repository = InMemoryCollectionTaskRepository()
        service = CollectionTaskService(repository)
        task_id, etag = create_task(service, key=f"create-{iteration}")
        barrier = Barrier(2)

        with ThreadPoolExecutor(max_workers=2) as pool:
            close_result = pool.submit(race_close, barrier, service, task_id, etag, iteration)
            association_result = pool.submit(
                race_associate,
                barrier,
                repository,
                task_id,
                iteration,
            )
            assert close_result.result() == "CLOSED"
            assert association_result.result() in {"associated", "closed"}

        assert (
            service.detail(ORGANIZATION_ID, "project-a", task_id).status
            is CollectionTaskStatus.CLOSED
        )
        received = service.progress(
            ORGANIZATION_ID, "project-a", task_id, "cn-test"
        ).received_package_count
        assert received == (1 if association_result.result() == "associated" else 0)
        with pytest.raises(ProblemException) as after_close:
            repository.record_received_package(
                organization_id=ORGANIZATION_ID,
                project_id="project-a",
                collection_task_id=task_id,
                package_id=f"later-{iteration}",
            )
        assert_problem(after_close, "COLLECTION_TASK_CLOSED")


def test_cursor_is_bound_to_project_and_filter() -> None:
    service = CollectionTaskService()
    for index in range(3):
        service.create(
            organization_id=ORGANIZATION_ID,
            project_id="project-a",
            command=command(name=f"Task {index}"),
            idempotency_key=f"task-{index}",
        )
    first = service.list(
        organization_id=ORGANIZATION_ID,
        project_id="project-a",
        status=None,
        limit=2,
        cursor=None,
    )
    assert len(first.items) == 2
    assert first.next_cursor is not None

    second = service.list(
        organization_id=ORGANIZATION_ID,
        project_id="project-a",
        status=None,
        limit=2,
        cursor=first.next_cursor,
    )
    assert len(second.items) == 1

    with pytest.raises(ProblemException) as cross_scope:
        service.list(
            organization_id="org-b",
            project_id="project-b",
            status=None,
            limit=2,
            cursor=first.next_cursor,
        )
    assert_problem(cross_scope, "INVALID_CURSOR")
