from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.core.openapi import aggregate_fragments
from hc_data_platform.ingest.models import RolloutManifestV1
from hc_data_platform.ingest.ports import crc64_ecma
from hc_data_platform.ingest.router import get_service, router
from hc_data_platform.security.auth import AuthContext


def manifest_payload() -> dict[str, Any]:
    body = b"body"
    start = datetime(2026, 8, 14, 8, tzinfo=timezone.utc)
    return RolloutManifestV1(
        project_id="p1",
        task_id="t1",
        collection_job_id="j1",
        rollout_id="r1",
        sequence_no=1,
        robot_id="robot1",
        start_time=start,
        end_time=start + timedelta(seconds=1),
        expected_topics=[],
        actual_topics=[],
        file_size=len(body),
        sha256=__import__("hashlib").sha256(body).hexdigest(),
        crc64=crc64_ecma(body),
        compression="none",
        recorder_version="test",
    ).model_dump(mode="json")


def app_with_auth(auth: AuthContext | None) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(ProblemException)
    async def handle_problem(_request: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(exc.problem.model_dump(mode="json"), status_code=exc.problem.status)

    if auth is not None:

        @app.middleware("http")
        async def install_auth(request: Request, call_next: Any) -> Any:
            request.state.auth_context = auth
            return await call_next(request)

    app.include_router(router)
    return app


def test_router_requires_be02_auth_context_role_and_scope() -> None:
    get_service.cache_clear()
    path = "/api/v1/projects/p1/regions/cn-hz/upload-sessions"
    payload = {"manifest": manifest_payload(), "part_numbers": [1]}
    assert (
        TestClient(app_with_auth(None))
        .post(
            path,
            json=payload,
            headers={"Idempotency-Key": "unauthenticated"},
        )
        .status_code
        == 401
    )

    wrong_scope = AuthContext(
        subject_id="u1",
        project_ids=frozenset({"p2"}),
        region_codes=frozenset({"cn-hz"}),
        roles=frozenset({"uploader"}),
    )
    denied = TestClient(app_with_auth(wrong_scope)).post(
        path,
        json=payload,
        headers={"Idempotency-Key": "denied"},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "PROJECT_SCOPE_DENIED"

    allowed = AuthContext(
        subject_id="u1",
        project_ids=frozenset({"p1"}),
        region_codes=frozenset({"cn-hz"}),
        roles=frozenset({"uploader"}),
    )
    created = TestClient(app_with_auth(allowed)).post(
        path,
        json=payload,
        headers={"Idempotency-Key": "allowed"},
    )
    assert created.status_code == 201
    assert created.headers["Cache-Control"] == "no-store"
    assert created.json()["session"]["object_key"].startswith("raw/v1/project=p1/")
    assert created.json()["parts"][0]["url"].startswith("memory://")


def test_openapi_fragment_covers_full_resumable_lifecycle_and_direct_upload() -> None:
    backend = Path(__file__).resolve().parents[2]
    fragment_path = backend / "openapi" / "ingest.yaml"
    fragment = yaml.safe_load(fragment_path.read_text(encoding="utf-8"))
    paths = fragment["paths"]
    route_suffixes = {
        "",
        "/{session_id}",
        "/{session_id}/parts",
        "/{session_id}:renew",
        "/{session_id}:complete",
        "/{session_id}:pause",
        "/{session_id}:resume",
        "/{session_id}:cancel",
        "/{session_id}:commit-manifest",
    }
    prefix = "/api/v1/projects/{project_id}/regions/{region_code}/upload-sessions"
    assert {path.removeprefix(prefix) for path in paths} == route_suffixes

    complete = paths[f"{prefix}/{{session_id}}:complete"]["post"]
    complete_schema = complete["requestBody"]["content"]["application/json"]["schema"]
    assert complete_schema["$ref"].endswith("/CompleteUploadRequest")
    assert "application/octet-stream" not in fragment_path.read_text(encoding="utf-8")
    assert "short-lived" in paths[f"{prefix}/{{session_id}}:renew"]["post"]["description"]

    aggregated = aggregate_fragments(backend / "openapi")
    operation_ids = [
        operation["operationId"]
        for path in aggregated["paths"].values()
        for operation in path.values()
    ]
    assert len(operation_ids) == len(set(operation_ids))
