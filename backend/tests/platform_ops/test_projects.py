from __future__ import annotations

from fastapi.testclient import TestClient

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.platform_ops.maintenance import InMemoryMaintenanceWriteGate
from hc_data_platform.platform_ops.projects import InMemoryPlatformProjectRepository
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.capabilities import (
    CAPABILITY_PLATFORM_ADMIN,
    CAPABILITY_PLATFORM_OPERATIONS_READ,
)
from hc_data_platform.security.http import require_auth_context


def _settings() -> Settings:
    return Settings(environment="test", runtime_backend="memory", _env_file=None)


def _auth(capability: str) -> AuthContext:
    return AuthContext(
        subject_id="platform-project-admin",
        project_ids=frozenset(),
        region_codes=frozenset(),
        capabilities=frozenset({capability}),
    )


def test_platform_admin_can_bootstrap_and_list_the_first_project() -> None:
    repository = InMemoryPlatformProjectRepository()
    audit = InMemoryMaintenanceWriteGate()
    app = create_app(
        settings=_settings(),
        platform_project_repository=repository,
        maintenance_write_gate=audit,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/api/v1/platform/projects").status_code == 401

        app.dependency_overrides[require_auth_context] = lambda: _auth(
            CAPABILITY_PLATFORM_OPERATIONS_READ
        )
        denied = client.post(
            "/api/v1/platform/organizations",
            json={
                "organization_id": "hangcha",
                "organization_name": "杭叉集团",
            },
        )
        assert denied.status_code == 403

        app.dependency_overrides[require_auth_context] = lambda: _auth(CAPABILITY_PLATFORM_ADMIN)
        created_organization = client.post(
            "/api/v1/platform/organizations",
            json={
                "organization_id": "hangcha",
                "organization_name": "杭叉集团",
            },
        )
        created = client.post(
            "/api/v1/platform/projects",
            json={
                "organization_id": "hangcha",
                "project_id": "robot-data-01",
                "project_name": "双臂采集一期",
            },
        )
        listed_organizations = client.get("/api/v1/platform/organizations")
        listed = client.get("/api/v1/platform/projects")
        duplicate = client.post(
            "/api/v1/platform/projects",
            json={
                "organization_id": "hangcha",
                "project_id": "robot-data-01",
                "project_name": "双臂采集一期",
            },
        )

    assert created_organization.status_code == 201
    assert created_organization.json() == {
        "organization_id": "hangcha",
        "organization_name": "杭叉集团",
    }
    assert listed_organizations.json() == {
        "format_version": "hc-platform-organization-directory/v1",
        "count": 1,
        "items": [created_organization.json()],
    }
    assert created.status_code == 201
    assert created.headers["Cache-Control"] == "private, no-store"
    assert created.json() == {
        "organization_id": "hangcha",
        "organization_name": "杭叉集团",
        "project_id": "robot-data-01",
        "project_name": "双臂采集一期",
    }
    assert listed.status_code == 200
    assert listed.json() == {
        "format_version": "hc-platform-project-directory/v1",
        "count": 1,
        "items": [created.json()],
    }
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "PLATFORM_PROJECT_ALREADY_EXISTS"
    assert [(event.action, event.outcome) for event in audit.platform_audit_events] == [
        ("platform.organization.create", "DENIED"),
        ("platform.organization.created", "SUCCEEDED"),
        ("platform.project.created", "SUCCEEDED"),
        ("platform.organizations.listed", "SUCCEEDED"),
        ("platform.projects.listed", "SUCCEEDED"),
        ("platform.project.create", "FAILED"),
    ]


def test_platform_project_ids_are_safe_for_scope_urls() -> None:
    app = create_app(
        settings=_settings(),
        platform_project_repository=InMemoryPlatformProjectRepository(),
    )
    app.dependency_overrides[require_auth_context] = lambda: _auth(CAPABILITY_PLATFORM_ADMIN)

    with TestClient(app, raise_server_exceptions=False) as client:
        client.post(
            "/api/v1/platform/organizations",
            json={"organization_id": "hangcha", "organization_name": "杭叉集团"},
        )
        response = client.post(
            "/api/v1/platform/projects",
            json={
                "organization_id": "hangcha",
                "project_id": "unsafe/project",
                "project_name": "不安全项目",
            },
        )

    assert response.status_code == 422
    assert response.json()["code"] == "REQUEST_VALIDATION_FAILED"


def test_project_provisioning_openapi_requires_exact_platform_admin() -> None:
    document = create_app(settings=_settings()).openapi()
    project_path = document["paths"]["/api/v1/platform/projects"]
    organization_path = document["paths"]["/api/v1/platform/organizations"]

    assert project_path["get"]["operationId"] == "listPlatformProjects"
    assert project_path["post"]["operationId"] == "createPlatformProject"
    assert organization_path["get"]["operationId"] == "listPlatformOrganizations"
    assert organization_path["post"]["operationId"] == "createPlatformOrganization"
    for operation in (
        project_path["get"],
        project_path["post"],
        organization_path["get"],
        organization_path["post"],
    ):
        assert operation["security"] == [{"bearerAuth": []}]
        assert operation["x-hc-platform-capability-policy"] == {
            "mode": "one-exact",
            "capabilities": ["platform.admin"],
        }
