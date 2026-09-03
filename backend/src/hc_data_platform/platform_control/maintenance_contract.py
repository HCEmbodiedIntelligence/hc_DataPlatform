"""Pure, fail-closed reference contract for platform maintenance fencing.

SYS1-03 will persist and enforce this contract.  Keeping the transition and
writer-permit checks pure here makes the DR0-03 ownership decision executable
without pretending that the control plane already exists.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class MaintenanceState(str, Enum):
    REQUESTED = "REQUESTED"
    LEASED = "LEASED"
    READ_ONLY = "READ_ONLY"
    DRAINING = "DRAINING"
    FENCED = "FENCED"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RELEASING = "RELEASING"
    WRITE_ENABLE_PENDING = "WRITE_ENABLE_PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RELEASED = "FAILED_RELEASED"
    FAILED_READ_ONLY = "FAILED_READ_ONLY"
    CANCELLED = "CANCELLED"


ALLOWED_MAINTENANCE_TRANSITIONS = MappingProxyType(
    {
        MaintenanceState.REQUESTED: frozenset(
            {MaintenanceState.LEASED, MaintenanceState.CANCELLED}
        ),
        MaintenanceState.LEASED: frozenset(
            {MaintenanceState.READ_ONLY, MaintenanceState.FAILED_RELEASED}
        ),
        MaintenanceState.READ_ONLY: frozenset(
            {MaintenanceState.DRAINING, MaintenanceState.FAILED_READ_ONLY}
        ),
        MaintenanceState.DRAINING: frozenset(
            {MaintenanceState.FENCED, MaintenanceState.FAILED_READ_ONLY}
        ),
        MaintenanceState.FENCED: frozenset(
            {MaintenanceState.EXECUTING, MaintenanceState.FAILED_READ_ONLY}
        ),
        MaintenanceState.EXECUTING: frozenset(
            {MaintenanceState.VERIFYING, MaintenanceState.FAILED_READ_ONLY}
        ),
        MaintenanceState.VERIFYING: frozenset(
            {MaintenanceState.RELEASING, MaintenanceState.FAILED_READ_ONLY}
        ),
        MaintenanceState.RELEASING: frozenset(
            {MaintenanceState.WRITE_ENABLE_PENDING, MaintenanceState.FAILED_READ_ONLY}
        ),
        MaintenanceState.WRITE_ENABLE_PENDING: frozenset(
            {MaintenanceState.SUCCEEDED, MaintenanceState.FAILED_READ_ONLY}
        ),
        MaintenanceState.FAILED_READ_ONLY: frozenset({MaintenanceState.RELEASING}),
        MaintenanceState.SUCCEEDED: frozenset(),
        MaintenanceState.FAILED_RELEASED: frozenset(),
        MaintenanceState.CANCELLED: frozenset(),
    }
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_utc(value: datetime) -> datetime:
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError("control-plane timestamps must be UTC")
    return value


class MaintenanceLeaseV1(_StrictModel):
    operation_id: str = Field(min_length=1, max_length=128)
    environment_id: str = Field(min_length=1, max_length=128)
    state: MaintenanceState
    owner_instance_id: str = Field(min_length=1, max_length=255)
    fencing_token: int = Field(gt=0)
    lease_until: AwareDatetime
    state_version: int = Field(ge=0)

    _utc_lease = field_validator("lease_until")(_require_utc)


class MaintenanceCommandV1(_StrictModel):
    operation_id: str = Field(min_length=1, max_length=128)
    environment_id: str = Field(min_length=1, max_length=128)
    owner_instance_id: str = Field(min_length=1, max_length=255)
    fencing_token: int = Field(gt=0)
    expected_state: MaintenanceState
    expected_state_version: int = Field(ge=0)
    next_state: MaintenanceState
    manual_approval_id: str | None = Field(default=None, min_length=1, max_length=255)


class EnvironmentFenceV1(_StrictModel):
    environment_id: str = Field(min_length=1, max_length=128)
    mode: Literal["READ_WRITE", "READ_ONLY_MAINTENANCE"]
    fencing_token: int = Field(gt=0)


class WriterPermitV1(_StrictModel):
    environment_id: str = Field(min_length=1, max_length=128)
    writer_id: str = Field(min_length=1, max_length=255)
    writer_kind: Literal[
        "api_command",
        "presigned_upload_grant",
        "outbox_claim",
        "temporal_activity",
        "aligned_media_attempt",
        "maintenance_controller",
        "kubernetes_job",
    ]
    fencing_token: int = Field(gt=0)
    lease_until: AwareDatetime

    _utc_lease = field_validator("lease_until")(_require_utc)


class MaintenanceContractError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def apply_maintenance_transition(
    lease: MaintenanceLeaseV1,
    command: MaintenanceCommandV1,
    *,
    database_now: datetime,
) -> MaintenanceLeaseV1:
    """Validate one owner/token/version-bound transition without side effects."""

    _require_utc(database_now)
    if database_now >= lease.lease_until:
        raise MaintenanceContractError(
            "PLATFORM_MAINTENANCE_LEASE_EXPIRED", "the operation lease is no longer current"
        )
    if command.operation_id != lease.operation_id or command.environment_id != lease.environment_id:
        raise MaintenanceContractError(
            "PLATFORM_MAINTENANCE_OPERATION_MISMATCH", "command targets another operation"
        )
    if command.owner_instance_id != lease.owner_instance_id:
        raise MaintenanceContractError(
            "PLATFORM_MAINTENANCE_OWNER_MISMATCH", "command owner is no longer current"
        )
    if command.fencing_token != lease.fencing_token:
        raise MaintenanceContractError(
            "PLATFORM_MAINTENANCE_FENCING_TOKEN_STALE", "command fencing token is stale"
        )
    if (
        command.expected_state is not lease.state
        or command.expected_state_version != lease.state_version
    ):
        raise MaintenanceContractError(
            "PLATFORM_MAINTENANCE_STATE_CONFLICT", "operation state or version changed"
        )
    if command.next_state not in ALLOWED_MAINTENANCE_TRANSITIONS[lease.state]:
        raise MaintenanceContractError(
            "PLATFORM_MAINTENANCE_TRANSITION_INVALID",
            f"{lease.state.value} cannot transition to {command.next_state.value}",
        )
    if (
        lease.state is MaintenanceState.FAILED_READ_ONLY
        and command.next_state is MaintenanceState.RELEASING
        and command.manual_approval_id is None
    ):
        raise MaintenanceContractError(
            "PLATFORM_MAINTENANCE_MANUAL_APPROVAL_REQUIRED",
            "failed read-only operations require explicit recovery approval",
        )
    return lease.model_copy(
        update={"state": command.next_state, "state_version": lease.state_version + 1}
    )


def assert_writer_commit_allowed(
    permit: WriterPermitV1,
    fence: EnvironmentFenceV1,
    *,
    database_now: datetime,
) -> None:
    """Reject every stale or maintenance-time business write at commit time."""

    _require_utc(database_now)
    if permit.environment_id != fence.environment_id:
        raise MaintenanceContractError(
            "PLATFORM_WRITER_ENVIRONMENT_MISMATCH", "writer permit belongs to another environment"
        )
    if fence.mode != "READ_WRITE":
        raise MaintenanceContractError(
            "PLATFORM_MAINTENANCE", "the environment is read-only for maintenance"
        )
    if permit.fencing_token != fence.fencing_token:
        raise MaintenanceContractError(
            "PLATFORM_WRITER_FENCING_TOKEN_STALE", "writer permit predates the current epoch"
        )
    if database_now >= permit.lease_until:
        raise MaintenanceContractError(
            "PLATFORM_WRITER_PERMIT_EXPIRED", "writer permit is no longer current"
        )
