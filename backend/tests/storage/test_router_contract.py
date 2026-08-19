from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.core.health import ReadinessProbe
from hc_data_platform.security.auth import AuthContext, Role
from hc_data_platform.storage.router import router


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
            return await call_next(request)

    app.include_router(router)
    return app


def test_storage_router_requires_auth_and_exact_service_scope() -> None:
    path = "/api/v1/projects/project-a/storage/lifecycle-policies"
    assert TestClient(app_with_auth(None)).get(path).status_code == 401

    wrong_scope = AuthContext.service(
        subject_id="worker",
        roles={Role.ADMIN},
        project_ids={"project-b"},
    )
    denied = TestClient(app_with_auth(wrong_scope)).get(path)
    assert denied.status_code == 403
    assert denied.json()["code"] == "SERVICE_SCOPE_REQUIRED"


def test_runtime_openapi_has_storage_contract_and_no_impact_preview_path() -> None:
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
        "/api/v1/projects/{project_id}/storage/inventory",
        "/api/v1/projects/{project_id}/storage/lifecycle-audit",
        "/api/v1/projects/{project_id}/storage/lifecycle-policies",
        "/api/v1/projects/{project_id}/storage/lifecycle-policies/{policy_id}",
        "/api/v1/projects/{project_id}/storage/lifecycle-policies/{policy_id}/enable",
        "/api/v1/projects/{project_id}/storage/lifecycle-policies/{policy_id}/pause",
    }
    serialized = str(storage_paths).lower()
    forbidden_fragments = ("simulat", "impact-preview", "dry-run")
    assert all(fragment not in serialized for fragment in forbidden_fragments)

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
