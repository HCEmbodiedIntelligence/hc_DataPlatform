from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.core.openapi import aggregate_fragments
from hc_data_platform.ingest.models import (
    ManifestFileV1,
    RolloutManifestV1,
    raw_object_key,
)
from hc_data_platform.ingest.ports import InMemoryObjectStorage, crc64_ecma
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
        collection_session_id="session1",
        recording_request_id="request1",
        data_package_id="package1",
        sequence_no=1,
        robot_id="robot1",
        start_time=start,
        end_time=start + timedelta(seconds=1),
        expected_topics=[],
        actual_topics=[],
        cameras=[],
        topics=[],
        files=[
            ManifestFileV1(
                path="recording.mcap",
                size=len(body),
                sha256=__import__("hashlib").sha256(body).hexdigest(),
                crc64=crc64_ecma(body),
            )
        ],
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


def test_router_uses_exact_project_capability_without_legacy_role_expansion() -> None:
    get_service.cache_clear()
    path = "/api/v1/projects/p1/regions/cn-hz/upload-sessions"
    payload = {"manifest": manifest_payload(), "part_numbers": [1]}
    scoped = AuthContext(
        subject_id="platform-session",
        project_ids=frozenset({"p1", "p2"}),
        region_codes=frozenset({"cn-hz"}),
        roles=frozenset(),
        scope_pairs=frozenset({("p1", "cn-hz"), ("p2", "cn-hz")}),
        scoped_capabilities=frozenset({("p1", "collection.upload")}),
    )
    allowed = TestClient(app_with_auth(scoped)).post(
        path,
        json=payload,
        headers={"Idempotency-Key": "capability-allowed"},
    )
    assert allowed.status_code == 201

    denied = TestClient(app_with_auth(scoped)).post(
        "/api/v1/projects/p2/regions/cn-hz/upload-sessions",
        json={
            "manifest": {**manifest_payload(), "project_id": "p2"},
            "part_numbers": [1],
        },
        headers={"Idempotency-Key": "capability-denied"},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "ROLE_REQUIRED"


def test_router_preflight_and_authorized_object_reference_are_real_json_controls() -> None:
    get_service.cache_clear()
    auth = AuthContext(
        subject_id="u1",
        project_ids=frozenset({"p1"}),
        region_codes=frozenset({"cn-hz"}),
        roles=frozenset({"uploader"}),
    )
    client = TestClient(app_with_auth(auth))
    manifest = manifest_payload()
    preflight_path = "/api/v1/projects/p1/regions/cn-hz/upload-manifests:preflight"

    unsupported = client.post(
        preflight_path,
        content=b"not-json",
        headers={"Content-Type": "text/plain"},
    )
    assert unsupported.status_code == 415
    assert unsupported.json()["code"] == "MANIFEST_CONTENT_TYPE_INVALID"

    preflight = client.post(preflight_path, json=manifest)
    assert preflight.status_code == 200
    assert preflight.json()["identifiers"]["data_package_id"] == "package1"
    assert preflight.json()["discovery"]["read_only"] is True
    expected_crc64 = str(crc64_ecma(b"body"))
    assert int(expected_crc64) > 2**53 - 1
    assert manifest["crc64"] == expected_crc64
    assert preflight.json()["manifest"]["crc64"] == expected_crc64
    assert preflight.json()["manifest"]["files"][0]["crc64"] == expected_crc64

    parsed = RolloutManifestV1.model_validate(manifest)
    key = raw_object_key(parsed)
    service = get_service()
    cast(InMemoryObjectStorage, service.storage).objects[key] = b"body"
    created = client.post(
        "/api/v1/projects/p1/regions/cn-hz/upload-sessions",
        json={
            "manifest": manifest,
            "object_storage_uri": f"memory://object/{key}",
        },
        headers={"Idempotency-Key": "router-object-reference"},
    )
    assert created.status_code == 201
    assert created.json()["session"]["source_type"] == "OBJECT_STORAGE_REFERENCE"
    assert created.json()["session"]["multipart_upload_id"] is None
    assert created.json()["session"]["expected_crc64"] == expected_crc64


def test_router_rejects_numeric_crc64_wire_values_before_precision_can_be_lost() -> None:
    get_service.cache_clear()
    auth = AuthContext(
        subject_id="u1",
        project_ids=frozenset({"p1"}),
        region_codes=frozenset({"cn-hz"}),
        roles=frozenset({"uploader"}),
    )
    client = TestClient(app_with_auth(auth))
    manifest = manifest_payload()
    manifest["crc64"] = int(cast(str, manifest["crc64"]))
    files = cast(list[dict[str, Any]], manifest["files"])
    files[0]["crc64"] = int(cast(str, files[0]["crc64"]))

    preflight = client.post(
        "/api/v1/projects/p1/regions/cn-hz/upload-manifests:preflight",
        json=manifest,
    )
    assert preflight.status_code == 422
    assert preflight.json()["code"] == "MANIFEST_INVALID"
    assert "decimal string" in preflight.json()["detail"]

    create = client.post(
        "/api/v1/projects/p1/regions/cn-hz/upload-sessions",
        json={"manifest": manifest, "part_numbers": [1]},
        headers={"Idempotency-Key": "numeric-crc64"},
    )
    assert create.status_code == 422
    assert create.json()["code"] == "MANIFEST_INVALID"


def test_openapi_fragment_covers_full_resumable_lifecycle_and_direct_upload() -> None:
    backend = Path(__file__).resolve().parents[2]
    fragment_path = backend / "openapi" / "ingest.yaml"
    fragment = yaml.safe_load(fragment_path.read_text(encoding="utf-8"))
    paths = fragment["paths"]
    prefix = "/api/v1/projects/{project_id}/regions/{region_code}/upload-sessions"
    expected_paths = {
        prefix,
        f"{prefix}/{{session_id}}",
        f"{prefix}/{{session_id}}/parts",
        f"{prefix}/{{session_id}}/manifest",
        f"{prefix}/{{session_id}}:renew",
        f"{prefix}/{{session_id}}:complete",
        f"{prefix}/{{session_id}}:retry-parts",
        f"{prefix}/{{session_id}}:pause",
        f"{prefix}/{{session_id}}:resume",
        f"{prefix}/{{session_id}}:cancel",
        f"{prefix}/{{session_id}}:commit-manifest",
        "/api/v1/projects/{project_id}/regions/{region_code}/upload-manifests:preflight",
    }
    assert set(paths) == expected_paths
    assert set(paths[prefix]) == {"get", "post"}

    complete = paths[f"{prefix}/{{session_id}}:complete"]["post"]
    complete_schema = complete["requestBody"]["content"]["application/json"]["schema"]
    assert complete_schema["$ref"].endswith("/CompleteUploadRequest")
    request_media_types = {
        media_type
        for path_item in paths.values()
        for operation in path_item.values()
        if isinstance(operation, dict) and "requestBody" in operation
        for media_type in operation["requestBody"]["content"]
    }
    assert request_media_types == {"application/json"}
    assert "short-lived" in paths[f"{prefix}/{{session_id}}:renew"]["post"]["description"]
    assert (
        "object_storage_uri"
        in fragment["components"]["schemas"]["CreateUploadRequest"]["properties"]
    )
    assert (
        fragment["components"]["schemas"]["ManifestDiscoveryV1"]["properties"]["read_only"]["const"]
        is True
    )
    assert fragment["components"]["schemas"]["UploadPart"]["properties"]["status"]["enum"] == [
        "AUTHORIZED",
        "UPLOADED",
        "FAILED",
    ]

    aggregated = aggregate_fragments(backend / "openapi")
    operation_ids = [
        operation["operationId"]
        for path in aggregated["paths"].values()
        for method, operation in path.items()
        if method != "parameters" and isinstance(operation, dict) and "operationId" in operation
    ]
    assert len(operation_ids) == len(set(operation_ids))
    assert all("start-ingest" not in path for path in aggregated["paths"])
    assert "createAnnotationTask" not in operation_ids
    locator = fragment["components"]["schemas"]["IngestWorkflowLocator"]
    assert locator["properties"]["status"]["enum"] == [
        "PENDING",
        "DISPATCHED",
        "RETRY_WAIT",
    ]
