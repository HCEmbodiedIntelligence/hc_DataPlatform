from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from hc_data_platform.core.pagination import PageInfo

DashboardIdentifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$"),
]
DashboardSourceIdentifier = Annotated[str, StringConstraints(min_length=1, max_length=512)]


class DashboardSectionStatus(str, Enum):
    READY = "READY"
    EMPTY = "EMPTY"
    PARTIAL = "PARTIAL"
    STALE = "STALE"
    ERROR = "ERROR"
    BLOCKED = "BLOCKED"


class SignalStage(str, Enum):
    COLLECTED = "COLLECTED"
    RECEIVED = "RECEIVED"
    AUTO_QC = "AUTO_QC"
    ALIGNED_30_HZ = "ALIGNED_30_HZ"
    LANCE = "LANCE"
    ANNOTATION = "ANNOTATION"
    REVIEW = "REVIEW"
    PUBLISHED = "PUBLISHED"


SIGNAL_STAGES: tuple[SignalStage, ...] = (
    SignalStage.COLLECTED,
    SignalStage.RECEIVED,
    SignalStage.AUTO_QC,
    SignalStage.ALIGNED_30_HZ,
    SignalStage.LANCE,
    SignalStage.ANNOTATION,
    SignalStage.REVIEW,
    SignalStage.PUBLISHED,
)


class DashboardActivityEventType(str, Enum):
    UPLOAD_COMMITTED = "UPLOAD_COMMITTED"
    QC_COMPLETED = "QC_COMPLETED"
    TAG_REVIEW_DECIDED = "TAG_REVIEW_DECIDED"
    DATASET_PUBLISHED = "DATASET_PUBLISHED"


class DashboardPendingItemType(str, Enum):
    UPLOAD_FAILED = "UPLOAD_FAILED"
    QC_ANOMALY = "QC_ANOMALY"
    TAG_REVIEW_PENDING = "TAG_REVIEW_PENDING"
    PUBLICATION_PENDING = "PUBLICATION_PENDING"


PENDING_ITEM_TYPES: tuple[DashboardPendingItemType, ...] = tuple(DashboardPendingItemType)


class DashboardPendingSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class DashboardResourceType(str, Enum):
    UPLOAD_SESSION = "UPLOAD_SESSION"
    ROLLOUT = "ROLLOUT"
    ANNOTATION_TASK = "ANNOTATION_TASK"
    DATASET_VERSION = "DATASET_VERSION"


class DashboardSectionError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    message: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    retryable: bool = False
    needs_product_confirmation: bool = False


class DashboardSectionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: DashboardSectionStatus
    as_of: AwareDatetime | None = None
    error: DashboardSectionError | None = None

    @model_validator(mode="after")
    def validate_state(self) -> DashboardSectionState:
        failed = {
            DashboardSectionStatus.PARTIAL,
            DashboardSectionStatus.STALE,
            DashboardSectionStatus.ERROR,
            DashboardSectionStatus.BLOCKED,
        }
        if self.status in failed and self.error is None:
            raise ValueError(f"{self.status.value} sections require a safe error")
        if self.status in {DashboardSectionStatus.READY, DashboardSectionStatus.EMPTY}:
            if self.error is not None:
                raise ValueError(f"{self.status.value} sections cannot carry an error")
            if self.as_of is None:
                raise ValueError(f"{self.status.value} sections require as_of")
        return self


class DashboardPublishedRegionState(DashboardSectionState):
    lineage_count: int | None = Field(default=None, ge=0)
    publication_count: int | None = Field(default=None, ge=0)
    unresolved_history_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> DashboardPublishedRegionState:
        if self.status is DashboardSectionStatus.BLOCKED:
            if self.lineage_count is not None or self.publication_count is not None:
                raise ValueError("blocked publication lineage cannot claim counts")
        else:
            if self.lineage_count is None or self.publication_count is None:
                raise ValueError("queryable publication lineage requires factual counts")
        if (
            self.status in {DashboardSectionStatus.READY, DashboardSectionStatus.EMPTY}
            and self.unresolved_history_count
        ):
            raise ValueError("complete publication lineage cannot have unresolved history")
        if self.status is DashboardSectionStatus.EMPTY and (
            self.lineage_count != 0 or self.publication_count != 0
        ):
            raise ValueError("empty publication lineage requires zero counts")
        return self


class DashboardSignalPipelineState(DashboardSectionState):
    stages: tuple[SignalStage, ...] = SIGNAL_STAGES
    published_region: DashboardPublishedRegionState

    @model_validator(mode="after")
    def validate_stages(self) -> DashboardSignalPipelineState:
        if self.stages != SIGNAL_STAGES:
            raise ValueError("the signal-stage catalog is fixed and ordered")
        return self


class DashboardSnapshotSections(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_pipeline: DashboardSignalPipelineState
    episodes: DashboardSectionState
    work: DashboardSectionState


class DashboardResponseBase(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["1"] = "1"
    project_id: DashboardIdentifier
    region_code: DashboardIdentifier
    range_start: AwareDatetime = Field(alias="from")
    range_end: AwareDatetime = Field(alias="to")
    timezone: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    as_of: AwareDatetime


class DashboardSnapshotResponse(DashboardResponseBase):
    sections: DashboardSnapshotSections


class DashboardPageSectionState(DashboardSectionState):
    page_info: PageInfo | None = None

    @model_validator(mode="after")
    def validate_page_info(self) -> DashboardPageSectionState:
        pageable = {
            DashboardSectionStatus.READY,
            DashboardSectionStatus.EMPTY,
            DashboardSectionStatus.PARTIAL,
            DashboardSectionStatus.STALE,
        }
        if self.status in pageable and self.page_info is None:
            raise ValueError(f"{self.status.value} page sections require page_info")
        if self.status not in pageable and self.page_info is not None:
            raise ValueError(f"{self.status.value} page sections cannot claim a page")
        return self


class DashboardTargetResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_type: DashboardResourceType
    resource_id: DashboardSourceIdentifier
    resource_version: DashboardSourceIdentifier | None = None
    deep_link: Annotated[str, StringConstraints(min_length=1, max_length=1024)] | None = None

    @field_validator("deep_link")
    @classmethod
    def allowlisted_deep_link(cls, value: str | None) -> str | None:
        if value is None:
            return None
        allowed = ("/ingest/uploads/", "/annotations/tasks/", "/datasets/")
        if not value.startswith(allowed) or ".." in value or "://" in value:
            raise ValueError("dashboard deep link is outside the allowlist")
        return value


class DashboardActivityEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: DashboardSourceIdentifier
    event_type: DashboardActivityEventType
    source_id: DashboardSourceIdentifier
    deduplication_key: DashboardSourceIdentifier
    occurred_at: AwareDatetime
    source_state: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    title: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    summary: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    target: DashboardTargetResource


class DashboardActivityPage(DashboardPageSectionState):
    items: tuple[DashboardActivityEvent, ...] = ()

    @model_validator(mode="after")
    def validate_items(self) -> DashboardActivityPage:
        if (
            self.status in {DashboardSectionStatus.ERROR, DashboardSectionStatus.BLOCKED}
            and self.items
        ):
            raise ValueError("unavailable activity pages cannot carry items")
        return self


class DashboardActivityResponse(DashboardResponseBase):
    activity: DashboardActivityPage


class DashboardCoverageResponse(DashboardResponseBase):
    coverage: DashboardSectionState


class DashboardPendingItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_type: DashboardPendingItemType
    source_id: DashboardSourceIdentifier
    deduplication_key: DashboardSourceIdentifier
    source_state: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    severity: DashboardPendingSeverity
    opened_at: AwareDatetime
    target: DashboardTargetResource

    @model_validator(mode="after")
    def require_allowlisted_action(self) -> DashboardPendingItem:
        if self.target.deep_link is None:
            raise ValueError("pending items require an allowlisted deep link")
        return self


class DashboardPendingItemsPage(DashboardPageSectionState):
    authorized_source_types: tuple[DashboardPendingItemType, ...] = ()
    items: tuple[DashboardPendingItem, ...] = ()

    @model_validator(mode="after")
    def validate_items(self) -> DashboardPendingItemsPage:
        if (
            self.status in {DashboardSectionStatus.ERROR, DashboardSectionStatus.BLOCKED}
            and self.items
        ):
            raise ValueError("unavailable pending pages cannot carry items")
        if any(item.item_type not in self.authorized_source_types for item in self.items):
            raise ValueError("pending items must be intersected with principal capability")
        return self


class DashboardPendingItemsResponse(DashboardResponseBase):
    pending_items: DashboardPendingItemsPage
