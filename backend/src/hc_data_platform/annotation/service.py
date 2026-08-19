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
    AnnotationReviewCheck,
    AnnotationRevision,
    AnnotationStatus,
    AnnotationSubmission,
    AnnotationSubmissionMutationRecord,
    AnnotationTag,
    AnnotationTask,
    AnnotationTaskCreationSource,
    AnnotationTaskKind,
    AutoAnnotationCapability,
    ExclusionRange,
    LegacyAuditReference,
    OperationKind,
    ReviewCheckKind,
    ReviewDecision,
    RevisionOrigin,
    SelfReviewPolicy,
    TagSchemaDocument,
    TagSchemaStatus,
    TagSchemaTarget,
    TagSchemaVersion,
    utc_now,
)
from .ports import ActorContext, AnnotationAggregate, AnnotationRepositoryPort
from .repository import InMemoryAnnotationRepository
from .validation import (
    TagValidationIssue,
    legacy_flat_schema,
    revision_content_hash,
    schema_content_hash,
    stable_hash,
    validate_tag_revision,
)


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
    """A stale expected revision or ETag; maps to the required HTTP 409."""

    status = 409
    code = "ANNOTATION_REVISION_CONFLICT"
    title = "Annotation revision conflict"


class AnnotationMutationConflictError(AnnotationConflictError):
    status = 409
    code = "CLIENT_MUTATION_ID_REUSED"
    title = "Client mutation ID reused"


class AnnotationIdempotencyConflictError(AnnotationConflictError):
    status = 409
    code = "IDEMPOTENCY_KEY_REUSED"
    title = "Idempotency key reused"


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


class AnnotationValidationError(AnnotationError):
    status = 422
    code = "ANNOTATION_TAG_VALIDATION_FAILED"
    title = "Annotation Tag validation failed"


class AnnotationPolicyUnconfirmedError(AnnotationError):
    status = 409
    code = "ANNOTATION_REVIEW_POLICY_UNCONFIRMED"
    title = "Annotation review policy is not confirmed"


class TagSchemaImmutableError(AnnotationError):
    status = 409
    code = "TAG_SCHEMA_VERSION_IMMUTABLE"
    title = "Published Tag Schema version is immutable"


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
    tags: Sequence[AnnotationTag],
    expected_revision: int,
    if_match: str,
) -> str:
    payload = {
        "actor_id": actor_id,
        "expected_revision": expected_revision,
        "if_match": if_match,
        "operations": [operation.model_dump(mode="json") for operation in operations],
        "tags": [tag.model_dump(mode="json") for tag in tags],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _fingerprint_submit(
    *, actor_id: str, task_id: str, revision: AnnotationRevision, if_match: str
) -> str:
    return stable_hash(
        {
            "actor_id": actor_id,
            "task_id": task_id,
            "revision": revision.revision,
            "revision_content_hash": revision.content_hash,
            "if_match": if_match,
        }
    )


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
        self_review_policy: SelfReviewPolicy = SelfReviewPolicy.UNCONFIRMED,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._id_factory = id_factory
        self._self_review_policy = self_review_policy

    def create_tag_schema_version(
        self,
        *,
        project_id: str,
        name: str,
        document: TagSchemaDocument,
        compatible_targets: Sequence[TagSchemaTarget] = (),
        actor: ActorContext,
        schema_id: str | None = None,
        version: int | None = None,
    ) -> TagSchemaVersion:
        self._authorize_project(project_id, actor, roles={"publisher"})
        identity = self._identity(actor, project_id)
        resolved_id = schema_id or self._id_factory()
        existing = self._repository.list_tag_schema_versions(
            project_id=project_id, schema_id=resolved_id
        )
        resolved_version = version if version is not None else len(existing) + 1
        schema = TagSchemaVersion(
            schema_id=resolved_id,
            project_id=project_id,
            name=name,
            version=resolved_version,
            document=document,
            compatible_targets=tuple(compatible_targets),
            content_hash=schema_content_hash(document),
            created_by=identity.actor_id,
            created_at=self._clock(),
        )
        persisted = self._repository.create_tag_schema_version(schema)
        if persisted != schema:
            raise TagSchemaImmutableError(
                "the Tag Schema version already exists with different immutable content"
            )
        return persisted

    def publish_tag_schema_version(
        self,
        *,
        project_id: str,
        schema_id: str,
        version: int,
        actor: ActorContext,
    ) -> TagSchemaVersion:
        self._authorize_project(project_id, actor, roles={"publisher"})
        draft = self._repository.get_tag_schema_version(schema_id=schema_id, version=version)
        if draft is None or draft.project_id != project_id:
            raise AnnotationNotFoundError("Tag Schema version does not exist")
        if draft.status is TagSchemaStatus.PUBLISHED:
            return draft
        published = draft.model_copy(
            update={
                "status": TagSchemaStatus.PUBLISHED,
                "published_by": self._identity(actor, project_id).actor_id,
                "published_at": self._clock(),
            }
        )
        if not self._repository.publish_tag_schema_version(draft, published):
            latest = self._repository.get_tag_schema_version(schema_id=schema_id, version=version)
            if latest is not None and latest.status is TagSchemaStatus.PUBLISHED:
                return latest
            raise TagSchemaImmutableError("Tag Schema version changed concurrently")
        return published

    def get_tag_schema_version(
        self,
        *,
        project_id: str,
        schema_id: str,
        version: int,
        actor: ActorContext,
    ) -> TagSchemaVersion:
        self._authorize_project(project_id, actor, roles=set(self._READ_ROLES))
        schema = self._schema_for(project_id, schema_id, version)
        if schema.project_id != project_id:
            raise AnnotationNotFoundError("Tag Schema version does not exist")
        return schema

    def list_tag_schema_versions(
        self, *, project_id: str, schema_id: str, actor: ActorContext
    ) -> tuple[TagSchemaVersion, ...]:
        self._authorize_project(project_id, actor, roles=set(self._READ_ROLES))
        if schema_id == "legacy-flat":
            return (legacy_flat_schema(project_id),)
        return self._repository.list_tag_schema_versions(project_id=project_id, schema_id=schema_id)

    def create_task(
        self,
        *,
        task_id: str,
        project_id: str,
        dataset_id: str,
        dataset_version: int,
        rollout_id: str,
        region_code: str | None = None,
        task_kind: AnnotationTaskKind = AnnotationTaskKind.TAGGING,
        creation_source: AnnotationTaskCreationSource = AnnotationTaskCreationSource.LEGACY,
        source_workflow_id: str | None = None,
        base_lance_version: int | None = None,
        base_step_count: int | None = None,
        tag_schema_id: str = "legacy-flat",
        tag_schema_version: int = 1,
    ) -> AnnotationTask:
        now = self._clock()
        resolved_lance_version = (
            dataset_version if base_lance_version is None else base_lance_version
        )
        schema = self._schema_for(project_id, tag_schema_id, tag_schema_version)
        if schema.status is not TagSchemaStatus.PUBLISHED:
            raise InvalidAnnotationStateError(
                "annotation tasks must pin a published Tag Schema version"
            )
        initial_hash = revision_content_hash(
            base_lance_version=resolved_lance_version,
            tag_schema_id=tag_schema_id,
            tag_schema_version=tag_schema_version,
            tags=(),
            operations=(),
        )
        initial_revision = AnnotationRevision(
            task_id=task_id,
            revision=0,
            parent_revision=None,
            author_id="system",
            client_mutation_id="initial",
            base_lance_version=resolved_lance_version,
            tag_schema_id=tag_schema_id,
            tag_schema_version=tag_schema_version,
            tags=(),
            operations=(),
            content_hash=initial_hash,
            created_at=now,
        )
        task = AnnotationTask(
            task_id=task_id,
            project_id=project_id,
            region_code=region_code,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            base_lance_version=resolved_lance_version,
            base_step_count=base_step_count,
            tag_schema_id=tag_schema_id,
            tag_schema_version=tag_schema_version,
            rollout_id=rollout_id,
            task_kind=task_kind,
            creation_source=creation_source,
            source_workflow_id=source_workflow_id,
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
            persisted_task.region_code,
            persisted_task.dataset_id,
            persisted_task.dataset_version,
            persisted_task.base_lance_version,
            persisted_task.base_step_count,
            persisted_task.tag_schema_id,
            persisted_task.tag_schema_version,
            persisted_task.rollout_id,
            persisted_task.task_kind,
            persisted_task.creation_source,
            persisted_task.source_workflow_id,
        ) != (
            task_id,
            project_id,
            region_code,
            dataset_id,
            dataset_version,
            resolved_lance_version,
            base_step_count,
            tag_schema_id,
            tag_schema_version,
            rollout_id,
            task_kind,
            creation_source,
            source_workflow_id,
        ):
            raise AnnotationClaimConflictError(
                "the task ID or rollout already describes another annotation target"
            )
        return persisted_task

    def claim(self, task_id: str, actor: ActorContext) -> AnnotationTask:
        while True:
            aggregate = self._required_aggregate(task_id)
            self._authorize(aggregate.task, actor, roles={"annotator"})
            identity = self._identity(actor, aggregate.task.project_id)
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
                submissions=aggregate.submissions,
                submission_mutations=aggregate.submission_mutations,
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
        tags: Sequence[AnnotationTag] | None = None,
        expected_revision: int,
        if_match: str,
        client_mutation_id: str | None = None,
        mutation_id: str | None = None,
        origin: RevisionOrigin = RevisionOrigin.ANNOTATION,
        legacy_audit: LegacyAuditReference | None = None,
        imported_author_id: str | None = None,
        imported_created_at: datetime | None = None,
    ) -> AnnotationRevision:
        resolved_mutation_id = self._resolve_mutation_id(client_mutation_id, mutation_id)
        while True:
            aggregate = self._required_aggregate(task_id)
            self._authorize(aggregate.task, actor, roles={"annotator"})
            identity = self._identity(actor, aggregate.task.project_id)
            self._require_assignee(aggregate.task, identity)
            inherited_tags = (
                aggregate.revisions[expected_revision].tags
                if 0 <= expected_revision < len(aggregate.revisions)
                else ()
            )
            resolved_tags = tuple(tags) if tags is not None else inherited_tags
            fingerprint = _fingerprint_save(
                actor_id=identity.actor_id,
                operations=operations,
                tags=resolved_tags,
                expected_revision=expected_revision,
                if_match=if_match,
            )

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
            self._validate_tag_content(
                aggregate.task,
                tags=resolved_tags,
                operations=operations,
            )
            revision_number = aggregate.task.current_revision + 1
            now = imported_created_at or self._clock()
            cumulative_operations = tuple(
                operation
                for saved_revision in aggregate.revisions
                for operation in saved_revision.operations
            ) + tuple(operations)
            revision = AnnotationRevision(
                task_id=task_id,
                revision=revision_number,
                parent_revision=aggregate.task.current_revision,
                author_id=imported_author_id or identity.actor_id,
                client_mutation_id=resolved_mutation_id,
                base_lance_version=aggregate.task.base_lance_version,
                tag_schema_id=aggregate.task.tag_schema_id,
                tag_schema_version=aggregate.task.tag_schema_version,
                tags=resolved_tags,
                operations=tuple(operations),
                origin=origin,
                legacy_audit=legacy_audit,
                content_hash=revision_content_hash(
                    base_lance_version=aggregate.task.base_lance_version,
                    tag_schema_id=aggregate.task.tag_schema_id,
                    tag_schema_version=aggregate.task.tag_schema_version,
                    tags=resolved_tags,
                    operations=cumulative_operations,
                ),
                created_at=now,
            )
            updated = self._next_task(
                aggregate.task,
                current_revision=revision_number,
                status=AnnotationStatus.DRAFT,
                submitted_revision=None,
                submitted_by=None,
                current_submission_id=None,
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
                submissions=aggregate.submissions,
                submission_mutations=aggregate.submission_mutations,
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
        idempotency_key: str | None = None,
    ) -> AnnotationTask:
        task = self._required_aggregate(task_id).task
        resolved_key = idempotency_key or (
            f"compat-submit:{task_id}:{expected_revision}:"
            f"{self._identity(actor, task.project_id).actor_id}"
        )
        self.submit_for_review(
            task_id,
            actor,
            expected_revision=expected_revision,
            if_match=if_match,
            idempotency_key=resolved_key,
        )
        return self._required_aggregate(task_id).task

    def submit_for_review(
        self,
        task_id: str,
        actor: ActorContext,
        *,
        expected_revision: int,
        if_match: str,
        idempotency_key: str,
    ) -> AnnotationSubmission:
        if not idempotency_key.strip():
            raise AnnotationIdempotencyConflictError("Idempotency-Key is required")
        while True:
            aggregate = self._required_aggregate(task_id)
            self._authorize(aggregate.task, actor, roles={"annotator"})
            identity = self._identity(actor, aggregate.task.project_id)
            self._require_assignee(aggregate.task, identity)
            if expected_revision < 0 or expected_revision >= len(aggregate.revisions):
                raise AnnotationConflictError("submitted revision does not exist")
            revision = aggregate.revisions[expected_revision]
            fingerprint = _fingerprint_submit(
                actor_id=identity.actor_id,
                task_id=task_id,
                revision=revision,
                if_match=if_match,
            )
            replay = next(
                (
                    record
                    for record in aggregate.submission_mutations
                    if record.idempotency_key == idempotency_key
                ),
                None,
            )
            if replay is not None:
                if replay.request_fingerprint != fingerprint:
                    raise AnnotationIdempotencyConflictError(
                        "Idempotency-Key was reused with a different submit request"
                    )
                return next(
                    submission
                    for submission in aggregate.submissions
                    if submission.submission_id == replay.submission_id
                )
            self._check_version(aggregate.task, expected_revision, if_match)
            legacy_submission_upgrade = (
                aggregate.task.status is AnnotationStatus.SUBMITTED
                and aggregate.task.current_submission_id is None
            )
            if not legacy_submission_upgrade and aggregate.task.status not in {
                AnnotationStatus.DRAFT,
                AnnotationStatus.NEEDS_REVISION,
                AnnotationStatus.REJECTED,
            }:
                raise InvalidAnnotationStateError(
                    f"cannot submit from {aggregate.task.status.value}"
                )
            checks = self._review_checks(aggregate, revision)
            now = self._clock()
            submission = AnnotationSubmission(
                submission_id=self._id_factory(),
                task_id=task_id,
                revision=revision.revision,
                submitted_by=identity.actor_id,
                base_lance_version=aggregate.task.base_lance_version,
                tag_schema_id=aggregate.task.tag_schema_id,
                tag_schema_version=aggregate.task.tag_schema_version,
                tag_schema_hash=self._schema_for_task(aggregate.task).content_hash,
                revision_content_hash=revision.content_hash,
                checks=checks,
                created_at=now,
            )
            updated = self._next_task(
                aggregate.task,
                status=AnnotationStatus.SUBMITTED,
                submitted_revision=aggregate.task.current_revision,
                submitted_by=identity.actor_id,
                current_submission_id=submission.submission_id,
                approved_revision=None,
                approved_review_id=None,
                updated_at=now,
            )
            mutation = AnnotationSubmissionMutationRecord(
                task_id=task_id,
                idempotency_key=idempotency_key,
                actor_id=identity.actor_id,
                request_fingerprint=fingerprint,
                submission_id=submission.submission_id,
                result_etag=updated.etag,
                created_at=now,
            )
            replacement = AnnotationAggregate(
                task=updated,
                revisions=aggregate.revisions,
                reviews=aggregate.reviews,
                mutations=aggregate.mutations,
                submissions=(*aggregate.submissions, submission),
                submission_mutations=(*aggregate.submission_mutations, mutation),
            )
            if self._repository.compare_and_swap(
                replacement,
                expected_state_version=aggregate.task.state_version,
            ):
                return submission

    def review(
        self,
        task_id: str,
        actor: ActorContext,
        decision: ReviewDecision,
        *,
        revision: int,
        if_match: str,
        submission_id: str | None = None,
        comment: str = "",
    ) -> AnnotationApprovedV1 | None:
        while True:
            aggregate = self._required_aggregate(task_id)
            self._authorize(aggregate.task, actor, roles={"reviewer"})
            identity = self._identity(actor, aggregate.task.project_id)
            task = aggregate.task
            if task.status is not AnnotationStatus.SUBMITTED:
                raise InvalidAnnotationStateError("only a submitted revision may be reviewed")
            resolved_submission_id = submission_id or task.current_submission_id
            if (
                revision != task.submitted_revision
                or resolved_submission_id != task.current_submission_id
                or if_match != task.etag
            ):
                raise AnnotationConflictError(
                    "review does not target the current submitted revision",
                    details={
                        "current_revision": task.current_revision,
                        "current_etag": task.etag,
                    },
                )
            submission = next(
                (
                    item
                    for item in aggregate.submissions
                    if item.submission_id == resolved_submission_id
                ),
                None,
            )
            if submission is None:
                raise InvalidAnnotationStateError("reviewable submission snapshot is missing")
            if (
                submission.revision != revision
                or submission.base_lance_version != task.base_lance_version
                or submission.tag_schema_id != task.tag_schema_id
                or submission.tag_schema_version != task.tag_schema_version
                or submission.revision_content_hash != aggregate.revisions[revision].content_hash
            ):
                raise AnnotationConflictError(
                    "review input no longer matches its fixed Lance/Schema revision"
                )
            if identity.actor_id == task.submitted_by:
                if self._self_review_policy is SelfReviewPolicy.UNCONFIRMED:
                    raise AnnotationPolicyUnconfirmedError(
                        "OPEN-08 is unresolved; self-review has no configured policy"
                    )
                if self._self_review_policy is SelfReviewPolicy.DENY:
                    raise AnnotationPermissionError(
                        "configured review policy forbids reviewing your own revision"
                    )

            now = self._clock()
            review = AnnotationReview(
                review_id=self._id_factory(),
                submission_id=submission.submission_id,
                task_id=task_id,
                revision=revision,
                reviewer_id=identity.actor_id,
                decision=decision,
                checked_kinds=tuple(check.kind for check in submission.checks),
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
                submissions=aggregate.submissions,
                submission_mutations=aggregate.submission_mutations,
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
            current_submission_id=task.current_submission_id,
            approved_revision=task.approved_revision,
            approved_review_id=task.approved_review_id,
            etag=task.etag,
            updated_at=task.updated_at,
        )

    def get_draft(self, task_id: str, actor: ActorContext) -> AnnotationDraft:
        aggregate = self._required_aggregate(task_id)
        self._authorize(aggregate.task, actor, roles={"annotator"})
        self._require_assignee(
            aggregate.task,
            self._identity(actor, aggregate.task.project_id),
        )
        revision = aggregate.revisions[aggregate.task.current_revision]
        return AnnotationDraft(
            task_id=task_id,
            revision=revision.revision,
            author_id=revision.author_id,
            client_mutation_id=revision.client_mutation_id,
            base_lance_version=aggregate.task.base_lance_version,
            base_step_count=aggregate.task.base_step_count,
            tag_schema_id=aggregate.task.tag_schema_id,
            tag_schema_version=aggregate.task.tag_schema_version,
            tags=revision.tags,
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
            submissions=aggregate.submissions,
            reviews=aggregate.reviews,
        )

    def get_submission(
        self, task_id: str, submission_id: str, actor: ActorContext
    ) -> AnnotationSubmission:
        aggregate = self._required_aggregate(task_id)
        self._authorize(aggregate.task, actor, roles=set(self._READ_ROLES))
        submission = next(
            (item for item in aggregate.submissions if item.submission_id == submission_id),
            None,
        )
        if submission is None:
            raise AnnotationNotFoundError("annotation submission does not exist")
        return submission

    def list_submissions(
        self, task_id: str, actor: ActorContext
    ) -> tuple[AnnotationSubmission, ...]:
        aggregate = self._required_aggregate(task_id)
        self._authorize(aggregate.task, actor, roles=set(self._READ_ROLES))
        return aggregate.submissions

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
            submission_id=review.submission_id,
            base_lance_version=task.base_lance_version,
            tag_schema_id=task.tag_schema_id,
            tag_schema_version=task.tag_schema_version,
            revision_content_hash=aggregate.revisions[review.revision].content_hash,
            review_id=review.review_id,
            reviewer_id=review.reviewer_id,
            excluded_ranges=normalize_operations(aggregate.revisions, review.revision),
            approved_at=review.created_at,
        )

    def _schema_for(self, project_id: str, schema_id: str, version: int) -> TagSchemaVersion:
        if schema_id == "legacy-flat" and version == 1:
            return legacy_flat_schema(project_id)
        schema = self._repository.get_tag_schema_version(schema_id=schema_id, version=version)
        if schema is None or schema.project_id != project_id:
            raise AnnotationNotFoundError("pinned Tag Schema version does not exist")
        return schema

    def _schema_for_task(self, task: AnnotationTask) -> TagSchemaVersion:
        return self._schema_for(task.project_id, task.tag_schema_id, task.tag_schema_version)

    def _validate_tag_content(
        self,
        task: AnnotationTask,
        *,
        tags: Sequence[AnnotationTag],
        operations: Sequence[AnnotationOperation],
    ) -> tuple[AnnotationReviewCheck, ...]:
        try:
            return validate_tag_revision(
                schema=self._schema_for_task(task),
                base_step_count=task.base_step_count,
                tags=tags,
                operations=operations,
            )
        except TagValidationIssue as exc:
            raise AnnotationValidationError(
                exc.message,
                details={"check": exc.kind.value},
            ) from exc

    def _review_checks(
        self, aggregate: AnnotationAggregate, revision: AnnotationRevision
    ) -> tuple[AnnotationReviewCheck, ...]:
        cumulative_operations = tuple(
            operation
            for saved_revision in aggregate.revisions[: revision.revision + 1]
            for operation in saved_revision.operations
        )
        checks = self._validate_tag_content(
            aggregate.task,
            tags=revision.tags,
            operations=cumulative_operations,
        )
        expected_hash = revision_content_hash(
            base_lance_version=aggregate.task.base_lance_version,
            tag_schema_id=aggregate.task.tag_schema_id,
            tag_schema_version=aggregate.task.tag_schema_version,
            tags=revision.tags,
            operations=cumulative_operations,
        )
        if revision.content_hash != expected_hash:
            raise AnnotationValidationError(
                "annotation revision content hash does not match the review snapshot",
                details={"check": ReviewCheckKind.SCHEMA_VERSION.value},
            )
        return checks

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
    def _identity(actor: ActorContext, project_id: str | None = None) -> AnnotationActor:
        if isinstance(actor, AuthContext):
            return AnnotationActor.from_auth(actor, project_id)
        return actor

    def _authorize(self, task: AnnotationTask, actor: ActorContext, *, roles: set[str]) -> None:
        self._authorize_project(
            task.project_id,
            actor,
            roles=roles,
            region_code=task.region_code,
        )

    @staticmethod
    def _authorize_project(
        project_id: str,
        actor: ActorContext,
        *,
        roles: set[str],
        region_code: str | None = None,
    ) -> None:
        if isinstance(actor, AuthContext):
            try:
                ScopeGuard.require(actor, project_id, region_code)
                actor.require_role(*roles, "admin", project_id=project_id)
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
        self_review_policy: SelfReviewPolicy = SelfReviewPolicy.UNCONFIRMED,
    ) -> None:
        super().__init__(
            InMemoryAnnotationRepository(),
            clock=clock,
            id_factory=id_factory,
            self_review_policy=self_review_policy,
        )
