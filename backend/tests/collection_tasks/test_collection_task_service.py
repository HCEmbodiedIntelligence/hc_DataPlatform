from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any

import pytest

from hc_data_platform.collection_tasks.models import (
    CollectionTarget,
    CollectionTaskStatus,
    CreateCollectionTask,
    QcOutcome,
    UpdateCollectionTask,
)
from hc_data_platform.collection_tasks.repository import InMemoryCollectionTaskRepository
from hc_data_platform.collection_tasks.service import CollectionTaskService
from hc_data_platform.core.errors import ProblemException


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
    result = service.create(project_id="project-a", command=command(), idempotency_key=key)
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
            project_id="project-a",
            collection_task_id=task_id,
            package_id=f"package-{iteration}",
        )
        return "associated"
    except ProblemException as exc:
        assert exc.problem.code == "COLLECTION_TASK_CLOSED"
        return "closed"


def test_create_is_idempotent_and_open_questions_have_no_invented_defaults() -> None:
    service = CollectionTaskService()
    without_policy = CreateCollectionTask(
        name="Open policy",
        type="COLLECTION",
        scenario="general",
    )

    first = service.create(
        project_id="project-a",
        command=without_policy,
        idempotency_key="same-key",
    )
    replay = service.create(
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
    assert CollectionTarget(package_count=0, duration_seconds=-1).model_dump() == {
        "package_count": 0,
        "duration_seconds": -1.0,
    }

    with pytest.raises(ProblemException) as reused:
        service.create(
            project_id="project-a",
            command=command(name="Different request"),
            idempotency_key="same-key",
        )
    assert_problem(reused, "IDEMPOTENCY_KEY_REUSED")


@pytest.mark.parametrize(
    ("initial_status", "action", "allowed", "final_status"),
    [
        (CollectionTaskStatus.ACTIVE, "update", True, CollectionTaskStatus.ACTIVE),
        (CollectionTaskStatus.ACTIVE, "close", True, CollectionTaskStatus.CLOSED),
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
            project_id="project-a",
            collection_task_id=task_id,
            if_match=active_etag,
            idempotency_key="initial-close",
        )
        current_etag = service.etag(closed.record)

    if action == "update":

        def invoke() -> Any:
            return service.update(
                project_id="project-a",
                collection_task_id=task_id,
                command=UpdateCollectionTask(description="updated"),
                if_match=current_etag,
            )

    else:

        def invoke() -> Any:
            return service.close(
                project_id="project-a",
                collection_task_id=task_id,
                if_match=current_etag,
                idempotency_key="table-close",
            ).record

    if allowed:
        record = invoke()
        assert record.status is final_status
    else:
        with pytest.raises(ProblemException) as rejected:
            invoke()
        assert_problem(rejected, "COLLECTION_TASK_CLOSED")
        assert service.detail("project-a", task_id).status is final_status


def test_update_requires_current_etag_and_only_changes_confirmed_fields() -> None:
    service = CollectionTaskService()
    task_id, etag = create_task(service)
    updated = service.update(
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
            project_id="project-a",
            collection_task_id=task_id,
            command=UpdateCollectionTask(description="stale"),
            if_match=etag,
        )
    assert_problem(stale, "ETAG_MISMATCH")

    with pytest.raises(ValueError):
        UpdateCollectionTask(name=None)


def test_close_replay_is_idempotent_and_has_no_reopen_transition() -> None:
    service = CollectionTaskService()
    task_id, etag = create_task(service)

    first = service.close(
        project_id="project-a",
        collection_task_id=task_id,
        if_match=etag,
        idempotency_key="close-1",
    )
    replay = service.close(
        project_id="project-a",
        collection_task_id=task_id,
        if_match=etag,
        idempotency_key="close-1",
    )

    assert first.record == replay.record
    assert replay.replayed is True
    assert first.record.status is CollectionTaskStatus.CLOSED
    assert not hasattr(service, "reopen")


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
        project_id="project-a",
        collection_task_id=task_id,
        package_id="package-3",
        qc_outcome=QcOutcome.PASS,
    )

    progress = service.progress("project-a", task_id, "cn-test")
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
        project_id="project-a",
        collection_task_id=task_id,
        package_id="pending",
    )

    progress = service.progress("project-a", task_id, "cn-test")
    assert progress.qc.pass_rate.numerator == 0
    assert progress.qc.pass_rate.denominator == 0
    assert progress.qc.pass_rate.value is None
    assert progress.qc.pending_count == 1


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

        assert service.detail("project-a", task_id).status is CollectionTaskStatus.CLOSED
        received = service.progress("project-a", task_id, "cn-test").received_package_count
        assert received == (1 if association_result.result() == "associated" else 0)
        with pytest.raises(ProblemException) as after_close:
            repository.record_received_package(
                project_id="project-a",
                collection_task_id=task_id,
                package_id=f"later-{iteration}",
            )
        assert_problem(after_close, "COLLECTION_TASK_CLOSED")


def test_cursor_is_bound_to_project_and_filter() -> None:
    service = CollectionTaskService()
    for index in range(3):
        service.create(
            project_id="project-a",
            command=command(name=f"Task {index}"),
            idempotency_key=f"task-{index}",
        )
    first = service.list(project_id="project-a", status=None, limit=2, cursor=None)
    assert len(first.items) == 2
    assert first.next_cursor is not None

    second = service.list(
        project_id="project-a",
        status=None,
        limit=2,
        cursor=first.next_cursor,
    )
    assert len(second.items) == 1

    with pytest.raises(ProblemException) as cross_scope:
        service.list(
            project_id="project-b",
            status=None,
            limit=2,
            cursor=first.next_cursor,
        )
    assert_problem(cross_scope, "INVALID_CURSOR")
