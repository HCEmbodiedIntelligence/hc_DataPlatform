"""Ports exposed by annotation; consumers never read mutable drafts directly."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
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
    AnnotationRevisionThread,
    AnnotationStatus,
    AnnotationSubmission,
    AnnotationSubmissionMutationRecord,
    AnnotationTag,
    AnnotationTask,
    AnnotationTaskCreationSource,
    AnnotationTaskKind,
    AutoAnnotationCapability,
    ExclusionRange,
    ReviewDecision,
    RevisionOrigin,
    TagSchemaVersion,
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
    submissions: tuple[AnnotationSubmission, ...] = ()
    submission_mutations: tuple[AnnotationSubmissionMutationRecord, ...] = ()


@runtime_checkable
class AnnotationRepositoryPort(Protocol):
    """PostgreSQL-shaped persistence port with an atomic aggregate CAS.

    Revision, operation, submission, review, and mutation rows are append-only. Only
    the task's current pointers are updated by ``compare_and_swap``.
    """

    def create(self, aggregate: AnnotationAggregate) -> AnnotationAggregate: ...

    def get(self, task_id: str) -> AnnotationAggregate | None: ...

    def find_by_rollout(
        self, *, project_id: str, rollout_id: str
    ) -> AnnotationAggregate | None: ...

    def list_for_project(self, project_id: str) -> tuple[AnnotationAggregate, ...]: ...

    def list_revision_threads(
        self,
        *,
        project_id: str,
        region_code: str,
        status: AnnotationStatus | None,
        origin: RevisionOrigin | None,
        snapshot_at: datetime,
        after_updated_at: datetime | None,
        after_task_id: str | None,
        limit: int,
    ) -> tuple[AnnotationRevisionThread, ...]: ...

    def append_revision_thread_list_audit(
        self,
        *,
        project_id: str,
        region_code: str,
        actor_id: str,
        request_id: str,
        status: AnnotationStatus | None,
        origin: RevisionOrigin | None,
        limit: int,
    ) -> None: ...

    def compare_and_swap(
        self,
        aggregate: AnnotationAggregate,
        *,
        expected_state_version: int,
    ) -> bool: ...

    def create_tag_schema_version(self, schema: TagSchemaVersion) -> TagSchemaVersion: ...

    def get_tag_schema_version(
        self, *, schema_id: str, version: int
    ) -> TagSchemaVersion | None: ...

    def list_tag_schema_versions(
        self, *, project_id: str, schema_id: str
    ) -> tuple[TagSchemaVersion, ...]: ...

    def publish_tag_schema_version(
        self, draft: TagSchemaVersion, published: TagSchemaVersion
    ) -> bool: ...

    def resolve_published_schema(
        self,
        *,
        project_id: str,
        region_code: str,
        dataset_id: str,
        dataset_schema_snapshot_id: str,
        task_kind: AnnotationTaskKind,
    ) -> TagSchemaVersion | None: ...


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

    def get_submission(
        self, task_id: str, submission_id: str, actor: ActorContext
    ) -> AnnotationSubmission: ...

    def list_submissions(
        self, task_id: str, actor: ActorContext
    ) -> tuple[AnnotationSubmission, ...]: ...

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
        tags: Sequence[AnnotationTag] | None = None,
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

    def submit_for_review(
        self,
        task_id: str,
        actor: ActorContext,
        *,
        expected_revision: int,
        if_match: str,
        idempotency_key: str,
    ) -> AnnotationSubmission: ...

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
        region_code: str | None = None,
        task_kind: AnnotationTaskKind = AnnotationTaskKind.TAGGING,
        creation_source: AnnotationTaskCreationSource = AnnotationTaskCreationSource.SYSTEM_LANCE,
        source_workflow_id: str | None = None,
        base_lance_version: int | None = None,
        base_step_count: int | None = None,
        tag_schema_id: str = "default-flat",
        tag_schema_version: int = 1,
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
