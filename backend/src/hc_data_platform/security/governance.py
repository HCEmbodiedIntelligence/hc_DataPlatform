"""Scope-aware organization and project administration."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
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


class MembershipRemoval(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    organization_id: str = Field(min_length=1, max_length=256)
    project_id: str | None = Field(default=None, min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=500)


class AdministratorChange(MembershipRemoval):
    enabled: bool


class MemberEntry(BaseModel):
    principal_id: str
    username: str
    administrator: bool


class ProjectRoleChange(MembershipRemoval):
    project_id: str = Field(min_length=1, max_length=256)
    roles: set[
        Literal[
            "role.observer", "role.collector", "role.annotator", "role.reviewer", "role.publisher"
        ]
    ]
    expected_roles: set[str]


ORGANIZATION_ASSET_PERMISSIONS = frozenset(
    {
        "robot.read",
        "teleoperation.connect",
        "robot.manage",
        "robot_model.read",
        "robot_model.manage",
        "robot_model_binding.read",
        "robot_model_binding.manage",
        "quality.manage",
        "tag.manage",
    }
)


class ProjectPermissionChange(MembershipRemoval):
    project_id: str = Field(min_length=1, max_length=256)
    permissions: set[str] = Field(max_length=512)
    expected_permissions: set[str] = Field(max_length=512)

    @field_validator("permissions")
    @classmethod
    def valid_project_permissions(cls, values: set[str]) -> set[str]:
        from .access_models import CapabilityRequestCreate
        from .capabilities import KNOWN_BUSINESS_CAPABILITIES

        for value in values:
            base, separator, resource = value.partition("/")
            if base not in KNOWN_BUSINESS_CAPABILITIES:
                raise ValueError("须选择已有的项目业务权限")
            CapabilityRequestCreate(capability_keys=(base,), resource_id=resource if separator else None)
        return values


class MemberGrant(BaseModel):
    source_request_id: str | None = None
    can_revoke: bool = False
    capability: str
    expires_at: datetime | None = None


class ManagedMemberDetail(MemberEntry):
    display_name: str
    status: str
    created_at: datetime
    platform_administrator: bool
    organization_administrator: bool
    assigned_roles: list[str]
    assigned_permissions: list[str]
    assignable_permissions: list[str]
    approved_grants: list[MemberGrant]
    effective_permissions: list[str]
    can_set_roles: bool
    can_change_administrator: bool
    can_remove: bool


def can_revoke_approval(auth, item):
    from .access_models import AccessRequestStatus
    from .access_repository import check_grant_decision
    from hc_data_platform.core.errors import ProblemException

    if item.status is not AccessRequestStatus.APPROVED or (
        item.requester_id == auth.subject_id
        and (auth.service_identity or not (auth.is_platform_admin or item.organization_id in auth.managed_organization_ids))
    ):
        return False
    try:
        check_grant_decision(auth, item, AccessRequestStatus.REVOKED)
    except ProblemException:
        return False
    return True


def member_detail(
    auth,
    principal,
    profile,
    org_admin,
    project_admin,
    platform_admin,
    assigned,
    grants,
    project,
    manages_organization,
    organization_id,
):
    from .capabilities import (
        ALL_PLATFORM_ADMIN_EFFECTIVE_CAPABILITIES,
        ORGANIZATION_ADMIN_CAPABILITIES,
        ORGANIZATION_ADMIN_PROJECT_CAPABILITIES,
        expand_project_roles,
    )

    from .capabilities import KNOWN_BUSINESS_CAPABILITIES, ORDINARY_ROLE_PERMISSIONS, PROJECT_MEMBER_ROLES
    from .access_repository import DELIVERY_GRANTS, can_approve_delivery

    keys = set(assigned) | {g.capability for g in grants}
    if org_admin:
        keys.update(ORGANIZATION_ADMIN_PROJECT_CAPABILITIES if project else ORGANIZATION_ADMIN_CAPABILITIES)
    if project and project_admin:
        keys.add("role.project_admin")
    if platform_admin:
        keys.update(ALL_PLATFORM_ADMIN_EFFECTIVE_CAPABILITIES)
    expanded = expand_project_roles(keys)
    peer = platform_admin or org_admin or (project and "role.project_admin" in expanded)
    other_member = auth.subject_id != principal
    manages_project_members = project and manages_organization
    can_edit = not auth.service_identity and bool(
        manages_project_members or (other_member and (auth.is_platform_admin or not peer))
    )
    assignable = set()
    if project and can_edit and profile["status"] == "ACTIVE":
        if manages_organization:
            assignable.update(KNOWN_BUSINESS_CAPABILITIES)
        else:
            assignable.update(ORDINARY_ROLE_PERMISSIONS)
            if can_approve_delivery(auth, project, organization_id):
                assignable.update(DELIVERY_GRANTS)
    return ManagedMemberDetail(
        principal_id=principal,
        **profile,
        administrator=project_admin if project else org_admin,
        platform_administrator=platform_admin,
        organization_administrator=org_admin,
        assigned_roles=sorted(set(assigned) & PROJECT_MEMBER_ROLES.keys()),
        assigned_permissions=sorted(set(assigned) - PROJECT_MEMBER_ROLES.keys()),
        assignable_permissions=sorted(assignable),
        approved_grants=grants,
        effective_permissions=sorted(k for k in expanded if not k.startswith("role.")),
        can_set_roles=bool(project) and can_edit and profile["status"] == "ACTIVE",
        can_change_administrator=profile["status"] == "ACTIVE" and can_edit and (
            manages_organization if project else auth.is_platform_admin
        ),
        can_remove=can_edit,
    )


def assignment_change(detail, command):
    if isinstance(command, ProjectPermissionChange):
        previous = set(detail.assigned_permissions)
        changed_keys = {key.partition("/")[0] for key in previous ^ command.permissions}
        if not changed_keys <= set(detail.assignable_permissions):
            raise denied()
        if previous != command.permissions and previous != command.expected_permissions:
            raise problem(status=409, code="MEMBER_PERMISSIONS_CHANGED", title="Permissions changed",
                          detail="成员权限已变更，请重新读取后再保存。")
        return "permissions", previous, command.permissions

    previous = set(detail.assigned_roles)
    if previous != command.roles and previous != command.expected_roles:
        raise roles_changed()
    return "roles", previous, command.roles


def roles_changed():
    return problem(
        status=409,
        code="MEMBER_ROLES_CHANGED",
        title="Member roles changed",
        detail="岗位已变更，请刷新成员资料后重试。",
    )


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
    return problem(
        status=403,
        code="GOVERNANCE_SCOPE_REQUIRED",
        title="Administrator access required",
        detail="当前账号没有此范围的管理权限。",
    )


def missing_member():
    return problem(
        status=409,
        code="ACTIVE_MEMBERSHIP_REQUIRED",
        title="Membership required",
        detail="请先加入所属组织和目标项目，再任命项目管理员。",
    )
