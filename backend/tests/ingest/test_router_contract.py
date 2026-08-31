from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.core.openapi import aggregate_fragments
from hc_data_platform.ingest.device_facts import (
    DeviceCaptureFactService,
    InMemoryDeviceCaptureFactRepository,
)
from hc_data_platform.ingest.models import (
    CompletedPart,
    ManifestFileV1,
    RolloutManifestV1,
    raw_object_key,
)
from hc_data_platform.ingest.persistence import InMemoryIngestPersistence
from hc_data_platform.ingest.ports import InMemoryObjectStorage, crc64_ecma
from hc_data_platform.ingest.router import (
    configure_device_capture_facts,
    configure_ingest_job_status,
    get_service,
    router,
)
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.capabilities import CAPABILITY_PLATFORM_ADMIN
from hc_data_platform.workflow.models import JobRecord, JobStatus
from hc_data_platform.workflow.names import INGEST_ROLLOUT_WORKFLOW


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
            token = bind_request_context(
                RequestContext(
                    organization_id=(
                        next(iter(auth.organization_ids))
                        if len(auth.organization_ids) == 1
                        else None
                    ),
                    subject_id=auth.subject_id,
                    request_id="test-request-id",
                    roles=auth.roles,
                    service_identity=auth.service_identity,
                )
            )
            try:
                return await call_next(request)
            finally:
                reset_request_context(token)

    app.include_router(router)
    return app


def device_fact_payload() -> dict[str, Any]:
    start = datetime(2026, 8, 24, 8, tzinfo=timezone.utc)
    return {
        "schema_version": "device-capture-fact/v1",
        "source_event_id": "device-event-1",
        "event_type": "CAPTURED",
        "collection_task_id": "t1",
        "collection_job_id": "j1",
        "recording_request_id": "request1",
        "data_package_id": "package1",
        "robot_id": "robot1",
        "device_id": "pico1",
        "device_sequence_no": 1,
        "capture_started_at": start.isoformat(),
        "capture_ended_at": (start + timedelta(seconds=30)).isoformat(),
        "saved_at": None,
        "local_artifact_size": None,
        "local_artifact_sha256": None,
        "recorder_version": "recorder-1.2.3",
        "occurred_at": (start + timedelta(seconds=30)).isoformat(),
    }


def test_device_capture_facts_require_service_identity_and_are_immutable() -> None:
    configure_device_capture_facts(DeviceCaptureFactService(InMemoryDeviceCaptureFactRepository()))
    path = "/api/v1/projects/p1/regions/cn-hz/device-capture-facts"
    scope = {
        "project_ids": frozenset({"p1"}),
        "region_codes": frozenset({"cn-hz"}),
        "roles": frozenset({"uploader"}),
        "organization_ids": frozenset({"org-a"}),
        "organization_scope_triples": frozenset({("org-a", "p1", "cn-hz")}),
    }

    human = TestClient(app_with_auth(AuthContext(subject_id="human-uploader", **scope))).post(
        path, json=device_fact_payload()
    )
    assert human.status_code == 403
    assert human.json()["code"] == "DEVICE_SERVICE_IDENTITY_REQUIRED"

    client = TestClient(
        app_with_auth(AuthContext(subject_id="device-agent:pico1", service_identity=True, **scope))
    )
    first = client.post(path, json=device_fact_payload())
    retried = client.post(path, json=device_fact_payload())
    assert first.status_code == retried.status_code == 200
    assert first.headers["Cache-Control"] == "no-store"
    assert first.json() == retried.json()
    assert first.json()["event_type"] == "CAPTURED"
    assert first.json()["producer_subject_id"] == "device-agent:pico1"

    changed = client.post(
        path,
        json={**device_fact_payload(), "recorder_version": "recorder-1.2.4"},
    )
    assert changed.status_code == 409
    assert changed.json()["code"] == "DEVICE_CAPTURE_FACT_IMMUTABLE"

    invalid_saved = client.post(
        path,
        json={**device_fact_payload(), "source_event_id": "device-event-2", "event_type": "SAVED"},
    )
    assert invalid_saved.status_code == 422


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


def test_platform_admin_uploads_any_existing_project_without_membership() -> None:
    get_service.cache_clear()
    platform_admin = AuthContext(
        subject_id="platform-admin",
        project_ids=frozenset({"p1", "p2"}),
        region_codes=frozenset(),
        roles=frozenset(),
        capabilities=frozenset({CAPABILITY_PLATFORM_ADMIN}),
        organization_ids=frozenset({"org-a", "org-b"}),
        organization_scope_triples=frozenset({("org-a", "p1", None), ("org-b", "p2", None)}),
    )
    client = TestClient(app_with_auth(platform_admin))

    first = client.post(
        "/api/v1/projects/p1/regions/cn-hz/upload-sessions",
        json={"manifest": manifest_payload(), "part_numbers": [1]},
        headers={"Idempotency-Key": "platform-admin-p1"},
    )
    second = client.post(
        "/api/v1/projects/p2/regions/eu-central/upload-sessions",
        json={
            "manifest": {**manifest_payload(), "project_id": "p2", "rollout_id": "r2"},
            "part_numbers": [1],
        },
        headers={"Idempotency-Key": "platform-admin-p2"},
    )
    missing = client.post(
        "/api/v1/projects/missing/regions/cn-hz/upload-sessions",
        json={
            "manifest": {**manifest_payload(), "project_id": "missing"},
            "part_numbers": [1],
        },
        headers={"Idempotency-Key": "platform-admin-missing"},
    )

    assert first.status_code == second.status_code == 201
    assert missing.status_code == 404
    assert missing.json()["code"] == "PROJECT_NOT_FOUND"


def test_current_upload_manage_is_project_scoped_and_upload_read_is_not_write() -> None:
    get_service.cache_clear()
    writer = AuthContext(
        subject_id="writer",
        project_ids=frozenset({"p1", "p2"}),
        region_codes=frozenset({"cn-hz"}),
        roles=frozenset(),
        scope_pairs=frozenset({("p1", "cn-hz"), ("p2", "cn-hz")}),
        scoped_capabilities=frozenset({("p1", "upload.manage")}),
    )
    allowed = TestClient(app_with_auth(writer)).post(
        "/api/v1/projects/p1/regions/cn-hz/upload-sessions",
        json={"manifest": manifest_payload(), "part_numbers": [1]},
        headers={"Idempotency-Key": "current-upload-manage"},
    )
    denied_project = TestClient(app_with_auth(writer)).post(
        "/api/v1/projects/p2/regions/cn-hz/upload-sessions",
        json={
            "manifest": {**manifest_payload(), "project_id": "p2"},
            "part_numbers": [1],
        },
        headers={"Idempotency-Key": "current-upload-manage-cross-project"},
    )
    reader = AuthContext(
        subject_id="reader",
        project_ids=frozenset({"p1"}),
        region_codes=frozenset({"cn-hz"}),
        roles=frozenset(),
        scope_pairs=frozenset({("p1", "cn-hz")}),
        scoped_capabilities=frozenset({("p1", "upload.read")}),
    )
    denied_reader = TestClient(app_with_auth(reader)).post(
        "/api/v1/projects/p1/regions/cn-hz/upload-sessions",
        json={"manifest": {**manifest_payload(), "rollout_id": "read-only"}, "part_numbers": [1]},
        headers={"Idempotency-Key": "upload-read-only"},
    )

    assert allowed.status_code == 201
    assert denied_project.status_code == 403
    assert denied_project.json()["code"] == "ROLE_REQUIRED"
    assert denied_reader.status_code == 403
    assert denied_reader.json()["code"] == "ROLE_REQUIRED"


def test_router_issues_an_audited_non_cacheable_raw_mcap_source_only_after_commit() -> None:
    get_service.cache_clear()
    auth = AuthContext(
        subject_id="u1",
        project_ids=frozenset({"p1"}),
        region_codes=frozenset({"cn-hz"}),
        roles=frozenset({"uploader"}),
    )
    client = TestClient(app_with_auth(auth))
    payload = manifest_payload()
    resource_root = "/api/v1/projects/p1/regions/cn-hz/upload-sessions"
    created = client.post(
        resource_root,
        json={"manifest": payload, "part_numbers": [1]},
        headers={"Idempotency-Key": "raw-media-router"},
    )
    assert created.status_code == 201
    session_id = created.json()["session"]["session_id"]
    raw_path = f"{resource_root}/{session_id}/raw-media"

    before_commit = client.get(raw_path)
    assert before_commit.status_code == 409
    assert before_commit.json()["code"] == "RAW_MEDIA_NOT_COMMITTED"

    service = get_service()
    storage = cast(InMemoryObjectStorage, service.storage)
    session = service.get_session(session_id)
    assert session.multipart_upload_id is not None
    part = storage.upload_part(session.multipart_upload_id, 1, b"body", key=session.object_key)
    service.complete_upload(
        session.session_id,
        [CompletedPart(part_number=part.part_number, etag=part.etag)],
    )
    service.commit_manifest(
        session_id=session.session_id,
        manifest=RolloutManifestV1.model_validate(payload),
    )

    authorized = client.get(raw_path)
    assert authorized.status_code == 200
    assert authorized.headers["Cache-Control"] == "no-store"
    source = authorized.json()
    assert source["schema_version"] == "raw-media-source/v1"
    assert source["format"] == "MCAP"
    assert source["media_type"] == "application/x-mcap"
    assert source["byte_length"] == 4
    assert source["sha256"] == __import__("hashlib").sha256(b"body").hexdigest()
    assert source["download_url"].startswith("memory://object/")
    assert "object_key" not in source
    persistence = service.persistence
    assert isinstance(persistence, InMemoryIngestPersistence)
    assert persistence.raw_media_audit_events[0].actor_id == "u1"
    assert persistence.raw_media_audit_events[0].request_id == "test-request-id"


def test_router_projects_only_the_processing_result_linked_to_the_upload_scope() -> None:
    get_service.cache_clear()
    auth = AuthContext(
        subject_id="uploader-without-workflow-read",
        project_ids=frozenset({"p1"}),
        region_codes=frozenset({"cn-hz"}),
        roles=frozenset({"uploader"}),
    )
    client = TestClient(app_with_auth(auth))
    payload = manifest_payload()
    resource_root = "/api/v1/projects/p1/regions/cn-hz/upload-sessions"
    created = client.post(
        resource_root,
        json={"manifest": payload, "part_numbers": [1]},
        headers={"Idempotency-Key": "processing-router"},
    )
    session_id = created.json()["session"]["session_id"]
    before_commit = client.get(f"{resource_root}/{session_id}/processing")
    assert before_commit.status_code == 409
    assert before_commit.json()["code"] == "UPLOAD_PROCESSING_NOT_STARTED"

    service = get_service()
    storage = cast(InMemoryObjectStorage, service.storage)
    session = service.get_session(session_id)
    assert session.multipart_upload_id is not None
    part = storage.upload_part(session.multipart_upload_id, 1, b"body", key=session.object_key)
    service.complete_upload(
        session.session_id,
        [CompletedPart(part_number=part.part_number, etag=part.etag)],
    )
    committed = service.commit_manifest(
        session_id=session.session_id,
        manifest=RolloutManifestV1.model_validate(payload),
    )
    assert committed.workflow is not None

    class StatusPort:
        def get(self, job_id: str) -> JobRecord:
            assert job_id == committed.workflow.workflow_id
            return JobRecord(
                job_id=job_id,
                workflow_id=job_id,
                job_type=INGEST_ROLLOUT_WORKFLOW,
                project_id="p1",
                resource_id="r1",
                status=JobStatus.SUCCEEDED,
                stage="completed",
                attempt=1,
                result={
                    "alignment": {"frequency_hz": 30.0},
                    "derived": {
                        "project_id": "p1",
                        "dataset_id": "dataset_ingest_1",
                        "rollout_id": "r1",
                        "source_sha256": payload["sha256"],
                        "converter_version": "converter-v1",
                        "dataset_version": 1,
                        "lance_version": 1,
                        "step_count": 30,
                        "content_hash": "c" * 64,
                    },
                    "viewer_target": {
                        "schema_version": "dataset-ingest-viewer-target/v1",
                        "dataset_id": "dataset_ingest_1",
                        "version_id": "version_lance_1",
                        "episode_id": "episode_ingest_1",
                        "revision_id": "revision_ingest_1",
                    },
                    "annotation_task": {"task_id": "task1", "status": "CREATED"},
                    "raw_secret": "must-not-be-returned",
                },
            )

    configure_ingest_job_status(StatusPort())
    try:
        response = client.get(f"{resource_root}/{session_id}/processing")
    finally:
        configure_ingest_job_status(None)

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert body["status"] == "SUCCEEDED"
    assert body["aligned_media"] == {
        "schema_version": "upload-aligned-media-target/v1",
        "project_id": "p1",
        "dataset_id": "dataset_ingest_1",
        "rollout_id": "r1",
        "dataset_version": 1,
        "annotation_task_id": "task1",
        "fps": 30,
        "start_step": 0,
        "end_step": 30,
    }
    assert body["viewer"] == {
        "schema_version": "dataset-ingest-viewer-target/v1",
        "dataset_id": "dataset_ingest_1",
        "version_id": "version_lance_1",
        "episode_id": "episode_ingest_1",
        "revision_id": "revision_ingest_1",
    }
    assert "result" not in body
    assert "raw_secret" not in response.text


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
    assert preflight.headers["Cache-Control"] == "no-store"
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
    assert created.headers["Cache-Control"] == "no-store"
    assert created.json()["session"]["source_type"] == "OBJECT_STORAGE_REFERENCE"
    assert created.json()["session"]["multipart_upload_id"] is None
    assert created.json()["session"]["expected_crc64"] == expected_crc64
    session_id = created.json()["session"]["session_id"]
    assert isinstance(session_id, str)

    resource_root = "/api/v1/projects/p1/regions/cn-hz/upload-sessions"
    for response in (
        client.get(resource_root),
        client.get(f"{resource_root}/{session_id}"),
        client.get(f"{resource_root}/{session_id}/manifest"),
        client.get(f"{resource_root}/{session_id}/parts"),
    ):
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"


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
        f"{prefix}/{{session_id}}/raw-media",
        f"{prefix}/{{session_id}}/processing",
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
        "/api/v1/projects/{project_id}/regions/{region_code}/device-capture-facts",
    }
    assert set(paths) == expected_paths
    assert set(paths[prefix]) == {"get", "post"}
    list_operation = paths[prefix]["get"]
    list_parameters = {parameter["name"]: parameter for parameter in list_operation["parameters"]}
    assert list_parameters["cursor"]["schema"] == {
        "type": "string",
        "minLength": 16,
        "maxLength": 16_384,
    }
    list_schema = fragment["components"]["schemas"]["UploadSessionListV1"]
    assert "next_cursor" in list_schema["required"]
    assert list_schema["properties"]["next_cursor"] == {
        "type": ["string", "null"],
        "minLength": 16,
        "maxLength": 16_384,
    }

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
