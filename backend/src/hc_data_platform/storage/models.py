from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hc_data_platform.core.pagination import PageInfo

ByteString = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]*)$")]


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


class CapacityInventoryPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str
    project_id: str
    items: tuple[CapacityInventoryFact, ...]
    page_info: PageInfo


class LifecyclePolicyAction(str, Enum):
    """V1 policy actions; physical cleanup is restricted to rebuildable cache objects."""

    RETAIN = "RETAIN"
    REVIEW_EXPIRATION = "REVIEW_EXPIRATION"
    CLEAN_REBUILDABLE_CACHE = "CLEAN_REBUILDABLE_CACHE"

    @property
    def dangerous(self) -> bool:
        return self is LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE


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


class LifecycleExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: str
    project_id: str
    policy_id: str
    policy_version: int = Field(ge=1)
    action: LifecyclePolicyAction
    production: bool = True
    production_execution_approved: bool = False
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
    # OPEN-10 is unresolved. No in-process approval bit can enable production work;
    # changing this requires a separately reviewed contract and persistence migration.
    if request.production:
        blocked.append("OPEN_10_PRODUCTION_EXECUTION_DISABLED")
    if request.action is not LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE:
        blocked.append("NO_PHYSICAL_ACTION_FOR_POLICY")
    for candidate in request.candidates:
        if candidate.object_role.protected:
            blocked.append(f"PROTECTED_OBJECT:{candidate.physical_instance_id}")
        if candidate.object_role is not ObjectRole.REBUILDABLE_DERIVATIVE:
            blocked.append(f"NOT_REBUILDABLE:{candidate.physical_instance_id}")
        if candidate.rebuild_source_id is None:
            blocked.append(f"REBUILD_SOURCE_MISSING:{candidate.physical_instance_id}")
        if candidate.active_reference_count:
            blocked.append(f"ACTIVE_REFERENCE:{candidate.physical_instance_id}")
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
    project_id: str
    policy_id: str
    policy_version: int = Field(ge=1)
    action: LifecyclePolicyAction
    production: bool
    production_execution_approved: bool
    batch_index: int = Field(ge=0)
    candidates: tuple[LifecycleExecutionCandidate, ...]


class LifecycleBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: str
    batch_index: int = Field(ge=0)
    processed_instance_ids: tuple[str, ...]
    replayed: bool = False
