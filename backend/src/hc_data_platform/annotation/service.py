"""Immutable annotation workflow and non-destructive interval semantics."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import datetime
from uuid import uuid4

from hc_data_platform.core.errors import ProblemException, problem
from hc_data_platform.security import AuthContext, ScopeGuard

from .models import (
    AnnotationActor,
    AnnotationApprovedV1,
    AnnotationCurrent,
    AnnotationDraft,
    AnnotationHistory,
    AnnotationMutationRecord,
    AnnotationOperation,
    AnnotationReview,
    AnnotationRevision,
    AnnotationStatus,
    AnnotationTask,
    AutoAnnotationCapability,
    ExclusionRange,
    OperationKind,
    ReviewDecision,
    utc_now,
)
from .ports import ActorContext, AnnotationAggregate, AnnotationRepositoryPort
from .repository import InMemoryAnnotationRepository


class AnnotationError(ProblemException):
    status = 400
    code = "ANNOTATION_ERROR"
    title = "Annotation request failed"

    def __init__(self, detail: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(
            problem(
                status=self.status,
                code=self.code,
                title=self.title,
                detail=detail,
                details=details,
            ).problem
        )


class AnnotationNotFoundError(AnnotationError):
    status = 404
    code = "ANNOTATION_TASK_NOT_FOUND"
    title = "Annotation task not found"


class AnnotationConflictError(AnnotationError):
    """A stale expected revision or ETag; maps to HTTP 412."""

    status = 412
    code = "ANNOTATION_REVISION_CONFLICT"
    title = "Annotation revision conflict"


class AnnotationMutationConflictError(AnnotationConflictError):
    status = 409
    code = "CLIENT_MUTATION_ID_REUSED"
    title = "Client mutation ID reused"


class AnnotationClaimConflictError(AnnotationConflictError):
    status = 409
    code = "ANNOTATION_TASK_ALREADY_CLAIMED"
    title = "Annotation task already claimed"


class AnnotationPermissionError(AnnotationError):
    status = 403
    code = "ANNOTATION_FORBIDDEN"
    title = "Annotation access denied"


class InvalidAnnotationStateError(AnnotationError):
    status = 409
    code = "ANNOTATION_INVALID_STATE"
    title = "Invalid annotation state"


class FeatureDisabledError(AnnotationError):
    status = 501
    code = "FEATURE_DISABLED"
    title = "Feature disabled"


class DisabledAutoAnnotationProvider:
    """Explicitly disabled capability; it creates no fake jobs or results."""

    @property
    def enabled(self) -> bool:
        return False

    def capability(self) -> AutoAnnotationCapability:
        return AutoAnnotationCapability()

    def request(self, *, task_id: str, revision: int) -> str:
        del task_id, revision
        raise FeatureDisabledError("automatic/VLM annotation is disabled in phase one")


def annotation_etag(task_id: str, revision: int, state_version: int) -> str:
    """Strong ETag covering immutable content and mutable workflow pointers."""

    return f'"annotation:{task_id}:{revision}:{state_version}"'


def _fingerprint_save(
    *,
    actor_id: str,
    operations: Sequence[AnnotationOperation],
    expected_revision: int,
    if_match: str,
) -> str:
    payload = {
        "actor_id": actor_id,
        "expected_revision": expected_revision,
        "if_match": if_match,
        "operations": [operation.model_dump(mode="json") for operation in operations],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def normalize_operations(
    revisions: Sequence[AnnotationRevision], revision: int
) -> tuple[ExclusionRange, ...]:
    """Replay append-only operations into canonical, disjoint half-open ranges."""

    if revision < 0 or revision >= len(revisions):
        raise AnnotationNotFoundError(
            "the requested annotation revision does not exist",
            details={"revision": revision},
        )
    intervals: list[tuple[int, int]] = []
    for saved_revision in revisions[1 : revision + 1]:
        for operation in saved_revision.operations:
            if operation.kind is OperationKind.EXCLUDE:
                intervals = _add_interval(intervals, operation.start_step, operation.end_step)
            else:
                intervals = _subtract_interval(intervals, operation.start_step, operation.end_step)
    return tuple(ExclusionRange(start_step=start, end_step=end) for start, end in intervals)


def _add_interval(
    intervals: Sequence[tuple[int, int]], start: int, end: int
) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for current_start, current_end in sorted((*intervals, (start, end))):
        if not merged or current_start > merged[-1][1]:
            merged.append((current_start, current_end))
        else:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, current_end))
    return merged


def _subtract_interval(
    intervals: Sequence[tuple[int, int]], start: int, end: int
) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for current_start, current_end in intervals:
        if end <= current_start or start >= current_end:
            result.append((current_start, current_end))
            continue
        if current_start < start:
            result.append((current_start, start))
        if end < current_end:
            result.append((end, current_end))
    return result


class AnnotationService:
    """Application service backed by an atomic PostgreSQL-shaped repository port."""

    _READ_ROLES = frozenset({"annotator", "reviewer", "publisher"})

    def __init__(
        self,
        repository: AnnotationRepositoryPort,
        *,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] = lambda: str(uuid4()),
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._id_factory = id_factory

    def create_task(
        self,
        *,
        task_id: str,
        project_id: str,
        dataset_id: str,
        dataset_version: int,
        rollout_id: str,
    ) -> AnnotationTask:
        now = self._clock()
        initial_revision = AnnotationRevision(
            task_id=task_id,
            revision=0,
            parent_revision=None,
            author_id="system",
            client_mutation_id="initial",
            operations=(),
            created_at=now,
        )
        task = AnnotationTask(
            task_id=task_id,
            project_id=project_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            rollout_id=rollout_id,
            current_revision=0,
            state_version=0,
            status=AnnotationStatus.DRAFT,
            etag=annotation_etag(task_id, 0, 0),
            created_at=now,
            updated_at=now,
        )
        persisted = self._repository.create(
            AnnotationAggregate(task=task, revisions=(initial_revision,))
        )
        persisted_task = persisted.task
        if (
            persisted_task.task_id,
            persisted_task.project_id,
            persisted_task.dataset_id,
            persisted_task.dataset_version,
            persisted_task.rollout_id,
        ) != (task_id, project_id, dataset_id, dataset_version, rollout_id):
            raise AnnotationClaimConflictError(
                "the task ID or rollout already describes another annotation target"
            )
        return persisted_task

    def claim(self, task_id: str, actor: ActorContext) -> AnnotationTask:
        while True:
            aggregate = self._required_aggregate(task_id)
            self._authorize(aggregate.task, actor, roles={"annotator"})
            identity = self._identity(actor)
            task = aggregate.task
            if task.assignee_id == identity.actor_id:
                return task
            if task.assignee_id is not None and "admin" not in identity.roles:
                raise AnnotationClaimConflictError("annotation task is already claimed")
            updated = self._next_task(task, assignee_id=identity.actor_id)
            replacement = AnnotationAggregate(
                task=updated,
                revisions=aggregate.revisions,
                reviews=aggregate.reviews,
                mutations=aggregate.mutations,
            )
            if self._repository.compare_and_swap(
                replacement, expected_state_version=task.state_version
            ):
                return updated

    def save_draft(
        self,
        task_id: str,
        actor: ActorContext,
        operations: Sequence[AnnotationOperation],
        *,
        expected_revision: int,
        if_match: str,
        client_mutation_id: str | None = None,
        mutation_id: str | None = None,
    ) -> AnnotationRevision:
        resolved_mutation_id = self._resolve_mutation_id(client_mutation_id, mutation_id)
        identity = self._identity(actor)
        fingerprint = _fingerprint_save(
            actor_id=identity.actor_id,
            operations=operations,
            expected_revision=expected_revision,
            if_match=if_match,
        )
        while True:
            aggregate = self._required_aggregate(task_id)
            self._authorize(aggregate.task, actor, roles={"annotator"})
            self._require_assignee(aggregate.task, identity)

            replay = next(
                (
                    record
                    for record in aggregate.mutations
                    if record.client_mutation_id == resolved_mutation_id
                ),
                None,
            )
            if replay is not None:
                if replay.request_fingerprint != fingerprint:
                    raise AnnotationMutationConflictError(
                        "client_mutation_id was reused with a different request"
                    )
                return aggregate.revisions[replay.result_revision]

            self._check_version(aggregate.task, expected_revision, if_match)
            self._validate_operations(aggregate, operations)
            revision_number = aggregate.task.current_revision + 1
            now = self._clock()
            revision = AnnotationRevision(
                task_id=task_id,
                revision=revision_number,
                parent_revision=aggregate.task.current_revision,
                author_id=identity.actor_id,
                client_mutation_id=resolved_mutation_id,
                operations=tuple(operations),
                created_at=now,
            )
            updated = self._next_task(
                aggregate.task,
                current_revision=revision_number,
                status=AnnotationStatus.DRAFT,
                submitted_revision=None,
                submitted_by=None,
                approved_revision=None,
                approved_review_id=None,
                updated_at=now,
            )
            mutation = AnnotationMutationRecord(
                task_id=task_id,
                client_mutation_id=resolved_mutation_id,
                actor_id=identity.actor_id,
                request_fingerprint=fingerprint,
                expected_revision=expected_revision,
                request_etag=if_match,
                result_revision=revision_number,
                result_etag=updated.etag,
                created_at=now,
            )
            replacement = AnnotationAggregate(
                task=updated,
                revisions=(*aggregate.revisions, revision),
                reviews=aggregate.reviews,
                mutations=(*aggregate.mutations, mutation),
            )
            if self._repository.compare_and_swap(
                replacement,
                expected_state_version=aggregate.task.state_version,
            ):
                return revision

    def submit(
        self,
        task_id: str,
        actor: ActorContext,
        *,
        expected_revision: int,
        if_match: str,
    ) -> AnnotationTask:
        while True:
            aggregate = self._required_aggregate(task_id)
            self._authorize(aggregate.task, actor, roles={"annotator"})
            identity = self._identity(actor)
            self._require_assignee(aggregate.task, identity)
            self._check_version(aggregate.task, expected_revision, if_match)
            if aggregate.task.status not in {
                AnnotationStatus.DRAFT,
                AnnotationStatus.NEEDS_REVISION,
                AnnotationStatus.REJECTED,
            }:
                raise InvalidAnnotationStateError(
                    f"cannot submit from {aggregate.task.status.value}"
                )
            updated = self._next_task(
                aggregate.task,
                status=AnnotationStatus.SUBMITTED,
                submitted_revision=aggregate.task.current_revision,
                submitted_by=identity.actor_id,
                approved_revision=None,
                approved_review_id=None,
            )
            replacement = AnnotationAggregate(
                task=updated,
                revisions=aggregate.revisions,
                reviews=aggregate.reviews,
                mutations=aggregate.mutations,
            )
            if self._repository.compare_and_swap(
                replacement,
                expected_state_version=aggregate.task.state_version,
            ):
                return updated

    def review(
        self,
        task_id: str,
        actor: ActorContext,
        decision: ReviewDecision,
        *,
        revision: int,
        if_match: str,
        comment: str = "",
    ) -> AnnotationApprovedV1 | None:
        while True:
            aggregate = self._required_aggregate(task_id)
            self._authorize(aggregate.task, actor, roles={"reviewer"})
            identity = self._identity(actor)
            task = aggregate.task
            if task.status is not AnnotationStatus.SUBMITTED:
                raise InvalidAnnotationStateError("only a submitted revision may be reviewed")
            if revision != task.submitted_revision or if_match != task.etag:
                raise AnnotationConflictError(
                    "review does not target the current submitted revision",
                    details={
                        "current_revision": task.current_revision,
                        "current_etag": task.etag,
                    },
                )
            if identity.actor_id == task.submitted_by:
                raise AnnotationPermissionError("a submitter cannot review their own revision")

            now = self._clock()
            review = AnnotationReview(
                review_id=self._id_factory(),
                task_id=task_id,
                revision=revision,
                reviewer_id=identity.actor_id,
                decision=decision,
                comment=comment,
                created_at=now,
            )
            status_by_decision = {
                ReviewDecision.APPROVE: AnnotationStatus.APPROVED,
                ReviewDecision.NEEDS_REVISION: AnnotationStatus.NEEDS_REVISION,
                ReviewDecision.REJECT: AnnotationStatus.REJECTED,
            }
            approved_revision = revision if decision is ReviewDecision.APPROVE else None
            approved_review_id = review.review_id if decision is ReviewDecision.APPROVE else None
            updated = self._next_task(
                task,
                status=status_by_decision[decision],
                approved_revision=approved_revision,
                approved_review_id=approved_review_id,
                updated_at=now,
            )
            replacement = AnnotationAggregate(
                task=updated,
                revisions=aggregate.revisions,
                reviews=(*aggregate.reviews, review),
                mutations=aggregate.mutations,
            )
            if self._repository.compare_and_swap(
                replacement, expected_state_version=task.state_version
            ):
                if decision is not ReviewDecision.APPROVE:
                    return None
                return self._approval_from(replacement, review)

    def read_task(self, task_id: str, actor: ActorContext) -> AnnotationTask:
        aggregate = self._required_aggregate(task_id)
        self._authorize(aggregate.task, actor, roles=set(self._READ_ROLES))
        return aggregate.task

    def get_task(self, task_id: str, actor: ActorContext | None = None) -> AnnotationTask:
        """Read a task; passing an actor enforces the public read authorization contract."""

        aggregate = self._required_aggregate(task_id)
        if actor is not None:
            self._authorize(aggregate.task, actor, roles=set(self._READ_ROLES))
        return aggregate.task

    def get_current(self, task_id: str, actor: ActorContext) -> AnnotationCurrent:
        task = self.read_task(task_id, actor)
        return AnnotationCurrent(
            task_id=task.task_id,
            current_revision=task.current_revision,
            state_version=task.state_version,
            status=task.status,
            submitted_revision=task.submitted_revision,
            submitted_by=task.submitted_by,
            approved_revision=task.approved_revision,
            approved_review_id=task.approved_review_id,
            etag=task.etag,
            updated_at=task.updated_at,
        )

    def get_draft(self, task_id: str, actor: ActorContext) -> AnnotationDraft:
        aggregate = self._required_aggregate(task_id)
        self._authorize(aggregate.task, actor, roles={"annotator"})
        self._require_assignee(aggregate.task, self._identity(actor))
        revision = aggregate.revisions[aggregate.task.current_revision]
        return AnnotationDraft(
            task_id=task_id,
            revision=revision.revision,
            author_id=revision.author_id,
            client_mutation_id=revision.client_mutation_id,
            operations=revision.operations,
            effective_exclusions=normalize_operations(
                aggregate.revisions, aggregate.task.current_revision
            ),
            etag=aggregate.task.etag,
            updated_at=aggregate.task.updated_at,
        )

    def get_revision(
        self, task_id: str, revision: int, actor: ActorContext | None = None
    ) -> AnnotationRevision:
        aggregate = self._required_aggregate(task_id)
        if actor is not None:
            self._authorize(aggregate.task, actor, roles=set(self._READ_ROLES))
        if revision < 0 or revision >= len(aggregate.revisions):
            raise AnnotationNotFoundError("the requested annotation revision does not exist")
        return aggregate.revisions[revision]

    def list_revisions(
        self, task_id: str, actor: ActorContext | None = None
    ) -> tuple[AnnotationRevision, ...]:
        aggregate = self._required_aggregate(task_id)
        if actor is not None:
            self._authorize(aggregate.task, actor, roles=set(self._READ_ROLES))
        return aggregate.revisions

    def list_reviews(
        self, task_id: str, actor: ActorContext | None = None
    ) -> tuple[AnnotationReview, ...]:
        aggregate = self._required_aggregate(task_id)
        if actor is not None:
            self._authorize(aggregate.task, actor, roles=set(self._READ_ROLES))
        return aggregate.reviews

    def get_history(self, task_id: str, actor: ActorContext) -> AnnotationHistory:
        aggregate = self._required_aggregate(task_id)
        self._authorize(aggregate.task, actor, roles=set(self._READ_ROLES))
        return AnnotationHistory(
            task=aggregate.task,
            revisions=aggregate.revisions,
            reviews=aggregate.reviews,
        )

    def list_tasks(
        self,
        *,
        project_id: str,
        actor: ActorContext,
        status: AnnotationStatus | None = None,
    ) -> tuple[AnnotationTask, ...]:
        self._authorize_project(project_id, actor, roles=set(self._READ_ROLES))
        return tuple(
            aggregate.task
            for aggregate in self._repository.list_for_project(project_id)
            if status is None or aggregate.task.status is status
        )

    def effective_exclusions(
        self,
        task_id: str,
        *,
        revision: int | None = None,
        actor: ActorContext | None = None,
    ) -> tuple[ExclusionRange, ...]:
        aggregate = self._required_aggregate(task_id)
        if actor is not None:
            self._authorize(aggregate.task, actor, roles=set(self._READ_ROLES))
        selected = aggregate.task.current_revision if revision is None else revision
        return normalize_operations(aggregate.revisions, selected)

    def effective_ranges(
        self, *, project_id: str, rollout_id: str, annotation_revision: int
    ) -> tuple[ExclusionRange, ...]:
        """Internal project-scoped port consumed by preview for an exact revision."""

        aggregate = self._repository.find_by_rollout(project_id=project_id, rollout_id=rollout_id)
        if aggregate is None:
            raise AnnotationNotFoundError("annotation task does not exist in this project")
        return normalize_operations(aggregate.revisions, annotation_revision)

    def approved_revision(self, task_id: str) -> AnnotationRevision:
        """Legacy internal read returning only the exact currently approved revision."""

        aggregate = self._required_aggregate(task_id)
        revision = aggregate.task.approved_revision
        if aggregate.task.status is not AnnotationStatus.APPROVED or revision is None:
            raise InvalidAnnotationStateError("task has no currently approved revision")
        return aggregate.revisions[revision]

    def approved_snapshot(
        self, *, project_id: str, rollout_id: str, actor: ActorContext
    ) -> AnnotationApprovedV1:
        """Authorized publishing read; only Publisher/Admin can consume it."""

        self._authorize_project(project_id, actor, roles={"publisher"})
        aggregate = self._repository.find_by_rollout(project_id=project_id, rollout_id=rollout_id)
        if aggregate is None:
            raise AnnotationNotFoundError("annotation task does not exist in this project")
        self._authorize(aggregate.task, actor, roles={"publisher"})
        return self._current_approval(aggregate)

    def get_approved_revision(
        self, *, project_id: str, rollout_id: str
    ) -> AnnotationApprovedV1 | None:
        """Trusted service-to-service port consumed by the publishing module."""

        aggregate = self._repository.find_by_rollout(project_id=project_id, rollout_id=rollout_id)
        if aggregate is None or aggregate.task.status is not AnnotationStatus.APPROVED:
            return None
        return self._current_approval(aggregate)

    def _current_approval(self, aggregate: AnnotationAggregate) -> AnnotationApprovedV1:
        review_id = aggregate.task.approved_review_id
        revision = aggregate.task.approved_revision
        if (
            aggregate.task.status is not AnnotationStatus.APPROVED
            or review_id is None
            or revision is None
        ):
            raise InvalidAnnotationStateError("task has no currently approved revision")
        review = next(
            (saved for saved in aggregate.reviews if saved.review_id == review_id),
            None,
        )
        if review is None or review.revision != revision:
            raise InvalidAnnotationStateError("approved review pointer is inconsistent")
        return self._approval_from(aggregate, review)

    @staticmethod
    def _approval_from(
        aggregate: AnnotationAggregate, review: AnnotationReview
    ) -> AnnotationApprovedV1:
        task = aggregate.task
        return AnnotationApprovedV1(
            project_id=task.project_id,
            dataset_id=task.dataset_id,
            dataset_version=task.dataset_version,
            rollout_id=task.rollout_id,
            task_id=task.task_id,
            annotation_revision=review.revision,
            review_id=review.review_id,
            reviewer_id=review.reviewer_id,
            excluded_ranges=normalize_operations(aggregate.revisions, review.revision),
            approved_at=review.created_at,
        )

    def _required_aggregate(self, task_id: str) -> AnnotationAggregate:
        aggregate = self._repository.get(task_id)
        if aggregate is None:
            raise AnnotationNotFoundError("annotation task does not exist")
        return aggregate

    def _next_task(self, task: AnnotationTask, **changes: object) -> AnnotationTask:
        state_version = task.state_version + 1
        changed_revision = changes.get("current_revision", task.current_revision)
        if not isinstance(changed_revision, int):
            raise TypeError("current_revision must be an integer")
        revision = changed_revision
        changes.setdefault("state_version", state_version)
        changes.setdefault("updated_at", self._clock())
        changes.setdefault("etag", annotation_etag(task.task_id, revision, state_version))
        return task.model_copy(update=changes)

    @staticmethod
    def _identity(actor: ActorContext) -> AnnotationActor:
        if isinstance(actor, AuthContext):
            return AnnotationActor.from_auth(actor)
        return actor

    def _authorize(self, task: AnnotationTask, actor: ActorContext, *, roles: set[str]) -> None:
        self._authorize_project(task.project_id, actor, roles=roles)

    @staticmethod
    def _authorize_project(project_id: str, actor: ActorContext, *, roles: set[str]) -> None:
        if isinstance(actor, AuthContext):
            try:
                ScopeGuard.require(actor, project_id)
                actor.require_role(*roles, "admin")
            except ProblemException as exc:
                raise AnnotationPermissionError(exc.problem.detail) from exc
            return
        if "admin" not in actor.roles and project_id not in actor.project_ids:
            raise AnnotationPermissionError("actor is outside the task project scope")
        if "admin" not in actor.roles and not actor.roles.intersection(roles):
            required = ", ".join(sorted(roles))
            raise AnnotationPermissionError(f"one of these roles is required: {required}")

    @staticmethod
    def _require_assignee(task: AnnotationTask, actor: AnnotationActor) -> None:
        if task.assignee_id != actor.actor_id and "admin" not in actor.roles:
            raise AnnotationPermissionError("only the assignee may edit or submit")

    @staticmethod
    def _check_version(task: AnnotationTask, expected_revision: int, if_match: str) -> None:
        if expected_revision != task.current_revision or if_match != task.etag:
            raise AnnotationConflictError(
                "expected_revision or If-Match is stale",
                details={
                    "current_revision": task.current_revision,
                    "current_etag": task.etag,
                },
            )

    @staticmethod
    def _validate_operations(
        aggregate: AnnotationAggregate, operations: Sequence[AnnotationOperation]
    ) -> None:
        operation_ids = [operation.operation_id for operation in operations]
        if len(operation_ids) != len(set(operation_ids)):
            raise AnnotationMutationConflictError("operation_id is duplicated in the revision")
        existing_ids = {
            operation.operation_id
            for revision in aggregate.revisions
            for operation in revision.operations
        }
        reused = existing_ids.intersection(operation_ids)
        if reused:
            raise AnnotationMutationConflictError(
                "operation_id was already used in this task",
                details={"operation_ids": sorted(reused)},
            )

    @staticmethod
    def _resolve_mutation_id(client_mutation_id: str | None, mutation_id: str | None) -> str:
        if (
            client_mutation_id is not None
            and mutation_id is not None
            and client_mutation_id != mutation_id
        ):
            raise AnnotationMutationConflictError(
                "client_mutation_id and legacy mutation_id disagree"
            )
        resolved = client_mutation_id or mutation_id
        if resolved is None or not resolved.strip():
            raise AnnotationMutationConflictError("client_mutation_id is required")
        return resolved


class InMemoryAnnotationService(AnnotationService):
    """Executable fake with the same CAS and append-only rules as PostgreSQL."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] = lambda: str(uuid4()),
    ) -> None:
        super().__init__(
            InMemoryAnnotationRepository(),
            clock=clock,
            id_factory=id_factory,
        )
