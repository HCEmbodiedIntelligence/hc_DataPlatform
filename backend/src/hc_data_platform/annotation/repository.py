"""Deterministic repository fake enforcing the PostgreSQL persistence invariants."""

from __future__ import annotations

from threading import RLock

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


class InMemoryAnnotationRepository:
    """Thread-safe fake for the durable annotation repository.

    The fake deliberately exposes the same compare-and-swap boundary required from
    PostgreSQL so concurrency tests do not get weaker semantics in memory.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._aggregates: dict[str, AnnotationAggregate] = {}
        self._rollouts: dict[tuple[str, str], str] = {}

    def create(self, aggregate: AnnotationAggregate) -> AnnotationAggregate:
        validate_aggregate(aggregate)
        task = aggregate.task
        rollout_key = (task.project_id, task.rollout_id)
        with self._lock:
            existing = self._aggregates.get(task.task_id)
            if existing is not None:
                return existing
            other_task_id = self._rollouts.get(rollout_key)
            if other_task_id is not None:
                return self._aggregates[other_task_id]
            self._aggregates[task.task_id] = aggregate
            self._rollouts[rollout_key] = task.task_id
            return aggregate

    def get(self, task_id: str) -> AnnotationAggregate | None:
        with self._lock:
            return self._aggregates.get(task_id)

    def find_by_rollout(self, *, project_id: str, rollout_id: str) -> AnnotationAggregate | None:
        with self._lock:
            task_id = self._rollouts.get((project_id, rollout_id))
            return None if task_id is None else self._aggregates[task_id]

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

    @staticmethod
    def _validate_append_only(
        existing: AnnotationAggregate, replacement: AnnotationAggregate
    ) -> None:
        old_task = existing.task
        new_task = replacement.task
        old_identity = (
            old_task.task_id,
            old_task.project_id,
            old_task.dataset_id,
            old_task.dataset_version,
            old_task.rollout_id,
            old_task.created_at,
        )
        new_identity = (
            new_task.task_id,
            new_task.project_id,
            new_task.dataset_id,
            new_task.dataset_version,
            new_task.rollout_id,
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
        if len(replacement.revisions) > len(existing.revisions) + 1:
            raise AnnotationRepositoryInvariantError("one transaction may append one revision")
        if len(replacement.reviews) > len(existing.reviews) + 1:
            raise AnnotationRepositoryInvariantError("one transaction may append one review")
        if len(replacement.mutations) > len(existing.mutations) + 1:
            raise AnnotationRepositoryInvariantError("one transaction may append one mutation")


class FakeAnnotationRepository(InMemoryAnnotationRepository):
    """Explicit test-fake spelling used by persistence contract tests."""
