"""Regression coverage for the organization and project administration boundaries."""
import asyncio
import os
from uuid import uuid4

import psycopg
import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.core.migrations import apply_migrations
from hc_data_platform.security.access_models import AccessDecisionCommand, AccessRequestStatus, LoginCommand, MembershipRequestCreate, OrganizationMembershipRequestCreate, RegistrationCommand
from hc_data_platform.security.access_postgres import PostgresAccessRepository
from hc_data_platform.security.access_repository import InMemoryAccessRepository
from hc_data_platform.security.access_service import AccessService
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.capabilities import KNOWN_BUSINESS_CAPABILITIES, PROJECT_MEMBER_ROLES
from hc_data_platform.security.governance import AdministratorChange, MembershipRemoval, ProjectRoleChange


@pytest.mark.parametrize("postgres", [False,True])
def test_organization_and_project_administration(postgres):
    marker=uuid4().hex[:9]
    org_a,org_b,project = "org-a-"+marker,"org-b-"+marker,"shared-project"
    projects=((org_a,project),(org_b,project))
    if postgres:
        dsn=os.environ.get("HC_TEST_POSTGRES_DSN")
        if not dsn: pytest.skip("isolated PostgreSQL required")
        asyncio.run(apply_migrations(dsn))
        with psycopg.connect(dsn) as db:
            for org,p in projects:
                db.execute("INSERT INTO registry.organization_projects(organization_id,project_id,display_name) VALUES(%s,%s,%s)",(org,p,p))
                db.execute("INSERT INTO access_control.organization_join_codes(organization_id,display_name,join_code) VALUES (%s,%s,%s)",(org,org,"code-"+org))
        repo=PostgresAccessRepository.from_dsn(dsn)
    else: repo=InMemoryAccessRepository(organization_projects=projects)
    service=AccessService(repo)
    admin=AuthContext(subject_id="system-admin",project_ids=frozenset(),region_codes=frozenset(),capabilities=frozenset({"platform.admin"}))
    password="Organization test passphrase 2026!"
    def account(tag):
        username=tag+"-"+marker
        service.register(RegistrationCommand(username=username,password=password),request_id=uuid4().hex)
        token=service.login(LoginCommand(username=username,password=password),request_id=uuid4().hex).access_token
        return lambda:service.authenticate_access_token(token)
    account("bootstrap")
    lead=account("org-lead")
    member=account("member")
    def appointment(by,who,org,project_id=None,enabled=True):
        return service.change_administrator(by,who.subject_id,AdministratorChange(
            organization_id=org,project_id=project_id,enabled=enabled,reason="scope test"),uuid4().hex)
    def request_project(who,org):
        return service.create_membership_request(auth=who,organization_id=org,project_id=project,
            command=MembershipRequestCreate(reason="project"),idempotency_key=uuid4().hex,request_id=uuid4().hex)
    def decide(which,who,item,**extra):
        return which(auth=who,access_request_id=item.request_id,target_status=AccessRequestStatus.APPROVED,
            command=AccessDecisionCommand(reason="test"),idempotency_key=uuid4().hex,request_id=uuid4().hex,**extra)
    with pytest.raises(ProblemException) as no_org: request_project(member(),org_a)
    assert no_org.value.problem.code=="ORGANIZATION_MEMBERSHIP_REQUIRED"
    appointment(admin,lead(),org_a)
    assert {o.organization_id for o in service.governance_directory(lead()).organizations}=={org_a}
    assert lead().has_capability("access.manage",project,org_a)
    assert not lead().has_capability("access.manage",project,org_b)
    with pytest.raises(ProblemException): appointment(lead(),member(),org_a)
    with pytest.raises(ProblemException): service.governance_members(lead(),org_b,project,0)
    org_request=service.create_organization_membership_request(auth=member(),
        command=OrganizationMembershipRequestCreate(organization_id_or_join_code=org_a,reason="join"),idempotency_key=uuid4().hex,request_id=uuid4().hex)
    assert org_request.request_id in [r.request_id for r in service.governance_inbox(lead(),org_a,None,0)]
    decide(service.decide_organization_membership_request,lead(),org_request)
    assert org_a in member().organization_ids and org_b not in member().organization_ids
    def unassigned():
        return {row.principal_id for row in service.governance_members(lead(), org_a, None, 0, unassigned_only=True)}
    assert member().subject_id in unassigned()
    assert project not in member().project_ids
    assert org_a in lead().managed_organization_ids
    assert lead().has_organization_capability(org_a, "robot.manage")
    with pytest.raises(ProblemException): appointment(lead(),member(),org_a,project)
    def assign(by, target, org, key):
        return service.assign_project_member(by, org, project, target.subject_id, key, uuid4().hex)

    def candidates(by, org, target=None):
        return [row.project_id for row in service.joinable_projects(by, org, target.subject_id if target else None)]

    assert candidates(member(), org_a) == [project]
    with pytest.raises(ProblemException): candidates(member(), org_b)
    with pytest.raises(ProblemException): candidates(member(), org_a, lead())
    with pytest.raises(ProblemException): assign(member(), lead(), org_a, uuid4().hex)
    with pytest.raises(ProblemException): assign(lead(), member(), org_b, uuid4().hex)
    join=request_project(member(),org_a)
    assert candidates(member(),org_a) == []
    assert candidates(lead(),org_a,member()) == [project]
    replay = uuid4().hex
    assign(lead(), member(), org_a, replay)
    assign(lead(), member(), org_a, replay)
    assert candidates(lead(),org_a,member()) == []
    assert service.get_membership_request(auth=member(),organization_id=org_a,project_id=project,
        access_request_id=join.request_id).status is AccessRequestStatus.APPROVED
    assert not member().has_capability("access.manage",project,org_a)
    appointment(lead(),member(),org_a,project)
    for grants in PROJECT_MEMBER_ROLES.values():
        assert grants <= member().effective_capabilities(project, org_a)
    assert not member().has_capability("storage.lifecycle.manage", project, org_a)
    assert not member().has_capability("upload.manage", project, org_b)
    assert member().subject_id not in unassigned()
    assert lead().subject_id in unassigned()
    with pytest.raises(ProblemException):
        service.governance_members(member(), org_a, None, 0, unassigned_only=True)
    assert member().has_capability("access.manage",project,org_a)
    assert not member().has_capability("access.manage",project,org_b)
    with pytest.raises(ProblemException): service.governance_members(member(),org_a,None,0)
    assert member().subject_id in [m.principal_id for m in service.governance_members(member(),org_a,project,0)]
    other = account("second-project-admin")
    other_org = service.create_organization_membership_request(auth=other(),command=OrganizationMembershipRequestCreate(
        organization_id_or_join_code=org_a,reason="join"),idempotency_key=uuid4().hex,request_id=uuid4().hex)
    decide(service.decide_organization_membership_request,lead(),other_org)
    assign(lead(),other(),org_a,uuid4().hex)
    other_project = next(item for item in service.list_membership_requests(auth=lead(),organization_id=org_a,project_id=project).items
                         if item.requester_id == other().subject_id)
    assert other_project.reason == "管理员直接添加至项目"
    assert other_project.decided_by == lead().subject_id
    assert other().subject_id not in unassigned()
    appointment(lead(),other(),org_a,project)
    def remove(by, target, project_id=None):
        service.remove_managed_member(by,target.subject_id,MembershipRemoval(organization_id=org_a,
            project_id=project_id,reason="scope test"),uuid4().hex)
    with pytest.raises(ProblemException): remove(member(),other(),project)
    with pytest.raises(ProblemException): remove(lead(),lead())
    with pytest.raises(ProblemException): service.decide_membership_request(auth=member(),organization_id=org_a,
        project_id=project,access_request_id=other_project.request_id,target_status=AccessRequestStatus.REVOKED,
        command=AccessDecisionCommand(reason="scope test"),idempotency_key=uuid4().hex,request_id=uuid4().hex)
    info=service.managed_member_detail(lead(),other().subject_id,org_a,project)
    assert info.username.startswith("second-project-admin") and info.administrator
    assert "access.manage" in info.effective_permissions and not info.assigned_roles
    assert not service.managed_member_detail(member(),other().subject_id,org_a,project).can_set_roles
    def set_roles(by, target, roles, expected=()):
        return service.set_managed_roles(by, target.subject_id, ProjectRoleChange(organization_id=org_a,
            project_id=project, roles=set(roles),expected_roles=set(expected),reason="岗位调整测试"),uuid4().hex)
    with pytest.raises(ProblemException): set_roles(member(),other(),["role.reviewer"])
    with pytest.raises(ProblemException): set_roles(member(),member(),["role.reviewer"])
    with pytest.raises(ProblemException): service.managed_member_detail(member(),other().subject_id,org_a,None)
    with pytest.raises(ProblemException): service.managed_member_detail(lead(),other().subject_id,org_b,project)
    appointment(lead(),other(),org_a,project,False)
    from pydantic import ValidationError
    from hc_data_platform.security.governance import ProjectPermissionChange

    def set_permissions(by, permissions, expected=()):
        command = ProjectPermissionChange(organization_id=org_a, project_id=project,
            permissions=set(permissions), expected_permissions=set(expected), reason="专项权限调整")
        return service.set_managed_permissions(by, other().subject_id, command, uuid4().hex)

    grants = {"export.create", "training.create", "teleoperation.connect/robot-1", "annotation.save"}
    for invalid in ("teleoperation.connect", "role.project_admin", "platform.admin", "training.create/robot-1"):
        with pytest.raises(ValidationError):
            set_permissions(lead(), [invalid])
    with pytest.raises(ProblemException):
        set_permissions(member(), grants)
    granted = set_permissions(lead(), grants)
    assert set(granted.assigned_permissions) == grants
    assert grants <= set(granted.effective_permissions)
    assert set_permissions(lead(), grants).assigned_permissions == sorted(grants)
    organization_detail = service.managed_member_detail(lead(), other().subject_id, org_a, None)
    assert "teleoperation.connect/robot-1" in organization_detail.effective_permissions
    assert other().has_organization_capability(org_a, "teleoperation.connect/robot-1")
    assert not other().has_organization_capability(org_a, "teleoperation.connect/robot-2")
    with pytest.raises(ProblemException) as stale:
        set_permissions(lead(), {"export.create"})
    assert stale.value.problem.code == "MEMBER_PERMISSIONS_CHANGED"
    assert grants <= other().effective_capabilities(project, org_a)
    assert not other().has_capability("training.create", project, org_b)
    assert not other().has_capability("teleoperation.connect", project, org_a)
    with pytest.raises(ProblemException):
        set_permissions(member(), set(), grants)
    reduced = set_permissions(member(), grants - {"annotation.save"}, grants)
    assert "annotation.save" not in reduced.assigned_permissions
    assert "training.create" not in reduced.assignable_permissions
    assert set_roles(member(),other(),["role.reviewer"]).assigned_roles==["role.reviewer"]
    assert other().has_capability("training.create", project, org_a)
    assert other().has_capability("annotation.review",project,org_a)
    assert not other().has_capability("annotation.review",project,org_b)
    assert set_roles(member(),other(),["role.reviewer"]).assigned_roles==["role.reviewer"]
    with pytest.raises(ProblemException): set_roles(member(),other(),["role.publisher"])
    from hc_data_platform.security.access_models import CapabilityRequestCreate
    approved=service.create_capability_request(auth=other(),organization_id=org_a,project_id=project,
        command=CapabilityRequestCreate(capability_keys=("role.publisher",),reason="publish"),idempotency_key=uuid4().hex,request_id=uuid4().hex)
    decide(service.decide_capability_request,lead(),approved,organization_id=org_a,project_id=project)
    granted=service.managed_member_detail(member(),other().subject_id,org_a,project)
    assert "dataset_version.publish" in granted.effective_permissions
    assert any(g.source_request_id==approved.request_id and g.can_revoke for g in granted.approved_grants)
    service.decide_capability_request(auth=member(),organization_id=org_a,project_id=project,access_request_id=approved.request_id,
        target_status=AccessRequestStatus.REVOKED,command=AccessDecisionCommand(reason="revoke"),idempotency_key=uuid4().hex,request_id=uuid4().hex)
    assert not other().has_capability("dataset_version.publish",project,org_a)
    set_roles(lead(),other(),["role.observer"],["role.reviewer"])
    assert not other().has_capability("annotation.review",project,org_a)
    remove(member(),other(),project)
    assert not other().has_capability("training.create", project, org_a)
    assert other().subject_id not in [m.principal_id for m in service.governance_members(lead(),org_a,project,0)]
    assert other().subject_id in [m.principal_id for m in service.governance_members(lead(),org_a,None,0)]
    joined_again=request_project(other(),org_a)
    decide(service.decide_membership_request,lead(),joined_again,organization_id=org_a,project_id=project)
    assert not service.managed_member_detail(lead(),other().subject_id,org_a,project).assigned_roles
    remove(lead(),other())
    with pytest.raises(ProblemException): request_project(other(),org_a)
    appointment(lead(),member(),org_a,project,False)
    assert not member().has_capability("access.manage",project,org_a)
    if postgres:
        empty = "empty-"+marker
        with psycopg.connect(dsn) as db:
            db.execute("INSERT INTO access_control.organization_join_codes(organization_id,display_name,join_code) VALUES(%s,%s,%s)",(empty,empty,"code-"+empty))
        appointment(admin,other(),empty)
        assert other().has_organization_capability(empty,"robot.manage")
        assert not other().has_organization_capability(org_b,"robot.manage")
        assert not any(o==empty for o,_,_ in other().organization_scope_triples)
        assert empty in {o.organization_id for o in service.governance_directory(other()).organizations}
        appointment(admin,other(),empty,enabled=False)
        assert not other().has_organization_capability(empty,"robot.manage")
    # Organization authority is inherited even without an explicit project membership.
    assert KNOWN_BUSINESS_CAPABILITIES <= lead().effective_capabilities(project, org_a)
    assert not lead().has_capability("dashboard.read", project, org_b)
    assert not lead().is_platform_admin
    assert not member().has_capability("quality.manage", project, org_a)
    appointment(admin, member(), org_a)
    assert service.managed_member_detail(lead(), member().subject_id, org_a, project).can_remove
    appointment(lead(), member(), org_a, project)
    set_roles(lead(), member(), ["role.reviewer"])
    remove(lead(), member(), project)
    assert member().has_capability("dashboard.read", project, org_a)
    assert not member().has_capability("dashboard.read", project, org_b)
    assert project in candidates(lead(), org_a, lead())
    assign(lead(), lead(), org_a, uuid4().hex)
    detail = service.managed_member_detail(lead(), lead().subject_id, org_a, project)
    assert detail.can_set_roles and detail.can_change_administrator and detail.can_remove
    assert KNOWN_BUSINESS_CAPABILITIES <= set(detail.effective_permissions)
    assert set_roles(lead(), lead(), ["role.reviewer"]).assigned_roles == ["role.reviewer"]
    assert appointment(lead(), lead(), org_a, project).administrator
    assert not appointment(lead(), lead(), org_a, project, False).administrator
    grant = service.create_capability_request(auth=lead(), organization_id=org_a, project_id=project,
        command=CapabilityRequestCreate(capability_keys=("role.publisher",), reason="project role"),
        idempotency_key=uuid4().hex, request_id=uuid4().hex)
    decide(service.decide_capability_request, lead(), grant, organization_id=org_a, project_id=project)
    assert any(g.can_revoke for g in service.managed_member_detail(lead(), lead().subject_id, org_a, project).approved_grants)
    remove(lead(), lead(), project)
    assert lead().subject_id in unassigned()
    assert lead().has_capability("dashboard.read", project, org_a)
    appointment(admin,lead(),org_a,enabled=False)
    assert not lead().has_capability("access.manage",project,org_a)
    assert not lead().has_capability("dashboard.read",project,org_a)
    # Platform administrators can operate on their own project membership as well.
    from dataclasses import replace
    platform = replace(lead(), capabilities=frozenset({"platform.admin"}))
    assign(platform, platform, org_a, uuid4().hex)
    assert appointment(platform, platform, org_a, project).administrator
    assert service.managed_member_detail(platform, platform.subject_id, org_a, project).can_set_roles
    remove(platform, platform, project)
