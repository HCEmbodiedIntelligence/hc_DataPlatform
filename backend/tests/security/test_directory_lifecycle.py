import asyncio
import os
from uuid import uuid4

import psycopg
import pytest
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.core.migrations import apply_migrations
from hc_data_platform.platform_ops.projects import PlatformProjectCreate
from hc_data_platform.security.access_models import (
    LoginCommand,
    RegistrationCommand,
    OrganizationMembershipRequestCreate,
)
from hc_data_platform.security.access_postgres import PostgresAccessRepository
from hc_data_platform.security.access_repository import InMemoryAccessRepository
from hc_data_platform.security.access_service import AccessService
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.directory_changes import DirectoryChange, OrganizationCreate


@pytest.mark.parametrize("postgres", [False, True])
def test_scoped_directory_lifecycle(postgres):
    if postgres:
        dsn = os.environ.get("HC_TEST_POSTGRES_DSN")
        if not dsn:
            pytest.skip("isolated PostgreSQL required")
        asyncio.run(apply_migrations(dsn))
        repository = PostgresAccessRepository.from_dsn(dsn)
    else:
        repository = InMemoryAccessRepository()
    service = AccessService(repository)
    nonce = lambda: uuid4().hex

    def account():
        user = "org-" + nonce()[:16]
        password = "Test account password 2026!"
        service.register(RegistrationCommand(username=user, password=password), request_id=nonce())
        token = service.login(
            LoginCommand(username=user, password=password), request_id=nonce()
        ).access_token
        return lambda: service.authenticate_access_token(token)

    account()  # First registration bootstraps the installation.
    owner, other = account(), account()
    admin_actor = "platform-qa"
    if postgres:
        admin_actor = account()().subject_id
        with psycopg.connect(dsn) as db:
            db.execute(
                "INSERT INTO access_control.platform_capability_grants(principal_id,capability_key,granted_by) VALUES (%s,'platform.admin','test') ON CONFLICT DO NOTHING",
                (admin_actor,),
            )
    admin = AuthContext(
        subject_id=admin_actor,
        project_ids=frozenset(),
        region_codes=frozenset(),
        capabilities=frozenset({"platform.admin"}),
    )
    assert not owner().is_platform_admin
    creation_key = nonce()

    def create(actor, name, key):
        return service.create_personal_organization(
            actor(), OrganizationCreate(name=name), key, nonce()
        )

    org = create(owner, "研发", creation_key).organization_id
    assert create(owner, "研发", creation_key).organization_id == org
    sibling = create(other, "独立组织", nonce()).organization_id

    def command(actor, op, **kwargs):
        key = kwargs.pop("key", nonce())
        service.change_directory(
            actor, DirectoryChange(organization_id=org, operation=op, **kwargs), key, nonce()
        )

    def directory(actor):
        return service.governance_directory(actor)

    def code():
        return next(
            o.join_code for o in directory(owner()).organizations if o.organization_id == org
        )

    assert code() is None
    command(owner(), "generate_code", key=creation_key)
    old = code()
    assert old
    command(owner(), "generate_code", key=creation_key)
    assert code() == old
    command(owner(), "generate_code")
    assert code() and code() != old

    def apply(value):
        return service.create_organization_membership_request(
            auth=other(),
            command=OrganizationMembershipRequestCreate(
                organization_id_or_join_code=value, reason="协作"
            ),
            idempotency_key=nonce(),
            request_id=nonce(),
        )

    with pytest.raises(ProblemException):
        apply(old)
    active = code()
    assert apply(active).organization_id == org
    command(owner(), "revoke_code")
    assert code() is None
    with pytest.raises(ProblemException):
        apply(active)
    with pytest.raises(ProblemException):
        command(other(), "rename", name="不允许")
    command(owner(), "rename", name="研发组")
    for actor, target in [(owner(), org), (other(), sibling)]:
        service.create_managed_project(
            actor,
            PlatformProjectCreate(organization_id=target, project_id="shared", project_name="项目"),
            nonce(),
        )
    command(owner(), "rename", project_id="shared", name="项目甲")
    assert (
        next(p.project_name for p in directory(owner()).projects if p.organization_id == org)
        == "项目甲"
    )
    with pytest.raises(ProblemException):
        command(owner(), "delete", confirmation=org, retention_days=0)
    command(owner(), "archive", project_id="shared", confirmation="shared")
    assert not directory(owner()).projects
    assert any(p.organization_id == sibling for p in directory(other()).projects)
    command(owner(), "delete", project_id="shared", confirmation="shared", retention_days=30)
    command(admin, "archive", confirmation=org)
    assert not directory(owner()).organizations
    assert any(item.organization_id == org for item in service.archived_directory(admin))
    command(admin, "delete", confirmation=org, retention_days=0)
    assert not any(
        item.organization_id == org and item.status == "ARCHIVED"
        for item in service.archived_directory(admin)
    )
    assert owner().subject_id and other().subject_id
    if postgres:
        with psycopg.connect(dsn) as db:
            assert db.execute(
                "SELECT purge_after <= now() FROM access_control.retired_scopes WHERE organization_id=%s AND project_id=''",
                (org,),
            ).fetchone() == (True,)
            assert db.execute(
                "SELECT purge_after-retired_at=interval '30 days' FROM access_control.retired_scopes WHERE organization_id=%s AND project_id='shared'",
                (org,),
            ).fetchone() == (True,)
