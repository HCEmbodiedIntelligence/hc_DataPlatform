from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, cast

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Identifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$"),
]
TaskCode = Annotated[str, StringConstraints(pattern=r"^[0-9]{8}$")]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _omitted_text() -> str:
    """PATCH omission sentinel that remains non-null in the public JSON Schema."""

    return cast(str, None)


class CollectionTaskStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class QcOutcome(str, Enum):
    PASS = "PASS"
    RISK = "RISK"
    REJECT = "REJECT"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CollectionTarget(StrictModel):
    """Unconfirmed target dimensions remain independently optional.

    OPEN-05 deliberately has no at-least-one validator and does not imply automatic
    completion.  Either dimension can be omitted without the service inventing policy.
    """

    package_count: int | None = None
    duration_seconds: float | None = Field(default=None, allow_inf_nan=False)


class CreateCollectionTask(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    type: str = Field(min_length=1, max_length=100)
    scenario: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10_000)
    target: CollectionTarget | None = None
    quality_threshold: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class UpdateCollectionTask(StrictModel):
    # A non-null annotation with a None omission default makes PATCH fields optional
    # while keeping explicit JSON null invalid in both validation and OpenAPI.
    name: str = Field(default_factory=_omitted_text, min_length=1, max_length=200)
    type: str = Field(default_factory=_omitted_text, min_length=1, max_length=100)
    scenario: str = Field(default_factory=_omitted_text, min_length=1, max_length=200)
    description: str = Field(default_factory=_omitted_text, max_length=10_000)
    target: CollectionTarget | None = None
    quality_threshold: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class CollectionTask(StrictModel):
    schema_version: str = Field(default="1", pattern=r"^1$")
    collection_task_id: Identifier
    project_id: Identifier
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
    project_id: Identifier
    status: CollectionTaskStatus
    as_of: datetime
    received_package_count: int = Field(ge=0)
    qc: CollectionTaskQcProgress
    observed_sources: ManifestObservedSources


class CollectionTaskRecord(StrictModel):
    collection_task_id: Identifier
    project_id: Identifier
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
            project_id=self.project_id,
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
    device_ids: tuple[str, ...] = ()
    camera_ids: tuple[str, ...] = ()
    topic_names: tuple[str, ...] = ()

    @property
    def evaluated_count(self) -> int:
        return self.pass_count + self.risk_count + self.reject_count

    @property
    def pending_count(self) -> int:
        return self.received_package_count - self.evaluated_count
