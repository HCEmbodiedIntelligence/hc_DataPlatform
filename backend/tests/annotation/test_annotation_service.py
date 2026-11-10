from __future__ import annotations

import pytest

from hc_data_platform.annotation import (
    AnnotationActor,
    AnnotationConflictError,
    AnnotationOperation,
    AnnotationPermissionError,
    AnnotationRestoreTargetError,
    AnnotationService,
    AnnotationStatus,
    AnnotationTag,
    DisabledAutoAnnotationProvider,
    FeatureDisabledError,
    InMemoryAnnotationService,
    OperationKind,
    ReviewDecision,
    RevisionOrigin,
    SelfReviewPolicy,
)
from hc_data_platform.annotation.repository import InMemoryAnnotationRepository

ANNOTATOR_CAPABILITIES = frozenset(
    {
        "annotation_task.read",
        "annotation_task.claim",
        "annotation.edit",
        "annotation.save",
        "annotation.submit",
    }
)
REVIEWER_CAPABILITIES = frozenset({"annotation_task.read", "annotation.review"})


def actor(actor_id: str, *capabilities: str, projects: tuple[str, ...] = ("project-a",)):
    return AnnotationActor(
        actor_id=actor_id,
        capabilities=frozenset(capabilities),
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
        base_step_count=2_000,
    )
    annotator = actor("alice", *ANNOTATOR_CAPABILITIES)
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
        client_mutation_id="mutation-1",
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
        client_mutation_id="mutation-1",
    )
    task = service.get_task("task-1")
    restored = service.save_draft(
        "task-1",
        annotator,
        [operation("restore", OperationKind.RESTORE, 350, 400)],
        expected_revision=1,
        if_match=task.etag,
        client_mutation_id="mutation-2",
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


def test_restore_to_historic_data_revision_appends_a_new_immutable_revision() -> None:
    service, annotator = service_with_claimed_task()
    initial = service.get_task("task-1")
    service.save_draft(
        "task-1",
        annotator,
        [operation("exclude", OperationKind.EXCLUDE, 300, 450)],
        expected_revision=0,
        if_match=initial.etag,
        client_mutation_id="save-exclude",
    )
    after_exclude = service.get_task("task-1")
    service.save_draft(
        "task-1",
        annotator,
        [operation("restore-middle", OperationKind.RESTORE, 350, 400)],
        expected_revision=1,
        if_match=after_exclude.etag,
        client_mutation_id="save-partial-restore",
    )
    before_rollback = service.get_task("task-1")

    restored = service.restore_revision(
        "task-1",
        annotator,
        target_revision=0,
        expected_revision=2,
        if_match=before_rollback.etag,
        client_mutation_id="restore-to-r0",
    )

    assert restored.revision == 3
    assert restored.parent_revision == 2
    assert restored.origin is RevisionOrigin.ANNOTATION_RESTORE
    assert [item.kind for item in restored.operations] == [
        OperationKind.RESTORE,
        OperationKind.RESTORE,
    ]
    assert [
        (item.start_step, item.end_step)
        for item in service.effective_exclusions("task-1", revision=3)
    ] == []
    assert [
        (item.start_step, item.end_step)
        for item in service.effective_exclusions("task-1", revision=2)
    ] == [(300, 350), (400, 450)]
    assert (
        service.restore_revision(
            "task-1",
            annotator,
            target_revision=0,
            expected_revision=2,
            if_match=before_rollback.etag,
            client_mutation_id="restore-to-r0",
        )
        == restored
    )


def test_restore_rejects_current_or_future_target_without_mutating_history() -> None:
    service, annotator = service_with_claimed_task()
    task = service.get_task("task-1")
    service.save_draft(
        "task-1",
        annotator,
        [operation("exclude", OperationKind.EXCLUDE, 300, 450)],
        expected_revision=0,
        if_match=task.etag,
        client_mutation_id="save-exclude",
    )
    current = service.get_task("task-1")

    with pytest.raises(AnnotationRestoreTargetError):
        service.restore_revision(
            "task-1",
            annotator,
            target_revision=1,
            expected_revision=1,
            if_match=current.etag,
            client_mutation_id="invalid-target",
        )
    assert service.get_task("task-1").current_revision == 1


def test_stale_revision_or_etag_returns_conflict_without_overwrite() -> None:
    service, annotator = service_with_claimed_task()
    initial = service.get_task("task-1")
    service.save_draft(
        "task-1",
        annotator,
        [operation("one", OperationKind.EXCLUDE, 0, 10)],
        expected_revision=0,
        if_match=initial.etag,
        client_mutation_id="mutation-1",
    )

    with pytest.raises(AnnotationConflictError):
        service.save_draft(
            "task-1",
            annotator,
            [operation("two", OperationKind.EXCLUDE, 20, 30)],
            expected_revision=0,
            if_match=initial.etag,
            client_mutation_id="mutation-2",
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
        client_mutation_id="same-mutation",
    )
    replay = service.save_draft(
        "task-1",
        annotator,
        operations,
        expected_revision=0,
        if_match=initial.etag,
        client_mutation_id="same-mutation",
    )
    assert replay == first

    with pytest.raises(AnnotationConflictError):
        service.save_draft(
            "task-1",
            annotator,
            [operation("changed", OperationKind.EXCLUDE, 0, 11)],
            expected_revision=1,
            if_match=service.get_task("task-1").etag,
            client_mutation_id="same-mutation",
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
        client_mutation_id="mutation-1",
    )
    task = service.get_task("task-1")
    submitted = service.submit("task-1", annotator, expected_revision=1, if_match=task.etag)
    reviewer = actor("bob", *REVIEWER_CAPABILITIES)
    event = service.review(
        "task-1",
        reviewer,
        ReviewDecision.APPROVE,
        revision=1,
        if_match=submitted.etag,
    )

    assert event is not None
    assert event.annotation_revision == 1
    approval = service.get_approved_revision(project_id="project-a", rollout_id="rollout-a")
    assert approval is not None
    assert approval.annotation_revision == 1
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
        client_mutation_id="mutation-1",
    )
    task = service.submit(
        "task-1",
        annotator,
        expected_revision=1,
        if_match=service.get_task("task-1").etag,
    )
    service.review(
        "task-1",
        actor("bob", *REVIEWER_CAPABILITIES),
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
        client_mutation_id="mutation-2",
    )

    assert service.get_task("task-1").status is AnnotationStatus.DRAFT
    assert service.get_task("task-1").approved_revision is None
    assert service.get_revision("task-1", 1).revision == 1
    assert service.list_reviews("task-1")[0].revision == 1
    assert service.get_approved_revision(project_id="project-a", rollout_id="rollout-a") is None


@pytest.mark.parametrize("approved_revision", [0, 1])
def test_reopen_copy_edit_and_reapprove_preserves_completed_snapshot(approved_revision: int) -> None:
    service, annotator = service_with_claimed_task()
    original_tag = AnnotationTag(
        annotation_id="manual-task",
        tag_id="manual-task",
        path=("manual-task",),
        label="拿起物体",
        start_step=100,
        end_step=150,
    )
    if approved_revision:
        service.save_draft(
            "task-1", annotator, [operation("exclude", OperationKind.EXCLUDE, 3, 8)],
            tags=[original_tag], expected_revision=0,
            if_match=service.get_task("task-1").etag, client_mutation_id="initial-save",
        )
    submitted = service.submit(
        "task-1", annotator, expected_revision=approved_revision,
        if_match=service.get_task("task-1").etag,
    )
    reviewer = actor("bob", *REVIEWER_CAPABILITIES)
    service.review(
        "task-1", reviewer, ReviewDecision.APPROVE,
        revision=approved_revision, if_match=submitted.etag,
    )
    completed = service.get_task("task-1")
    source = service.get_revision("task-1", approved_revision)
    original_reviews = service.list_reviews("task-1")
    exclusions = service.effective_exclusions("task-1")

    with pytest.raises(AnnotationPermissionError):
        service.save_draft(
            "task-1", actor("other", *ANNOTATOR_CAPABILITIES), (), tags=source.tags,
            expected_revision=approved_revision, if_match=completed.etag,
            client_mutation_id="unauthorized-reopen",
        )
    reopened = service.save_draft(
        "task-1", annotator, (), tags=source.tags,
        expected_revision=approved_revision, if_match=completed.etag,
        client_mutation_id="reopen",
    )
    assert service.get_task("task-1").status is AnnotationStatus.DRAFT
    assert service.get_task("task-1").approved_revision is None
    assert reopened.tags == source.tags
    assert reopened.content_hash == source.content_hash
    assert service.effective_exclusions("task-1") == exclusions
    with pytest.raises(AnnotationConflictError):
        service.save_draft(
            "task-1", annotator, (), tags=source.tags,
            expected_revision=approved_revision, if_match=completed.etag,
            client_mutation_id="stale-reopen",
        )
    changed_tag = original_tag.model_copy(update={"label": "拿起物体并放入盒中", "end_step": 180})
    edited = service.save_draft(
        "task-1", annotator, (), tags=[changed_tag],
        expected_revision=reopened.revision, if_match=service.get_task("task-1").etag,
        client_mutation_id="save-edit",
    )
    submitted = service.submit(
        "task-1", annotator, expected_revision=edited.revision,
        if_match=service.get_task("task-1").etag,
    )
    service.review(
        "task-1", reviewer, ReviewDecision.APPROVE,
        revision=edited.revision, if_match=submitted.etag,
    )
    assert service.get_task("task-1").status is AnnotationStatus.APPROVED
    assert service.get_task("task-1").approved_revision == edited.revision
    assert service.get_draft("task-1", annotator).tags == (changed_tag,)
    assert service.get_revision("task-1", approved_revision) == source
    assert service.list_reviews("task-1")[:-1] == original_reviews


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
            actor("mallory", *REVIEWER_CAPABILITIES, projects=("project-b",)),
            ReviewDecision.APPROVE,
            revision=0,
            if_match=submitted.etag,
        )
    with pytest.raises(AnnotationPermissionError):
        service.review(
            "task-1",
            actor("charlie", *ANNOTATOR_CAPABILITIES),
            ReviewDecision.APPROVE,
            revision=0,
            if_match=submitted.etag,
        )


@pytest.mark.parametrize("use_fake_service", [False, True])
@pytest.mark.parametrize(
    ("decision", "expected_status"),
    [
        (ReviewDecision.APPROVE, AnnotationStatus.APPROVED),
        (ReviewDecision.NEEDS_REVISION, AnnotationStatus.NEEDS_REVISION),
        (ReviewDecision.REJECT, AnnotationStatus.REJECTED),
    ],
)
def test_submitter_can_review_own_revision_by_default(
    use_fake_service: bool, decision: ReviewDecision, expected_status: AnnotationStatus
) -> None:
    service = (
        InMemoryAnnotationService()
        if use_fake_service
        else AnnotationService(InMemoryAnnotationRepository())
    )
    service.create_task(
        task_id="task-1",
        project_id="project-a",
        dataset_id="dataset-a",
        dataset_version=1,
        rollout_id="rollout-a",
    )
    dual_role = actor("alice", *ANNOTATOR_CAPABILITIES, *REVIEWER_CAPABILITIES)
    claimed = service.claim("task-1", dual_role)
    submitted = service.submit("task-1", dual_role, expected_revision=0, if_match=claimed.etag)

    approved = service.review(
        "task-1",
        dual_role,
        decision,
        revision=0,
        submission_id=submitted.current_submission_id,
        if_match=submitted.etag,
    )

    task = service.get_task("task-1")
    review = service.list_reviews("task-1")[0]
    assert task.status is expected_status
    assert task.submitted_by == review.reviewer_id == "alice"
    assert review.decision is decision
    assert review.submission_id == submitted.current_submission_id
    assert (approved is not None) == (decision is ReviewDecision.APPROVE)


def test_explicit_deny_policy_blocks_reviewing_own_revision() -> None:
    service = InMemoryAnnotationService(self_review_policy=SelfReviewPolicy.DENY)
    service.create_task(
        task_id="task-1",
        project_id="project-a",
        dataset_id="dataset-a",
        dataset_version=1,
        rollout_id="rollout-a",
        base_step_count=2_000,
    )
    dual_role = actor("alice", *ANNOTATOR_CAPABILITIES, *REVIEWER_CAPABILITIES)
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


def test_unconfigured_vlm_provider_fails_with_retryable_service_error() -> None:
    provider = DisabledAutoAnnotationProvider()
    assert provider.enabled is False
    with pytest.raises(FeatureDisabledError) as error:
        provider.request(task_id="task-1", revision=1)
    assert error.value.code == "AUTO_ANNOTATION_PROVIDER_UNAVAILABLE"
    assert error.value.status == 503


@pytest.mark.parametrize("outcome", ["success", "fail"])
def test_episode_outcome_is_versioned_reviewed_and_exported_without_tags(outcome):
    from hc_data_platform.annotation.models import EpisodeOutcome
    from hc_data_platform.annotation.revision_files import revision_document
    from hc_data_platform.annotation.validation import default_flat_schema

    service, annotator = service_with_claimed_task()
    initial = service.get_draft("task-1", annotator)
    assert initial.episode_outcome == EpisodeOutcome.SUCCESS
    revision = service.save_draft(
        "task-1", annotator, (), episode_outcome=EpisodeOutcome(outcome),
        expected_revision=0, if_match=initial.etag, client_mutation_id="outcome",
    )
    assert revision.tags == ()
    assert service.get_draft("task-1", annotator).episode_outcome == outcome
    assert (revision.content_hash == service.get_revision("task-1", 0).content_hash) == (outcome == "success")
    document = revision_document(
        service.get_history("task-1", annotator), 1, default_flat_schema("project-a"), "org-a"
    )
    assert document["revision"]["episode_outcome"] == outcome
    submitted = service.submit(
        "task-1", annotator, expected_revision=1, if_match=service.get_task("task-1").etag,
    )
    approved = service.review(
        "task-1", actor("bob", *REVIEWER_CAPABILITIES), ReviewDecision.APPROVE,
        revision=1, if_match=submitted.etag,
    )
    assert approved.episode_outcome == outcome
    # Omitted fields in older clients and automatic Tag updates preserve the result.
    inherited = service.save_draft(
        "task-1", annotator, (), expected_revision=1,
        if_match=service.get_task("task-1").etag, client_mutation_id="inherit",
    )
    assert inherited.episode_outcome == outcome
    before_restore = service.get_task("task-1")
    restored = service.restore_revision(
        "task-1", annotator, target_revision=0, expected_revision=2,
        if_match=before_restore.etag, client_mutation_id="restore-outcome",
    )
    assert restored.episode_outcome == EpisodeOutcome.SUCCESS
    assert restored.content_hash == service.get_revision("task-1", 0).content_hash
    assert service.restore_revision(
        "task-1", annotator, target_revision=0, expected_revision=2,
        if_match=before_restore.etag, client_mutation_id="restore-outcome",
    ) == restored
    assert service.get_revision("task-1", 1).episode_outcome == outcome


def test_episode_outcome_idempotency_conflict_and_clear():
    from hc_data_platform.annotation.service import AnnotationMutationConflictError
    from hc_data_platform.annotation.models import EpisodeOutcome

    service, annotator = service_with_claimed_task()
    task = service.get_task("task-1")
    def save(outcome):
        return service.save_draft(
            "task-1", annotator, (), episode_outcome=outcome, expected_revision=0,
            if_match=task.etag, client_mutation_id="same-outcome-mutation",
        )
    first = save(EpisodeOutcome.SUCCESS)
    assert save(EpisodeOutcome.SUCCESS) == first
    with pytest.raises(AnnotationMutationConflictError):
        save(EpisodeOutcome.FAILURE)
    with pytest.raises(AnnotationConflictError):
        service.save_draft(
            "task-1", annotator, (), episode_outcome=EpisodeOutcome.FAILURE,
            expected_revision=0, if_match=task.etag, client_mutation_id="stale-outcome",
        )
    cleared = service.save_draft(
        "task-1", annotator, (), episode_outcome=EpisodeOutcome.UNMARKED,
        expected_revision=1, if_match=service.get_task("task-1").etag,
        client_mutation_id="clear-outcome",
    )
    assert cleared.episode_outcome == EpisodeOutcome.UNMARKED
