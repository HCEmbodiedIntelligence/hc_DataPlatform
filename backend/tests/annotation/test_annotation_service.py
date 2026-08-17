from __future__ import annotations

import pytest

from hc_data_platform.annotation import (
    AnnotationActor,
    AnnotationConflictError,
    AnnotationOperation,
    AnnotationPermissionError,
    AnnotationStatus,
    DisabledAutoAnnotationProvider,
    FeatureDisabledError,
    InMemoryAnnotationService,
    InvalidAnnotationStateError,
    OperationKind,
    ReviewDecision,
)


def actor(actor_id: str, *roles: str, projects: tuple[str, ...] = ("project-a",)):
    return AnnotationActor(
        actor_id=actor_id,
        roles=frozenset(roles),
        project_ids=frozenset(projects),
    )


def service_with_claimed_task():
    service = InMemoryAnnotationService()
    service.create_task(
        task_id="task-1",
        project_id="project-a",
        dataset_id="dataset-a",
        dataset_version=7,
        rollout_id="rollout-a",
    )
    annotator = actor("alice", "annotator")
    service.claim("task-1", annotator)
    return service, annotator


def operation(
    operation_id: str,
    kind: OperationKind,
    start: int,
    end: int,
) -> AnnotationOperation:
    return AnnotationOperation(
        operation_id=operation_id,
        kind=kind,
        start_step=start,
        end_step=end,
    )


def test_exclude_is_half_open_and_applies_as_one_cross_modal_range() -> None:
    service, annotator = service_with_claimed_task()
    task = service.get_task("task-1")
    revision = service.save_draft(
        "task-1",
        annotator,
        [operation("op-1", OperationKind.EXCLUDE, 300, 450)],
        expected_revision=0,
        if_match=task.etag,
        mutation_id="mutation-1",
    )

    assert revision.revision == 1
    ranges = service.effective_exclusions("task-1")
    assert [(item.start_step, item.end_step) for item in ranges] == [(300, 450)]

    def excluded(step: int) -> bool:
        return any(r.start_step <= step < r.end_step for r in ranges)

    assert not excluded(299)
    assert excluded(300)
    assert excluded(449)
    assert not excluded(450)


def test_restore_creates_new_revision_and_preserves_history() -> None:
    service, annotator = service_with_claimed_task()
    task = service.get_task("task-1")
    service.save_draft(
        "task-1",
        annotator,
        [operation("exclude", OperationKind.EXCLUDE, 300, 450)],
        expected_revision=0,
        if_match=task.etag,
        mutation_id="mutation-1",
    )
    task = service.get_task("task-1")
    restored = service.save_draft(
        "task-1",
        annotator,
        [operation("restore", OperationKind.RESTORE, 350, 400)],
        expected_revision=1,
        if_match=task.etag,
        mutation_id="mutation-2",
    )

    assert restored.parent_revision == 1
    assert service.get_revision("task-1", 1).operations[0].kind is OperationKind.EXCLUDE
    assert [
        (item.start_step, item.end_step)
        for item in service.effective_exclusions("task-1", revision=2)
    ] == [(300, 350), (400, 450)]
    assert [
        (item.start_step, item.end_step)
        for item in service.effective_exclusions("task-1", revision=1)
    ] == [(300, 450)]


def test_stale_revision_or_etag_returns_conflict_without_overwrite() -> None:
    service, annotator = service_with_claimed_task()
    initial = service.get_task("task-1")
    service.save_draft(
        "task-1",
        annotator,
        [operation("one", OperationKind.EXCLUDE, 0, 10)],
        expected_revision=0,
        if_match=initial.etag,
        mutation_id="mutation-1",
    )

    with pytest.raises(AnnotationConflictError):
        service.save_draft(
            "task-1",
            annotator,
            [operation("two", OperationKind.EXCLUDE, 20, 30)],
            expected_revision=0,
            if_match=initial.etag,
            mutation_id="mutation-2",
        )
    assert service.get_task("task-1").current_revision == 1


def test_mutation_id_is_idempotent_but_cannot_change_payload() -> None:
    service, annotator = service_with_claimed_task()
    initial = service.get_task("task-1")
    operations = [operation("one", OperationKind.EXCLUDE, 0, 10)]
    first = service.save_draft(
        "task-1",
        annotator,
        operations,
        expected_revision=0,
        if_match=initial.etag,
        mutation_id="same-mutation",
    )
    replay = service.save_draft(
        "task-1",
        annotator,
        operations,
        expected_revision=0,
        if_match=initial.etag,
        mutation_id="same-mutation",
    )
    assert replay == first

    with pytest.raises(AnnotationConflictError):
        service.save_draft(
            "task-1",
            annotator,
            [operation("changed", OperationKind.EXCLUDE, 0, 11)],
            expected_revision=1,
            if_match=service.get_task("task-1").etag,
            mutation_id="same-mutation",
        )


def test_submit_and_approval_bind_the_exact_revision() -> None:
    service, annotator = service_with_claimed_task()
    task = service.get_task("task-1")
    service.save_draft(
        "task-1",
        annotator,
        [operation("one", OperationKind.EXCLUDE, 3, 8)],
        expected_revision=0,
        if_match=task.etag,
        mutation_id="mutation-1",
    )
    task = service.get_task("task-1")
    submitted = service.submit("task-1", annotator, expected_revision=1, if_match=task.etag)
    reviewer = actor("bob", "reviewer")
    event = service.review(
        "task-1",
        reviewer,
        ReviewDecision.APPROVE,
        revision=1,
        if_match=submitted.etag,
    )

    assert event is not None
    assert event.annotation_revision == 1
    assert service.approved_revision("task-1").revision == 1
    assert service.get_task("task-1").status is AnnotationStatus.APPROVED


def test_edit_after_approval_creates_new_draft_and_keeps_review_history() -> None:
    service, annotator = service_with_claimed_task()
    task = service.get_task("task-1")
    service.save_draft(
        "task-1",
        annotator,
        [operation("one", OperationKind.EXCLUDE, 3, 8)],
        expected_revision=0,
        if_match=task.etag,
        mutation_id="mutation-1",
    )
    task = service.submit(
        "task-1",
        annotator,
        expected_revision=1,
        if_match=service.get_task("task-1").etag,
    )
    service.review(
        "task-1",
        actor("bob", "reviewer"),
        ReviewDecision.APPROVE,
        revision=1,
        if_match=task.etag,
    )
    approved_task = service.get_task("task-1")
    service.save_draft(
        "task-1",
        annotator,
        [operation("two", OperationKind.RESTORE, 4, 5)],
        expected_revision=1,
        if_match=approved_task.etag,
        mutation_id="mutation-2",
    )

    assert service.get_task("task-1").status is AnnotationStatus.DRAFT
    assert service.get_task("task-1").approved_revision is None
    assert service.get_revision("task-1", 1).revision == 1
    assert service.list_reviews("task-1")[0].revision == 1
    with pytest.raises(InvalidAnnotationStateError):
        service.approved_revision("task-1")


def test_reviewer_scope_and_role_are_enforced() -> None:
    service, annotator = service_with_claimed_task()
    submitted = service.submit(
        "task-1",
        annotator,
        expected_revision=0,
        if_match=service.get_task("task-1").etag,
    )
    with pytest.raises(AnnotationPermissionError):
        service.review(
            "task-1",
            actor("mallory", "reviewer", projects=("project-b",)),
            ReviewDecision.APPROVE,
            revision=0,
            if_match=submitted.etag,
        )
    with pytest.raises(AnnotationPermissionError):
        service.review(
            "task-1",
            actor("charlie", "annotator"),
            ReviewDecision.APPROVE,
            revision=0,
            if_match=submitted.etag,
        )


def test_submitter_cannot_review_own_revision() -> None:
    service = InMemoryAnnotationService()
    service.create_task(
        task_id="task-1",
        project_id="project-a",
        dataset_id="dataset-a",
        dataset_version=1,
        rollout_id="rollout-a",
    )
    dual_role = actor("alice", "annotator", "reviewer")
    service.claim("task-1", dual_role)
    submitted = service.submit(
        "task-1",
        dual_role,
        expected_revision=0,
        if_match=service.get_task("task-1").etag,
    )

    with pytest.raises(AnnotationPermissionError):
        service.review(
            "task-1",
            dual_role,
            ReviewDecision.APPROVE,
            revision=0,
            if_match=submitted.etag,
        )


def test_vlm_capability_is_explicitly_disabled() -> None:
    provider = DisabledAutoAnnotationProvider()
    assert provider.enabled is False
    with pytest.raises(FeatureDisabledError) as error:
        provider.request(task_id="task-1", revision=1)
    assert error.value.code == "FEATURE_DISABLED"
