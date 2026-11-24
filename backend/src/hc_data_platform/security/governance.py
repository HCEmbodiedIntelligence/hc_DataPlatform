"""Scope-aware organization and project administration."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from hc_data_platform.core.errors import problem


class OrganizationEntry(BaseModel):
    organization_id: str
    organization_name: str
    join_code: str | None = None
    can_manage: bool


class ProjectEntry(BaseModel):
    organization_id: str
    project_id: str
    project_name: str


class GovernanceDirectory(BaseModel):
    organizations: list[OrganizationEntry]
    projects: list[ProjectEntry]


class AdministratorChange(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    organization_id: str = Field(min_length=1, max_length=256)
    project_id: str | None = Field(default=None, min_length=1, max_length=256)
    enabled: bool
    reason: str = Field(min_length=1, max_length=500)


class MemberEntry(BaseModel):
    principal_id: str
    username: str
    administrator: bool


class InboxEntry(BaseModel):
    request_id: str
    requester_id: str
    username: str
    organization_id: str
    project_id: str | None
    kind: Literal["ORGANIZATION", "PROJECT", "CAPABILITY"]
    reason: str | None
    capability_keys: list[str]
    resource_id: str | None = None
    expires_at: str | None = None
    replaces_request_id: str | None = None


def denied():
    return problem(status=403, code="GOVERNANCE_SCOPE_REQUIRED", title="Administrator access required",
                   detail="当前账号没有此范围的管理权限。")


def missing_member():
    return problem(status=409, code="ACTIVE_MEMBERSHIP_REQUIRED", title="Membership required",
                   detail="请先加入所属组织和目标项目，再任命项目管理员。")
