from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.security.access_models import LoginCommand, RegistrationCommand
from hc_data_platform.security.access_repository import InMemoryAccessRepository
from hc_data_platform.security.access_service import AccessService
from hc_data_platform.security.admin_accounts import (
    AdminAccountService,
    ManagedAccountCreate,
    ManagedAccountPasswordReset,
    PlatformAccountRole,
)
from hc_data_platform.security.auth import AuthContext, Role
from hc_data_platform.security.passwords import PasswordHasher, PasswordPolicy, ScryptParameters

_ADMIN_PASSWORD = "admin-safe-password"
_USER_PASSWORD = "user-safe-password"


def _stack(
    *, organization_projects: tuple[tuple[str, str], ...] = ()
) -> tuple[TestClient, InMemoryAccessRepository, AccessService, AdminAccountService]:
    repository = InMemoryAccessRepository(organization_projects=organization_projects)
    hasher = PasswordHasher(ScryptParameters(n=2**10))
    policy = PasswordPolicy(min_length=6, max_length=128)
    access = AccessService(repository, password_hasher=hasher, password_policy=policy)
    admin = AdminAccountService(repository, password_hasher=hasher, password_policy=policy)
    principal = access.register(
        RegistrationCommand(username="hc-admin", password=_ADMIN_PASSWORD),
        request_id="register-admin",
    ).principal
    repository.grant_platform_admin_for_test(principal.principal_id)
    client = TestClient(
        create_app(
            settings=Settings(environment="test", runtime_backend="memory", _env_file=None),
            access_service=access,
            admin_account_service=admin,
        )
    )
    return client, repository, access, admin


def _admin_session(client: TestClient) -> tuple[str, str]:
    response = client.post(
        "/api/v1/auth/sessions",
        json={"username": "hc-admin", "password": _ADMIN_PASSWORD},
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"], response.json()["principal"]["principal_id"]


def _headers(token: str, etag: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        **({"If-Match": etag} if etag is not None else {}),
    }


def test_platform_admin_can_manage_account_lifecycle_without_exposing_credentials() -> None:
    client, _, _, _ = _stack()
    token, _ = _admin_session(client)

    created = client.post(
        "/api/v1/platform/accounts",
        headers=_headers(token),
        json={
            "username": "robot-operator",
            "display_name": "机器人操作员",
            "password": _USER_PASSWORD,
            "recovery_email": "operator@example.com",
            "platform_role": "USER",
        },
    )
    assert created.status_code == 201, created.text
    account = created.json()
    assert account["recovery_email_hint"] == "op******@example.com"
    assert "password" not in account
    assert "password_hash" not in account
    assert "operator@example.com" not in created.text

    listed = client.get(
        "/api/v1/platform/accounts",
        headers=_headers(token),
        params={"query": "robot-operator", "page": 1, "page_size": 20},
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert listed.headers["cache-control"] == "private, no-store"

    user_session = client.post(
        "/api/v1/auth/sessions",
        json={"username": "robot-operator", "password": _USER_PASSWORD},
    ).json()["access_token"]
    disabled = client.post(
        f"/api/v1/platform/accounts/{account['principal_id']}:disable",
        headers=_headers(token, account["etag"]),
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["state"] == "DISABLED"
    assert (
        client.get("/api/v1/auth/session/bootstrap", headers=_headers(user_session)).status_code
        == 401
    )

    enabled = client.post(
        f"/api/v1/platform/accounts/{account['principal_id']}:enable",
        headers=_headers(token, disabled.json()["etag"]),
    )
    assert enabled.status_code == 200, enabled.text
    reset = client.post(
        f"/api/v1/platform/accounts/{account['principal_id']}:reset-password",
        headers=_headers(token, enabled.json()["etag"]),
        json={"new_password": "new-safe-password", "confirm_password": "new-safe-password"},
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["sessions_revoked"] == 0
    assert (
        client.post(
            "/api/v1/auth/sessions",
            json={"username": "robot-operator", "password": _USER_PASSWORD},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/sessions",
            json={"username": "robot-operator", "password": "new-safe-password"},
        ).status_code
        == 201
    )

    deleted = client.delete(
        f"/api/v1/platform/accounts/{account['principal_id']}",
        headers=_headers(token, reset.json()["account"]["etag"]),
    )
    assert deleted.status_code == 204, deleted.text
    deleted_page = client.get(
        "/api/v1/platform/accounts",
        headers=_headers(token),
        params={"state": "DELETED", "page": 1, "page_size": 20},
    )
    assert deleted_page.status_code == 200
    assert [item["principal_id"] for item in deleted_page.json()["items"]] == [
        account["principal_id"]
    ]


def test_project_administrator_cannot_read_or_manage_platform_accounts() -> None:
    _, _, _, admin = _stack()
    project_admin = AuthContext(
        subject_id="project-admin",
        project_ids=frozenset({"project-a"}),
        region_codes=frozenset(),
        roles=frozenset({Role.ADMIN.value}),
        scope_pairs=frozenset({("project-a", None)}),
    )

    with pytest.raises(ProblemException) as denied:
        admin.list_accounts(
            auth=project_admin,
            query=None,
            state=None,
            role=None,
            page=1,
            page_size=20,
        )
    assert denied.value.problem.code == "PLATFORM_ACCOUNT_READ_REQUIRED"


def test_platform_admin_bootstrap_lists_every_real_project_without_membership() -> None:
    client, repository, _, _ = _stack(
        organization_projects=(("org-a", "project-a"), ("org-b", "project-b"))
    )
    token, principal_id = _admin_session(client)

    bootstrap = client.get("/api/v1/auth/session/bootstrap", headers=_headers(token))

    assert bootstrap.status_code == 200
    assert bootstrap.json()["platform_capabilities"] == [
        "platform.account.manage",
        "platform.account.read",
        "platform.account_security.manage",
        "platform.admin",
    ]
    assert bootstrap.json()["available_scopes"] == [
        {
            "organization_id": "org-a",
            "project_id": "project-a",
            "region_codes": [],
            "project_wide": True,
            "capabilities": [],
        },
        {
            "organization_id": "org-b",
            "project_id": "project-b",
            "region_codes": [],
            "project_wide": True,
            "capabilities": [],
        },
    ]
    assert not any(member_id == principal_id for member_id, _, _ in repository._memberships)


def test_platform_role_revision_revokes_old_session_and_relogin_gets_super_admin() -> None:
    client, repository, _, _ = _stack(organization_projects=(("org-a", "project-a"),))
    admin_token, _ = _admin_session(client)
    created = client.post(
        "/api/v1/platform/accounts",
        headers=_headers(admin_token),
        json={
            "username": "promoted-admin",
            "password": _USER_PASSWORD,
            "platform_role": "USER",
        },
    )
    assert created.status_code == 201
    account = created.json()
    old_session = client.post(
        "/api/v1/auth/sessions",
        json={"username": "promoted-admin", "password": _USER_PASSWORD},
    ).json()
    before_revision = repository.credential_for_principal(account["principal_id"])
    assert before_revision is not None

    promoted = client.post(
        f"/api/v1/platform/accounts/{account['principal_id']}:role",
        headers=_headers(admin_token, account["etag"]),
        json={"platform_role": "PLATFORM_ADMIN"},
    )
    assert promoted.status_code == 200
    after_revision = repository.credential_for_principal(account["principal_id"])
    assert after_revision is not None
    assert after_revision.capability_revision > before_revision.capability_revision
    assert (
        client.get(
            "/api/v1/auth/session/bootstrap",
            headers=_headers(old_session["access_token"]),
        ).status_code
        == 401
    )

    new_session = client.post(
        "/api/v1/auth/sessions",
        json={"username": "promoted-admin", "password": _USER_PASSWORD},
    )
    assert new_session.status_code == 201
    bootstrap = client.get(
        "/api/v1/auth/session/bootstrap",
        headers=_headers(new_session.json()["access_token"]),
    ).json()
    assert "platform.admin" in bootstrap["platform_capabilities"]
    assert [scope["project_id"] for scope in bootstrap["available_scopes"]] == ["project-a"]


def test_admin_guards_self_management_last_admin_and_password_confirmation() -> None:
    _, _, access, admin = _stack()
    session = access.login(
        LoginCommand(username="hc-admin", password=_ADMIN_PASSWORD),
        request_id="admin-login",
    )
    auth = access.authenticate_access_token(session.access_token)
    assert auth is not None

    with pytest.raises(ProblemException) as self_delete:
        admin.delete_account(
            auth=auth,
            principal_id=auth.subject_id,
            if_match='"v1"',
            request_id="self-delete",
        )
    assert self_delete.value.problem.code == "PLATFORM_ADMIN_SELF_MANAGEMENT_DENIED"

    with pytest.raises(ProblemException) as self_reset:
        admin.reset_password(
            auth=auth,
            principal_id=auth.subject_id,
            command=ManagedAccountPasswordReset(
                new_password="new-safe-password",
                confirm_password="new-safe-password",
            ),
            if_match='"v1"',
            request_id="self-reset",
        )
    assert self_reset.value.problem.code == "PLATFORM_ADMIN_SELF_MANAGEMENT_DENIED"

    external_admin = AuthContext(
        subject_id="external-root",
        project_ids=frozenset(),
        region_codes=frozenset(),
        roles=frozenset(),
        capabilities=auth.capabilities,
    )
    with pytest.raises(ProblemException) as last_admin:
        admin.delete_account(
            auth=external_admin,
            principal_id=auth.subject_id,
            if_match='"v1"',
            request_id="last-admin",
        )
    assert last_admin.value.problem.code == "LAST_PLATFORM_ADMIN_REQUIRED"

    created = admin.create_account(
        auth=auth,
        command=ManagedAccountCreate(
            username="second-admin",
            password=_USER_PASSWORD,
            platform_role=PlatformAccountRole.PLATFORM_ADMIN,
        ),
        request_id="second-admin",
    )
    assert created.platform_role is PlatformAccountRole.PLATFORM_ADMIN
    with pytest.raises(ValueError):
        ManagedAccountPasswordReset(
            new_password="new-safe-password",
            confirm_password="different-safe-password",
        )
    assert (
        access.login(
            LoginCommand(username="second-admin", password=_USER_PASSWORD),
            request_id="second-admin-login",
        ).principal.principal_id
        == created.principal_id
    )
