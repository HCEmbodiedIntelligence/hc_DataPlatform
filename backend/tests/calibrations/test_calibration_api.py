from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.calibrations.models import (
    CalibrationSetRecord,
    CalibrationValidation,
)
from hc_data_platform.calibrations.repository import InMemoryCalibrationRepository
from hc_data_platform.calibrations.router import configure_calibrations, router
from hc_data_platform.calibrations.service import CalibrationService
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.security.auth import AuthContext

PROJECT_ID = "project-a"
REGION_CODE = "cn-shanghai-01"
ORGANIZATION_ID = "org-a"
DATASET_ID = "dataset_p16calibration"
DATASET_VERSION_ID = "version_p16calibration"


def _auth(
    *, can_read: bool = True, can_publish: bool = False, can_dataset_read: bool = False
) -> AuthContext:
    return AuthContext(
        subject_id="calibration-reader",
        project_ids=frozenset({PROJECT_ID}),
        organization_ids=frozenset({ORGANIZATION_ID}),
        region_codes=frozenset({REGION_CODE}),
        roles=frozenset(),
        scope_pairs=frozenset({(PROJECT_ID, REGION_CODE)}),
        organization_scope_triples=frozenset({(ORGANIZATION_ID, PROJECT_ID, REGION_CODE)}),
        scoped_capabilities=(
            (frozenset({(PROJECT_ID, "calibration.read")}) if can_read else frozenset())
            | (frozenset({(PROJECT_ID, "calibration.publish")}) if can_publish else frozenset())
            | (frozenset({(PROJECT_ID, "dataset.read")}) if can_dataset_read else frozenset())
        ),
        organization_scoped_capabilities=(
            (
                frozenset({(ORGANIZATION_ID, PROJECT_ID, "calibration.read")})
                if can_read
                else frozenset()
            )
            | (
                frozenset({(ORGANIZATION_ID, PROJECT_ID, "calibration.publish")})
                if can_publish
                else frozenset()
            )
            | (
                frozenset({(ORGANIZATION_ID, PROJECT_ID, "dataset.read")})
                if can_dataset_read
                else frozenset()
            )
        ),
    )


def _service() -> tuple[CalibrationService, InMemoryCalibrationRepository]:
    item = CalibrationSetRecord(
        id="calibration-a",
        robot_instance_id="robot-a",
        component_id="camera-a",
        version="1",
        snapshot_status="DRAFT",
        availability=None,
        content_hash="a" * 64,
        validation_context_hash="b" * 64,
        validation=CalibrationValidation(
            status="PASSED",
            content_hash="a" * 64,
            validation_context_hash="b" * 64,
            report_id="report-a",
        ),
        etag='"calibration-a:1"',
        allowed_actions=("VIEW",),
    )
    repository = InMemoryCalibrationRepository(
        sets=((PROJECT_ID, REGION_CODE, item),),
        dataset_versions=((PROJECT_ID, REGION_CODE, DATASET_ID, DATASET_VERSION_ID),),
    )
    return (
        CalibrationService(
            repository,
            clock=lambda: datetime(2026, 8, 19, 12, tzinfo=timezone.utc),
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
        request.state.request_id = "calibration-router-test"
        return await call_next(request)

    app.include_router(router)
    return app


def test_calibration_read_contract_scope_and_durable_publish() -> None:
    service, repository = _service()
    configure_calibrations(service)
    current: dict[str, AuthContext | None] = {"value": _auth()}
    client = TestClient(_app(current))
    root = f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}/calibration-sets"
    headers = {"Authorization": "Bearer test", "X-Organization-Id": ORGANIZATION_ID}

    listing = client.get(root, params={"robot_id": "robot-a"}, headers=headers)
    assert listing.status_code == 200
    assert listing.headers["cache-control"] == "private, no-store"
    assert listing.json()["items"][0]["component_id"] == "camera-a"

    detail = client.get(f"{root}/calibration-a", headers=headers)
    assert detail.status_code == 200
    assert detail.headers["etag"] == '"calibration-a:1"'
    assert detail.json()["data"]["validation"]["report_id"] == "report-a"

    current["value"] = _auth(can_publish=True)
    preflight = client.post(
        f"{root}/calibration-a/versions/1:preflight-publish",
        headers={
            **headers,
            "If-Match": '"calibration-a:1"',
            "Idempotency-Key": "calibration-publish-a",
        },
        json={
            "expected_hash": "a" * 64,
            "expected_etag": '"calibration-a:1"',
            "validation_report_id": "report-a",
            "compatibility_check_id": None,
            "change_summary": "P16 验证通过后发布固定标定。",
            "acknowledge_warning_codes": [],
            "validation_context_hash": "b" * 64,
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["cache-control"] == "private, no-store"
    token = preflight.json()["data"]["preflight_token"]
    assert preflight.json()["data"]["allowed"] is True
    assert isinstance(token, str) and len(token) >= 32

    published = client.post(
        f"{root}/calibration-a/versions/1:publish",
        headers={
            **headers,
            "If-Match": '"calibration-a:1"',
            "Idempotency-Key": "calibration-publish-a",
        },
        json={"preflight_token": token},
    )
    assert published.status_code == 200
    assert published.headers["etag"] != '"calibration-a:1"'
    assert published.json()["data"]["snapshot_status"] == "READY"
    assert published.json()["data"]["availability"] == "ACTIVE"
    replay = client.post(
        f"{root}/calibration-a/versions/1:publish",
        headers={
            **headers,
            "If-Match": '"calibration-a:1"',
            "Idempotency-Key": "calibration-publish-a",
        },
        json={"preflight_token": token},
    )
    assert replay.status_code == 200
    assert replay.json()["data"]["etag"] == published.json()["data"]["etag"]
    assert repository.audit_events[-1].action == "calibration_set.published"

    stale_replay = client.post(
        f"{root}/calibration-a/versions/1:publish",
        headers={
            **headers,
            "If-Match": published.headers["etag"],
            "Idempotency-Key": "calibration-publish-a",
        },
        json={"preflight_token": token},
    )
    assert stale_replay.status_code == 412
    assert stale_replay.json()["code"] == "CALIBRATION_SET_ETAG_MISMATCH"
    malformed = client.post(
        f"{root}/calibration-a/versions/1:publish",
        headers={
            **headers,
            "If-Match": published.headers["etag"],
            "Idempotency-Key": "calibration-publish-malformed",
        },
        json={"preflight_token": "x" * 64},
    )
    assert malformed.status_code == 422
    assert malformed.json()["code"] == "CALIBRATION_PUBLISH_PREFLIGHT_TOKEN_INVALID"

    missing = client.get(f"{root}/unknown", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["code"] == "CALIBRATION_SET_NOT_FOUND"

    current["value"] = _auth()
    denied = client.post(
        f"{root}/calibration-a/versions/1:preflight-publish",
        headers={
            **headers,
            "If-Match": published.headers["etag"],
            "Idempotency-Key": "calibration-publish-denied",
        },
        json={
            "expected_hash": "a" * 64,
            "expected_etag": published.headers["etag"],
            "validation_report_id": "report-a",
            "compatibility_check_id": None,
            "change_summary": "权限拒绝测试。",
            "acknowledge_warning_codes": [],
            "validation_context_hash": "b" * 64,
        },
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "CAPABILITY_REQUIRED"


def test_calibration_router_rejects_missing_auth_and_capability() -> None:
    service, _repository = _service()
    configure_calibrations(service)
    current: dict[str, AuthContext | None] = {"value": None}
    client = TestClient(_app(current))
    path = f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}/calibration-sets"
    assert client.get(path).status_code == 401

    current["value"] = _auth(can_read=False)
    denied = client.get(
        path,
        headers={"Authorization": "Bearer test", "X-Organization-Id": ORGANIZATION_ID},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "CAPABILITY_REQUIRED"


def test_calibration_router_rejects_a_same_project_foreign_organization() -> None:
    service, _repository = _service()
    configure_calibrations(service)
    current: dict[str, AuthContext | None] = {"value": _auth()}
    client = TestClient(_app(current))
    path = f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}/calibration-sets"

    denied = client.get(
        path,
        headers={"Authorization": "Bearer test", "X-Organization-Id": "org-foreign"},
    )

    assert denied.status_code == 403
    assert denied.json()["code"] == "ORGANIZATION_SCOPE_DENIED"


def test_calibration_create_document_validate_report_and_publish_real_facts() -> None:
    service, repository = _service()
    configure_calibrations(service)
    current: dict[str, AuthContext | None] = {
        "value": _auth(can_publish=True, can_dataset_read=True)
    }
    client = TestClient(_app(current))
    root = f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}/calibration-sets"
    headers = {
        "Authorization": "Bearer test",
        "X-Organization-Id": ORGANIZATION_ID,
        "Idempotency-Key": "calibration-create-v1",
    }
    document = {
        "frame_transforms": [
            {
                "parent_frame": "base_link",
                "child_frame": "camera_front",
                "translation_m": [0.12, 0.0, 0.42],
                "quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
                "covariance": None,
            }
        ],
        "camera_intrinsics": [
            {
                "frame_id": "camera_front",
                "width_px": 1920,
                "height_px": 1080,
                "fx_px": 1010.2,
                "fy_px": 1008.4,
                "cx_px": 960.0,
                "cy_px": 540.0,
                "distortion": [0.01, -0.03, 0.0, 0.0, 0.0],
            }
        ],
    }
    command = {
        "set_id": "calibration-real-v1",
        "robot_instance_id": "robot-a",
        "component_id": "camera-a",
        "source": "IMPORT",
        "document": document,
    }
    created = client.post(root, headers=headers, json=command)
    assert created.status_code == 201
    assert created.headers["cache-control"] == "private, no-store"
    assert created.headers["location"].endswith("/calibration-real-v1")
    created_hash = created.json()["data"]["content_hash"]
    created_etag = created.headers["etag"]
    assert len(created_hash) == 64

    replay = client.post(root, headers=headers, json=command)
    assert replay.status_code == 201
    assert replay.json()["data"] == created.json()["data"]
    reused = client.post(
        root,
        headers=headers,
        json={**command, "source": "MANUAL"},
    )
    assert reused.status_code == 409
    assert reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"

    version_path = f"{root}/calibration-real-v1/versions/1"
    durable_document = client.get(f"{version_path}/document", headers=headers)
    assert durable_document.status_code == 200
    assert durable_document.json()["data"]["document"] == document
    assert durable_document.json()["data"]["content_hash"] == created_hash

    validated = client.post(
        f"{version_path}:validate",
        headers={
            **headers,
            "Idempotency-Key": "calibration-validate-v1",
            "If-Match": created_etag,
        },
    )
    assert validated.status_code == 200
    report = validated.json()["data"]
    assert report["status"] == "PASSED"
    assert report["findings"] == []
    assert report["content_hash"] == created_hash
    report_read = client.get(
        f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}/calibration-validation-reports/{report['id']}",
        headers=headers,
    )
    assert report_read.status_code == 200
    assert report_read.json()["data"] == report
    assert repository.audit_events[-1].action == "calibration_validation_report.read"

    updated = client.get(f"{root}/calibration-real-v1", headers=headers)
    assert updated.status_code == 200
    assert updated.json()["data"]["validation"]["report_id"] == report["id"]
    association_path = f"{version_path}/dataset-associations"
    before_ready = client.post(
        association_path,
        headers={
            **headers,
            "Idempotency-Key": "calibration-associate-before-ready",
            "If-Match": updated.headers["etag"],
        },
        json={"dataset_id": DATASET_ID, "dataset_version_id": DATASET_VERSION_ID},
    )
    assert before_ready.status_code == 409
    assert before_ready.json()["code"] == "CALIBRATION_DATASET_ASSOCIATION_REQUIRES_READY_VERSION"
    preflight = client.post(
        f"{version_path}:preflight-publish",
        headers={
            **headers,
            "Idempotency-Key": "calibration-real-preflight",
            "If-Match": updated.headers["etag"],
        },
        json={
            "expected_hash": created_hash,
            "expected_etag": updated.headers["etag"],
            "validation_report_id": report["id"],
            "compatibility_check_id": None,
            "change_summary": "真实标定内容已通过服务端检查。",
            "acknowledge_warning_codes": [],
            "validation_context_hash": report["validation_context_hash"],
        },
    )
    assert preflight.status_code == 200
    assert preflight.json()["data"]["allowed"] is True
    published = client.post(
        f"{version_path}:publish",
        headers={
            **headers,
            "Idempotency-Key": "calibration-real-preflight",
            "If-Match": updated.headers["etag"],
        },
        json={"preflight_token": preflight.json()["data"]["preflight_token"]},
    )
    assert published.status_code == 200
    associated = client.post(
        association_path,
        headers={
            **headers,
            "Idempotency-Key": "calibration-dataset-association",
            "If-Match": published.headers["etag"],
        },
        json={"dataset_id": DATASET_ID, "dataset_version_id": DATASET_VERSION_ID},
    )
    assert associated.status_code == 201
    assert associated.json()["data"] == {
        "set_id": "calibration-real-v1",
        "calibration_version": "1",
        "dataset_id": DATASET_ID,
        "dataset_version_id": DATASET_VERSION_ID,
        "associated_by": "calibration-reader",
        "associated_at": "2026-08-19T12:00:00Z",
    }
    association_replay = client.post(
        association_path,
        headers={
            **headers,
            "Idempotency-Key": "calibration-dataset-association",
            "If-Match": published.headers["etag"],
        },
        json={"dataset_id": DATASET_ID, "dataset_version_id": DATASET_VERSION_ID},
    )
    assert association_replay.status_code == 201
    assert association_replay.json()["data"] == associated.json()["data"]
    listed_associations = client.get(association_path, headers=headers)
    assert listed_associations.status_code == 200
    assert listed_associations.json()["items"] == [associated.json()["data"]]
    invalid_dataset = client.post(
        association_path,
        headers={
            **headers,
            "Idempotency-Key": "calibration-invalid-dataset-association",
            "If-Match": published.headers["etag"],
        },
        json={
            "dataset_id": "dataset_notavailable",
            "dataset_version_id": DATASET_VERSION_ID,
        },
    )
    assert invalid_dataset.status_code == 422
    assert invalid_dataset.json()["code"] == "DATASET_VERSION_ASSOCIATION_INVALID"


def test_calibration_validation_reports_invalid_real_graph_without_publishing_it() -> None:
    service, _repository = _service()
    configure_calibrations(service)
    current: dict[str, AuthContext | None] = {"value": _auth(can_publish=True)}
    client = TestClient(_app(current))
    root = f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}/calibration-sets"
    created = client.post(
        root,
        headers={
            "Authorization": "Bearer test",
            "X-Organization-Id": ORGANIZATION_ID,
            "Idempotency-Key": "calibration-invalid-create",
        },
        json={
            "set_id": "calibration-cycle-v1",
            "robot_instance_id": "robot-a",
            "component_id": "camera-a",
            "source": "MANUAL",
            "document": {
                "frame_transforms": [
                    {
                        "parent_frame": "base",
                        "child_frame": "camera",
                        "translation_m": [0, 0, 0],
                        "quaternion_xyzw": [0, 0, 0, 1],
                    },
                    {
                        "parent_frame": "camera",
                        "child_frame": "base",
                        "translation_m": [0, 0, 0],
                        "quaternion_xyzw": [0, 0, 0, 1],
                    },
                ]
            },
        },
    )
    assert created.status_code == 201
    validated = client.post(
        f"{root}/calibration-cycle-v1/versions/1:validate",
        headers={
            "Authorization": "Bearer test",
            "X-Organization-Id": ORGANIZATION_ID,
            "Idempotency-Key": "calibration-invalid-validate",
            "If-Match": created.headers["etag"],
        },
    )
    assert validated.status_code == 200
    report = validated.json()["data"]
    assert report["status"] == "FAILED"
    assert {item["code"] for item in report["findings"]} == {"CALIBRATION_FRAME_CYCLE"}
    updated = client.get(
        f"{root}/calibration-cycle-v1",
        headers={"Authorization": "Bearer test", "X-Organization-Id": ORGANIZATION_ID},
    )
    blocked = client.post(
        f"{root}/calibration-cycle-v1/versions/1:preflight-publish",
        headers={
            "Authorization": "Bearer test",
            "X-Organization-Id": ORGANIZATION_ID,
            "Idempotency-Key": "calibration-invalid-preflight",
            "If-Match": updated.headers["etag"],
        },
        json={
            "expected_hash": created.json()["data"]["content_hash"],
            "expected_etag": updated.headers["etag"],
            "validation_report_id": report["id"],
            "compatibility_check_id": None,
            "change_summary": "错误图应阻断发布。",
            "acknowledge_warning_codes": [],
            "validation_context_hash": report["validation_context_hash"],
        },
    )
    assert blocked.status_code == 200
    assert blocked.json()["data"]["allowed"] is False
    assert blocked.json()["data"]["blockers"][0]["code"] == "CALIBRATION_VALIDATION_REQUIRED"


def test_calibration_recalibration_creates_immutable_successor_and_version_history() -> None:
    service, _repository = _service()
    configure_calibrations(service)
    current: dict[str, AuthContext | None] = {"value": _auth(can_publish=True)}
    client = TestClient(_app(current))
    root = f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}/calibration-sets"
    created = client.post(
        root,
        headers={
            "Authorization": "Bearer test",
            "X-Organization-Id": ORGANIZATION_ID,
            "Idempotency-Key": "calibration-recal-create",
        },
        json={
            "set_id": "calibration-recal-v1",
            "robot_instance_id": "robot-a",
            "component_id": "camera-a",
            "source": "MANUAL",
            "document": {
                "frame_transforms": [
                    {
                        "parent_frame": "base_link",
                        "child_frame": "camera_front",
                        "translation_m": [0.1, 0, 0.4],
                        "quaternion_xyzw": [0, 0, 0, 1],
                    }
                ]
            },
        },
    )
    assert created.status_code == 201
    recalibrated = client.post(
        f"{root}/calibration-recal-v1/versions",
        headers={
            "Authorization": "Bearer test",
            "X-Organization-Id": ORGANIZATION_ID,
            "If-Match": created.headers["etag"],
            "Idempotency-Key": "calibration-recal-v2",
        },
        json={
            "change_summary": "重新测量前置相机外参。",
            "document": {
                "frame_transforms": [
                    {
                        "parent_frame": "base_link",
                        "child_frame": "camera_front",
                        "translation_m": [0.14, 0, 0.44],
                        "quaternion_xyzw": [0, 0, 0, 1],
                    }
                ]
            },
        },
    )
    assert recalibrated.status_code == 201
    assert recalibrated.json()["data"]["version"] == "2"
    assert recalibrated.json()["data"]["snapshot_status"] == "DRAFT"
    assert recalibrated.json()["data"]["validation"] is None
    assert recalibrated.json()["data"]["content_hash"] != created.json()["data"]["content_hash"]

    history = client.get(
        f"{root}/calibration-recal-v1/versions",
        headers={"Authorization": "Bearer test", "X-Organization-Id": ORGANIZATION_ID},
    )
    assert history.status_code == 200
    assert [item["version"] for item in history.json()["items"]] == ["2", "1"]
    assert history.json()["items"][0]["source"] == "RECALIBRATION"
    original = client.get(
        f"{root}/calibration-recal-v1/versions/1/document",
        headers={"Authorization": "Bearer test", "X-Organization-Id": ORGANIZATION_ID},
    )
    successor = client.get(
        f"{root}/calibration-recal-v1/versions/2/document",
        headers={"Authorization": "Bearer test", "X-Organization-Id": ORGANIZATION_ID},
    )
    assert original.json()["data"]["document"]["frame_transforms"][0]["translation_m"] == [
        0.1,
        0.0,
        0.4,
    ]
    assert successor.json()["data"]["document"]["frame_transforms"][0]["translation_m"] == [
        0.14,
        0.0,
        0.44,
    ]

    replay = client.post(
        f"{root}/calibration-recal-v1/versions",
        headers={
            "Authorization": "Bearer test",
            "X-Organization-Id": ORGANIZATION_ID,
            "If-Match": created.headers["etag"],
            "Idempotency-Key": "calibration-recal-v2",
        },
        json={
            "change_summary": "重新测量前置相机外参。",
            "document": {
                "frame_transforms": [
                    {
                        "parent_frame": "base_link",
                        "child_frame": "camera_front",
                        "translation_m": [0.14, 0, 0.44],
                        "quaternion_xyzw": [0, 0, 0, 1],
                    }
                ]
            },
        },
    )
    assert replay.status_code == 201
    assert replay.json()["data"] == recalibrated.json()["data"]
    stale = client.post(
        f"{root}/calibration-recal-v1/versions",
        headers={
            "Authorization": "Bearer test",
            "X-Organization-Id": ORGANIZATION_ID,
            "If-Match": created.headers["etag"],
            "Idempotency-Key": "calibration-recal-stale",
        },
        json={
            "change_summary": "使用陈旧版本测试。",
            "document": {
                "frame_transforms": [
                    {
                        "parent_frame": "base_link",
                        "child_frame": "camera_front",
                        "translation_m": [0.16, 0, 0.45],
                        "quaternion_xyzw": [0, 0, 0, 1],
                    }
                ]
            },
        },
    )
    assert stale.status_code == 412
    assert stale.json()["code"] == "CALIBRATION_SET_ETAG_MISMATCH"
