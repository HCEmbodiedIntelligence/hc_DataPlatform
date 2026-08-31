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
        roles=frozenset(),
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
            "/api/v1/platform/projects",
            json={
                "organization_id": "hangcha",
                "organization_name": "杭叉集团",
                "project_id": "robot-data-01",
            },
        )
        assert denied.status_code == 403

        app.dependency_overrides[require_auth_context] = lambda: _auth(CAPABILITY_PLATFORM_ADMIN)
        created = client.post(
            "/api/v1/platform/projects",
            json={
                "organization_id": "hangcha",
                "organization_name": "杭叉集团",
                "project_id": "robot-data-01",
            },
        )
        listed = client.get("/api/v1/platform/projects")
        duplicate = client.post(
            "/api/v1/platform/projects",
            json={
                "organization_id": "hangcha",
                "organization_name": "杭叉集团",
                "project_id": "robot-data-01",
            },
        )

    assert created.status_code == 201
    assert created.headers["Cache-Control"] == "private, no-store"
    assert created.json() == {
        "organization_id": "hangcha",
        "organization_name": "杭叉集团",
        "project_id": "robot-data-01",
        "project_name": "robot-data-01",
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
        ("platform.project.create", "DENIED"),
        ("platform.project.created", "SUCCEEDED"),
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
        response = client.post(
            "/api/v1/platform/projects",
            json={
                "organization_id": "hangcha",
                "organization_name": "杭叉集团",
                "project_id": "unsafe/project",
            },
        )

    assert response.status_code == 422
    assert response.json()["code"] == "REQUEST_VALIDATION_FAILED"


def test_project_provisioning_openapi_requires_exact_platform_admin() -> None:
    document = create_app(settings=_settings()).openapi()
    path = document["paths"]["/api/v1/platform/projects"]

    assert path["get"]["operationId"] == "listPlatformProjects"
    assert path["post"]["operationId"] == "createPlatformProject"
    for operation in (path["get"], path["post"]):
        assert operation["security"] == [{"bearerAuth": []}]
        assert operation["x-hc-platform-capability-policy"] == {
            "mode": "one-exact",
            "capabilities": ["platform.admin"],
        }
