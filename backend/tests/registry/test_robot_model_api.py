from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.ingest.ports import InMemoryObjectStorage
from hc_data_platform.registry.models import (
    BindRobotModelVersionRequest,
    CompleteRobotModelAssetFileRequest,
    CreateRobotModelAssetUploadRequest,
    PublishRobotModelVersionRequest,
    ReplaceRobotModelJointMappingsRequest,
    RobotAssetCompletedPart,
    RobotAssetRole,
    RobotJointDirection,
    RobotModelAssetUploadFileRequest,
    RobotModelJointMapping,
    RobotModelSummary,
    RobotModelVersion,
)
from hc_data_platform.registry.repository import InMemoryRegistryRepository
from hc_data_platform.registry.router import configure_registry, router
from hc_data_platform.registry.service import RegistryService
from hc_data_platform.security.auth import AuthContext


def _auth(
    *,
    project_id: str = "project-a",
    can_read: bool = True,
    can_manage: bool = False,
    region_code: str | None = None,
    can_robot_manage: bool = False,
) -> AuthContext:
    return AuthContext(
        subject_id="registry-reader",
        project_ids=frozenset({project_id}),
        region_codes=frozenset({region_code} if region_code else ()),
        roles=frozenset(),
        scope_pairs=frozenset(
            {(project_id, None)} | ({(project_id, region_code)} if region_code else set())
        ),
        scoped_capabilities=frozenset(
            capability
            for capability, enabled in (
                ((project_id, "robot_model.read"), can_read),
                ((project_id, "robot_model.manage"), can_manage),
                ((project_id, "robot.manage"), can_robot_manage),
            )
            if enabled
        ),
    )


def _service() -> tuple[RegistryService, InMemoryRegistryRepository]:
    repository = InMemoryRegistryRepository(
        organization_projects=(("organization-a", "project-a"),),
        models=(
            (
                "organization-a",
                RobotModelSummary(
                    id="model-a",
                    manufacturer="HC Robotics",
                    model_code="XR-01",
                    display_name="XR-01 协作机器人",
                    current_published_version_id="version-a",
                ),
            ),
        ),
        versions=(
            (
                "organization-a",
                RobotModelVersion(
                    id="version-a",
                    robot_model_id="model-a",
                    version_label="1.0.0",
                    lifecycle="PUBLISHED",
                    asset_availability="AVAILABLE",
                    publish_readiness="READY",
                    asset_manifest_hash="a" * 64,
                    validation_input_hash="b" * 64,
                    etag='"registry:version-a:1"',
                    allowed_actions=("VIEW",),
                    blocked_reasons=(),
                ),
            ),
        ),
    )
    return (
        RegistryService(
            repository,
            clock=lambda: datetime(2026, 8, 19, 10, tzinfo=timezone.utc),
        ),
        repository,
    )


def _app(current: dict[str, AuthContext | None]) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(ProblemException)
    async def handle_problem(_request: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(exc.problem.model_dump(mode="json"), status_code=exc.problem.status)

    @app.middleware("http")
    async def install_auth(request: Request, call_next: Any) -> Any:
        request.state.auth_context = current["value"]
        request.state.request_id = "registry-router-test"
        return await call_next(request)

    app.include_router(router)
    return app


def test_robot_model_read_contract_scope_and_publish_is_not_a_product_501() -> None:
    service, repository = _service()
    configure_registry(service)
    current: dict[str, AuthContext | None] = {"value": _auth(can_manage=True)}
    client = TestClient(_app(current))
    headers = {
        "Authorization": "Bearer test",
        "X-Project-ID": "project-a",
    }

    listing = client.get("/api/v1/organizations/organization-a/robot-models", headers=headers)
    assert listing.status_code == 200
    assert listing.headers["cache-control"] == "private, no-store"
    assert listing.json() == {
        "items": [
            {
                "id": "model-a",
                "manufacturer": "HC Robotics",
                "model_code": "XR-01",
                "display_name": "XR-01 协作机器人",
                "current_published_version_id": "version-a",
            }
        ],
        "page_info": {
            "has_next_page": False,
            "has_previous_page": False,
            "start_cursor": None,
            "end_cursor": None,
        },
        "snapshot_at": "2026-08-19T10:00:00Z",
        "scope": {"organization_id": "organization-a", "project_id": "project-a"},
        "request_id": "registry-router-test",
        "contract_version": "2026-08-19",
    }

    detail = client.get(
        "/api/v1/organizations/organization-a/robot-model-versions/version-a", headers=headers
    )
    assert detail.status_code == 200
    assert detail.headers["etag"] == '"registry:version-a:1"'
    assert detail.json()["data"]["allowed_actions"] == ["VIEW"]

    preflight = client.post(
        "/api/v1/organizations/organization-a/robot-model-versions/version-a:preflight-publish",
        headers={
            **headers,
            "If-Match": '"registry:version-a:1"',
            "Idempotency-Key": "preflight-published-version",
        },
    )
    assert preflight.status_code == 200
    assert preflight.json()["data"]["allowed"] is False
    assert preflight.json()["data"]["preflight_token"] is None
    assert any(item["code"] == "DRAFT_VERSION" for item in preflight.json()["data"]["blockers"])

    invalid_publish = client.post(
        "/api/v1/organizations/organization-a/robot-model-versions/version-a:publish",
        headers={**headers, "If-Match": '"registry:version-a:1"', "Idempotency-Key": "publish-a"},
        json={"preflight_token": "x" * 32},
    )
    assert invalid_publish.status_code == 422
    assert invalid_publish.json()["code"] == "ROBOT_MODEL_PUBLISH_PREFLIGHT_TOKEN_INVALID"
    assert repository.audit_events[-1].outcome == "SUCCEEDED"

    foreign_organization = client.get(
        "/api/v1/organizations/organization-b/robot-models", headers=headers
    )
    assert foreign_organization.status_code == 403
    assert foreign_organization.json()["code"] == "ORGANIZATION_SCOPE_DENIED"

    current["value"] = _auth(project_id="project-b")
    foreign_project = client.get(
        "/api/v1/organizations/organization-a/robot-models",
        headers={**headers, "X-Project-ID": "project-a"},
    )
    assert foreign_project.status_code == 403


def test_robot_model_router_rejects_missing_auth_and_missing_capability() -> None:
    service, _repository = _service()
    configure_registry(service)
    current: dict[str, AuthContext | None] = {"value": None}
    client = TestClient(_app(current))
    path = "/api/v1/organizations/organization-a/robot-models"
    assert client.get(path).status_code == 401

    current["value"] = _auth(can_read=False)
    denied = client.get(path, headers={"Authorization": "Bearer test", "X-Project-ID": "project-a"})
    assert denied.status_code == 403
    assert denied.json()["code"] == "CAPABILITY_REQUIRED"


def test_robot_model_asset_upload_direct_transfer_manifest_and_download_authorization() -> None:
    repository = InMemoryRegistryRepository(
        organization_projects=(("organization-a", "project-a"),),
        models=(
            (
                "organization-a",
                RobotModelSummary(
                    id="model-a",
                    manufacturer="HC Robotics",
                    model_code="XR-02",
                    display_name="XR-02 协作机器人",
                    current_published_version_id=None,
                ),
            ),
        ),
        versions=(
            (
                "organization-a",
                RobotModelVersion(
                    id="version-draft",
                    robot_model_id="model-a",
                    version_label="2.0.0-rc.1",
                    lifecycle="DRAFT",
                    asset_availability="MISSING",
                    publish_readiness="NOT_READY",
                    asset_manifest_hash=None,
                    validation_input_hash=None,
                    etag='"registry:version-draft:1"',
                    allowed_actions=("MANAGE",),
                    blocked_reasons=(),
                ),
            ),
        ),
    )
    storage = InMemoryObjectStorage()
    configure_registry(
        RegistryService(
            repository,
            storage=storage,
            clock=lambda: datetime(2026, 8, 19, 11, tzinfo=timezone.utc),
        )
    )
    current: dict[str, AuthContext | None] = {"value": _auth(can_manage=True)}
    client = TestClient(_app(current))
    headers = {
        "Authorization": "Bearer test",
        "X-Project-ID": "project-a",
        "Idempotency-Key": "asset-upload-a",
    }
    body = b'<robot name="xr-02"/>'
    upload = client.post(
        "/api/v1/organizations/organization-a/robot-model-versions/version-draft/upload-sessions",
        headers=headers,
        json={
            "files": [
                {
                    "relative_path": "models/xr-02.urdf",
                    "role": "URDF",
                    "media_type": "application/xml",
                    "size_bytes": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                }
            ]
        },
    )
    assert upload.status_code == 201
    assert upload.headers["cache-control"] == "private, no-store"
    assert upload.headers["location"].endswith(upload.json()["data"]["upload_id"])
    upload_data = upload.json()["data"]
    assert upload_data["status"] == "UPLOADING"
    assert upload_data["files"][0]["part_authorizations"][0]["part_number"] == 1
    assert "object_key" not in str(upload_data)

    repeated = client.post(
        "/api/v1/organizations/organization-a/robot-model-versions/version-draft/upload-sessions",
        headers=headers,
        json={
            "files": [
                {
                    "relative_path": "models/xr-02.urdf",
                    "role": "URDF",
                    "media_type": "application/xml",
                    "size_bytes": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                }
            ]
        },
    )
    assert repeated.status_code == 201
    assert repeated.json()["data"]["upload_id"] == upload_data["upload_id"]

    record = repository.get_asset_upload(
        organization_id="organization-a",
        project_id="project-a",
        upload_id=upload_data["upload_id"],
    )
    assert record is not None
    part = storage.upload_part(
        record.files[0].multipart_upload_id,
        1,
        body,
        key=record.files[0].object_key,
    )
    renewed = client.post(
        f"/api/v1/organizations/organization-a/robot-model-asset-uploads/{record.upload_id}:authorize-parts",
        headers={key: value for key, value in headers.items() if key != "Idempotency-Key"},
        json={"relative_path": "models/xr-02.urdf", "part_numbers": [1]},
    )
    assert renewed.status_code == 200
    assert renewed.headers["cache-control"] == "private, no-store"
    assert renewed.json()["items"][0]["part_number"] == 1

    completed = client.post(
        f"/api/v1/organizations/organization-a/robot-model-asset-uploads/{record.upload_id}:complete-file",
        headers={key: value for key, value in headers.items() if key != "Idempotency-Key"},
        json={
            "relative_path": "models/xr-02.urdf",
            "parts": [{"part_number": 1, "etag": part.etag}],
        },
    )
    assert completed.status_code == 200
    assert completed.json()["data"]["status"] == "COMPLETED"

    assets = client.get(
        "/api/v1/organizations/organization-a/robot-model-versions/version-draft/assets",
        headers={key: value for key, value in headers.items() if key != "Idempotency-Key"},
    )
    assert assets.status_code == 200
    assert assets.json()["items"][0]["relative_path"] == "models/xr-02.urdf"
    asset_id = assets.json()["items"][0]["asset_id"]
    download = client.get(
        "/api/v1/organizations/organization-a/robot-model-versions/"
        f"version-draft/assets/{asset_id}/download",
        headers={key: value for key, value in headers.items() if key != "Idempotency-Key"},
    )
    assert download.status_code == 200
    assert download.json()["asset_id"] == asset_id
    assert download.json()["download_url"].startswith("memory://object/")
    assert all("object_key" not in str(event) for event in repository.audit_events)

    invalid = client.post(
        "/api/v1/organizations/organization-a/robot-model-versions/version-draft/upload-sessions",
        headers={**headers, "Idempotency-Key": "invalid-asset"},
        json={
            "files": [
                {
                    "relative_path": "models/xr-02.exe",
                    "role": "URDF",
                    "media_type": "application/octet-stream",
                    "size_bytes": 1,
                    "sha256": "a" * 64,
                }
            ]
        },
    )
    assert invalid.status_code == 422


def test_robot_model_joint_mapping_replaces_a_draft_under_etag_control() -> None:
    repository = InMemoryRegistryRepository(
        organization_projects=(("organization-a", "project-a"),),
        versions=(
            (
                "organization-a",
                RobotModelVersion(
                    id="version-draft",
                    robot_model_id="model-a",
                    version_label="2.0.0-rc.1",
                    lifecycle="DRAFT",
                    asset_availability="AVAILABLE",
                    publish_readiness="MAPPING_REQUIRED",
                    asset_manifest_hash="a" * 64,
                    validation_input_hash=None,
                    etag='"registry:version-draft:1"',
                    allowed_actions=("MANAGE",),
                    blocked_reasons=(),
                ),
            ),
        ),
    )
    service = RegistryService(
        repository,
        clock=lambda: datetime(2026, 8, 19, 11, tzinfo=timezone.utc),
    )
    command = ReplaceRobotModelJointMappingsRequest(
        mappings=(
            RobotModelJointMapping(
                source_joint_name="shoulder_pan_joint",
                target_joint_name="joint_1",
                direction=RobotJointDirection.SAME,
            ),
        )
    )
    updated = service.replace_robot_model_joint_mappings(
        auth=_auth(can_manage=True),
        organization_id="organization-a",
        project_id="project-a",
        version_id="version-draft",
        expected_etag='"registry:version-draft:1"',
        idempotency_key="mapping-replace-first",
        request_id="joint-mapping-replace",
        command=command,
    )
    assert updated.data.publish_readiness == "SAMPLE_VALIDATION_REQUIRED"
    mappings = service.list_robot_model_joint_mappings(
        auth=_auth(),
        organization_id="organization-a",
        project_id="project-a",
        version_id="version-draft",
        request_id="joint-mapping-list",
    )
    assert mappings.items == command.mappings
    assert mappings.mapping_hash is not None

    replay = service.replace_robot_model_joint_mappings(
        auth=_auth(can_manage=True),
        organization_id="organization-a",
        project_id="project-a",
        version_id="version-draft",
        expected_etag='"registry:version-draft:1"',
        idempotency_key="mapping-replace-first",
        request_id="joint-mapping-replay",
        command=command,
    )
    assert replay.data.etag == updated.data.etag

    with pytest.raises(ProblemException) as reused:
        service.replace_robot_model_joint_mappings(
            auth=_auth(can_manage=True),
            organization_id="organization-a",
            project_id="project-a",
            version_id="version-draft",
            expected_etag='"registry:version-draft:1"',
            idempotency_key="mapping-replace-first",
            request_id="joint-mapping-reused-key",
            command=ReplaceRobotModelJointMappingsRequest(mappings=()),
        )
    assert reused.value.problem.code == "IDEMPOTENCY_KEY_REUSED"

    with pytest.raises(ProblemException) as stale:
        service.replace_robot_model_joint_mappings(
            auth=_auth(can_manage=True),
            organization_id="organization-a",
            project_id="project-a",
            version_id="version-draft",
            expected_etag='"registry:version-draft:1"',
            idempotency_key="mapping-replace-stale",
            request_id="joint-mapping-stale",
            command=command,
        )
    assert stale.value.problem.code == "ROBOT_MODEL_VERSION_ETAG_MISMATCH"


def test_robot_model_publish_uses_a_durable_preflight_and_one_time_proof() -> None:
    repository = InMemoryRegistryRepository(
        organization_projects=(("organization-a", "project-a"),),
        versions=(
            (
                "organization-a",
                RobotModelVersion(
                    id="version-draft",
                    robot_model_id="model-a",
                    version_label="2.0.0-rc.1",
                    lifecycle="DRAFT",
                    asset_availability="MISSING",
                    publish_readiness="CONFIGURATION_REQUIRED",
                    asset_manifest_hash=None,
                    validation_input_hash=None,
                    etag='"registry:version-draft:1"',
                    allowed_actions=("MANAGE",),
                    blocked_reasons=(),
                ),
            ),
        ),
    )
    storage = InMemoryObjectStorage()
    now = datetime(2026, 8, 19, 12, tzinfo=timezone.utc)
    service = RegistryService(repository, storage=storage, clock=lambda: now)
    auth = _auth(can_manage=True)
    body = b'<robot name="xr"><joint name="joint_1" type="revolute"/></robot>'
    created = service.create_asset_upload(
        auth=auth,
        organization_id="organization-a",
        project_id="project-a",
        version_id="version-draft",
        request_id="create-asset",
        idempotency_key="asset-create",
        command=CreateRobotModelAssetUploadRequest(
            files=(
                RobotModelAssetUploadFileRequest(
                    relative_path="models/xr.urdf",
                    role=RobotAssetRole.URDF,
                    media_type="application/xml",
                    size_bytes=len(body),
                    sha256=hashlib.sha256(body).hexdigest(),
                ),
            )
        ),
    )
    record = repository.get_asset_upload(
        organization_id="organization-a",
        project_id="project-a",
        upload_id=created.data.upload_id,
    )
    assert record is not None
    part = storage.upload_part(
        record.files[0].multipart_upload_id,
        1,
        body,
        key=record.files[0].object_key,
    )
    service.complete_asset_upload_file(
        auth=auth,
        organization_id="organization-a",
        project_id="project-a",
        upload_id=record.upload_id,
        request_id="complete-asset",
        command=CompleteRobotModelAssetFileRequest(
            relative_path="models/xr.urdf",
            parts=(RobotAssetCompletedPart(part_number=1, etag=part.etag),),
        ),
    )
    uploaded = service.get_robot_model_version(
        auth=auth,
        organization_id="organization-a",
        project_id="project-a",
        version_id="version-draft",
        request_id="draft-after-upload",
    )
    mapped = service.replace_robot_model_joint_mappings(
        auth=auth,
        organization_id="organization-a",
        project_id="project-a",
        version_id="version-draft",
        expected_etag=uploaded.data.etag,
        idempotency_key="mapping-replace-publish",
        request_id="set-mappings",
        command=ReplaceRobotModelJointMappingsRequest(
            mappings=(
                RobotModelJointMapping(
                    source_joint_name="joint_1",
                    target_joint_name="actuator_1",
                    direction=RobotJointDirection.SAME,
                ),
            )
        ),
    )
    preflight = service.preflight_robot_model_publish(
        auth=auth,
        organization_id="organization-a",
        project_id="project-a",
        version_id="version-draft",
        expected_etag=mapped.data.etag,
        idempotency_key="publish-once",
        request_id="publish-preflight",
    )
    assert preflight.data.allowed is True
    assert preflight.data.preflight_token is not None
    assert {check.code for check in preflight.data.checks} >= {
        "ASSET_MANIFEST_CURRENT",
        "URDF_WELL_FORMED",
        "JOINT_MAPPINGS_COMPLETE",
    }
    repeated_preflight = service.preflight_robot_model_publish(
        auth=auth,
        organization_id="organization-a",
        project_id="project-a",
        version_id="version-draft",
        expected_etag=mapped.data.etag,
        idempotency_key="publish-once",
        request_id="publish-preflight-repeat",
    )
    assert repeated_preflight.data.preflight_token == preflight.data.preflight_token

    published = service.publish_robot_model_version(
        auth=auth,
        organization_id="organization-a",
        project_id="project-a",
        version_id="version-draft",
        expected_etag=mapped.data.etag,
        idempotency_key="publish-once",
        request_id="publish",
        command=PublishRobotModelVersionRequest(preflight_token=preflight.data.preflight_token),
    )
    assert published.data.lifecycle == "PUBLISHED"
    assert published.data.publish_readiness == "READY"
    replay = service.publish_robot_model_version(
        auth=auth,
        organization_id="organization-a",
        project_id="project-a",
        version_id="version-draft",
        expected_etag=mapped.data.etag,
        idempotency_key="publish-once",
        request_id="publish-replay",
        command=PublishRobotModelVersionRequest(preflight_token=preflight.data.preflight_token),
    )
    assert replay.data.etag == published.data.etag

    with pytest.raises(ProblemException) as invalid_token:
        service.publish_robot_model_version(
            auth=auth,
            organization_id="organization-a",
            project_id="project-a",
            version_id="version-draft",
            expected_etag=mapped.data.etag,
            idempotency_key="publish-once",
            request_id="publish-invalid-token",
            command=PublishRobotModelVersionRequest(preflight_token="x" * 32),
        )
    assert invalid_token.value.problem.code == "ROBOT_MODEL_PUBLISH_PREFLIGHT_TOKEN_INVALID"


def test_robot_model_binding_requires_both_model_and_robot_authority() -> None:
    repository = InMemoryRegistryRepository(
        organization_projects=(("organization-a", "project-a"),),
        versions=(
            (
                "organization-a",
                RobotModelVersion(
                    id="version-published",
                    robot_model_id="model-a",
                    version_label="2.0.0",
                    lifecycle="PUBLISHED",
                    asset_availability="AVAILABLE",
                    publish_readiness="READY",
                    asset_manifest_hash="a" * 64,
                    validation_input_hash="b" * 64,
                    etag='"registry:version-published:2"',
                    allowed_actions=("VIEW",),
                    blocked_reasons=(),
                ),
            ),
        ),
        robot_etags=(("project-a", "region-a", "robot-a", '"robot-a:1"'),),
    )
    service = RegistryService(
        repository,
        clock=lambda: datetime(2026, 8, 19, 13, tzinfo=timezone.utc),
    )
    command = BindRobotModelVersionRequest(
        region_code="region-a",
        robot_id="robot-a",
        robot_etag='"robot-a:1"',
    )
    auth = _auth(can_manage=True, region_code="region-a", can_robot_manage=True)
    bound = service.bind_robot_model_version(
        auth=auth,
        organization_id="organization-a",
        project_id="project-a",
        version_id="version-published",
        idempotency_key="bind-robot-a",
        request_id="bind-robot",
        command=command,
    )
    assert bound.status.value == "ACTIVE"
    replay = service.bind_robot_model_version(
        auth=auth,
        organization_id="organization-a",
        project_id="project-a",
        version_id="version-published",
        idempotency_key="bind-robot-a",
        request_id="bind-robot-replay",
        command=command,
    )
    assert replay.binding_id == bound.binding_id
    bindings = service.list_robot_model_bindings(
        auth=_auth(),
        organization_id="organization-a",
        project_id="project-a",
        version_id="version-published",
        request_id="list-bindings",
    )
    assert [item.binding_id for item in bindings.items] == [bound.binding_id]

    with pytest.raises(ProblemException) as missing_robot_authority:
        service.bind_robot_model_version(
            auth=_auth(can_manage=True, region_code="region-a"),
            organization_id="organization-a",
            project_id="project-a",
            version_id="version-published",
            idempotency_key="bind-robot-without-capability",
            request_id="bind-robot-without-capability",
            command=command,
        )
    assert missing_robot_authority.value.problem.code == "CAPABILITY_REQUIRED"
