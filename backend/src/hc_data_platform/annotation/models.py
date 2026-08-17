"""Versioned public models owned by the annotation module."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from hc_data_platform.security import AuthContext


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AnnotationStatus(str, Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    NEEDS_REVISION = "NEEDS_REVISION"
    REJECTED = "REJECTED"


class OperationKind(str, Enum):
    EXCLUDE = "EXCLUDE"
    RESTORE = "RESTORE"


class ReviewDecision(str, Enum):
    APPROVE = "APPROVE"
    NEEDS_REVISION = "NEEDS_REVISION"
    REJECT = "REJECT"


class AnnotationActor(BaseModel):
    """Compatibility identity accepted by the domain service and unit-test fake.

    HTTP adapters use BE-02's :class:`AuthContext` and convert it at the module
    boundary. Keeping this small value object makes the persistence fake useful
    without requiring a JWT in domain tests.
    """

    model_config = ConfigDict(frozen=True)

    actor_id: str = Field(min_length=1)
    roles: frozenset[str]
    project_ids: frozenset[str]

    @classmethod
    def from_auth(cls, auth: AuthContext) -> AnnotationActor:
        return cls(
            actor_id=auth.subject_id,
            roles=auth.roles,
            project_ids=auth.project_ids,
        )


class ExclusionRange(BaseModel):
    """Normalized half-open range that applies to every synchronized modality."""

    model_config = ConfigDict(frozen=True)

    start_step: int = Field(ge=0)
    end_step: int = Field(gt=0)
    modality_scope: Literal["ALL_MODALITIES"] = "ALL_MODALITIES"

    @model_validator(mode="after")
    def validate_non_empty(self) -> ExclusionRange:
        if self.start_step >= self.end_step:
            raise ValueError("end_step must be greater than start_step")
        return self


class AnnotationOperation(BaseModel):
    """An append-only operation over the half-open interval ``[start, end)``."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1"] = "1"
    operation_id: str = Field(min_length=1)
    kind: OperationKind
    start_step: int = Field(ge=0)
    end_step: int = Field(gt=0)
    reason: str = Field(default="", max_length=2000)
    modality_scope: Literal["ALL_MODALITIES"] = "ALL_MODALITIES"

    @model_validator(mode="after")
    def validate_non_empty(self) -> AnnotationOperation:
        if self.start_step >= self.end_step:
            raise ValueError("end_step must be greater than start_step")
        return self


class AnnotationRevision(BaseModel):
    """An immutable revision containing the operations added by one draft save."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    schema_version: Literal["1"] = "1"
    task_id: str = Field(min_length=1)
    revision: int = Field(ge=0)
    parent_revision: int | None
    author_id: str = Field(min_length=1)
    client_mutation_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("client_mutation_id", "mutation_id"),
    )
    operations: tuple[AnnotationOperation, ...]
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def mutation_id(self) -> str:
        """Backward-compatible spelling; serialized contracts use client_mutation_id."""

        return self.client_mutation_id

    @model_validator(mode="after")
    def validate_parent(self) -> AnnotationRevision:
        expected_parent = None if self.revision == 0 else self.revision - 1
        if self.parent_revision != expected_parent:
            raise ValueError("parent_revision must identify the immediately preceding revision")
        return self


class AnnotationReview(BaseModel):
    """An immutable reviewer decision bound to one exact revision."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1"] = "1"
    review_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    revision: int = Field(ge=0)
    reviewer_id: str = Field(min_length=1)
    decision: ReviewDecision
    comment: str = Field(default="", max_length=10000)
    created_at: datetime = Field(default_factory=utc_now)


class AnnotationTask(BaseModel):
    """Task identity plus its current workflow pointers.

    ``state_version`` changes for claim, save, submit, and review. ``current_revision``
    changes only when an immutable draft revision is appended.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1"] = "1"
    task_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_version: int = Field(ge=1)
    rollout_id: str = Field(min_length=1)
    assignee_id: str | None = None
    current_revision: int = Field(ge=0)
    state_version: int = Field(default=0, ge=0)
    status: AnnotationStatus
    submitted_revision: int | None = Field(default=None, ge=0)
    submitted_by: str | None = None
    approved_revision: int | None = Field(default=None, ge=0)
    approved_review_id: str | None = None
    etag: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_state_pointers(self) -> AnnotationTask:
        if self.submitted_revision is not None and self.submitted_revision > self.current_revision:
            raise ValueError("submitted_revision cannot be newer than current_revision")
        if self.approved_revision is not None and self.approved_revision > self.current_revision:
            raise ValueError("approved_revision cannot be newer than current_revision")
        if (self.approved_revision is None) != (self.approved_review_id is None):
            raise ValueError("approved revision and review pointers must be set together")
        if self.status is AnnotationStatus.APPROVED and self.approved_revision is None:
            raise ValueError("APPROVED state must identify an exact approved revision")
        if self.status is not AnnotationStatus.APPROVED and self.approved_revision is not None:
            raise ValueError("only APPROVED state may expose an approved revision")
        return self


class AnnotationDraft(BaseModel):
    """Current editable read model backed by an immutable revision."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1"] = "1"
    task_id: str
    revision: int = Field(ge=0)
    author_id: str
    client_mutation_id: str
    operations: tuple[AnnotationOperation, ...]
    effective_exclusions: tuple[ExclusionRange, ...]
    etag: str
    updated_at: datetime


class AnnotationCurrent(BaseModel):
    """Stable projection of the mutable pointers separated from task identity."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1"] = "1"
    task_id: str
    current_revision: int = Field(ge=0)
    state_version: int = Field(ge=0)
    status: AnnotationStatus
    submitted_revision: int | None = Field(default=None, ge=0)
    submitted_by: str | None = None
    approved_revision: int | None = Field(default=None, ge=0)
    approved_review_id: str | None = None
    etag: str
    updated_at: datetime


class AnnotationApprovedV1(BaseModel):
    """Immutable publishing snapshot of one approved revision."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1"] = "1"
    project_id: str
    dataset_id: str
    dataset_version: int = Field(ge=1)
    rollout_id: str
    task_id: str
    annotation_revision: int = Field(ge=0)
    review_id: str
    reviewer_id: str
    excluded_ranges: tuple[ExclusionRange, ...]
    approved_at: datetime


class AnnotationHistory(BaseModel):
    """Append-only history plus the current task projection."""

    model_config = ConfigDict(frozen=True)

    task: AnnotationTask
    revisions: tuple[AnnotationRevision, ...]
    reviews: tuple[AnnotationReview, ...]


class AnnotationMutationRecord(BaseModel):
    """Durable idempotency record for a draft-save response."""

    model_config = ConfigDict(frozen=True)

    task_id: str
    client_mutation_id: str
    actor_id: str
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_revision: int = Field(ge=0)
    request_etag: str
    result_revision: int = Field(ge=1)
    result_etag: str
    created_at: datetime = Field(default_factory=utc_now)


class AutoAnnotationCapability(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: Literal[False] = False
    code: Literal["FEATURE_DISABLED"] = "FEATURE_DISABLED"
