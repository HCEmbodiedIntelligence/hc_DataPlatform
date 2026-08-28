from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, cast

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$"),
]
TaskCode = Annotated[str, StringConstraints(pattern=r"^[0-9]{8}$")]
DatasetIdentifier = Annotated[
    str,
    StringConstraints(
        min_length=10,
        max_length=104,
        pattern=r"^dataset_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$",
    ),
]


def dataset_id_for_collection_task(
    organization_id: str,
    project_id: str,
    collection_task_id: str,
) -> str:
    """Return the region-independent Dataset identity owned by one collection task."""

    canonical_identity = "\x1f".join((organization_id, project_id, collection_task_id))
    digest = hashlib.sha256(canonical_identity.encode("utf-8")).hexdigest()[:32]
    return f"dataset_task_{digest}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _omitted_text() -> str:
    """PATCH omission sentinel that remains non-null in the public JSON Schema."""

    return cast(str, None)


class CollectionTaskStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class QcOutcome(str, Enum):
    PASS = "PASS"
    RISK = "RISK"
    REJECT = "REJECT"


class CollectionTaskPackageState(str, Enum):
    """One received package's current path toward a browsable Episode."""

    PENDING_QC = "PENDING_QC"
    PROCESSING = "PROCESSING"
    PUBLISHED = "PUBLISHED"
    QUALITY_RISK = "QUALITY_RISK"
    QUALITY_REJECTED = "QUALITY_REJECTED"
    TECHNICAL_FAILED = "TECHNICAL_FAILED"
    DATASET_MISMATCH = "DATASET_MISMATCH"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CollectionTarget(StrictModel):
    """Optional, explicit task target with one or two independently measured dimensions.

    A task may intentionally have no target (for exploratory collection), but a supplied
    target must contain at least one strictly-positive goal.  Attainment is a read-only
    fact and never changes the task state by itself.
    """

    package_count: int | None = Field(default=None, gt=0)
    duration_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def has_a_goal(self) -> CollectionTarget:
        if self.package_count is None and self.duration_seconds is None:
            raise ValueError("a collection target must define package_count or duration_seconds")
        return self


class CreateCollectionTask(StrictModel):
    # Optional when omitted, but explicit JSON null is invalid.
    dataset_id: DatasetIdentifier = Field(default_factory=_omitted_text)
    name: str = Field(min_length=1, max_length=200)
    type: str = Field(min_length=1, max_length=100)
    scenario: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10_000)
    target: CollectionTarget | None = None
    quality_threshold: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class UpdateCollectionTask(StrictModel):
    # A non-null annotation with a None omission default makes PATCH fields optional
    # while keeping explicit JSON null invalid in both validation and OpenAPI.
    dataset_id: DatasetIdentifier = Field(default_factory=_omitted_text)
    name: str = Field(default_factory=_omitted_text, min_length=1, max_length=200)
    type: str = Field(default_factory=_omitted_text, min_length=1, max_length=100)
    scenario: str = Field(default_factory=_omitted_text, min_length=1, max_length=200)
    description: str = Field(default_factory=_omitted_text, max_length=10_000)
    target: CollectionTarget | None = None
    quality_threshold: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class CollectionTask(StrictModel):
    schema_version: str = Field(default="1", pattern=r"^1$")
    collection_task_id: Identifier
    organization_id: Identifier
    project_id: Identifier
    dataset_id: DatasetIdentifier
    task_code: TaskCode
    name: str
    type: str
    scenario: str
    description: str
    target: CollectionTarget | None
    quality_threshold: float | None = Field(ge=0, le=1, allow_inf_nan=False)
    status: CollectionTaskStatus


class CollectionTaskPage(StrictModel):
    items: tuple[CollectionTask, ...]
    next_cursor: str | None = None


class CollectionTaskRatio(StrictModel):
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    value: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class CollectionTaskTargetMetricStatus(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    MET = "MET"
    EXCEEDED = "EXCEEDED"
    UNKNOWN = "UNKNOWN"


class CollectionTaskAttainmentStatus(str, Enum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    IN_PROGRESS = "IN_PROGRESS"
    ATTAINED = "ATTAINED"
    EXCEEDED = "EXCEEDED"


class CollectionTaskQualityRequirementStatus(str, Enum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    PENDING_QC = "PENDING_QC"
    NOT_MET = "NOT_MET"
    MET = "MET"


class CollectionTaskTargetMetric(StrictModel):
    """One configured target with a fact-derived, non-clamped progress ratio."""

    actual: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    target: float = Field(gt=0, allow_inf_nan=False)
    progress: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    status: CollectionTaskTargetMetricStatus


class CollectionTaskAttainment(StrictModel):
    """Explicit attainment rules; never an automatic task-state transition."""

    status: CollectionTaskAttainmentStatus
    package_count: CollectionTaskTargetMetric | None = None
    duration_seconds: CollectionTaskTargetMetric | None = None
    quality_threshold: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    quality_status: CollectionTaskQualityRequirementStatus


class CollectionTaskQcProgress(StrictModel):
    evaluated_count: int = Field(ge=0)
    pass_count: int = Field(ge=0)
    risk_count: int = Field(ge=0)
    reject_count: int = Field(ge=0)
    pending_count: int = Field(ge=0)
    pass_rate: CollectionTaskRatio


class ManifestObservedSources(StrictModel):
    """Read-only identities discovered from committed package Manifests."""

    device_ids: tuple[str, ...] = ()
    camera_ids: tuple[str, ...] = ()
    topic_names: tuple[str, ...] = ()


class CollectionTaskProgress(StrictModel):
    schema_version: str = Field(default="1", pattern=r"^1$")
    collection_task_id: Identifier
    organization_id: Identifier
    project_id: Identifier
    status: CollectionTaskStatus
    as_of: datetime
    received_package_count: int = Field(ge=0)
    captured_duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    duration_observed_package_count: int = Field(ge=0)
    duration_unknown_package_count: int = Field(ge=0)
    qc: CollectionTaskQcProgress
    attainment: CollectionTaskAttainment
    observed_sources: ManifestObservedSources


class CollectionTaskPackage(StrictModel):
    """Read-only lineage from a committed package to its published Episode, if any."""

    schema_version: str = Field(default="1", pattern=r"^1$")
    data_package_id: str = Field(min_length=1, max_length=256)
    rollout_id: str = Field(min_length=1, max_length=256)
    robot_id: str = Field(min_length=1, max_length=256)
    state: CollectionTaskPackageState
    qc_outcome: QcOutcome | None = None
    workflow_status: str | None = Field(default=None, max_length=128)
    workflow_stage: str | None = Field(default=None, max_length=128)
    error_code: str | None = Field(default=None, max_length=256)
    dataset_id: DatasetIdentifier | None = None
    version_id: str | None = Field(default=None, max_length=128)
    episode_id: str | None = Field(default=None, max_length=128)
    revision_id: str | None = Field(default=None, max_length=128)
    visualizable: bool
    received_at: datetime
    updated_at: datetime


class CollectionTaskPackageList(StrictModel):
    schema_version: str = Field(default="1", pattern=r"^1$")
    collection_task_id: Identifier
    organization_id: Identifier
    project_id: Identifier
    as_of: datetime
    items: tuple[CollectionTaskPackage, ...]


class CollectionTaskRecord(StrictModel):
    collection_task_id: Identifier
    organization_id: Identifier
    project_id: Identifier
    dataset_id: DatasetIdentifier | None = None
    created_by: Identifier | None = None
    task_code: TaskCode | None = None
    name: str
    type: str
    scenario: str
    description: str
    target: CollectionTarget | None
    quality_threshold: float | None
    status: CollectionTaskStatus = CollectionTaskStatus.ACTIVE
    version: int = Field(default=1, ge=1)
    create_fingerprint: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def public(self) -> CollectionTask:
        if self.task_code is None:
            raise RuntimeError("a persisted collection task must have a task code")
        return CollectionTask(
            collection_task_id=self.collection_task_id,
            organization_id=self.organization_id,
            project_id=self.project_id,
            dataset_id=self.dataset_id
            or dataset_id_for_collection_task(
                self.organization_id,
                self.project_id,
                self.collection_task_id,
            ),
            task_code=self.task_code,
            name=self.name,
            type=self.type,
            scenario=self.scenario,
            description=self.description,
            target=self.target,
            quality_threshold=self.quality_threshold,
            status=self.status,
        )


class ProgressFacts(StrictModel):
    as_of: datetime
    received_package_count: int = Field(ge=0)
    pass_count: int = Field(ge=0)
    risk_count: int = Field(ge=0)
    reject_count: int = Field(ge=0)
    captured_duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    duration_observed_package_count: int = Field(default=0, ge=0)
    duration_unknown_package_count: int = Field(default=0, ge=0)
    device_ids: tuple[str, ...] = ()
    camera_ids: tuple[str, ...] = ()
    topic_names: tuple[str, ...] = ()

    @property
    def evaluated_count(self) -> int:
        return self.pass_count + self.risk_count + self.reject_count

    @property
    def pending_count(self) -> int:
        return self.received_package_count - self.evaluated_count

    @model_validator(mode="after")
    def consistent_duration_facts(self) -> ProgressFacts:
        if self.duration_observed_package_count + self.duration_unknown_package_count != (
            self.received_package_count
        ):
            raise ValueError("duration fact counts must partition received packages")
        if self.duration_unknown_package_count and self.captured_duration_seconds is not None:
            raise ValueError(
                "captured duration is unknown while received package durations are missing"
            )
        if self.duration_observed_package_count == 0 and self.captured_duration_seconds is not None:
            raise ValueError("captured duration requires at least one observed package duration")
        return self
