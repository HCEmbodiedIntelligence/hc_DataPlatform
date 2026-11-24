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
from hc_data_platform.security.governance import AdministratorChange


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
    with pytest.raises(ProblemException): appointment(lead(),member(),org_a,project)
    join=request_project(member(),org_a)
    decide(service.decide_membership_request,lead(),join,organization_id=org_a,project_id=project)
    appointment(lead(),member(),org_a,project)
    assert member().has_capability("access.manage",project,org_a)
    assert not member().has_capability("access.manage",project,org_b)
    with pytest.raises(ProblemException): service.governance_members(member(),org_a,None,0)
    assert member().subject_id in [m.principal_id for m in service.governance_members(member(),org_a,project,0)]
    appointment(lead(),member(),org_a,project,False)
    assert not member().has_capability("access.manage",project,org_a)
    appointment(admin,lead(),org_a,enabled=False)
    assert not lead().has_capability("access.manage",project,org_a)
