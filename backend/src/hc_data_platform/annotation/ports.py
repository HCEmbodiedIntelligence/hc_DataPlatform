"""Ports exposed by annotation; consumers never read mutable drafts directly."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TypeAlias, runtime_checkable

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
    ReviewDecision,
)

if TYPE_CHECKING:
    from hc_data_platform.security import AuthContext

    ActorContext: TypeAlias = AnnotationActor | AuthContext
else:
    ActorContext = object


@dataclass(frozen=True)
class AnnotationAggregate:
    """Complete persisted state for one annotation task."""

    task: AnnotationTask
    revisions: tuple[AnnotationRevision, ...]
    reviews: tuple[AnnotationReview, ...] = ()
    mutations: tuple[AnnotationMutationRecord, ...] = ()


@runtime_checkable
class AnnotationRepositoryPort(Protocol):
    """PostgreSQL-shaped persistence port with an atomic aggregate CAS.

    Revision, operation, review, and mutation rows are append-only. Only the task's
    current pointers are updated by ``compare_and_swap``.
    """

    def create(self, aggregate: AnnotationAggregate) -> AnnotationAggregate: ...

    def get(self, task_id: str) -> AnnotationAggregate | None: ...

    def find_by_rollout(
        self, *, project_id: str, rollout_id: str
    ) -> AnnotationAggregate | None: ...

    def list_for_project(self, project_id: str) -> tuple[AnnotationAggregate, ...]: ...

    def compare_and_swap(
        self,
        aggregate: AnnotationAggregate,
        *,
        expected_state_version: int,
    ) -> bool: ...


@runtime_checkable
class AnnotationReadPort(Protocol):
    def read_task(self, task_id: str, actor: ActorContext) -> AnnotationTask: ...

    def get_task(self, task_id: str, actor: ActorContext) -> AnnotationTask: ...

    def get_current(self, task_id: str, actor: ActorContext) -> AnnotationCurrent: ...

    def get_draft(self, task_id: str, actor: ActorContext) -> AnnotationDraft: ...

    def get_revision(
        self, task_id: str, revision: int, actor: ActorContext
    ) -> AnnotationRevision: ...

    def list_revisions(
        self, task_id: str, actor: ActorContext
    ) -> tuple[AnnotationRevision, ...]: ...

    def list_reviews(self, task_id: str, actor: ActorContext) -> tuple[AnnotationReview, ...]: ...

    def get_history(self, task_id: str, actor: ActorContext) -> AnnotationHistory: ...

    def approved_snapshot(
        self, *, project_id: str, rollout_id: str, actor: ActorContext
    ) -> AnnotationApprovedV1: ...


@runtime_checkable
class EffectiveExclusionPort(Protocol):
    """Consumer port matching preview's project-scoped immutable-revision lookup."""

    def effective_ranges(
        self, *, project_id: str, rollout_id: str, annotation_revision: int
    ) -> Sequence[ExclusionRange]: ...

    def effective_exclusions(
        self,
        task_id: str,
        *,
        revision: int | None = None,
        actor: ActorContext,
    ) -> tuple[ExclusionRange, ...]: ...


@runtime_checkable
class AnnotationWritePort(AnnotationReadPort, EffectiveExclusionPort, Protocol):
    def claim(self, task_id: str, actor: ActorContext) -> AnnotationTask: ...

    def save_draft(
        self,
        task_id: str,
        actor: ActorContext,
        operations: Sequence[AnnotationOperation],
        *,
        expected_revision: int,
        if_match: str,
        client_mutation_id: str,
    ) -> AnnotationRevision: ...

    def submit(
        self,
        task_id: str,
        actor: ActorContext,
        *,
        expected_revision: int,
        if_match: str,
    ) -> AnnotationTask: ...

    def review(
        self,
        task_id: str,
        actor: ActorContext,
        decision: ReviewDecision,
        *,
        revision: int,
        if_match: str,
        comment: str = "",
    ) -> AnnotationApprovedV1 | None: ...


@runtime_checkable
class AnnotationTaskProvisioningPort(Protocol):
    """Pipeline-facing task creation boundary; it never writes Raw or Lance data."""

    def create_task(
        self,
        *,
        task_id: str,
        project_id: str,
        dataset_id: str,
        dataset_version: int,
        rollout_id: str,
    ) -> AnnotationTask: ...


@runtime_checkable
class AnnotationQueuePort(Protocol):
    def list_tasks(
        self,
        *,
        project_id: str,
        actor: ActorContext,
        status: AnnotationStatus | None = None,
    ) -> tuple[AnnotationTask, ...]: ...


@runtime_checkable
class AutoAnnotationProvider(Protocol):
    @property
    def enabled(self) -> bool: ...

    def capability(self) -> AutoAnnotationCapability: ...

    def request(self, *, task_id: str, revision: int) -> str: ...
