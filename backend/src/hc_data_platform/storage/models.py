from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hc_data_platform.core.pagination import PageInfo

ByteString = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]*)$")]
SignedByteString = Annotated[str, Field(pattern=r"^-?(0|[1-9][0-9]*)$")]


class BusinessCapacityCategory(str, Enum):
    """The only four business-capacity categories exposed by P12."""

    RAW = "RAW"
    ANNOTATION_COMPLETE = "ANNOTATION_COMPLETE"
    PENDING_ANNOTATION = "PENDING_ANNOTATION"
    ISSUE_DATA = "ISSUE_DATA"


class InventoryDisposition(str, Enum):
    PRIMARY = "PRIMARY"
    REPLICA = "REPLICA"
    TEMPORARY = "TEMPORARY"


class ObjectRole(str, Enum):
    RAW = "RAW"
    MANIFEST = "MANIFEST"
    PUBLISHED_MANIFEST = "PUBLISHED_MANIFEST"
    REBUILDABLE_DERIVATIVE = "REBUILDABLE_DERIVATIVE"
    OTHER = "OTHER"

    @property
    def protected(self) -> bool:
        return self in {
            ObjectRole.RAW,
            ObjectRole.MANIFEST,
            ObjectRole.PUBLISHED_MANIFEST,
        }


class CapacityInventoryFact(BaseModel):
    """One provider-reported physical instance in an immutable inventory snapshot.

    Duplicate rows may repeat the same ``physical_instance_id`` with exactly the same
    fields. They are ignored by reconciliation. Replicas use a distinct physical ID but
    share one logical object ID, so the physical and candidate business totals can both be
    reported without counting a business object twice.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str = Field(min_length=1, max_length=256)
    project_id: str = Field(min_length=1, max_length=256)
    physical_instance_id: str = Field(min_length=1, max_length=1024)
    logical_object_id: str | None = Field(default=None, max_length=1024)
    physical_bytes: ByteString
    disposition: InventoryDisposition
    business_category: BusinessCapacityCategory | None = None
    object_role: ObjectRole
    observed_at: datetime

    @model_validator(mode="after")
    def validate_business_identity(self) -> CapacityInventoryFact:
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must include a timezone")
        if self.disposition is InventoryDisposition.TEMPORARY:
            if self.business_category is not None:
                raise ValueError("temporary inventory cannot enter a business category")
            return self
        if self.logical_object_id is None or self.business_category is None:
            raise ValueError(
                "primary and replica inventory require one logical object and business category"
            )
        return self


class CapacityCategoryTotal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: BusinessCapacityCategory
    candidate_bytes: ByteString
    logical_object_count: int = Field(ge=0)


class CapacityReconciliation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    replica_overhead_bytes: ByteString
    replica_instance_count: int = Field(ge=0)
    temporary_bytes: ByteString
    temporary_instance_count: int = Field(ge=0)
    duplicate_inventory_rows_ignored: int = Field(ge=0)
    formula: str = (
        "physical_total_bytes = candidate_business_total_bytes + "
        "replica_overhead_bytes + temporary_bytes"
    )
    balanced: bool


class CapacitySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str
    project_id: str
    observed_at: datetime
    physical_total_bytes: ByteString
    physical_instance_count: int = Field(ge=0)
    candidate_business_total_bytes: ByteString
    candidate_logical_object_count: int = Field(ge=0)
    categories: tuple[CapacityCategoryTotal, ...]
    reconciliation: CapacityReconciliation

    @model_validator(mode="after")
    def validate_categories(self) -> CapacitySnapshot:
        expected = tuple(BusinessCapacityCategory)
        actual = tuple(item.category for item in self.categories)
        if actual != expected:
            raise ValueError("capacity snapshot must contain each fixed category exactly once")
        if sum(int(item.candidate_bytes) for item in self.categories) != int(
            self.candidate_business_total_bytes
        ):
            raise ValueError("category bytes do not reconcile to candidate business total")
        return self


class CapacityHistoryPoint(BaseModel):
    """The latest immutable capacity fact for one UTC calendar day."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str = Field(min_length=1, max_length=256)
    observed_at: datetime
    physical_total_bytes: ByteString
    candidate_business_total_bytes: ByteString

    @model_validator(mode="after")
    def validate_observed_at(self) -> CapacityHistoryPoint:
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must include a timezone")
        return self


class CapacityGrowth(BaseModel):
    """Exact integer trend over a bounded window, never a client-side estimate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    from_snapshot_id: str = Field(min_length=1, max_length=256)
    from_observed_at: datetime
    to_snapshot_id: str = Field(min_length=1, max_length=256)
    to_observed_at: datetime
    candidate_change_bytes: SignedByteString
    elapsed_seconds: int = Field(ge=1)
    candidate_bytes_per_day: SignedByteString

    @model_validator(mode="after")
    def validate_interval(self) -> CapacityGrowth:
        if self.from_observed_at.tzinfo is None or self.to_observed_at.tzinfo is None:
            raise ValueError("growth timestamps must include a timezone")
        if self.to_observed_at <= self.from_observed_at:
            raise ValueError("growth interval must be positive")
        return self


class CapacityHistory(BaseModel):
    """Bounded project-scoped daily history and an exact business-capacity trend."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str = Field(min_length=1, max_length=256)
    window_start: datetime
    window_end: datetime
    items: tuple[CapacityHistoryPoint, ...] = Field(max_length=31)
    growth: CapacityGrowth | None

    @model_validator(mode="after")
    def validate_window(self) -> CapacityHistory:
        if self.window_start.tzinfo is None or self.window_end.tzinfo is None:
            raise ValueError("history window timestamps must include a timezone")
        if self.window_start > self.window_end:
            raise ValueError("history window start must not exceed end")
        if self.items != tuple(
            sorted(self.items, key=lambda item: (item.observed_at, item.snapshot_id))
        ):
            raise ValueError("history points must be chronological")
        dates = tuple(item.observed_at.astimezone(timezone.utc).date() for item in self.items)
        if len(dates) != len(set(dates)):
            raise ValueError("history must contain at most one capacity fact per UTC day")
        if len(self.items) < 2:
            if self.growth is not None:
                raise ValueError("single-point history must not provide growth")
            return self
        if self.growth is None:
            raise ValueError("multi-point history must provide exact growth")
        first = self.items[0]
        last = self.items[-1]
        if (
            self.growth.from_snapshot_id != first.snapshot_id
            or self.growth.from_observed_at != first.observed_at
            or self.growth.to_snapshot_id != last.snapshot_id
            or self.growth.to_observed_at != last.observed_at
        ):
            raise ValueError("growth endpoints must match history endpoints")
        elapsed_seconds = int((last.observed_at - first.observed_at).total_seconds())
        if self.growth.elapsed_seconds != elapsed_seconds:
            raise ValueError("growth elapsed seconds must match history endpoints")
        candidate_change = int(last.candidate_business_total_bytes) - int(
            first.candidate_business_total_bytes
        )
        if int(self.growth.candidate_change_bytes) != candidate_change:
            raise ValueError("growth change must match history endpoints")
        expected_per_day = abs(candidate_change) * 86_400 // elapsed_seconds
        if candidate_change < 0:
            expected_per_day = -expected_per_day
        if int(self.growth.candidate_bytes_per_day) != expected_per_day:
            raise ValueError("growth daily rate must match history endpoints")
        return self


class CapacityInventoryPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str
    project_id: str
    items: tuple[CapacityInventoryFact, ...]
    page_info: PageInfo


class LifecyclePolicyAction(str, Enum):
    """Lifecycle actions with explicit physical-safety semantics."""

    RETAIN = "RETAIN"
    REVIEW_EXPIRATION = "REVIEW_EXPIRATION"
    ARCHIVE = "ARCHIVE"
    TRANSITION_TO_COLD = "TRANSITION_TO_COLD"
    CLEAN_REBUILDABLE_CACHE = "CLEAN_REBUILDABLE_CACHE"

    @property
    def dangerous(self) -> bool:
        return self is LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE

    @property
    def physical(self) -> bool:
        return self in {
            LifecyclePolicyAction.ARCHIVE,
            LifecyclePolicyAction.TRANSITION_TO_COLD,
            LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE,
        }


class LifecyclePolicyState(str, Enum):
    DRAFT = "DRAFT"
    ENABLED = "ENABLED"
    PAUSED = "PAUSED"


class LifecyclePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str = Field(min_length=1, max_length=256)
    project_id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=256)
    business_category: BusinessCapacityCategory
    object_role: ObjectRole
    action: LifecyclePolicyAction
    minimum_age_days: int = Field(ge=0, le=36500)
    priority: int = Field(ge=0, le=10000)
    state: LifecyclePolicyState
    version: int = Field(ge=1)
    etag: str
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def protect_immutable_roles(self) -> LifecyclePolicy:
        if (
            self.object_role.protected
            and self.action is LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE
        ):
            raise ValueError("Raw and Manifest roles cannot be cache-cleanup policy targets")
        if (
            self.action is LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE
            and self.object_role is not ObjectRole.REBUILDABLE_DERIVATIVE
        ):
            raise ValueError("cache cleanup is limited to rebuildable derivatives")
        return self


class LifecyclePolicyPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str
    items: tuple[LifecyclePolicy, ...]
    page_info: PageInfo


class LifecycleAuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    audit_id: str
    project_id: str
    policy_id: str
    actor_id: str
    action: str
    before_digest: str | None = None
    after_digest: str | None = None
    request_id: str
    details: dict[str, str | int | bool | None] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LifecycleAuditPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str
    items: tuple[LifecycleAuditEvent, ...]
    page_info: PageInfo


class CreateLifecyclePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=256)
    business_category: BusinessCapacityCategory
    object_role: ObjectRole
    action: LifecyclePolicyAction
    minimum_age_days: int = Field(ge=0, le=36500)
    priority: int = Field(ge=0, le=10000)

    @model_validator(mode="after")
    def validate_cleanup_target(self) -> CreateLifecyclePolicy:
        if (
            self.action is LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE
            and self.object_role is not ObjectRole.REBUILDABLE_DERIVATIVE
        ):
            raise ValueError("cache cleanup is limited to rebuildable derivatives")
        return self


class UpdateLifecyclePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=256)
    business_category: BusinessCapacityCategory
    object_role: ObjectRole
    action: LifecyclePolicyAction
    minimum_age_days: int = Field(ge=0, le=36500)
    priority: int = Field(ge=0, le=10000)

    @model_validator(mode="after")
    def validate_cleanup_target(self) -> UpdateLifecyclePolicy:
        if (
            self.action is LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE
            and self.object_role is not ObjectRole.REBUILDABLE_DERIVATIVE
        ):
            raise ValueError("cache cleanup is limited to rebuildable derivatives")
        return self


class LifecycleExecutionCandidate(BaseModel):
    """Internal Worker input; it is intentionally absent from the public HTTP router."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    physical_instance_id: str
    logical_object_id: str
    object_role: ObjectRole
    rebuild_source_id: str | None = None
    active_reference_count: int = Field(default=0, ge=0)
    protection_verified: bool
    retention_active: bool = False
    legal_hold: bool = False
    governance_hold: bool = False


class LifecycleExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: str
    organization_id: str | None = Field(default=None, min_length=1, max_length=256)
    project_id: str
    region_code: str | None = Field(default=None, min_length=1, max_length=256)
    policy_id: str
    policy_version: int = Field(ge=1)
    action: LifecyclePolicyAction
    production: bool = True
    production_execution_approved: bool = False
    approval_id: str | None = Field(default=None, min_length=1, max_length=256)
    plan_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    batch_size: int = Field(default=100, ge=1, le=1000)
    candidates: tuple[LifecycleExecutionCandidate, ...]


class LifecycleExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: str
    status: str
    processed_instance_ids: tuple[str, ...] = ()
    blocked_reasons: tuple[str, ...] = ()
    replayed_batches: int = Field(default=0, ge=0)


def evaluate_execution_protection(
    request: LifecycleExecutionRequest,
) -> LifecycleExecutionResult:
    """Pure, deterministic protection gate shared by HTTP services and Workers."""

    blocked: list[str] = []
    if request.production and (
        not request.production_execution_approved
        or request.approval_id is None
        or request.plan_hash is None
    ):
        blocked.append("LIFECYCLE_APPROVAL_REQUIRED")
    if not request.action.physical:
        blocked.append("NO_PHYSICAL_ACTION_FOR_POLICY")
    for candidate in request.candidates:
        if (
            request.action is LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE
            and candidate.object_role.protected
        ):
            blocked.append(f"PROTECTED_OBJECT:{candidate.physical_instance_id}")
        if (
            request.action is LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE
            and candidate.object_role is not ObjectRole.REBUILDABLE_DERIVATIVE
        ):
            blocked.append(f"NOT_REBUILDABLE:{candidate.physical_instance_id}")
        if (
            request.action is LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE
            and candidate.rebuild_source_id is None
        ):
            blocked.append(f"REBUILD_SOURCE_MISSING:{candidate.physical_instance_id}")
        if candidate.active_reference_count:
            blocked.append(f"ACTIVE_REFERENCE:{candidate.physical_instance_id}")
        if candidate.retention_active:
            blocked.append(f"RETENTION_ACTIVE:{candidate.physical_instance_id}")
        if candidate.legal_hold:
            blocked.append(f"LEGAL_HOLD:{candidate.physical_instance_id}")
        if candidate.governance_hold:
            blocked.append(f"GOVERNANCE_HOLD:{candidate.physical_instance_id}")
        if not candidate.protection_verified:
            blocked.append(f"PROTECTION_UNVERIFIED:{candidate.physical_instance_id}")
    if blocked:
        return LifecycleExecutionResult(
            execution_id=request.execution_id,
            status="BLOCKED",
            blocked_reasons=tuple(dict.fromkeys(blocked)),
        )
    return LifecycleExecutionResult(
        execution_id=request.execution_id,
        status="VALIDATED",
        processed_instance_ids=tuple(
            candidate.physical_instance_id for candidate in request.candidates
        ),
    )


class LifecycleBatchCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: str
    organization_id: str | None = None
    project_id: str
    region_code: str | None = None
    policy_id: str
    policy_version: int = Field(ge=1)
    action: LifecyclePolicyAction
    production: bool
    production_execution_approved: bool
    approval_id: str | None = None
    plan_hash: str | None = None
    batch_index: int = Field(ge=0)
    final_batch: bool = False
    candidates: tuple[LifecycleExecutionCandidate, ...]


class LifecycleBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: str
    batch_index: int = Field(ge=0)
    processed_instance_ids: tuple[str, ...]
    replayed: bool = False


class StorageObjectStatus(str, Enum):
    ACTIVE = "ACTIVE"
    TRASHED = "TRASHED"
    ARCHIVED = "ARCHIVED"
    TRANSITIONING = "TRANSITIONING"
    FAILED = "FAILED"


class StorageTier(str, Enum):
    HOT = "HOT"
    COLD = "COLD"
    ARCHIVE = "ARCHIVE"


class StorageObjectAction(str, Enum):
    DOWNLOAD = "DOWNLOAD"
    TRASH = "TRASH"
    RESTORE = "RESTORE"
    ARCHIVE = "ARCHIVE"
    TRANSITION_TO_COLD = "TRANSITION_TO_COLD"
    PURGE = "PURGE"


class StorageObjectOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str
    project_id: str
    object_id: str | None = None
    multipart_id: str | None = None
    action: str
    status: str = Field(pattern=r"^(PENDING|RUNNING|SUCCEEDED|FAILED)$")
    idempotency_key: str
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    actor_id: str
    request_id: str
    attempt: int = Field(ge=0)
    error_code: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class ManagedStorageObject(BaseModel):
    """Public object fact. Physical bucket/key locators never cross this boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    object_id: str = Field(min_length=1, max_length=256)
    project_id: str = Field(min_length=1, max_length=256)
    display_key: str = Field(min_length=1, max_length=512)
    physical_bytes: ByteString
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    business_category: BusinessCapacityCategory
    object_role: ObjectRole
    storage_tier: StorageTier
    status: StorageObjectStatus
    active_reference_count: int = Field(ge=0)
    retention_until: datetime | None = None
    legal_hold: bool = False
    governance_hold: bool = False
    rebuild_source_id: str | None = Field(default=None, max_length=1024)
    recoverable_until: datetime | None = None
    version: int = Field(ge=1)
    etag: str = Field(min_length=1, max_length=256)
    allowed_actions: tuple[StorageObjectAction, ...] = ()
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_times(self) -> ManagedStorageObject:
        values = (self.created_at, self.updated_at, self.retention_until, self.recoverable_until)
        if any(value is not None and value.tzinfo is None for value in values):
            raise ValueError("storage object timestamps must include a timezone")
        if self.status is StorageObjectStatus.TRASHED and self.recoverable_until is None:
            raise ValueError("trashed objects require a recoverable-until timestamp")
        if self.status is not StorageObjectStatus.TRASHED and self.recoverable_until is not None:
            raise ValueError("only trashed objects expose a recoverable-until timestamp")
        return self


class ManagedStorageObjectRecord(ManagedStorageObject):
    """Internal durable object identity including the provider locator."""

    object_key: str = Field(min_length=1, max_length=2048)
    original_object_key: str = Field(min_length=1, max_length=2048)

    def public(self, *, now: datetime) -> ManagedStorageObject:
        actions: list[StorageObjectAction] = []
        protected = (
            self.active_reference_count > 0
            or self.legal_hold
            or self.governance_hold
            or (self.retention_until is not None and self.retention_until > now)
        )
        if self.status is StorageObjectStatus.ACTIVE:
            actions.append(StorageObjectAction.DOWNLOAD)
            if not protected:
                actions.extend(
                    (
                        StorageObjectAction.ARCHIVE,
                        StorageObjectAction.TRANSITION_TO_COLD,
                    )
                )
                if not self.object_role.protected:
                    actions.append(StorageObjectAction.TRASH)
        elif self.status in {StorageObjectStatus.TRASHED, StorageObjectStatus.ARCHIVED}:
            actions.append(StorageObjectAction.RESTORE)
        return ManagedStorageObject.model_validate(
            {
                **self.model_dump(exclude={"object_key", "original_object_key"}),
                "allowed_actions": actions,
            }
        )


class ManagedStorageObjectPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str
    items: tuple[ManagedStorageObject, ...]
    page_info: PageInfo


class StorageObjectDownloadGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    object_id: str
    url: str = Field(min_length=1, max_length=8192)
    expires_at: datetime
    physical_bytes: ByteString
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TrashStorageObjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=512)
    recoverable_days: int = Field(default=30, ge=1, le=365)


class RestoreStorageObjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=512)


class TransitionStorageObjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: LifecyclePolicyAction
    reason: str = Field(min_length=3, max_length=512)

    @model_validator(mode="after")
    def validate_action(self) -> TransitionStorageObjectRequest:
        if self.action not in {
            LifecyclePolicyAction.ARCHIVE,
            LifecyclePolicyAction.TRANSITION_TO_COLD,
        }:
            raise ValueError("object transition must archive or move to the cold tier")
        return self


class ManagedMultipartUpload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    multipart_id: str = Field(min_length=1, max_length=256)
    project_id: str = Field(min_length=1, max_length=256)
    display_key: str = Field(min_length=1, max_length=512)
    received_bytes: ByteString
    part_count: int = Field(ge=0)
    status: str = Field(pattern=r"^(ACTIVE|ABORTING|ABORTED|COMPLETED|FAILED)$")
    started_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)
    etag: str


class ManagedMultipartUploadRecord(ManagedMultipartUpload):
    object_key: str = Field(min_length=1, max_length=2048)
    upload_id: str = Field(min_length=1, max_length=1024)

    def public(self) -> ManagedMultipartUpload:
        return ManagedMultipartUpload.model_validate(
            self.model_dump(exclude={"object_key", "upload_id"})
        )


class AbortMultipartUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=512)


class LifecycleExecutionStatus(str, Enum):
    DRY_RUN = "DRY_RUN"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class LifecycleExecutionItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    object_id: str
    physical_instance_id: str
    status: str = Field(pattern=r"^(PENDING|PROCESSED|BLOCKED|FAILED)$")
    attempt: int = Field(ge=0)
    blocked_reasons: tuple[str, ...] = ()
    last_error_code: str | None = None


class LifecycleExecution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: str
    project_id: str
    policy_id: str
    policy_version: int = Field(ge=1)
    action: LifecyclePolicyAction
    status: LifecycleExecutionStatus
    dry_run: bool
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_id: str | None = None
    requested_by: str
    approved_by: str | None = None
    total_items: int = Field(ge=0)
    processed_items: int = Field(ge=0)
    blocked_items: int = Field(ge=0)
    failed_items: int = Field(ge=0)
    next_batch: int = Field(ge=0)
    items: tuple[LifecycleExecutionItem, ...] = ()
    created_at: datetime
    updated_at: datetime


class LifecycleDryRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str = Field(min_length=1, max_length=256)
    policy_etag: str = Field(min_length=1, max_length=256)
    limit: int = Field(default=1000, ge=1, le=1000)


class ApproveLifecycleExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    justification: str = Field(min_length=8, max_length=1024)


class StartLifecycleExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(min_length=1, max_length=256)
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class CancelLifecycleExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=8, max_length=1024)


class RetryLifecycleExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=8, max_length=1024)


class LifecycleExecutionLog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=1)
    project_id: str
    execution_id: str
    level: str = Field(pattern=r"^(INFO|WARNING|ERROR)$")
    event: str
    details: dict[str, str | int | bool | None] = Field(default_factory=dict)
    occurred_at: datetime


class LifecycleExecutionLogPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str
    execution_id: str
    items: tuple[LifecycleExecutionLog, ...]
    page_info: PageInfo


class LifecycleSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schedule_id: str = Field(min_length=1, max_length=256)
    project_id: str = Field(min_length=1, max_length=256)
    policy_id: str = Field(min_length=1, max_length=256)
    interval_seconds: int = Field(ge=300, le=2_678_400)
    enabled: bool
    next_run_at: datetime
    last_execution_id: str | None = None
    version: int = Field(ge=1)
    etag: str
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_timestamps(self) -> LifecycleSchedule:
        if any(
            value.tzinfo is None for value in (self.next_run_at, self.created_at, self.updated_at)
        ):
            raise ValueError("lifecycle schedule timestamps must include a timezone")
        return self


class CreateLifecycleScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str = Field(min_length=1, max_length=256)
    interval_seconds: int = Field(ge=300, le=2_678_400)
    first_run_at: datetime | None = None

    @model_validator(mode="after")
    def validate_first_run(self) -> CreateLifecycleScheduleRequest:
        if self.first_run_at is not None and self.first_run_at.tzinfo is None:
            raise ValueError("first_run_at must include a timezone")
        return self


class UpdateLifecycleScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interval_seconds: int | None = Field(default=None, ge=300, le=2_678_400)
    next_run_at: datetime | None = None

    @model_validator(mode="after")
    def validate_update(self) -> UpdateLifecycleScheduleRequest:
        if self.interval_seconds is None and self.next_run_at is None:
            raise ValueError("at least one lifecycle schedule field is required")
        if self.next_run_at is not None and self.next_run_at.tzinfo is None:
            raise ValueError("next_run_at must include a timezone")
        return self


class LifecycleSchedulePage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str
    items: tuple[LifecycleSchedule, ...]
    page_info: PageInfo


class LifecycleExecutionPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str
    items: tuple[LifecycleExecution, ...]
    page_info: PageInfo


class ProjectCapacitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str
    snapshot_id: str
    observed_at: datetime
    physical_total_bytes: ByteString
    candidate_business_total_bytes: ByteString
    categories: tuple[CapacityCategoryTotal, ...]
    balanced: bool


class CapacityPortfolio(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_ids: tuple[str, ...]
    physical_total_bytes: ByteString
    candidate_business_total_bytes: ByteString
    categories: tuple[CapacityCategoryTotal, ...]
    items: tuple[ProjectCapacitySummary, ...]
