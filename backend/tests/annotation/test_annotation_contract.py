from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from hc_data_platform.annotation import (
    AnnotationActor,
    AnnotationConflictError,
    AnnotationMutationConflictError,
    AnnotationOperation,
    AnnotationPermissionError,
    AnnotationStatus,
    InMemoryAnnotationService,
    InvalidAnnotationStateError,
    OperationKind,
    ReviewDecision,
    SelfReviewPolicy,
)
from hc_data_platform.security import AuthContext


def actor(actor_id: str, *roles: str, projects: tuple[str, ...] = ("project-a",)):
    return AnnotationActor(
        actor_id=actor_id,
        roles=frozenset(roles),
        project_ids=frozenset(projects),
    )


def auth(subject: str, *roles: str, projects: tuple[str, ...] = ("project-a",)):
    return AuthContext(
        subject_id=subject,
        project_ids=frozenset(projects),
        region_codes=frozenset(),
        roles=frozenset(roles),
    )


def operation(operation_id: str, kind: OperationKind, start: int, end: int) -> AnnotationOperation:
    return AnnotationOperation(
        operation_id=operation_id,
        kind=kind,
        start_step=start,
        end_step=end,
    )


def prepared() -> tuple[InMemoryAnnotationService, AnnotationActor]:
    service = InMemoryAnnotationService(self_review_policy=SelfReviewPolicy.DENY)
    service.create_task(
        task_id="task-1",
        project_id="project-a",
        dataset_id="dataset-a",
        dataset_version=3,
        rollout_id="rollout-a",
        base_step_count=2_000,
    )
    annotator = actor("alice", "annotator")
    service.claim("task-1", annotator)
    return service, annotator


def test_overlapping_ranges_are_normalized_and_restore_history_is_append_only() -> None:
    service, annotator = prepared()
    first_etag = service.get_task("task-1").etag
    service.save_draft(
        "task-1",
        annotator,
        (
            operation("exclude-a", OperationKind.EXCLUDE, 300, 450),
            operation("exclude-b", OperationKind.EXCLUDE, 400, 500),
        ),
        expected_revision=0,
        if_match=first_etag,
        client_mutation_id="save-1",
    )
    service.save_draft(
        "task-1",
        annotator,
        (
            operation("restore-a", OperationKind.RESTORE, 340, 360),
            operation("restore-b", OperationKind.RESTORE, 440, 520),
        ),
        expected_revision=1,
        if_match=service.get_task("task-1").etag,
        client_mutation_id="save-2",
    )

    ranges = service.effective_exclusions("task-1", revision=2)
    assert [(item.start_step, item.end_step) for item in ranges] == [
        (300, 340),
        (360, 440),
    ]
    assert {item.modality_scope for item in ranges} == {"ALL_MODALITIES"}
    assert [
        operation.kind
        for revision in service.list_revisions("task-1")
        for operation in revision.operations
    ] == [
        OperationKind.EXCLUDE,
        OperationKind.EXCLUDE,
        OperationKind.RESTORE,
        OperationKind.RESTORE,
    ]


def test_half_open_models_reject_empty_or_reversed_ranges() -> None:
    with pytest.raises(ValidationError):
        operation("empty", OperationKind.EXCLUDE, 12, 12)
    with pytest.raises(ValidationError):
        operation("reversed", OperationKind.RESTORE, 13, 12)


def test_concurrent_authorized_saves_have_one_winner_and_one_409() -> None:
    service, annotator = prepared()
    administrator = actor("root-reviewer", "admin", projects=())
    stale = service.get_task("task-1")

    def save(identity: AnnotationActor, suffix: str) -> int | str:
        try:
            return service.save_draft(
                "task-1",
                identity,
                (operation(f"operation-{suffix}", OperationKind.EXCLUDE, 0, 10),),
                expected_revision=0,
                if_match=stale.etag,
                client_mutation_id=f"mutation-{suffix}",
            ).revision
        except AnnotationConflictError as exc:
            return exc.problem.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = tuple(
            pool.submit(save, identity, suffix)
            for identity, suffix in ((annotator, "alice"), (administrator, "admin"))
        )
        results = tuple(future.result() for future in futures)

    assert sorted(results, key=str) == sorted((1, 409), key=str)
    assert service.get_task("task-1").current_revision == 1
    assert len(service.list_revisions("task-1")) == 2


def test_concurrent_identical_mutation_replays_one_revision() -> None:
    service, annotator = prepared()
    stale = service.get_task("task-1")
    command = (operation("operation-1", OperationKind.EXCLUDE, 2, 8),)

    def save(_: int) -> int:
        return service.save_draft(
            "task-1",
            annotator,
            command,
            expected_revision=0,
            if_match=stale.etag,
            client_mutation_id="same-client-mutation",
        ).revision

    with ThreadPoolExecutor(max_workers=12) as pool:
        assert list(pool.map(save, range(80))) == [1] * 80
    assert len(service.list_revisions("task-1")) == 2


def test_same_mutation_id_with_changed_precondition_or_payload_is_409() -> None:
    service, annotator = prepared()
    initial = service.get_task("task-1")
    service.save_draft(
        "task-1",
        annotator,
        (operation("operation-1", OperationKind.EXCLUDE, 1, 2),),
        expected_revision=0,
        if_match=initial.etag,
        client_mutation_id="mutation-1",
    )

    with pytest.raises(AnnotationMutationConflictError) as captured:
        service.save_draft(
            "task-1",
            annotator,
            (operation("operation-2", OperationKind.EXCLUDE, 1, 3),),
            expected_revision=1,
            if_match=service.get_task("task-1").etag,
            client_mutation_id="mutation-1",
        )
    assert captured.value.problem.status == 409
    assert len(service.list_revisions("task-1")) == 2


def test_be02_scope_roles_self_review_and_publisher_contract() -> None:
    service, _ = prepared()
    alice = auth("alice", "annotator")
    reviewer = auth("bob", "reviewer")
    publisher = auth("pat", "publisher")

    assert service.read_task("task-1", alice).project_id == "project-a"
    with pytest.raises(AnnotationPermissionError):
        service.read_task("task-1", auth("outsider", "reviewer", projects=("project-b",)))
    with pytest.raises(AnnotationPermissionError):
        service.read_task("task-1", auth("uploader", "uploader"))

    submitted = service.submit(
        "task-1",
        alice,
        expected_revision=0,
        if_match=service.read_task("task-1", alice).etag,
    )
    with pytest.raises(AnnotationPermissionError):
        service.review(
            "task-1",
            auth("alice", "annotator", "reviewer"),
            ReviewDecision.APPROVE,
            revision=0,
            if_match=submitted.etag,
        )
    approval = service.review(
        "task-1",
        reviewer,
        ReviewDecision.APPROVE,
        revision=0,
        if_match=submitted.etag,
    )
    assert approval is not None
    assert (
        service.approved_snapshot(project_id="project-a", rollout_id="rollout-a", actor=publisher)
        == approval
    )
    with pytest.raises(AnnotationPermissionError):
        service.approved_snapshot(project_id="project-a", rollout_id="rollout-a", actor=reviewer)


def test_admin_is_not_allowed_to_self_review() -> None:
    service = InMemoryAnnotationService(self_review_policy=SelfReviewPolicy.DENY)
    service.create_task(
        task_id="task-admin",
        project_id="project-a",
        dataset_id="dataset-a",
        dataset_version=1,
        rollout_id="rollout-admin",
        base_step_count=2_000,
    )
    administrator = actor("admin", "admin", projects=())
    service.claim("task-admin", administrator)
    submitted = service.submit(
        "task-admin",
        administrator,
        expected_revision=0,
        if_match=service.get_task("task-admin").etag,
    )
    with pytest.raises(AnnotationPermissionError):
        service.review(
            "task-admin",
            administrator,
            ReviewDecision.APPROVE,
            revision=0,
            if_match=submitted.etag,
        )


def test_needs_revision_reject_and_history_queries() -> None:
    service, annotator = prepared()
    reviewer = actor("bob", "reviewer")
    submitted = service.submit(
        "task-1",
        annotator,
        expected_revision=0,
        if_match=service.get_task("task-1").etag,
    )
    assert (
        service.review(
            "task-1",
            reviewer,
            ReviewDecision.NEEDS_REVISION,
            revision=0,
            if_match=submitted.etag,
            comment="adjust the interval",
        )
        is None
    )
    assert service.get_task("task-1").status is AnnotationStatus.NEEDS_REVISION
    service.save_draft(
        "task-1",
        annotator,
        (operation("operation-1", OperationKind.EXCLUDE, 5, 9),),
        expected_revision=0,
        if_match=service.get_task("task-1").etag,
        client_mutation_id="mutation-1",
    )
    submitted = service.submit(
        "task-1",
        annotator,
        expected_revision=1,
        if_match=service.get_task("task-1").etag,
    )
    service.review(
        "task-1",
        reviewer,
        ReviewDecision.REJECT,
        revision=1,
        if_match=submitted.etag,
        comment="unusable rollout",
    )
    history = service.get_history("task-1", reviewer)
    assert history.task.status is AnnotationStatus.REJECTED
    assert [review.decision for review in history.reviews] == [
        ReviewDecision.NEEDS_REVISION,
        ReviewDecision.REJECT,
    ]
    assert [revision.revision for revision in history.revisions] == [0, 1]


def test_edit_after_approval_removes_publishing_eligibility_but_not_snapshot_history() -> None:
    service, annotator = prepared()
    task = service.save_draft(
        "task-1",
        annotator,
        (operation("exclude", OperationKind.EXCLUDE, 300, 450),),
        expected_revision=0,
        if_match=service.get_task("task-1").etag,
        client_mutation_id="mutation-1",
    )
    submitted = service.submit(
        "task-1",
        annotator,
        expected_revision=task.revision,
        if_match=service.get_task("task-1").etag,
    )
    approval = service.review(
        "task-1",
        actor("bob", "reviewer"),
        ReviewDecision.APPROVE,
        revision=1,
        if_match=submitted.etag,
    )
    assert approval is not None
    assert [(item.start_step, item.end_step) for item in approval.excluded_ranges] == [(300, 450)]

    service.save_draft(
        "task-1",
        annotator,
        (operation("restore", OperationKind.RESTORE, 350, 400),),
        expected_revision=1,
        if_match=service.get_task("task-1").etag,
        client_mutation_id="mutation-2",
    )
    assert service.get_task("task-1").approved_revision is None
    assert service.list_reviews("task-1")[0].review_id == approval.review_id
    assert service.get_approved_revision(project_id="project-a", rollout_id="rollout-a") is None
    with pytest.raises(InvalidAnnotationStateError):
        service.approved_snapshot(
            project_id="project-a",
            rollout_id="rollout-a",
            actor=auth("pat", "publisher"),
        )
