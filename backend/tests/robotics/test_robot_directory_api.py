from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.robot_assets.repository import InMemoryOrganizationRobotAssetRepository
from hc_data_platform.robot_assets.router import configure_organization_robot_assets, router
from hc_data_platform.robot_assets.service import OrganizationRobotAssetService
from hc_data_platform.security.auth import AuthContext

ORGANIZATION_ID = "org-a"
PROJECT_ID = "project-a"


def _auth(*, manage: bool = False, model_manage: bool = False) -> AuthContext:
    capabilities = {(ORGANIZATION_ID, PROJECT_ID, "robot.read")}
    if manage:
        capabilities.add((ORGANIZATION_ID, PROJECT_ID, "robot.manage"))
    if model_manage:
        capabilities.add((ORGANIZATION_ID, PROJECT_ID, "robot_model.manage"))
    return AuthContext(
        subject_id="robot-user",
        project_ids=frozenset({PROJECT_ID}),
        organization_ids=frozenset({ORGANIZATION_ID}),
        region_codes=frozenset(),
        scope_pairs=frozenset(),
        organization_scope_triples=frozenset(),
        organization_scoped_capabilities=frozenset(capabilities),
    )


def _app(current: dict[str, AuthContext | None]) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(ProblemException)
    async def handle_problem(_request: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(exc.problem.model_dump(mode="json"), status_code=exc.problem.status)

    @app.middleware("http")
    async def install_auth(request: Request, call_next: Any) -> Any:
        request.state.auth_context = current["value"]
        request.state.request_id = "organization-robot-test"
        return await call_next(request)

    app.include_router(router)
    return app


def test_robot_assets_are_organization_scoped_without_project_or_region_headers() -> None:
    repository = InMemoryOrganizationRobotAssetRepository((ORGANIZATION_ID,))
    configure_organization_robot_assets(OrganizationRobotAssetService(repository))
    current: dict[str, AuthContext | None] = {"value": _auth(manage=True)}
    client = TestClient(_app(current))
    root = f"/api/v1/organizations/{ORGANIZATION_ID}/robots"

    created = client.post(
        root,
        headers={"Authorization": "Bearer test", "Idempotency-Key": "create-robot-a"},
        json={
            "display_name": "星舟 017",
            "serial_no": "SN-017",
            "lifecycle_status": "DRAFT",
            "connectivity_state": "ONLINE",
        },
    )
    assert created.status_code == 201
    robot_id = created.json()["data"]["robot"]["id"]
    assert created.json()["scope"] == {"organization_id": ORGANIZATION_ID}

    resumed = client.post(
        root,
        headers={"Authorization": "Bearer test", "Idempotency-Key": "resume-robot-a"},
        json={
            "display_name": "星舟 017（重新导入）",
            "serial_no": "SN-017",
            "lifecycle_status": "DRAFT",
            "connectivity_state": "OFFLINE",
        },
    )
    assert resumed.status_code == 201
    assert resumed.json()["data"]["robot"]["id"] == robot_id

    listing = client.get(root, headers={"Authorization": "Bearer test"})
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()["items"]] == [robot_id]
    assert listing.json()["scope"] == {"organization_id": ORGANIZATION_ID}

    bootstrap = client.get(f"{root}/{robot_id}/bootstrap", headers={"Authorization": "Bearer test"})
    assert bootstrap.status_code == 200
    assert bootstrap.headers["etag"] == created.headers["etag"]


def test_organization_binding_replaces_the_project_binding_contract() -> None:
    repository = InMemoryOrganizationRobotAssetRepository((ORGANIZATION_ID,))
    repository.add_published_version(ORGANIZATION_ID, "version-a")
    configure_organization_robot_assets(OrganizationRobotAssetService(repository))
    current: dict[str, AuthContext | None] = {"value": _auth(manage=True, model_manage=True)}
    client = TestClient(_app(current))
    root = f"/api/v1/organizations/{ORGANIZATION_ID}/robots"
    created = client.post(
        root,
        headers={"Authorization": "Bearer test", "Idempotency-Key": "create"},
        json={"display_name": "Robot", "serial_no": "SN", "connectivity_state": "OFFLINE"},
    )
    robot_id = created.json()["data"]["robot"]["id"]

    provisional_listing = client.get(
        root,
        params={"configured_only": "true"},
        headers={"Authorization": "Bearer test"},
    )
    assert provisional_listing.status_code == 200
    assert provisional_listing.json()["items"] == []

    bound = client.post(
        f"{root}/{robot_id}/model-bindings",
        headers={"Authorization": "Bearer test", "Idempotency-Key": "bind"},
        json={"version_id": "version-a", "robot_etag": created.headers["etag"]},
    )
    assert bound.status_code == 200
    assert bound.json()["robot_id"] == robot_id
    assert "region_code" not in bound.json()

    duplicate = client.post(
        root,
        headers={"Authorization": "Bearer test", "Idempotency-Key": "duplicate-serial"},
        json={"display_name": "Duplicate", "serial_no": "SN"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "ROBOT_SERIAL_CONFLICT"

    refreshed = client.get(f"{root}/{robot_id}/bootstrap", headers={"Authorization": "Bearer test"})
    renamed = client.patch(
        f"{root}/{robot_id}",
        headers={"Authorization": "Bearer test", "If-Match": refreshed.headers["etag"]},
        json={"display_name": "Robot A"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["data"]["robot"]["display_name"] == "Robot A"

    activated = client.post(
        f"{root}/{robot_id}/lifecycle",
        headers={"Authorization": "Bearer test", "If-Match": renamed.headers["etag"]},
        json={"lifecycle_status": "ACTIVE", "reason": "ready for production"},
    )
    assert activated.status_code == 200
    assert activated.json()["data"]["robot"]["lifecycle_status"] == "ACTIVE"

    configured_listing = client.get(
        root,
        params={"configured_only": "true"},
        headers={"Authorization": "Bearer test"},
    )
    assert [item["id"] for item in configured_listing.json()["items"]] == [robot_id]

    history = client.get(
        f"{root}/model-bindings",
        params={"version_id": "version-a"},
        headers={"Authorization": "Bearer test"},
    )
    assert history.status_code == 200
    assert [item["binding_id"] for item in history.json()["items"]] == [bound.json()["binding_id"]]

    protected = client.delete(f"{root}/{robot_id}", headers={"Authorization": "Bearer test"})
    assert protected.status_code == 409
    assert protected.json()["code"] == "ROBOT_NOT_PROVISIONAL"


def test_failed_import_can_delete_only_its_provisional_robot() -> None:
    repository = InMemoryOrganizationRobotAssetRepository((ORGANIZATION_ID,))
    configure_organization_robot_assets(OrganizationRobotAssetService(repository))
    current: dict[str, AuthContext | None] = {"value": _auth(manage=True)}
    client = TestClient(_app(current))
    root = f"/api/v1/organizations/{ORGANIZATION_ID}/robots"
    created = client.post(
        root,
        headers={"Authorization": "Bearer test", "Idempotency-Key": "failed-import"},
        json={"display_name": "临时机器人", "serial_no": "FAILED-001"},
    )
    robot_id = created.json()["data"]["robot"]["id"]

    deleted = client.delete(f"{root}/{robot_id}", headers={"Authorization": "Bearer test"})
    assert deleted.status_code == 204
    assert (
        client.get(f"{root}/{robot_id}/bootstrap", headers={"Authorization": "Bearer test"}).json()[
            "code"
        ]
        == "ROBOT_NOT_FOUND"
    )
    assert (
        client.delete(f"{root}/{robot_id}", headers={"Authorization": "Bearer test"}).status_code
        == 204
    )


def test_organization_robot_assets_enforce_membership_and_capability() -> None:
    repository = InMemoryOrganizationRobotAssetRepository((ORGANIZATION_ID,))
    configure_organization_robot_assets(OrganizationRobotAssetService(repository))
    current: dict[str, AuthContext | None] = {"value": _auth()}
    client = TestClient(_app(current))
    headers = {"Authorization": "Bearer test"}

    denied = client.post(
        f"/api/v1/organizations/{ORGANIZATION_ID}/robots",
        headers={**headers, "Idempotency-Key": "denied"},
        json={"display_name": "Robot", "serial_no": "SN"},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "ORGANIZATION_CAPABILITY_REQUIRED"

    foreign = client.get("/api/v1/organizations/org-foreign/robots", headers=headers)
    assert foreign.status_code == 403
    assert foreign.json()["code"] == "ORGANIZATION_SCOPE_DENIED"
