from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.core.health import ReadinessProbe
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.storage.inventory import StorageInventorySnapshotProducer
from hc_data_platform.storage.repository import InMemoryStorageRepository
from hc_data_platform.storage.router import get_storage_governance_service, router
from hc_data_platform.storage.service import StorageGovernanceService

from .test_inventory_producer import Catalog, Provider


class ReadyProbe:
    async def check(self) -> None:
        return None


def app_with_auth(auth: AuthContext | None) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(ProblemException)
    async def handle_problem(_request: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(exc.problem.model_dump(mode="json"), status_code=exc.problem.status)

    if auth is not None:

        @app.middleware("http")
        async def install_auth(request: Request, call_next: Any) -> Any:
            request.state.auth_context = auth
            request.state.request_id = "request-router-test"
            token = bind_request_context(
                RequestContext(
                    organization_id=request.headers.get("X-Organization-Id"),
                    project_id=request.headers.get("X-Project-Id"),
                    region_code=request.headers.get("X-Region-Code"),
                    subject_id=auth.subject_id,
                    request_id="request-router-test",
                    service_identity=auth.service_identity,
                )
            )
            try:
                return await call_next(request)
            finally:
                reset_request_context(token)

    app.include_router(router)
    return app


def test_storage_router_requires_auth_and_exact_service_scope() -> None:
    path = "/api/v1/projects/project-a/storage/lifecycle-policies"
    assert TestClient(app_with_auth(None)).get(path).status_code == 401

    wrong_scope = AuthContext(
        subject_id="worker",
        capabilities=frozenset({"storage.lifecycle.read"}),
        project_ids=frozenset({"project-b"}),
        region_codes=frozenset(),
        service_identity=True,
        scope_pairs=frozenset({("project-b", None)}),
        organization_ids=frozenset({"organization-a"}),
        organization_scope_triples=frozenset({("organization-a", "project-b", None)}),
        organization_scoped_capabilities=frozenset(
            {("organization-a", "project-b", "storage.lifecycle.read")}
        ),
    )
    denied = TestClient(app_with_auth(wrong_scope)).get(
        path,
        headers={"X-Organization-Id": "organization-a", "X-Project-Id": "project-a"},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "ORGANIZATION_SCOPE_DENIED"


def test_storage_router_enforces_page_capabilities_and_private_read_cache_headers() -> None:
    read_auth = AuthContext(
        subject_id="storage-reader",
        organization_ids=frozenset({"organization-a"}),
        project_ids=frozenset({"project-a"}),
        region_codes=frozenset(),
        capabilities=frozenset({"storage.lifecycle.read"}),
        scope_pairs=frozenset({("project-a", None)}),
        organization_scope_triples=frozenset({("organization-a", "project-a", None)}),
        organization_scoped_capabilities=frozenset(
            {("organization-a", "project-a", "storage.lifecycle.read")}
        ),
    )
    response = TestClient(app_with_auth(read_auth)).get(
        "/api/v1/projects/project-a/storage/lifecycle-policies",
        headers={"X-Organization-Id": "organization-a", "X-Project-Id": "project-a"},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"

    wrong_capability = AuthContext(
        subject_id="storage-overview-only",
        organization_ids=frozenset({"organization-a"}),
        project_ids=frozenset({"project-a"}),
        region_codes=frozenset(),
        capabilities=frozenset({"storage.overview.read"}),
        scope_pairs=frozenset({("project-a", None)}),
        organization_scope_triples=frozenset({("organization-a", "project-a", None)}),
        organization_scoped_capabilities=frozenset(
            {("organization-a", "project-a", "storage.overview.read")}
        ),
    )
    denied = TestClient(app_with_auth(wrong_capability)).get(
        "/api/v1/projects/project-a/storage/lifecycle-policies",
        headers={"X-Organization-Id": "organization-a", "X-Project-Id": "project-a"},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "CAPABILITY_REQUIRED"


def test_generated_inventory_serves_capacity_and_history_without_cross_project_leakage() -> None:
    service = StorageGovernanceService(
        InMemoryStorageRepository(), cursor_secret="router-inventory-test"
    )
    snapshot = StorageInventorySnapshotProducer(Catalog(), Provider(), service).run(
        project_id="project-inventory"
    )
    auth = AuthContext(
        subject_id="storage-inventory-reader",
        organization_ids=frozenset({"organization-a"}),
        project_ids=frozenset({"project-inventory", "project-other"}),
        region_codes=frozenset(),
        capabilities=frozenset({"storage.overview.read"}),
        scope_pairs=frozenset({("project-inventory", None), ("project-other", None)}),
        organization_scope_triples=frozenset(
            {
                ("organization-a", "project-inventory", None),
                ("organization-a", "project-other", None),
            }
        ),
        organization_scoped_capabilities=frozenset(
            {
                ("organization-a", "project-inventory", "storage.overview.read"),
                ("organization-a", "project-other", "storage.overview.read"),
            }
        ),
    )
    app = app_with_auth(auth)
    app.dependency_overrides[get_storage_governance_service] = lambda: service
    client = TestClient(app)

    capacity = client.get(
        "/api/v1/projects/project-inventory/storage/capacity",
        headers={
            "X-Organization-Id": "organization-a",
            "X-Project-Id": "project-inventory",
        },
    )
    history = client.get(
        "/api/v1/projects/project-inventory/storage/capacity/history",
        headers={
            "X-Organization-Id": "organization-a",
            "X-Project-Id": "project-inventory",
        },
    )
    other = client.get(
        "/api/v1/projects/project-other/storage/capacity",
        headers={"X-Organization-Id": "organization-a", "X-Project-Id": "project-other"},
    )

    assert capacity.status_code == 200
    assert capacity.json()["snapshot_id"] == snapshot.snapshot_id
    assert history.status_code == 200
    assert history.json()["items"][0]["snapshot_id"] == snapshot.snapshot_id
    assert other.status_code == 404
    assert other.json()["code"] == "CAPACITY_SNAPSHOT_NOT_FOUND"
    assert snapshot.snapshot_id not in other.text


def test_storage_router_preserves_the_verified_organization_and_region_scope() -> None:
    auth = AuthContext(
        subject_id="organization-storage-reader",
        project_ids=frozenset({"project-a"}),
        region_codes=frozenset({"region-a"}),
        organization_ids=frozenset({"organization-a"}),
        scope_pairs=frozenset({("project-a", "region-a")}),
        scoped_capabilities=frozenset({("project-a", "storage.lifecycle.read")}),
        organization_scope_triples=frozenset({("organization-a", "project-a", "region-a")}),
        organization_scoped_capabilities=frozenset(
            {("organization-a", "project-a", "storage.lifecycle.read")}
        ),
    )
    headers = {
        "X-Organization-Id": "organization-a",
        "X-Project-Id": "project-a",
        "X-Region-Code": "region-a",
    }
    response = TestClient(app_with_auth(auth)).get(
        "/api/v1/projects/project-a/storage/lifecycle-policies",
        headers=headers,
    )
    assert response.status_code == 200

    denied = TestClient(app_with_auth(auth)).get(
        "/api/v1/projects/project-a/storage/lifecycle-policies",
        headers={**headers, "X-Organization-Id": "organization-b"},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "ORGANIZATION_SCOPE_DENIED"


def test_runtime_openapi_has_storage_object_and_approval_bound_execution_contract() -> None:
    ready: ReadinessProbe = ReadyProbe()
    app = create_app(
        settings=Settings(environment="test", runtime_backend="memory"),
        readiness_probes={
            "postgresql": ready,
            "temporal": ready,
            "object_storage": ready,
        },
    )
    document = app.openapi()
    storage_paths = {path: item for path, item in document["paths"].items() if "/storage/" in path}
    assert set(storage_paths) == {
        "/api/v1/projects/{project_id}/storage/capacity",
        "/api/v1/projects/{project_id}/storage/capacity/history",
        "/api/v1/projects/{project_id}/storage/capacity/portfolio",
        "/api/v1/projects/{project_id}/storage/inventory",
        "/api/v1/projects/{project_id}/storage/lifecycle-audit",
        "/api/v1/projects/{project_id}/storage/lifecycle-executions",
        "/api/v1/projects/{project_id}/storage/lifecycle-executions/{execution_id}",
        "/api/v1/projects/{project_id}/storage/lifecycle-executions/{execution_id}/logs",
        "/api/v1/projects/{project_id}/storage/lifecycle-executions/{execution_id}:approve",
        "/api/v1/projects/{project_id}/storage/lifecycle-executions/{execution_id}:cancel",
        "/api/v1/projects/{project_id}/storage/lifecycle-executions/{execution_id}:retry",
        "/api/v1/projects/{project_id}/storage/lifecycle-executions/{execution_id}:start",
        "/api/v1/projects/{project_id}/storage/lifecycle-executions:dry-run",
        "/api/v1/projects/{project_id}/storage/lifecycle-policies",
        "/api/v1/projects/{project_id}/storage/lifecycle-policies/{policy_id}",
        "/api/v1/projects/{project_id}/storage/lifecycle-policies/{policy_id}/enable",
        "/api/v1/projects/{project_id}/storage/lifecycle-policies/{policy_id}/pause",
        "/api/v1/projects/{project_id}/storage/lifecycle-schedules",
        "/api/v1/projects/{project_id}/storage/lifecycle-schedules/{schedule_id}",
        "/api/v1/projects/{project_id}/storage/lifecycle-schedules/{schedule_id}/enable",
        "/api/v1/projects/{project_id}/storage/lifecycle-schedules/{schedule_id}/pause",
        "/api/v1/projects/{project_id}/storage/multipart-uploads/{multipart_id}:abort",
        "/api/v1/projects/{project_id}/storage/objects",
        "/api/v1/projects/{project_id}/storage/objects/{object_id}",
        "/api/v1/projects/{project_id}/storage/objects/{object_id}:download",
        "/api/v1/projects/{project_id}/storage/objects/{object_id}:restore",
        "/api/v1/projects/{project_id}/storage/objects/{object_id}:transition",
        "/api/v1/projects/{project_id}/storage/objects/{object_id}:trash",
    }
    serialized = str(storage_paths).lower()
    forbidden_fragments = ("simulat", "impact-preview")
    assert all(fragment not in serialized for fragment in forbidden_fragments)
    assert (
        storage_paths["/api/v1/projects/{project_id}/storage/lifecycle-executions:dry-run"]["post"][
            "operationId"
        ]
        == "createLifecycleExecutionDryRun"
    )

    history = storage_paths["/api/v1/projects/{project_id}/storage/capacity/history"]["get"]
    assert history["operationId"] == "getStorageCapacityHistory"
    assert {parameter["name"] for parameter in history["parameters"]} == {
        "project_id",
        "from",
        "to",
    }
    assert history["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "CapacityHistory"
    )

    schemas = document["components"]["schemas"]
    category = schemas["BusinessCapacityCategory"]
    assert category["enum"] == [
        "RAW",
        "ANNOTATION_COMPLETE",
        "PENDING_ANNOTATION",
        "ISSUE_DATA",
    ]
    actions = schemas["LifecyclePolicyAction"]["enum"]
    assert "DELETE" not in " ".join(actions)
    assert "ABORT" not in " ".join(actions)

    # Keep the assertion tied to runtime OpenAPI, not an external frontend draft.
    router_source = Path(__file__).resolve().parents[2] / "src/hc_data_platform/storage/router.py"
    assert router_source.is_file()
