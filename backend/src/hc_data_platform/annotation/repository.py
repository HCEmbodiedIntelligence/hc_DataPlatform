"""Deterministic repository fake enforcing the PostgreSQL persistence invariants."""

from __future__ import annotations

from datetime import datetime
from threading import RLock

from .models import (
    AnnotationRevisionThread,
    AnnotationRevisionThreadRevision,
    AnnotationStatus,
    AnnotationTaskKind,
    RevisionOrigin,
    TagSchemaStatus,
    TagSchemaVersion,
)
from .ports import AnnotationAggregate


class AnnotationRepositoryInvariantError(RuntimeError):
    pass


def validate_aggregate(aggregate: AnnotationAggregate) -> None:
    task = aggregate.task
    if not aggregate.revisions:
        raise AnnotationRepositoryInvariantError("an annotation task requires revision zero")
    numbers = tuple(revision.revision for revision in aggregate.revisions)
    if numbers != tuple(range(len(numbers))):
        raise AnnotationRepositoryInvariantError(
            "annotation revisions must be contiguous from zero"
        )
    if aggregate.revisions[-1].revision != task.current_revision:
        raise AnnotationRepositoryInvariantError(
            "current_revision must identify the latest revision"
        )
    if any(revision.task_id != task.task_id for revision in aggregate.revisions):
        raise AnnotationRepositoryInvariantError("revision belongs to another task")
    if any(review.task_id != task.task_id for review in aggregate.reviews):
        raise AnnotationRepositoryInvariantError("review belongs to another task")
    if any(mutation.task_id != task.task_id for mutation in aggregate.mutations):
        raise AnnotationRepositoryInvariantError("mutation belongs to another task")
    if any(submission.task_id != task.task_id for submission in aggregate.submissions):
        raise AnnotationRepositoryInvariantError("submission belongs to another task")
    if any(mutation.task_id != task.task_id for mutation in aggregate.submission_mutations):
        raise AnnotationRepositoryInvariantError("submission mutation belongs to another task")

    operation_ids = [
        operation.operation_id
        for revision in aggregate.revisions
        for operation in revision.operations
    ]
    if len(operation_ids) != len(set(operation_ids)):
        raise AnnotationRepositoryInvariantError("operation_id must be unique within a task")
    review_ids = [review.review_id for review in aggregate.reviews]
    if len(review_ids) != len(set(review_ids)):
        raise AnnotationRepositoryInvariantError("review_id must be unique")
    mutation_ids = [mutation.client_mutation_id for mutation in aggregate.mutations]
    if len(mutation_ids) != len(set(mutation_ids)):
        raise AnnotationRepositoryInvariantError("client_mutation_id must be unique within a task")
    submission_ids = [submission.submission_id for submission in aggregate.submissions]
    if len(submission_ids) != len(set(submission_ids)):
        raise AnnotationRepositoryInvariantError("submission_id must be unique")
    submit_keys = [mutation.idempotency_key for mutation in aggregate.submission_mutations]
    if len(submit_keys) != len(set(submit_keys)):
        raise AnnotationRepositoryInvariantError("submit Idempotency-Key must be unique")
    known_submissions = set(submission_ids)
    if any(
        mutation.submission_id not in known_submissions
        for mutation in aggregate.submission_mutations
    ):
        raise AnnotationRepositoryInvariantError("submit mutation references an unknown submission")


class InMemoryAnnotationRepository:
    """Thread-safe fake for the durable annotation repository.

    The fake deliberately exposes the same compare-and-swap boundary required from
    PostgreSQL so concurrency tests do not get weaker semantics in memory.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._aggregates: dict[str, AnnotationAggregate] = {}
        self._targets: dict[tuple[str, str | None, str, int, int, str, str], str] = {}
        self._schemas: dict[tuple[str, int], TagSchemaVersion] = {}
        self._schema_bindings: dict[tuple[str, str, str, str, str], tuple[str, int]] = {}
        self.revision_thread_list_audits: list[dict[str, object]] = []

    def create(self, aggregate: AnnotationAggregate) -> AnnotationAggregate:
        validate_aggregate(aggregate)
        task = aggregate.task
        target_key = (
            task.project_id,
            task.region_code,
            task.dataset_id,
            task.dataset_version,
            task.base_lance_version,
            task.rollout_id,
            task.task_kind.value,
        )
        with self._lock:
            existing = self._aggregates.get(task.task_id)
            if existing is not None:
                return existing
            other_task_id = self._targets.get(target_key)
            if other_task_id is not None:
                return self._aggregates[other_task_id]
            self._aggregates[task.task_id] = aggregate
            self._targets[target_key] = task.task_id
            return aggregate

    def get(self, task_id: str) -> AnnotationAggregate | None:
        with self._lock:
            return self._aggregates.get(task_id)

    def find_by_rollout(self, *, project_id: str, rollout_id: str) -> AnnotationAggregate | None:
        with self._lock:
            matches = [
                aggregate
                for aggregate in self._aggregates.values()
                if aggregate.task.project_id == project_id
                and aggregate.task.rollout_id == rollout_id
            ]
            return None if not matches else sorted(matches, key=lambda item: item.task.task_id)[0]

    def list_for_project(self, project_id: str) -> tuple[AnnotationAggregate, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        aggregate
                        for aggregate in self._aggregates.values()
                        if aggregate.task.project_id == project_id
                    ),
                    key=lambda aggregate: (aggregate.task.updated_at, aggregate.task.task_id),
                    reverse=True,
                )
            )

    def list_revision_threads(
        self,
        *,
        project_id: str,
        region_code: str,
        status: AnnotationStatus | None,
        origin: RevisionOrigin | None,
        legacy_draft_id: str | None,
        snapshot_at: datetime,
        after_updated_at: datetime | None,
        after_task_id: str | None,
        limit: int,
    ) -> tuple[AnnotationRevisionThread, ...]:
        with self._lock:
            records = [
                aggregate
                for aggregate in self._aggregates.values()
                if aggregate.task.project_id == project_id
                and aggregate.task.region_code == region_code
                and aggregate.task.updated_at <= snapshot_at
                and (status is None or aggregate.task.status is status)
                and (origin is None or aggregate.revisions[-1].origin is origin)
            ]
            if legacy_draft_id is None:
                threads = [_revision_thread(aggregate) for aggregate in records]
            else:
                threads = [
                    _revision_thread(aggregate, legacy_draft_id=legacy_draft_id)
                    for aggregate in records
                    if any(
                        revision.origin is RevisionOrigin.LEGACY_CLEANING
                        and revision.legacy_audit is not None
                        and revision.legacy_audit.draft_id == legacy_draft_id
                        for revision in aggregate.revisions
                    )
                ]
            threads.sort(
                key=lambda thread: (thread.updated_at, thread.task_id),
                reverse=True,
            )
            if after_updated_at is not None and after_task_id is not None:
                threads = [
                    thread
                    for thread in threads
                    if (thread.updated_at, thread.task_id) < (after_updated_at, after_task_id)
                ]
            return tuple(threads[:limit])

    def append_revision_thread_list_audit(
        self,
        *,
        project_id: str,
        region_code: str,
        actor_id: str,
        request_id: str,
        status: AnnotationStatus | None,
        origin: RevisionOrigin | None,
        legacy_draft_id: str | None,
        limit: int,
    ) -> None:
        with self._lock:
            self.revision_thread_list_audits.append(
                {
                    "project_id": project_id,
                    "region_code": region_code,
                    "actor_id": actor_id,
                    "request_id": request_id,
                    "action": "annotation.revision_thread.listed",
                    "status": None if status is None else status.value,
                    "origin": None if origin is None else origin.value,
                    "legacy_draft_filter": legacy_draft_id is not None,
                    "limit": limit,
                }
            )

    def compare_and_swap(
        self,
        aggregate: AnnotationAggregate,
        *,
        expected_state_version: int,
    ) -> bool:
        validate_aggregate(aggregate)
        task_id = aggregate.task.task_id
        with self._lock:
            existing = self._aggregates.get(task_id)
            if existing is None or existing.task.state_version != expected_state_version:
                return False
            self._validate_append_only(existing, aggregate)
            if aggregate.task.state_version != expected_state_version + 1:
                raise AnnotationRepositoryInvariantError(
                    "a successful transition must increment state_version exactly once"
                )
            self._aggregates[task_id] = aggregate
            return True

    def create_tag_schema_version(self, schema: TagSchemaVersion) -> TagSchemaVersion:
        key = (schema.schema_id, schema.version)
        with self._lock:
            existing = self._schemas.get(key)
            if existing is not None:
                return existing
            versions = [
                item.version
                for item in self._schemas.values()
                if item.schema_id == schema.schema_id
            ]
            if versions and schema.version != max(versions) + 1:
                raise AnnotationRepositoryInvariantError("Tag Schema versions must be contiguous")
            if not versions and schema.version != 1:
                raise AnnotationRepositoryInvariantError("the first Tag Schema version must be 1")
            self._schemas[key] = schema
            return schema

    def get_tag_schema_version(self, *, schema_id: str, version: int) -> TagSchemaVersion | None:
        with self._lock:
            return self._schemas.get((schema_id, version))

    def list_tag_schema_versions(
        self, *, project_id: str, schema_id: str
    ) -> tuple[TagSchemaVersion, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        item
                        for item in self._schemas.values()
                        if item.project_id == project_id and item.schema_id == schema_id
                    ),
                    key=lambda item: item.version,
                )
            )

    def publish_tag_schema_version(
        self, draft: TagSchemaVersion, published: TagSchemaVersion
    ) -> bool:
        key = (draft.schema_id, draft.version)
        with self._lock:
            current = self._schemas.get(key)
            if current != draft or current.status is not TagSchemaStatus.DRAFT:
                return False
            if (
                published.schema_id != draft.schema_id
                or published.version != draft.version
                or published.document != draft.document
                or published.content_hash != draft.content_hash
                or published.compatible_targets != draft.compatible_targets
            ):
                raise AnnotationRepositoryInvariantError(
                    "publishing cannot mutate Tag Schema content"
                )
            for target in published.compatible_targets:
                binding = (
                    published.project_id,
                    target.region_code,
                    target.dataset_id,
                    target.dataset_schema_snapshot_id,
                    target.task_kind.value,
                )
                owner = self._schema_bindings.get(binding)
                if owner is not None and owner != key:
                    raise AnnotationRepositoryInvariantError(
                        "a Tag Schema target is already bound to another published version"
                    )
            self._schemas[key] = published
            for target in published.compatible_targets:
                self._schema_bindings[
                    (
                        published.project_id,
                        target.region_code,
                        target.dataset_id,
                        target.dataset_schema_snapshot_id,
                        target.task_kind.value,
                    )
                ] = key
            return True

    def resolve_published_schema(
        self,
        *,
        project_id: str,
        region_code: str,
        dataset_id: str,
        dataset_schema_snapshot_id: str,
        task_kind: AnnotationTaskKind,
    ) -> TagSchemaVersion | None:
        with self._lock:
            key = self._schema_bindings.get(
                (
                    project_id,
                    region_code,
                    dataset_id,
                    dataset_schema_snapshot_id,
                    task_kind.value,
                )
            )
            return None if key is None else self._schemas[key]

    @staticmethod
    def _validate_append_only(
        existing: AnnotationAggregate, replacement: AnnotationAggregate
    ) -> None:
        old_task = existing.task
        new_task = replacement.task
        old_identity = (
            old_task.task_id,
            old_task.project_id,
            old_task.region_code,
            old_task.dataset_id,
            old_task.dataset_version,
            old_task.base_lance_version,
            old_task.base_step_count,
            old_task.tag_schema_id,
            old_task.tag_schema_version,
            old_task.rollout_id,
            old_task.task_kind,
            old_task.creation_source,
            old_task.source_workflow_id,
            old_task.created_at,
        )
        new_identity = (
            new_task.task_id,
            new_task.project_id,
            new_task.region_code,
            new_task.dataset_id,
            new_task.dataset_version,
            new_task.base_lance_version,
            new_task.base_step_count,
            new_task.tag_schema_id,
            new_task.tag_schema_version,
            new_task.rollout_id,
            new_task.task_kind,
            new_task.creation_source,
            new_task.source_workflow_id,
            new_task.created_at,
        )
        if old_identity != new_identity:
            raise AnnotationRepositoryInvariantError("task target identity is immutable")
        if replacement.revisions[: len(existing.revisions)] != existing.revisions:
            raise AnnotationRepositoryInvariantError("revisions are append-only")
        if replacement.reviews[: len(existing.reviews)] != existing.reviews:
            raise AnnotationRepositoryInvariantError("reviews are append-only")
        if replacement.mutations[: len(existing.mutations)] != existing.mutations:
            raise AnnotationRepositoryInvariantError("mutation records are append-only")
        if replacement.submissions[: len(existing.submissions)] != existing.submissions:
            raise AnnotationRepositoryInvariantError("submissions are append-only")
        if (
            replacement.submission_mutations[: len(existing.submission_mutations)]
            != existing.submission_mutations
        ):
            raise AnnotationRepositoryInvariantError("submission mutations are append-only")
        if len(replacement.revisions) > len(existing.revisions) + 1:
            raise AnnotationRepositoryInvariantError("one transaction may append one revision")
        if len(replacement.reviews) > len(existing.reviews) + 1:
            raise AnnotationRepositoryInvariantError("one transaction may append one review")
        if len(replacement.mutations) > len(existing.mutations) + 1:
            raise AnnotationRepositoryInvariantError("one transaction may append one mutation")
        if len(replacement.submissions) > len(existing.submissions) + 1:
            raise AnnotationRepositoryInvariantError("one transaction may append one submission")
        if len(replacement.submission_mutations) > len(existing.submission_mutations) + 1:
            raise AnnotationRepositoryInvariantError(
                "one transaction may append one submission mutation"
            )


class FakeAnnotationRepository(InMemoryAnnotationRepository):
    """Explicit test-fake spelling used by persistence contract tests."""


def _revision_thread(
    aggregate: AnnotationAggregate,
    *,
    legacy_draft_id: str | None = None,
) -> AnnotationRevisionThread:
    task = aggregate.task
    latest = aggregate.revisions[-1]
    legacy_revision = next(
        (
            revision
            for revision in reversed(aggregate.revisions)
            if revision.origin is RevisionOrigin.LEGACY_CLEANING
            and revision.legacy_audit is not None
            and (legacy_draft_id is None or revision.legacy_audit.draft_id == legacy_draft_id)
        ),
        None,
    )
    if task.region_code is None:
        raise AnnotationRepositoryInvariantError(
            "a scoped revision thread requires a non-null task region"
        )
    return AnnotationRevisionThread(
        task_id=task.task_id,
        project_id=task.project_id,
        region_code=task.region_code,
        dataset_id=task.dataset_id,
        dataset_version=task.dataset_version,
        rollout_id=task.rollout_id,
        status=task.status,
        latest_revision=AnnotationRevisionThreadRevision(
            revision=latest.revision,
            origin=latest.origin,
            author_id=latest.author_id,
            content_hash=latest.content_hash,
            created_at=latest.created_at,
        ),
        submitted_revision=task.submitted_revision,
        current_submission_id=task.current_submission_id,
        approved_revision=task.approved_revision,
        approved_review_id=task.approved_review_id,
        legacy_draft_id=(
            None
            if legacy_revision is None or legacy_revision.legacy_audit is None
            else legacy_revision.legacy_audit.draft_id
        ),
        updated_at=task.updated_at,
    )
