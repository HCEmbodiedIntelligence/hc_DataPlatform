from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from hc_data_platform.core.pagination import PageInfo
from hc_data_platform.robotics.models import RobotBootstrap, RobotRecord


class OrganizationRobotScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: str = Field(min_length=1, max_length=128)


class OrganizationRobotPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[RobotRecord, ...]
    page_info: PageInfo
    snapshot_at: datetime
    scope: OrganizationRobotScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-31"


class OrganizationRobotBootstrapEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data: RobotBootstrap
    scope: OrganizationRobotScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-31"


class BindOrganizationRobotModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version_id: str = Field(min_length=1, max_length=128)
    robot_etag: str = Field(min_length=1, max_length=256)


class OrganizationRobotModelBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    binding_id: str = Field(min_length=1, max_length=128)
    robot_id: str = Field(min_length=1, max_length=128)
    version_id: str = Field(min_length=1, max_length=128)
    status: str = Field(pattern=r"^(ACTIVE|SUPERSEDED|REVOKED)$")
    bound_at: datetime
    unbound_at: datetime | None = None


class OrganizationRobotModelBindingPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[OrganizationRobotModelBinding, ...]
    scope: OrganizationRobotScope
    request_id: str = Field(min_length=1, max_length=128)
