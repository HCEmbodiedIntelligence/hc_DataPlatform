import asyncio
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.context import RequestContext, bind_request_context, reset_request_context
from hc_data_platform.core.migrations import apply_migrations
from hc_data_platform.security.access_postgres import PostgresAccessRepository
from hc_data_platform.security.access_repository import InMemoryAccessRepository
from hc_data_platform.security.access_service import AccessService
from hc_data_platform.security.auth import AuthContext
from tests.security.test_authorization_pairs import (
    ProjectAdminVerifier,
    _approve_membership,
    _headers,
    _register,
    _request_membership,
)


def test_project_only_lookup_uses_the_verified_organization():
    project = "shared"
    user = AuthContext(
        subject_id="member", project_ids=frozenset({project}), region_codes=frozenset(),
        organization_ids=frozenset({"first"}),
        organization_scope_triples=frozenset({("first", project, None)}),
        organization_scoped_capabilities=frozenset({("first", project, "role.publisher")}),
    )
    assert user.has_capability("dataset_version.publish", project)
    both = replace(
        user, organization_ids=frozenset({"first", "second"}),
        organization_scope_triples=user.organization_scope_triples | {("second", project, None)},
        scoped_capabilities=frozenset({(project, "role.publisher")}),
        organization_scoped_capabilities=user.organization_scoped_capabilities | {
            ("second", project, "role.observer"),
        },
    )
    assert not both.has_capability("dataset_version.publish", project)
    token = bind_request_context(RequestContext(organization_id="second"))
    try:
        assert both.has_capability("dataset.read", project)
        assert not both.has_capability("dataset_version.publish", project)
        assert not user.has_capability("dataset.read", project)
    finally:
        reset_request_context(token)


def test_upgrade_transfer_dual_roles_and_temporary_grants(monkeypatch):
    repository = InMemoryAccessRepository(organization_projects=(("organization-a", "project-a"),))
    service = AccessService(repository)
    app = create_app(
        settings=Settings(environment="test", runtime_backend="memory", _env_file=None),
        jwt_verifier=ProjectAdminVerifier(),
        access_service=service,
    )
    root = "/api/v1/organizations/organization-a/projects/project-a/capability-requests"
    with TestClient(app) as client:
        _register(client, "initial-" + uuid4().hex[:12])
        token = _register(client, "member-" + uuid4().hex[:12])
        membership = _request_membership(client, token, "role")
        _approve_membership(client, membership["request_id"], "role")

        def apply(key, **fields):
            response = client.post(
                root,
                headers=_headers(token, uuid4().hex),
                json={"capability_keys": [key], **fields},
            )
            assert response.status_code == 201, response.text
            return response.json()["request_id"]

        def decide(request_id, actor="external-admin-a", action="approve"):
            return client.post(
                f"{root}/{request_id}:{action}", headers=_headers(actor, uuid4().hex), json={}
            )

        def has(key):
            return service.authenticate_access_token(token).has_capability(
                key, "project-a", "organization-a"
            )

        first = apply("role.collector", resource_id="task-one")
        assert decide(first).status_code == 200
        assert (
            has("upload.manage/task-one")
            and not has("upload.manage")
            and not has("export.download")
        )
        second = apply("role.reviewer", replaces_request_id=first)
        preview = next(
            row
            for row in client.get(root, headers=_headers(token)).json()["items"]
            if row["request_id"] == second
        )
        assert "upload.manage/task-one" in preview["project_grants_removed"]
        assert "annotation.review" in preview["project_grants_added"]
        assert "dataset.read" not in preview["project_grants_removed"]
        assert decide(second).status_code == 200
        assert has("annotation.review") and not has("upload.manage/task-one")
        assert client.get(f"{root}/{first}", headers=_headers(token)).json()["status"] == "REVOKED"
        manager = apply("role.project_admin")
        assert decide(manager).status_code == 403
        assert decide(manager, "global-admin").status_code == 200
        assert has("access.manage") and has("annotation.review")
        upgrade = apply("role.publisher")
        assert decide(upgrade, token).status_code == 403
        deadline = datetime.now(timezone.utc) + timedelta(minutes=3)
        scoped = apply("export.download", resource_id="version:v1", expires_at=deadline.isoformat())
        assert decide(scoped).status_code == 403
        delegate = replace(
            ProjectAdminVerifier().verify("external-admin-a"),
            subject_id="delivery-approver",
            capabilities=frozenset({"access.download_approval.manage"}),
        )
        inbox = service.list_capability_requests(
            auth=delegate, organization_id="organization-a", project_id="project-a"
        )
        assert {item.request_id for item in inbox.items} == {scoped}
        from hc_data_platform.core.errors import ProblemException
        from hc_data_platform.security.access_models import (
            AccessDecisionCommand,
            AccessRequestStatus,
        )

        def delegated(request_id):
            return service.decide_capability_request(
                auth=delegate,
                organization_id="organization-a",
                project_id="project-a",
                access_request_id=request_id,
                target_status=AccessRequestStatus.APPROVED,
                command=AccessDecisionCommand(),
                request_id=uuid4().hex,
                idempotency_key=uuid4().hex,
            )

        with pytest.raises(ProblemException):
            delegated(upgrade)
        assert delegated(scoped).status is AccessRequestStatus.APPROVED
        assert has("export.download/version:v1") and not has("export.download")
        assert not has("export.download/version:v2")
        monkeypatch.setattr(repository, "_clock", lambda: deadline)
        assert not has("export.download/version:v1") and has("annotation.review")
        assert decide(second, action="revoke").status_code == 200
        assert has("annotation.review") and has("access.manage")  # inherited from the project administrator role
        assert decide(manager, "global-admin", action="revoke").status_code == 200
        assert not has("annotation.review") and not has("access.manage")


@pytest.mark.integration
def test_postgres_role_terms_and_expiry():
    from hc_data_platform.security.access_models import (
        AccessDecisionCommand,
        AccessRequestStatus,
        CapabilityRequestCreate,
        LoginCommand,
        MembershipRequestCreate,
        RegistrationCommand,
    )

    dsn = os.environ.get("HC_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("An isolated test database is required")
    asyncio.run(apply_migrations(dsn))
    org, project = "organization-a", "project-a"
    service = AccessService(PostgresAccessRepository.from_dsn(dsn))
    with psycopg.connect(dsn) as connection:
        connection.execute(
            """INSERT INTO registry.organization_projects(organization_id, project_id, display_name)
            VALUES (%s, %s, 'Role test') ON CONFLICT DO NOTHING""",
            (org, project),
        )
    password = "Role lifecycle test passphrase!"

    def account():
        username = "member-" + uuid4().hex[:12]
        service.register(
            RegistrationCommand(username=username, password=password), request_id=uuid4().hex
        )
        return service.login(
            LoginCommand(username=username, password=password), request_id=uuid4().hex
        ).access_token

    account()
    token = account()

    def member():
        return service.authenticate_access_token(token)

    admin = ProjectAdminVerifier().verify("global-admin")

    def operation():
        return dict(
            organization_id=org,
            project_id=project,
            request_id=uuid4().hex,
            idempotency_key=uuid4().hex,
        )

    decision = dict(
        auth=admin, target_status=AccessRequestStatus.APPROVED, command=AccessDecisionCommand()
    )
    with psycopg.connect(dsn) as db:
        db.execute("""INSERT INTO access_control.organization_memberships(principal_id, organization_id, activated_by)
                   VALUES (%s,%s,'test-fixture')""",(member().subject_id,org))
    membership = service.create_membership_request(
        auth=member(), command=MembershipRequestCreate(), **operation()
    )
    service.decide_membership_request(
        access_request_id=membership.request_id, **decision, **operation()
    )

    def grant(key, **fields):
        item = service.create_capability_request(
            auth=member(),
            command=CapabilityRequestCreate(capability_keys=[key], **fields),
            **operation(),
        )
        return service.decide_capability_request(
            access_request_id=item.request_id, **decision, **operation()
        )

    first = grant("role.collector", resource_id="task-a")
    grant("role.annotator", replaces_request_id=first.request_id)
    scoped = grant(
        "export.download",
        resource_id="version:one",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    delegate = replace(
        admin,
        subject_id="delivery-reviewer",
        capabilities=frozenset({"access.download_approval.manage"}),
    )
    visible = service.list_capability_requests(
        auth=delegate, organization_id=org, project_id=project
    )
    assert scoped.request_id in {item.request_id for item in visible.items}
    assert first.request_id not in {item.request_id for item in visible.items}
    assert (
        service.get_capability_request(
            auth=delegate,
            organization_id=org,
            project_id=project,
            access_request_id=scoped.request_id,
        ).request_id
        == scoped.request_id
    )
    assert member().has_capability("annotation.submit", project, org)
    assert not member().has_capability("upload.manage/task-a", project, org)
    assert member().has_capability("export.download/version:one", project, org)
    assert not member().has_capability("export.download", project, org)
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "UPDATE access_control.capability_grants SET expires_at=now() WHERE source_request_id=%s",
            (scoped.request_id,),
        )
    assert not member().has_capability("export.download/version:one", project, org)


@pytest.mark.integration
def test_assigned_preview_is_bound_to_member_and_version():
    from types import SimpleNamespace

    from hc_data_platform.core.errors import ProblemException
    from hc_data_platform.episode_store.postgres import PostgresEpisodeStore
    from hc_data_platform.episode_store.preview_access import authorize_episode_preview
    from hc_data_platform.security.auth import AuthContext

    dsn = os.environ.get("HC_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("An isolated test database is required")
    asyncio.run(apply_migrations(dsn))
    org, project, region = "organization-a", "project-a", "cn-hz"
    task = "preview-" + uuid4().hex
    dataset = "dataset-" + uuid4().hex
    actor = AuthContext(
        subject_id="annotator",
        capabilities=frozenset({"role.annotator"}),
        organization_ids=frozenset({org}),
        scope_pairs=frozenset({(project, region)}),
        project_ids=frozenset({project}),
        region_codes=frozenset({region}),
        organization_scope_triples=frozenset({(org, project, region)}),
    )
    packages = SimpleNamespace(repository=PostgresEpisodeStore(lambda: psycopg.connect(dsn)))

    def preview(who, **selector):
        authorize_episode_preview(packages, who, org, project, region, dataset, **selector)

    with psycopg.connect(dsn) as db:
        db.execute(
            """SELECT set_config('app.organization_id',%s,true),
            set_config('app.project_id',%s,true),set_config('app.region_code',%s,true)""",
            (org, project, region),
        )
        db.execute(
            """INSERT INTO annotation.annotation_tasks
            (organization_id, project_id, region_code, task_id, dataset_id, dataset_version, rollout_id,
             assignee_id, etag, base_lance_version, status) VALUES (%s,%s,%s,%s,%s,1,%s,%s,%s,1,'DRAFT')""",
            (org, project, region, task, dataset, task, actor.subject_id, task),
        )
        db.execute(
            """INSERT INTO annotation.annotation_revisions
            (task_id, revision, author_id, client_mutation_id, base_lance_version)
            VALUES (%s, 0, %s, %s, 1)""",
            (task, actor.subject_id, task),
        )
    preview(actor, rollout_id=task, dataset_version=1)
    preview(replace(actor, capabilities=frozenset({"role.observer"})), rollout_id=task, dataset_version=1)
    for who, selector in (
        (replace(actor, subject_id="other"), dict(rollout_id=task, dataset_version=1)),
        (actor, dict(rollout_id=task, dataset_version=2)),
        (actor, dict(episode_id=task, version_id="another-fixed-version")),
        (
            replace(actor, capabilities=frozenset({"role.reviewer"})),
            dict(rollout_id=task, dataset_version=1),
        ),
    ):
        with pytest.raises(ProblemException, match="CAPABILITY_REQUIRED"):
            preview(who, **selector)


def test_role_matrix_task_scope_and_project_isolation():
    from hc_data_platform.security.auth import AuthContext

    org = "test-org"
    roles = {
        "observer": (
            {"dashboard.read", "collection_task.read", "dataset.read", "dataset_version.read",
             "episode.read", "annotation_task.read", "annotation.read", "annotation_set.read",
             "cleaning.read", "data_schema.read", "ingest_source.read", "upload.read",
             "export.read", "training.read", "manual_issue.read"},
            {"upload.manage", "ingest.import", "export.create", "annotation_task.claim",
             "annotation.edit", "annotation.save", "annotation.submit", "annotation.review"},
        ),
        "collector": (
            {"ingest_source.read"},
            {"upload.manage", "upload.read", "annotation.review"},
        ),
        "annotator": (
            {"annotation_task.claim", "annotation.edit", "annotation.submit"},
            {"annotation.review"},
        ),
        "reviewer": (
            {"annotation.review", "dataset_version.review"},
            {"annotation.edit", "dataset_version.publish"},
        ),
        "publisher": ({"dataset_version.publish"}, {"annotation.review", "data_schema.publish"}),
        "project_admin": (
            {"collection_task.manage", "annotation_task.assign", "access.manage", "audit.read",
             "annotation.review", "dataset_version.publish", "upload.manage"},
            {"organization.admin", "robot.manage", "storage.lifecycle.manage"},
        ),
    }
    sensitive = {
        "export.download",
        "dataset_version.download_raw",
        "training.create",
        "collection_task.delete",
        "dataset.delete",
        "dataset_version.delete",
        "platform.admin",
        "teleoperation.connect",
    }
    for role, (allowed, denied) in roles.items():
        actor = AuthContext(
            subject_id="user",
            project_ids=frozenset({"a", "b"}),
            region_codes=frozenset({"eu"}),
            organization_ids=frozenset({org}),
            scope_pairs=frozenset({("a", "eu"), ("b", "eu")}),
            organization_scope_triples=frozenset({(org, "a", "eu"), (org, "b", "eu")}),
            organization_scoped_capabilities=frozenset(
                {(org, "a", "role." + role), (org, "b", "role.observer")}
            ),
        )
        assert allowed <= actor.effective_capabilities("a", org)
        if role == "observer":
            assert actor.effective_capabilities("a", org) == allowed | {"role.observer"}
            assert not actor.has_capability("episode.read", "a", "other-org")
        assert (denied | sensitive).isdisjoint(actor.effective_capabilities("a", org))
        assert not actor.has_capability("access.manage", "b", org)
        assert not actor.has_capability("annotation.review", "b", org)
