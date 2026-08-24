"""Wire models for authoritative P15 robot and component reads."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hc_data_platform.core.pagination import PageInfo

COMPONENT_LIFECYCLE_TRANSITIONS: dict[str, frozenset[str]] = {
    "DRAFT": frozenset({"ACTIVE", "RETIRED"}),
    "ACTIVE": frozenset({"MAINTENANCE", "DISABLED", "RETIRED"}),
    "MAINTENANCE": frozenset({"ACTIVE", "DISABLED", "RETIRED"}),
    "DISABLED": frozenset({"ACTIVE", "RETIRED"}),
    "RETIRED": frozenset(),
}


class RobotScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str = Field(min_length=1, max_length=128)
    region_code: str = Field(min_length=1, max_length=64)


class Connectivity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: str = Field(min_length=1, max_length=64)
    observed_at: datetime | None = None
    source: str | None = Field(default=None, min_length=1, max_length=128)
    reason_code: str | None = Field(default=None, min_length=1, max_length=128)


class RobotRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=256)
    serial_no: str = Field(min_length=1, max_length=128)
    lifecycle_status: str = Field(min_length=1, max_length=64)
    connectivity: Connectivity


class CreateRobotRequest(BaseModel):
    """Immutable creation input for a physical robot instance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    display_name: str = Field(min_length=1, max_length=256)
    serial_no: str = Field(min_length=1, max_length=128)
    lifecycle_status: str = Field(default="DRAFT", pattern=r"^(DRAFT|ACTIVE|MAINTENANCE)$")
    connectivity_state: str = Field(default="OFFLINE", pattern=r"^(ONLINE|OFFLINE|DEGRADED)$")
    connectivity_source: str | None = Field(default=None, min_length=1, max_length=128)
    connectivity_reason_code: str | None = Field(default=None, min_length=1, max_length=128)


class UpdateRobotRequest(BaseModel):
    """Mutable robot facts; lifecycle transitions use their dedicated command."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    display_name: str | None = Field(default=None, min_length=1, max_length=256)
    connectivity_state: str | None = Field(default=None, pattern=r"^(ONLINE|OFFLINE|DEGRADED)$")
    connectivity_source: str | None = Field(default=None, min_length=1, max_length=128)
    connectivity_reason_code: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def require_change(self) -> UpdateRobotRequest:
        if all(
            value is None
            for value in (
                self.display_name,
                self.connectivity_state,
                self.connectivity_source,
                self.connectivity_reason_code,
            )
        ):
            raise ValueError("at least one robot field must change")
        return self


class RobotLifecycleTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lifecycle_status: str = Field(pattern=r"^(ACTIVE|MAINTENANCE|DISABLED|RETIRED)$")
    reason: str = Field(min_length=1, max_length=512)


class CreateMaintenanceRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str = Field(min_length=1, max_length=256)
    details: str | None = Field(default=None, min_length=1, max_length=4_000)


class MaintenanceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    robot_id: str = Field(min_length=1, max_length=128)
    component_id: str | None = Field(default=None, min_length=1, max_length=128)
    event_type: str = Field(min_length=1, max_length=64)
    previous_lifecycle_status: str | None = Field(default=None, min_length=1, max_length=64)
    lifecycle_status: str | None = Field(default=None, min_length=1, max_length=64)
    summary: str = Field(min_length=1, max_length=256)
    details: str | None = Field(default=None, min_length=1, max_length=4_000)
    actor_id: str = Field(min_length=1, max_length=128)
    occurred_at: datetime


class MaintenanceRecordPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[MaintenanceRecord, ...]
    page_info: PageInfo
    snapshot_at: datetime
    scope: RobotScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-21"


class MaintenanceRecordEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data: MaintenanceRecord
    scope: RobotScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-21"


class EffectiveModelBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    scope_type: str = Field(min_length=1, max_length=64)
    scope_id: str = Field(min_length=1, max_length=128)
    robot_model_version_id: str = Field(min_length=1, max_length=128)
    valid_from: datetime
    valid_to: datetime | None = None
    etag: str = Field(min_length=1, max_length=256)


class RobotBootstrap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    robot: RobotRecord
    etag: str = Field(min_length=1, max_length=256)
    topology_revision: str = Field(min_length=1, max_length=256)
    effective_model_binding: EffectiveModelBinding | None = None
    allowed_actions: tuple[str, ...] = ()


class RobotComponent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    robot_id: str = Field(min_length=1, max_length=128)
    parent_component_id: str | None = Field(default=None, min_length=1, max_length=128)
    component_model_id: str = Field(min_length=1, max_length=128)
    component_type: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=256)
    serial_no: str = Field(min_length=1, max_length=128)
    lifecycle_status: str = Field(min_length=1, max_length=64)
    sort_order: str = Field(pattern=r"^(0|[1-9]\d*)$")


class CreateRobotComponentRequest(BaseModel):
    """Creation input for one durable node in a robot topology."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parent_component_id: str | None = Field(default=None, min_length=1, max_length=128)
    component_model_id: str = Field(min_length=1, max_length=128)
    component_type: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=256)
    serial_no: str = Field(min_length=1, max_length=128)
    lifecycle_status: str = Field(default="DRAFT", pattern=r"^(DRAFT|ACTIVE|MAINTENANCE)$")
    sort_order: int = Field(default=0, ge=0, le=2_147_483_647)


class UpdateRobotComponentRequest(BaseModel):
    """Mutable component facts; state is changed only by the transition command."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parent_component_id: str | None = Field(default=None, min_length=1, max_length=128)
    component_model_id: str | None = Field(default=None, min_length=1, max_length=128)
    component_type: str | None = Field(default=None, min_length=1, max_length=128)
    display_name: str | None = Field(default=None, min_length=1, max_length=256)
    serial_no: str | None = Field(default=None, min_length=1, max_length=128)
    sort_order: int | None = Field(default=None, ge=0, le=2_147_483_647)

    @model_validator(mode="after")
    def require_change(self) -> UpdateRobotComponentRequest:
        if not self.model_fields_set:
            raise ValueError("at least one component field must change")
        return self


class ComponentLifecycleTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lifecycle_status: str = Field(pattern=r"^(ACTIVE|MAINTENANCE|DISABLED|RETIRED)$")
    reason: str = Field(min_length=1, max_length=512)


class RobotComponentMutation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    component: RobotComponent
    robot_etag: str = Field(min_length=1, max_length=256)
    topology_revision: str = Field(min_length=1, max_length=256)


class RobotComponentMutationEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data: RobotComponentMutation
    scope: RobotScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-21"


class FrameReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    parent_frame: str | None = Field(default=None, min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=128)
    calibration_set_id: str = Field(min_length=1, max_length=128)
    status: str = Field(min_length=1, max_length=64)
    valid_from: datetime
    valid_to: datetime | None = None


class ChannelReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    canonical_path: str = Field(min_length=1, max_length=512)
    display_name: str = Field(min_length=1, max_length=256)
    modality: str = Field(min_length=1, max_length=128)
    schema_id: str = Field(min_length=1, max_length=128)
    schema_version: str = Field(pattern=r"^[1-9]\d*$")
    role: str = Field(min_length=1, max_length=128)
    unit: str | None = Field(default=None, min_length=1, max_length=64)
    frequency_hz: str | None = Field(default=None, pattern=r"^(0|[1-9]\d*)(\.\d+)?$")
    frame_id: str | None = Field(default=None, min_length=1, max_length=128)
    clock_id: str | None = Field(default=None, min_length=1, max_length=128)
    status: str = Field(min_length=1, max_length=64)


class RobotPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[RobotRecord, ...]
    page_info: PageInfo
    snapshot_at: datetime
    scope: RobotScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-19"


class GlobalSearchRobotResult(BaseModel):
    """Minimal robot hit returned by the shell discovery surface.

    Search never duplicates protected topology or model-binding data. Selecting a
    result moves the user to the normal robot page, whose existing authorization
    boundary owns those facts.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_type: Literal["ROBOT"] = "ROBOT"
    robot: RobotRecord


class GlobalRobotSearchPage(BaseModel):
    """Bounded robot search within one verified project/region scope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=256)
    items: tuple[GlobalSearchRobotResult, ...]
    page_info: PageInfo
    snapshot_at: datetime
    scope: RobotScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-21"


class RobotBootstrapEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data: RobotBootstrap
    scope: RobotScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-19"


class ComponentPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[RobotComponent, ...]
    page_info: PageInfo
    snapshot_at: datetime
    topology_revision: str = Field(min_length=1, max_length=256)
    scope: RobotScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-19"


class FramePage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[FrameReference, ...]
    page_info: PageInfo
    snapshot_at: datetime
    scope: RobotScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-19"


class ChannelPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ChannelReference, ...]
    page_info: PageInfo
    snapshot_at: datetime
    scope: RobotScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-19"
