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


class TaskLifecycle(str, Enum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class TaskAttainment(str, Enum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    IN_PROGRESS = "IN_PROGRESS"
    ATTAINED = "ATTAINED"
    EXCEEDED = "EXCEEDED"
    UNKNOWN = "UNKNOWN"


class TaskProcessingStage(str, Enum):
    TASK_EXECUTION = "TASK_EXECUTION"
    PACKAGE_UPLOAD = "PACKAGE_UPLOAD"
    RAW_RECEIPT = "RAW_RECEIPT"
    AUTOMATIC_VALIDATION = "AUTOMATIC_VALIDATION"
    STANDARDIZATION = "STANDARDIZATION"
    ANNOTATION = "ANNOTATION"
    REVIEW = "REVIEW"
    PUBLICATION = "PUBLICATION"


TASK_PROCESSING_STAGES: tuple[TaskProcessingStage, ...] = tuple(TaskProcessingStage)


class TaskPackageMainState(str, Enum):
    DISCARDED = "DISCARDED"
    DUPLICATE = "DUPLICATE"
    REPROCESSING_CONFLICT = "REPROCESSING_CONFLICT"
    PROCESSING_RESUME_REQUIRED = "PROCESSING_RESUME_REQUIRED"
    REGISTERED = "REGISTERED"
    UPLOADING = "UPLOADING"
    UPLOAD_PAUSED = "UPLOAD_PAUSED"
    UPLOAD_FAILED = "UPLOAD_FAILED"
    CANCELLED = "CANCELLED"
    RAW_RECEIVED = "RAW_RECEIVED"
    VALIDATION_RUNNING = "VALIDATION_RUNNING"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    QC_WAITING = "QC_WAITING"
    QC_RISK = "QC_RISK"
    QC_REJECT = "QC_REJECT"
    STANDARDIZATION_WAITING = "STANDARDIZATION_WAITING"
    ALIGNING = "ALIGNING"
    ALIGNMENT_FAILED = "ALIGNMENT_FAILED"
    LANCE_WRITING = "LANCE_WRITING"
    LANCE_FAILED = "LANCE_FAILED"
    STANDARDIZED_READY = "STANDARDIZED_READY"
    ANNOTATING = "ANNOTATING"
    REVIEW_PENDING = "REVIEW_PENDING"
    PUBLISH_PENDING = "PUBLISH_PENDING"
    PUBLISHED = "PUBLISHED"
    UNKNOWN = "UNKNOWN"


class TaskQcState(str, Enum):
    WAITING = "WAITING"
    PASS = "PASS"
    RISK = "RISK"
    REJECT = "REJECT"
    UNAVAILABLE = "UNAVAILABLE"


class TaskTechnicalState(str, Enum):
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    WAITING = "WAITING"
    ALIGNING = "ALIGNING"
    ALIGNMENT_FAILED = "ALIGNMENT_FAILED"
    LANCE_WRITING = "LANCE_WRITING"
    LANCE_FAILED = "LANCE_FAILED"
    READY = "READY"
    UNAVAILABLE = "UNAVAILABLE"


class TaskStageCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: TaskProcessingStage
    waiting: int = Field(default=0, ge=0)
    running: int = Field(default=0, ge=0)
    succeeded: int = Field(default=0, ge=0)
    risk: int = Field(default=0, ge=0)
    isolated: int = Field(default=0, ge=0)
    blocked: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    unavailable: int = Field(default=0, ge=0)


class TaskQcCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    waiting: int = Field(ge=0)
    passed: int = Field(ge=0)
    risk: int = Field(ge=0)
    rejected: int = Field(ge=0)
    duplicate: int = Field(default=0, ge=0)
    reprocessing_conflicts: int = Field(default=0, ge=0)
    discarded: int = Field(default=0, ge=0)
    unavailable: int = Field(default=0, ge=0)


class TaskStandardizationCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    waiting: int = Field(ge=0)
    resume_required: int = Field(default=0, ge=0)
    aligning: int = Field(ge=0)
    alignment_failed: int = Field(ge=0)
    lance_writing: int = Field(ge=0)
    lance_failed: int = Field(ge=0)
    ready: int = Field(ge=0)
    isolated_by_quality: int = Field(default=0, ge=0)
    unavailable: int = Field(default=0, ge=0)


class TaskStatusTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_count: int | None = Field(default=None, gt=0)
    duration_seconds: float | None = Field(default=None, gt=0)


class TaskDeviceProgress(BaseModel):
    """Counts only authenticated device facts; cloud events never populate this object."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["DEVICE_ATTESTED_FACT"]
    captured_count: int = Field(ge=0)
    saved_count: int = Field(ge=0)
    confirmed_duration_seconds: float = Field(ge=0)


class TaskStatusListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: DashboardIdentifier
    task_code: Annotated[str, StringConstraints(min_length=1, max_length=16)]
    name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    lifecycle: TaskLifecycle
    target: TaskStatusTarget | None = None
    registered_count: int = Field(ge=0)
    received_count: int = Field(ge=0)
    device_progress: TaskDeviceProgress


class TaskStatusBlocker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_code: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    label: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    category: Literal["UPLOAD", "RAW_VALIDATION", "QUALITY", "TECHNICAL", "REVIEW"]
    count: int = Field(gt=0)
    retryable: bool = False
    deep_link: Annotated[str, StringConstraints(min_length=1, max_length=1024)] | None = None


class TaskStatusAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "VIEW_UPLOAD_FAILURES",
        "VIEW_RAW_DIAGNOSTICS",
        "VIEW_QC_ANOMALIES",
        "RETRY_TECHNICAL_PROCESSING",
        "ENTER_ANNOTATION",
        "VIEW_PENDING_REVIEW",
        "CLOSE_TASK",
        "VIEW_TASK",
    ]
    label: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    deep_link: Annotated[str, StringConstraints(min_length=1, max_length=1024)]


class SelectedTaskStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: TaskStatusListItem
    attainment: TaskAttainment
    current_stage: TaskProcessingStage
    current_stage_label: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    next_step: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    qc: TaskQcCounts
    standardization: TaskStandardizationCounts
    stages: tuple[TaskStageCounts, ...]
    main_state_counts: dict[TaskPackageMainState, int]
    blocker_count: int = Field(ge=0)
    blockers: tuple[TaskStatusBlocker, ...]
    actions: tuple[TaskStatusAction, ...]
    unavailable_sources: tuple[
        Annotated[str, StringConstraints(min_length=1, max_length=128)], ...
    ] = ()

    @model_validator(mode="after")
    def validate_stage_catalog(self) -> SelectedTaskStatus:
        if tuple(item.stage for item in self.stages) != TASK_PROCESSING_STAGES:
            raise ValueError("task stages must cover the fixed business catalog in order")
        if self.blocker_count != sum(item.count for item in self.blockers):
            raise ValueError("task blocker count must equal the blocker summaries")
        return self


class TaskIssueFinding(BaseModel):
    code: str
    topic: str | None = None
    severity: str
    message: str
    observed: float | str | None = None
    threshold: float | str | None = None
    start_ns: int | None = None
    end_ns: int | None = None


class TaskDataIssue(BaseModel):
    task_id: str
    rollout_id: str
    data_package_id: str
    category: Literal["DUPLICATE", "QUALITY", "TECHNICAL", "PROCESSING_CONFLICT", "RESUME_REQUIRED"]
    stage: TaskProcessingStage
    reason_code: str
    label: str
    description: str
    duplicate_of_rollout_id: str | None = None
    source_episode_index: int | None = None
    source_import_id: str | None = None
    qc_status: str | None = None
    lance_ready: bool = False
    alignment_attempt_id: str | None = None
    findings: tuple[TaskIssueFinding, ...] = ()


class TaskPipelineStatus(BaseModel):
    """Current package flow for either all tasks or one selected task."""

    model_config = ConfigDict(extra="forbid")

    task_count: int = Field(ge=0)
    package_count: int = Field(ge=0)
    qc: TaskQcCounts
    stages: tuple[TaskStageCounts, ...]
    issues: tuple[TaskDataIssue, ...] = ()
    unavailable_sources: tuple[
        Annotated[str, StringConstraints(min_length=1, max_length=128)], ...
    ] = ()

    @model_validator(mode="after")
    def validate_pipeline(self) -> TaskPipelineStatus:
        if tuple(item.stage for item in self.stages) != TASK_PROCESSING_STAGES:
            raise ValueError("task pipeline stages must cover the fixed business catalog in order")
        for stage in self.stages:
            counted = (
                stage.waiting
                + stage.running
                + stage.succeeded
                + stage.risk
                + stage.isolated
                + stage.blocked
                + stage.failed
                + stage.unavailable
            )
            if counted != self.package_count:
                raise ValueError("every task pipeline stage must classify every package once")
        return self


class DashboardTaskStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    project_id: DashboardIdentifier
    region_code: DashboardIdentifier
    as_of: AwareDatetime
    section: DashboardSectionState
    tasks: tuple[TaskStatusListItem, ...]
    pipeline: TaskPipelineStatus
    selected_task_id: DashboardIdentifier | None = None
    selected: SelectedTaskStatus | None = None

    @model_validator(mode="after")
    def validate_selection(self) -> DashboardTaskStatusResponse:
        if (self.selected_task_id is None) != (self.selected is None):
            raise ValueError("selected task id and detail must be present together")
        if self.selected is not None and self.selected.task.task_id != self.selected_task_id:
            raise ValueError("selected task detail does not match selected task id")
        expected_task_count = 1 if self.selected_task_id is not None else len(self.tasks)
        if self.pipeline.task_count != expected_task_count:
            raise ValueError("task pipeline scope does not match the selected task context")
        if self.section.status is DashboardSectionStatus.EMPTY and self.tasks:
            raise ValueError("empty task status cannot contain tasks")
        if not self.tasks and self.pipeline.package_count:
            raise ValueError("task pipeline cannot contain packages without tasks")
        return self


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


class DashboardResponseBase(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["1"] = "1"
    project_id: DashboardIdentifier
    region_code: DashboardIdentifier
    range_start: AwareDatetime = Field(alias="from")
    range_end: AwareDatetime = Field(alias="to")
    timezone: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    as_of: AwareDatetime


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
