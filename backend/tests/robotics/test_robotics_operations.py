from __future__ import annotations

from datetime import UTC, datetime

import pytest
from conftest import TEST_ENGINE, TEST_SESSIONS

from app.core.db import Base
from app.domains.robotics import models
from app.domains.robotics.router import router as robotics_router

ORGANIZATION = "org_fx_01"
PROJECT = "prj_fx_01"
REGION = "cn-shanghai-1"
ORG_ROOT = f"/organizations/{ORGANIZATION}"
REGION_ROOT = f"/projects/{PROJECT}/regions/{REGION}"
HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64

ROBOTICS_TABLES = [
    models.RobotModel.__table__,
    models.RobotModelVersion.__table__,
    models.RobotAssetUploadSession.__table__,
    models.RobotValidationReport.__table__,
    models.RobotModelBinding.__table__,
    models.Robot.__table__,
    models.Component.__table__,
    models.RoboticsPreflight.__table__,
    models.RoboticsJob.__table__,
    models.CalibrationSet.__table__,
    models.CalibrationVersion.__table__,
    models.CalibrationReport.__table__,
    models.DataSchema.__table__,
    models.DataSchemaVersion.__table__,
    models.SchemaCompatibilityCheck.__table__,
    models.SchemaImportValidation.__table__,
    models.DatasetSchemaSnapshot.__table__,
]

CAPABILITIES = (
    "robot_model.read",
    "robot_model.create",
    "robot_model.validate",
    "robot_model.publish",
    "robot_model.disable",
    "robot_model_binding.read",
    "robot_model_binding.manage",
    "robot.read",
    "robot.create",
    "robot.update",
    "robot.disable",
    "robot_component.create",
    "robot_component.update",
    "robot_component.change_mount",
    "robot_component.disable",
    "calibration.read",
    "calibration.create",
    "calibration.validate",
    "calibration.publish",
    "data_schema.read",
    "data_schema.create",
    "data_schema.validate",
    "data_schema.publish",
    "data_schema.import",
    "episode.read",
)


def headers(*, idem: str | None = None, etag: str | None = None, token: str | None = None):
    result = {
        "Authorization": f"Bearer test:robotics_admin:{','.join(CAPABILITIES)}",
        "X-Organization-Id": ORGANIZATION,
        "X-Project-Id": PROJECT,
        "X-Region-Code": REGION,
        "X-Client-Version": "test-1",
    }
    if idem is not None:
        result["Idempotency-Key"] = f"robotics-{idem}"
    if etag is not None:
        result["If-Match"] = etag
    if token is not None:
        result["X-Preflight-Token"] = token
    return result


async def prepare_robotics() -> None:
    async with TEST_ENGINE.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=ROBOTICS_TABLES))
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(sync, tables=ROBOTICS_TABLES)
        )


async def seed_robot_component() -> tuple[str, str, str]:
    now = datetime.now(UTC)
    async with TEST_SESSIONS.begin() as session:
        session.add(
            models.RobotModel(
                model_id="robot_model_fx_01",
                organization_id=ORGANIZATION,
                manufacturer="Fixture",
                normalized_manufacturer="fixture",
                model_code="FX-01",
                normalized_model_code="fx-01",
                display_name="Fixture model",
                current_published_version_id=None,
                status="ACTIVE",
                resource_version="1",
                etag='"rv-1"',
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            models.Robot(
                robot_id="robot_fx_01",
                organization_id=ORGANIZATION,
                project_id=PROJECT,
                region_code=REGION,
                display_name="Fixture robot",
                serial_no="FIXTURE-ROBOT-01",
                robot_model_id="robot_model_fx_01",
                lifecycle="ACTIVE",
                connectivity={"state": "ONLINE", "observed_at": now.isoformat()},
                topology_revision="1",
                resource_version="1",
                etag='"rv-1"',
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            models.Component(
                component_id="component_fx_01",
                organization_id=ORGANIZATION,
                project_id=PROJECT,
                region_code=REGION,
                robot_id="robot_fx_01",
                parent_component_id=None,
                component_type="CAMERA",
                display_name="Fixture camera",
                serial_no="FIXTURE-CAMERA-01",
                lifecycle="ACTIVE",
                connectivity={"state": "ONLINE", "observed_at": now.isoformat()},
                sort_key="00000000",
                bindings=[],
                frames=[],
                channels=[],
                resource_version="1",
                etag='"rv-1"',
                created_at=now,
                updated_at=now,
            )
        )
    return "robot_model_fx_01", "robot_fx_01", "component_fx_01"


def assert_status(response, expected: int = 200):
    assert response.status_code == expected, response.text
    return response.json() if response.content else None


@pytest.mark.asyncio
async def test_robotics_openapi_operation_registry_matches_frozen_110() -> None:
    operation_ids = [route.operation_id for route in robotics_router.routes]
    assert len(operation_ids) == 110
    assert len(set(operation_ids)) == 110
    assert "createRobotModel" in operation_ids
    assert "calibrationPublishSet" in operation_ids
    assert "dataSchemaGetSnapshotDiff" in operation_ids
    assert "preflightCalibrationAvailabilityChange" not in operation_ids
    assert "deprecateSchemaVersion" not in operation_ids


@pytest.mark.asyncio
async def test_robotics_three_aggregate_creation_smoke(client) -> None:
    await prepare_robotics()

    model_created = assert_status(
        await client.post(
            f"{ORG_ROOT}/robot-models",
            headers=headers(idem="model-create"),
            json={"manufacturer": "Fixture", "model_code": "FX-1", "display_name": "Fixture Robot"},
        ),
        201,
    )
    model_id = model_created["data"]["robot_model"]["id"]
    assert model_created["data"]["initial_version"]["lifecycle"] == "DRAFT"

    robot_created = assert_status(
        await client.post(
            f"{REGION_ROOT}/robots",
            headers=headers(idem="robot-create"),
            json={"display_name": "Robot 1", "serial_no": "SERIAL-1", "robot_model_id": model_id},
        ),
        201,
    )
    robot_id = robot_created["data"]["id"]
    robot_etag = robot_created["data"]["etag"]
    preflight = assert_status(
        await client.post(
            f"{REGION_ROOT}/robots/{robot_id}/components:preflight",
            headers=headers(idem="component-preflight"),
            json={
                "component_type": "CAMERA",
                "display_name": "Camera",
                "topology_revision": "1",
                "valid_from": datetime.now(UTC).isoformat(),
            },
        )
    )["data"]["preflight_token"]
    component_created = assert_status(
        await client.post(
            f"{REGION_ROOT}/robots/{robot_id}/components",
            headers=headers(idem="component-create", etag=robot_etag, token=preflight),
            json={"preflight_token": preflight},
        ),
        201,
    )
    component_id = component_created["data"]["id"]

    calibration = assert_status(
        await client.post(
            f"{REGION_ROOT}/calibration-sets",
            headers=headers(idem="calibration-create"),
            json={
                "robot_id": robot_id,
                "component_id": component_id,
                "display_name": "Camera calibration",
                "valid_from": datetime.now(UTC).isoformat(),
            },
        ),
        201,
    )
    assert calibration["data"]["initial_version"]["lifecycle"] == "DRAFT"

    stream_schema = assert_status(
        await client.post(
            f"{ORG_ROOT}/stream-schemas",
            headers=headers(idem="schema-create"),
            json={
                "family_id": "camera-family",
                "schema_name": "Camera Frame",
                "logical_type": "IMAGE",
                "compatibility_mode": "BACKWARD",
                "schema_definition": {"fields": [], "compatibility_policy": "BACKWARD"},
            },
        ),
        201,
    )
    assert stream_schema["data"]["status"] == "DRAFT"


@pytest.mark.asyncio
async def test_all_p14_p15_robot_and_component_operations(client) -> None:
    await prepare_robotics()
    now = datetime.now(UTC).isoformat()

    created = assert_status(
        await client.post(
            f"{ORG_ROOT}/robot-models",
            headers=headers(idem="p14-model"),
            json={"manufacturer": "HC", "model_code": "R-01", "display_name": "HC Robot"},
        ),
        201,
    )["data"]
    model_id = created["robot_model"]["id"]
    model_etag = created["robot_model"]["etag"]
    version_id = created["initial_version"]["id"]
    assert_status(await client.get(f"{ORG_ROOT}/robot-models", headers=headers()))
    assert_status(await client.get(f"{ORG_ROOT}/robot-models/{model_id}", headers=headers()))
    assert_status(
        await client.get(f"{ORG_ROOT}/robot-models/{model_id}/versions", headers=headers())
    )
    second = assert_status(
        await client.post(
            f"{ORG_ROOT}/robot-models/{model_id}/versions",
            headers=headers(idem="p14-version", etag=model_etag),
            json={"base_version_id": version_id, "version_note": "replacement"},
        ),
        201,
    )["data"]
    second_version_id = second["id"]
    assert_status(
        await client.get(f"{ORG_ROOT}/robot-model-versions/{version_id}", headers=headers())
    )
    assert_status(
        await client.get(
            f"{ORG_ROOT}/robot-model-versions/{version_id}/asset-manifest", headers=headers()
        )
    )

    upload_body = {
        "objects": [
            {
                "relative_path": "robot.urdf",
                "role": "ENTRY_URDF",
                "media_type": "application/xml",
                "bytes": "12",
                "sha256": HASH_A,
            }
        ]
    }
    upload = assert_status(
        await client.post(
            f"{ORG_ROOT}/robot-model-versions/{version_id}/upload-sessions",
            headers=headers(idem="p14-upload"),
            json=upload_body,
        ),
        201,
    )["data"]
    upload_id = upload["id"]
    object_id = upload["objects"][0]["object_id"]
    assert_status(
        await client.get(f"{ORG_ROOT}/robot-asset-upload-sessions/{upload_id}", headers=headers())
    )
    authorization = assert_status(
        await client.post(
            f"{ORG_ROOT}/robot-asset-upload-sessions/{upload_id}:authorize",
            headers=headers(idem="p14-authorize"),
            json={"object_ids": [object_id]},
        )
    )
    assert authorization["data"]["grants"][0]["authorization_ref"]
    completed = assert_status(
        await client.post(
            f"{ORG_ROOT}/robot-asset-upload-sessions/{upload_id}:complete",
            headers=headers(idem="p14-complete"),
            json={
                "manifest_hash": HASH_B,
                "objects": [
                    {
                        "relative_path": "robot.urdf",
                        "bytes": "12",
                        "sha256": HASH_A,
                        "provider_object_id": "private/provider/object",
                    }
                ],
            },
        )
    )
    assert "private/provider/object" not in str(completed)
    viewer = assert_status(
        await client.get(
            f"{ORG_ROOT}/robot-model-versions/{version_id}/viewer-manifest", headers=headers()
        )
    )
    assert viewer["data"]["assets"][0]["read_authorization_ref"]

    cancel_upload = assert_status(
        await client.post(
            f"{ORG_ROOT}/robot-model-versions/{second_version_id}/upload-sessions",
            headers=headers(idem="p14-cancel-upload-create"),
            json=upload_body,
        ),
        201,
    )["data"]["id"]
    assert_status(
        await client.post(
            f"{ORG_ROOT}/robot-asset-upload-sessions/{cancel_upload}:cancel",
            headers=headers(idem="p14-cancel-upload"),
            json={},
        )
    )

    detail = assert_status(
        await client.get(f"{ORG_ROOT}/robot-model-versions/{version_id}", headers=headers())
    )["data"]
    assert_status(
        await client.get(
            f"{ORG_ROOT}/robot-model-versions/{version_id}/configuration", headers=headers()
        )
    )
    configuration = assert_status(
        await client.put(
            f"{ORG_ROOT}/robot-model-versions/{version_id}/configuration",
            headers=headers(idem="p14-config", etag=detail["etag"]),
            json={"joint_names": ["joint_1"]},
        )
    )["data"]
    assert_status(
        await client.get(
            f"{ORG_ROOT}/robot-model-versions/{version_id}/joint-mappings", headers=headers()
        )
    )
    mapping = assert_status(
        await client.put(
            f"{ORG_ROOT}/robot-model-versions/{version_id}/joint-mappings",
            headers=headers(idem="p14-map", etag=configuration["etag"]),
            json={
                "context": "mapping-v1",
                "mappings": [{"source": "joint_1", "target": "joint_1"}],
            },
        )
    )["data"]
    assert mapping["mappings"] == [{"source": "joint_1", "target": "joint_1"}]

    async with TEST_SESSIONS.begin() as session:
        version_row = await session.get(models.RobotModelVersion, version_id)
        assert version_row is not None
        version_row.sample_candidates = [
            {
                "candidate_id": f"{version_id}:sample:fixture",
                "source_type": "CAPTURE_SAMPLE",
                "revision_id": "capture-revision-fx",
                "start_ns": "0",
                "end_ns": "1000",
                "joint_names": ["joint_1"],
                "timestamps_ns": ["0", "500", "999"],
                "positions": [[0.0], [0.5], [1.0]],
            }
        ]

    candidate = assert_status(
        await client.get(
            f"{ORG_ROOT}/robot-model-versions/{version_id}/sample-candidates", headers=headers()
        )
    )["items"][0]
    assert_status(
        await client.get(
            f"{ORG_ROOT}/robot-model-versions/{version_id}/sample-candidates/"
            f"{candidate['candidate_id']}/joint-window?start_ns=0&end_ns=1000&max_points=100",
            headers=headers(),
        )
    )
    current = assert_status(
        await client.get(f"{ORG_ROOT}/robot-model-versions/{version_id}", headers=headers())
    )["data"]
    assert_status(
        await client.post(
            f"{ORG_ROOT}/robot-model-versions/{version_id}/sample-validations",
            headers=headers(idem="p14-sample-validation"),
            json={
                "candidate_id": candidate["candidate_id"],
                "start_ns": "0",
                "end_ns": "1000",
                "max_points": 100,
                "validation_input_hash": current["validation_input_hash"],
            },
        ),
        202,
    )
    assert_status(
        await client.post(
            f"{ORG_ROOT}/robot-model-versions/{version_id}/validations",
            headers=headers(idem="p14-validation"),
            json={"validation_input_hash": current["validation_input_hash"]},
        ),
        202,
    )
    validations = assert_status(
        await client.get(
            f"{ORG_ROOT}/robot-model-versions/{version_id}/validations", headers=headers()
        )
    )["items"]
    report_id = next(item["id"] for item in validations if item.get("status") == "PASSED")
    assert_status(
        await client.get(
            f"{ORG_ROOT}/robot-model-validation-reports/{report_id}", headers=headers()
        )
    )
    current = assert_status(
        await client.get(f"{ORG_ROOT}/robot-model-versions/{version_id}", headers=headers())
    )["data"]
    preflight = assert_status(
        await client.post(
            f"{ORG_ROOT}/robot-model-versions/{version_id}:preflight-publish",
            headers=headers(idem="p14-publish-preflight", etag=current["etag"]),
            json={
                "expected_hash": current["validation_input_hash"],
                "expected_etag": current["etag"],
                "validation_report_id": report_id,
                "change_summary": "publish fixture",
            },
        )
    )["data"]["preflight_token"]
    published = assert_status(
        await client.post(
            f"{ORG_ROOT}/robot-model-versions/{version_id}:publish",
            headers=headers(idem="p14-publish", etag=current["etag"], token=preflight),
            json={"preflight_token": preflight},
        )
    )["data"]
    assert published["lifecycle"] == "PUBLISHED"

    robot = assert_status(
        await client.post(
            f"{REGION_ROOT}/robots",
            headers=headers(idem="p15-robot"),
            json={"display_name": "Robot A", "serial_no": "ROBOT-A", "robot_model_id": model_id},
        ),
        201,
    )["data"]
    robot_id = robot["id"]
    assert_status(await client.get(f"{REGION_ROOT}/robots", headers=headers()))
    assert_status(await client.get(f"{REGION_ROOT}/robots/{robot_id}/bootstrap", headers=headers()))
    robot = assert_status(
        await client.patch(
            f"{REGION_ROOT}/robots/{robot_id}",
            headers=headers(idem="p15-robot-update", etag=robot["etag"]),
            json={"display_name": "Robot A updated"},
        )
    )["data"]

    component_preflight = assert_status(
        await client.post(
            f"{REGION_ROOT}/robots/{robot_id}/components:preflight",
            headers=headers(idem="p15-component-preflight"),
            json={
                "component_type": "CAMERA",
                "display_name": "Camera A",
                "serial_no": "CAM-A",
                "topology_revision": robot["topology_revision"],
                "valid_from": now,
            },
        )
    )["data"]["preflight_token"]
    component = assert_status(
        await client.post(
            f"{REGION_ROOT}/robots/{robot_id}/components",
            headers=headers(idem="p15-component", token=component_preflight),
            json={"preflight_token": component_preflight},
        ),
        201,
    )["data"]
    component_id = component["id"]
    assert_status(
        await client.get(f"{REGION_ROOT}/robots/{robot_id}/components", headers=headers())
    )
    assert_status(await client.get(f"{REGION_ROOT}/components/{component_id}", headers=headers()))
    component = assert_status(
        await client.patch(
            f"{REGION_ROOT}/components/{component_id}",
            headers=headers(idem="p15-component-update", etag=component["etag"]),
            json={"display_name": "Camera A updated"},
        )
    )["data"]
    assert_status(
        await client.get(
            f"{REGION_ROOT}/components/{component_id}/maintenance-records", headers=headers()
        )
    )
    assert_status(
        await client.get(f"{REGION_ROOT}/components/{component_id}/bindings", headers=headers())
    )
    assert_status(
        await client.get(f"{REGION_ROOT}/components/{component_id}/frames", headers=headers())
    )
    assert_status(
        await client.get(f"{REGION_ROOT}/components/{component_id}/channels", headers=headers())
    )
    robot_now = assert_status(
        await client.get(f"{REGION_ROOT}/robots/{robot_id}/bootstrap", headers=headers())
    )["data"]["robot"]
    mount_preflight = assert_status(
        await client.post(
            f"{REGION_ROOT}/components/{component_id}/mount-changes:preflight",
            headers=headers(idem="p15-mount-preflight", etag=component["etag"]),
            json={
                "new_robot_id": robot_id,
                "new_parent_component_id": None,
                "valid_from": now,
                "topology_revision": robot_now["topology_revision"],
                "reason": "confirm mount",
            },
        )
    )["data"]["preflight_token"]
    component = assert_status(
        await client.post(
            f"{REGION_ROOT}/components/{component_id}/mount-changes",
            headers=headers(idem="p15-mount", etag=component["etag"], token=mount_preflight),
            json={"preflight_token": mount_preflight},
        )
    )["data"]

    binding_preflight = assert_status(
        await client.post(
            f"/projects/{PROJECT}/robot-model-bindings:preflight",
            headers=headers(idem="p15-binding-preflight", etag='"rv-0"'),
            json={
                "robot_model_version_id": version_id,
                "targets": [
                    {
                        "scope_type": "ROBOT_INSTANCE",
                        "scope_id": robot_id,
                        "valid_from": now,
                    }
                ],
                "reason": "activate fixture",
            },
        )
    )["data"]["preflight_token"]
    assert_status(
        await client.post(
            f"/projects/{PROJECT}/robot-model-bindings",
            headers=headers(idem="p15-binding", etag='"rv-0"', token=binding_preflight),
            json={"preflight_token": binding_preflight},
        )
    )
    assert_status(await client.get(f"/projects/{PROJECT}/robot-model-bindings", headers=headers()))
    assert_status(
        await client.get(f"{REGION_ROOT}/robots/{robot_id}/binding-history", headers=headers())
    )
    assert_status(
        await client.get(
            f"{REGION_ROOT}/robots/{robot_id}/model-version-candidates", headers=headers()
        )
    )
    bootstrap = assert_status(
        await client.get(f"{REGION_ROOT}/robots/{robot_id}/bootstrap", headers=headers())
    )
    assert bootstrap["data"]["effective_model_binding"] is not None

    model_disable_preflight = assert_status(
        await client.post(
            f"{ORG_ROOT}/robot-model-versions/{version_id}:preflight-disable",
            headers=headers(idem="p14-disable-preflight", etag=published["etag"]),
            json={"reason": "retire fixture"},
        )
    )["data"]["preflight_token"]
    assert_status(
        await client.post(
            f"{ORG_ROOT}/robot-model-versions/{version_id}:disable",
            headers=headers(
                idem="p14-disable", etag=published["etag"], token=model_disable_preflight
            ),
            json={"preflight_token": model_disable_preflight},
        )
    )

    component_disable_preflight = assert_status(
        await client.post(
            f"{REGION_ROOT}/components/{component_id}:preflight-disable",
            headers=headers(idem="p15-component-disable-preflight", etag=component["etag"]),
            json={},
        )
    )["data"]["preflight_token"]
    assert_status(
        await client.post(
            f"{REGION_ROOT}/components/{component_id}:disable",
            headers=headers(
                idem="p15-component-disable",
                etag=component["etag"],
                token=component_disable_preflight,
            ),
            json={},
        )
    )
    robot_after_component = assert_status(
        await client.get(f"{REGION_ROOT}/robots/{robot_id}/bootstrap", headers=headers())
    )["data"]["robot"]
    robot_disable_preflight = assert_status(
        await client.post(
            f"{REGION_ROOT}/robots/{robot_id}:preflight-disable",
            headers=headers(idem="p15-robot-disable-preflight", etag=robot_after_component["etag"]),
            json={},
        )
    )["data"]["preflight_token"]
    disabled_robot = assert_status(
        await client.post(
            f"{REGION_ROOT}/robots/{robot_id}:disable",
            headers=headers(
                idem="p15-robot-disable",
                etag=robot_after_component["etag"],
                token=robot_disable_preflight,
            ),
            json={},
        )
    )["data"]
    assert disabled_robot["lifecycle"] == "DISABLED"


@pytest.mark.asyncio
async def test_all_p16_calibration_operations(client) -> None:
    await prepare_robotics()
    _, robot_id, component_id = await seed_robot_component()
    now = datetime.now(UTC).isoformat()

    assert_status(await client.get(f"{REGION_ROOT}/calibration-sets/facets", headers=headers()))
    assert_status(await client.get(f"{REGION_ROOT}/calibration-sets", headers=headers()))
    created = assert_status(
        await client.post(
            f"{REGION_ROOT}/calibration-sets",
            headers=headers(idem="p16-set"),
            json={
                "robot_id": robot_id,
                "component_id": component_id,
                "display_name": "Primary calibration",
                "valid_from": now,
            },
        ),
        201,
    )["data"]
    set_id = created["calibration_set"]["id"]
    set_detail = assert_status(
        await client.get(f"{REGION_ROOT}/calibration-sets/{set_id}", headers=headers())
    )["data"]
    set_detail = assert_status(
        await client.patch(
            f"{REGION_ROOT}/calibration-sets/{set_id}",
            headers=headers(idem="p16-set-update", etag=set_detail["etag"]),
            json={"display_name": "Primary calibration updated"},
        )
    )["data"]

    record = assert_status(
        await client.post(
            f"{REGION_ROOT}/calibration-sets/{set_id}/intrinsics",
            headers=headers(idem="p16-record-create", etag=set_detail["etag"]),
            json={"record": {"camera_matrix": [1.0, 0.0, 1.0]}},
        ),
        201,
    )["data"]
    record_id = record["record_id"]
    assert_status(
        await client.get(f"{REGION_ROOT}/calibration-sets/{set_id}/intrinsics", headers=headers())
    )
    assert_status(
        await client.get(
            f"{REGION_ROOT}/calibration-sets/{set_id}/intrinsics/{record_id}",
            headers=headers(),
        )
    )
    set_detail = assert_status(
        await client.get(f"{REGION_ROOT}/calibration-sets/{set_id}", headers=headers())
    )["data"]
    assert_status(
        await client.patch(
            f"{REGION_ROOT}/calibration-sets/{set_id}/intrinsics/{record_id}",
            headers=headers(idem="p16-record-update", etag=set_detail["etag"]),
            json={"patch": {"distortion": [0.1]}},
        )
    )
    set_detail = assert_status(
        await client.get(f"{REGION_ROOT}/calibration-sets/{set_id}", headers=headers())
    )["data"]
    assert_status(
        await client.delete(
            f"{REGION_ROOT}/calibration-sets/{set_id}/intrinsics/{record_id}",
            headers=headers(idem="p16-record-delete", etag=set_detail["etag"]),
        ),
        204,
    )

    versions = assert_status(
        await client.get(f"{REGION_ROOT}/calibration-sets/{set_id}/versions", headers=headers())
    )["items"]
    base_version_id = f"{set_id}:1"
    assert versions[0]["version"] == "1"
    set_detail = assert_status(
        await client.get(f"{REGION_ROOT}/calibration-sets/{set_id}", headers=headers())
    )["data"]
    version = assert_status(
        await client.post(
            f"{REGION_ROOT}/calibration-sets/{set_id}/versions",
            headers=headers(idem="p16-version", etag=set_detail["etag"]),
            json={"base_version_id": base_version_id, "version_note": "replacement"},
        ),
        201,
    )["data"]
    version_number = version["version"]
    assert_status(
        await client.get(
            f"{REGION_ROOT}/calibration-sets/{set_id}/versions/{version_number}",
            headers=headers(),
        )
    )
    version = assert_status(
        await client.patch(
            f"{REGION_ROOT}/calibration-sets/{set_id}/versions/{version_number}",
            headers=headers(idem="p16-version-update", etag=version["etag"]),
            json={"transforms": [{"from": "base", "to": "camera"}]},
        )
    )["data"]
    assert_status(
        await client.get(
            f"{REGION_ROOT}/calibration-sets/{set_id}/versions/{version_number}/source-artifacts",
            headers=headers(),
        )
    )
    validation = assert_status(
        await client.post(
            f"{REGION_ROOT}/calibration-sets/{set_id}/versions/{version_number}/validations",
            headers=headers(idem="p16-version-validation", etag=version["etag"]),
            json={
                "expected_etag": version["etag"],
                "content_hash": version["content_hash"],
                "validation_context_hash": version["validation_context_hash"],
            },
        ),
        202,
    )
    assert validation["job"]["status"] == "SUCCEEDED"
    reports = assert_status(
        await client.get(
            f"{REGION_ROOT}/calibration-sets/{set_id}/versions/{version_number}/reports",
            headers=headers(),
        )
    )["items"]
    report_id = reports[0]["id"]
    assert_status(
        await client.get(f"{REGION_ROOT}/calibration-reports/{report_id}", headers=headers())
    )
    assert_status(
        await client.get(
            f"{REGION_ROOT}/calibration-sets/{set_id}/validation-runs", headers=headers()
        )
    )
    assert_status(
        await client.get(
            f"{REGION_ROOT}/calibration-validation-runs/{report_id}", headers=headers()
        )
    )
    version = assert_status(
        await client.get(
            f"{REGION_ROOT}/calibration-sets/{set_id}/versions/{version_number}",
            headers=headers(),
        )
    )["data"]
    preflight = assert_status(
        await client.post(
            f"{REGION_ROOT}/calibration-sets/{set_id}/versions/{version_number}:preflight-publish",
            headers=headers(idem="p16-version-preflight", etag=version["etag"]),
            json={
                "expected_hash": version["content_hash"],
                "expected_etag": version["etag"],
                "validation_report_id": report_id,
                "change_summary": "publish calibration",
            },
        )
    )["data"]["preflight_token"]
    published_version = assert_status(
        await client.post(
            f"{REGION_ROOT}/calibration-sets/{set_id}/versions/{version_number}:publish",
            headers=headers(idem="p16-version-publish", etag=version["etag"], token=preflight),
            json={"preflight_token": preflight},
        )
    )["data"]
    assert published_version["lifecycle"] == "READY"
    assert_status(
        await client.get(
            f"{REGION_ROOT}/calibration-sets/{set_id}/versions/{version_number}/availability",
            headers=headers(),
        )
    )
    assert_status(
        await client.get(
            f"{REGION_ROOT}/calibration-sets/{set_id}/binding-usages", headers=headers()
        )
    )
    preview = assert_status(
        await client.get(
            f"{REGION_ROOT}/calibration-sets/{set_id}/preview-context", headers=headers()
        )
    )
    assert preview["data"]["asset_read_authorization_ref"]

    set_level = assert_status(
        await client.post(
            f"{REGION_ROOT}/calibration-sets",
            headers=headers(idem="p16-set-level"),
            json={
                "robot_id": robot_id,
                "component_id": component_id,
                "display_name": "Set-level commands",
                "valid_from": now,
            },
        ),
        201,
    )["data"]["calibration_set"]
    set_validation = assert_status(
        await client.post(
            f"{REGION_ROOT}/calibration-sets/{set_level['id']}/validations",
            headers=headers(idem="p16-set-validate"),
            json={"expected_revision": set_level["etag"].strip('"').removeprefix("rv-")},
        ),
        202,
    )
    set_token = set_validation["preflight"]["preflight_token"]
    set_published = assert_status(
        await client.post(
            f"{REGION_ROOT}/calibration-sets/{set_level['id']}:publish",
            headers=headers(idem="p16-set-publish", etag=set_level["etag"], token=set_token),
            json={},
        )
    )["data"]
    assert set_published["lifecycle"] == "READY"
    cloned = assert_status(
        await client.post(
            f"{REGION_ROOT}/calibration-sets/{set_level['id']}:clone",
            headers=headers(idem="p16-clone", etag=set_published["etag"]),
            json={"valid_from": now, "reason": "new capture interval"},
        ),
        201,
    )
    assert cloned["data"]["calibration_set"]["lifecycle"] == "DRAFT"

    imported = assert_status(
        await client.post(
            f"{REGION_ROOT}/calibration-sets/imports",
            headers=headers(idem="p16-import"),
            json={
                "robot_id": robot_id,
                "component_id": component_id,
                "display_name": "Imported calibration",
                "valid_from": now,
                "source_artifact": {
                    "kind": "IMPORT_SOURCE",
                    "display_name": "calibration.json",
                    "media_type": "application/json",
                    "bytes": "128",
                    "sha256": HASH_A,
                    "classification": "INTERNAL",
                    "provider_object_id": "private/calibration/source",
                },
            },
        ),
        202,
    )
    assert "private/calibration/source" not in str(imported)
    jobs = assert_status(await client.get(f"{REGION_ROOT}/calibration-jobs", headers=headers()))[
        "items"
    ]
    assert jobs
    job_id = jobs[0]["job_id"]
    job_response = await client.get(f"{REGION_ROOT}/calibration-jobs/{job_id}", headers=headers())
    assert_status(job_response)
    conditional = await client.get(
        f"{REGION_ROOT}/calibration-jobs/{job_id}",
        headers={**headers(), "If-None-Match": job_response.headers["etag"]},
    )
    assert conditional.status_code == 304

    seed_time = datetime.now(UTC)
    scope = f"{ORGANIZATION}:{PROJECT}:{REGION}"
    async with TEST_SESSIONS.begin() as session:
        session.add_all(
            [
                models.RoboticsJob(
                    job_id="job_cancel_fx",
                    scope_key=scope,
                    job_type="CALIBRATION_VALIDATION",
                    status="RUNNING",
                    resource_type="CALIBRATION_SET",
                    resource_id=set_id,
                    result_ref=None,
                    error=None,
                    created_at=seed_time,
                    updated_at=seed_time,
                ),
                models.RoboticsJob(
                    job_id="job_retry_fx",
                    scope_key=scope,
                    job_type="CALIBRATION_VALIDATION",
                    status="FAILED",
                    resource_type="CALIBRATION_SET",
                    resource_id=set_id,
                    result_ref=None,
                    error={"code": "FIXTURE_FAILURE"},
                    created_at=seed_time,
                    updated_at=seed_time,
                ),
            ]
        )
    cancel_get = await client.get(
        f"{REGION_ROOT}/calibration-jobs/job_cancel_fx", headers=headers()
    )
    assert_status(cancel_get)
    cancelled = assert_status(
        await client.post(
            f"{REGION_ROOT}/calibration-jobs/job_cancel_fx:cancel",
            headers=headers(idem="p16-job-cancel", etag=cancel_get.headers["etag"]),
            json={},
        )
    )
    assert cancelled["data"]["status"] == "CANCELLED"
    retry_get = await client.get(f"{REGION_ROOT}/calibration-jobs/job_retry_fx", headers=headers())
    assert_status(retry_get)
    retried = assert_status(
        await client.post(
            f"{REGION_ROOT}/calibration-jobs/job_retry_fx:retry",
            headers=headers(idem="p16-job-retry", etag=retry_get.headers["etag"]),
            json={},
        ),
        202,
    )
    assert retried["job"]["result_ref"]["retry_of_job_id"] == "job_retry_fx"


@pytest.mark.asyncio
async def test_all_p17_schema_and_cross_page_resolution_operations(client) -> None:
    await prepare_robotics()
    _, robot_id, component_id = await seed_robot_component()
    now = datetime.now(UTC)
    async with TEST_SESSIONS.begin() as session:
        session.add_all(
            [
                models.DatasetSchemaSnapshot(
                    snapshot_id="snapshot_base_fx",
                    organization_id=ORGANIZATION,
                    dataset_id="dataset_fx",
                    dataset_version_id="dataset_version_1",
                    content_hash=HASH_A,
                    channels=[],
                    diff=[],
                    created_at=now,
                ),
                models.DatasetSchemaSnapshot(
                    snapshot_id="snapshot_target_fx",
                    organization_id=ORGANIZATION,
                    dataset_id="dataset_fx",
                    dataset_version_id="dataset_version_2",
                    content_hash=HASH_B,
                    channels=[
                        {
                            "channel_id": "channel_fx",
                            "canonical_path": "/camera/image",
                            "modality": "IMAGE",
                            "semantic_role": "RGB",
                            "unit": None,
                            "schema_id": "seed-patched-below",
                            "schema_version": "1",
                            "allowed_actions": ["VIEW"],
                            "blocked_reasons": [],
                        }
                    ],
                    diff=[
                        {
                            "change_id": "change_fx",
                            "change_type": "ADDED",
                            "path": "/camera/image",
                            "before": None,
                            "after": {"modality": "IMAGE"},
                            "allowed_actions": ["VIEW"],
                            "blocked_reasons": [],
                        }
                    ],
                    created_at=now,
                ),
            ]
        )

    assert_status(await client.get(f"{ORG_ROOT}/stream-schemas/facets", headers=headers()))
    assert_status(await client.get(f"{ORG_ROOT}/stream-schemas", headers=headers()))
    created = assert_status(
        await client.post(
            f"{ORG_ROOT}/stream-schemas",
            headers=headers(idem="p17-schema"),
            json={
                "family_id": "camera-family",
                "schema_name": "Camera Image",
                "logical_type": "IMAGE",
                "description": "RGB image stream",
                "compatibility_mode": "BACKWARD",
                "schema_definition": {
                    "fields": [
                        {
                            "field_id": "field_image",
                            "path": "/image",
                            "type": "bytes",
                            "required": True,
                        }
                    ],
                    "compatibility_policy": "BACKWARD",
                },
            },
        ),
        201,
    )["data"]
    schema_id = created["id"]
    assert_status(
        await client.get(f"{ORG_ROOT}/stream-schemas/{schema_id}", headers=headers())
    )
    assert_status(
        await client.get(
            f"{ORG_ROOT}/stream-schema-families/camera-family/versions", headers=headers()
        )
    )
    updated = assert_status(
        await client.patch(
            f"{ORG_ROOT}/stream-schemas/{schema_id}",
            headers=headers(idem="p17-root-update", etag=created["etag"]),
            json={
                "description": "RGB image stream v2",
                "compatibility_mode": "BACKWARD",
                "schema_definition": {
                    "fields": [
                        {
                            "field_id": "field_image",
                            "path": "/image",
                            "type": "bytes",
                            "required": True,
                        }
                    ],
                    "compatibility_policy": "BACKWARD",
                },
                "change_summary": "clarify description",
            },
        )
    )["data"]
    validated = assert_status(
        await client.post(
            f"{ORG_ROOT}/stream-schemas/{schema_id}:validate",
            headers=headers(idem="p17-root-validate", etag=updated["etag"]),
            json={
                "expected_etag": updated["etag"],
                "target_hash": updated["schema_hash"],
                "rule_set_version": "schema-rules-v1",
            },
        ),
        202,
    )
    root_token = validated["preflight"]["preflight_token"]
    root_published = assert_status(
        await client.post(
            f"{ORG_ROOT}/stream-schemas/{schema_id}:publish",
            headers=headers(
                idem="p17-root-publish", etag=updated["etag"], token=root_token
            ),
            json={},
        )
    )["data"]
    assert root_published["status"] == "PUBLISHED"

    versions = assert_status(
        await client.get(f"{ORG_ROOT}/stream-schemas/{schema_id}/versions", headers=headers())
    )["items"]
    first = next(item for item in versions if item["version"] == "1")
    second = assert_status(
        await client.post(
            f"{ORG_ROOT}/stream-schemas/{schema_id}/versions",
            headers=headers(idem="p17-version", etag=first["etag"]),
            json={"parent_version_id": first["id"], "change_summary": "add confidence"},
        ),
        201,
    )["data"]
    assert_status(
        await client.get(
            f"{ORG_ROOT}/stream-schemas/{schema_id}/versions/{second['version']}",
            headers=headers(),
        )
    )
    second = assert_status(
        await client.patch(
            f"{ORG_ROOT}/stream-schemas/{schema_id}/versions/{second['version']}",
            headers=headers(idem="p17-version-update", etag=second["etag"]),
            json={
                "fields": [
                    {
                        "field_id": "field_image",
                        "path": "/image",
                        "type": "bytes",
                        "required": True,
                    },
                    {
                        "field_id": "field_confidence",
                        "path": "/confidence",
                        "type": "float32",
                        "required": False,
                    },
                ],
                "compatibility_policy": "BACKWARD",
                "change_summary": "add optional confidence",
            },
        )
    )["data"]
    validation = assert_status(
        await client.post(
            f"{ORG_ROOT}/stream-schemas/{schema_id}/versions/{second['version']}/validate",
            headers=headers(idem="p17-version-validate", etag=second["etag"]),
            json={
                "expected_etag": second["etag"],
                "expected_hash": second["definition_hash"],
                "rule_set_version": "schema-rules-v1",
            },
        ),
        202,
    )
    validation_job_id = validation["job"]["job_id"]
    second = assert_status(
        await client.get(
            f"{ORG_ROOT}/stream-schemas/{schema_id}/versions/{second['version']}",
            headers=headers(),
        )
    )["data"]
    preflight = assert_status(
        await client.post(
            f"{ORG_ROOT}/stream-schemas/{schema_id}/versions/{second['version']}:preflight-publish",
            headers=headers(idem="p17-version-preflight", etag=second["etag"]),
            json={
                "expected_hash": second["definition_hash"],
                "expected_etag": second["etag"],
                "validation_report_id": validation_job_id,
                "change_summary": "publish schema",
            },
        )
    )["data"]["preflight_token"]
    second = assert_status(
        await client.post(
            f"{ORG_ROOT}/stream-schemas/{schema_id}/versions/{second['version']}:publish",
            headers=headers(
                idem="p17-version-publish", etag=second["etag"], token=preflight
            ),
            json={"preflight_token": preflight},
        )
    )["data"]
    assert second["lifecycle"] == "PUBLISHED"
    assert_status(
        await client.get(
            f"{ORG_ROOT}/stream-schemas/{schema_id}/versions/{second['version']}/references",
            headers=headers(),
        )
    )

    compatibility = assert_status(
        await client.post(
            f"{ORG_ROOT}/stream-schema-compatibility-checks",
            headers=headers(idem="p17-compatibility"),
            json={
                "baseline_version_id": first["id"],
                "target_version_id": second["id"],
                "baseline_hash": first["definition_hash"],
                "target_hash": second["definition_hash"],
                "mode": "BACKWARD",
                "rule_set_version": "schema-rules-v1",
                "mapping_overrides": [],
            },
        ),
        202,
    )
    check_id = compatibility["job"]["resource_id"]
    assert_status(
        await client.get(
            f"{ORG_ROOT}/stream-schema-compatibility-checks/{check_id}", headers=headers()
        )
    )

    import_validation = assert_status(
        await client.post(
            f"{ORG_ROOT}/stream-schema-imports:validate",
            headers=headers(idem="p17-import-validate"),
            json={
                "media_type": "application/json",
                "content": (
                    '{"name":"Imported IMU","logical_type":"IMU","fields":[],'
                    '"compatibility_policy":"STRICT"}'
                ),
            },
        )
    )["data"]
    committed = assert_status(
        await client.post(
            f"{ORG_ROOT}/stream-schema-imports:commit",
            headers=headers(idem="p17-import-commit"),
            json={
                "import_validation_id": import_validation["import_validation_id"],
                "target_schema_id": None,
                "change_summary": "import IMU schema",
            },
        ),
        201,
    )
    assert committed["data"]["lifecycle"] == "DRAFT"

    snapshots = assert_status(
        await client.get(f"{ORG_ROOT}/dataset-schema-snapshots", headers=headers())
    )["items"]
    assert len(snapshots) == 2
    assert_status(
        await client.get(
            f"{ORG_ROOT}/dataset-schema-snapshots/snapshot_target_fx", headers=headers()
        )
    )
    assert_status(
        await client.get(
            f"{ORG_ROOT}/dataset-schema-snapshots/snapshot_target_fx/channel-definitions",
            headers=headers(),
        )
    )
    assert_status(
        await client.get(
            f"{ORG_ROOT}/dataset-schema-snapshots/snapshot_target_fx/diff"
            "?base_snapshot_id=snapshot_base_fx",
            headers=headers(),
        )
    )

    calibration = assert_status(
        await client.post(
            f"{REGION_ROOT}/calibration-sets",
            headers=headers(idem="resolver-calibration"),
            json={
                "robot_id": robot_id,
                "component_id": component_id,
                "display_name": "Resolver calibration",
                "valid_from": now.isoformat(),
            },
        ),
        201,
    )["data"]["calibration_set"]
    async with TEST_SESSIONS.begin() as session:
        component = await session.get(models.Component, component_id)
        assert component is not None
        component.channels = [
            {
                "schema_id": schema_id,
                "schema_version": second["version"],
                "channel_id": "channel_fx",
            }
        ]
    assert_status(
        await client.get(
            f"{REGION_ROOT}/robots/{robot_id}/route-resolutions/p15-to-p16"
            f"?component_id={component_id}&set_id={calibration['id']}",
            headers=headers(),
        )
    )
    assert_status(
        await client.get(
            f"{REGION_ROOT}/route-resolutions/p15-to-p17?schema_id={schema_id}"
            f"&schema_version={second['version']}&component_id={component_id}&detail_tab=fields",
            headers=headers(),
        )
    )


@pytest.mark.asyncio
async def test_robotics_fail_closed_error_matrix(client) -> None:
    await prepare_robotics()
    model_id, robot_id, component_id = await seed_robot_component()

    denied = headers()
    denied["Authorization"] = "Bearer test:denied_actor:"
    for path in (
        f"{ORG_ROOT}/robot-models",
        f"{REGION_ROOT}/calibration-sets",
        f"{ORG_ROOT}/stream-schemas",
    ):
        response = await client.get(path, headers=denied)
        assert response.status_code == 403, (path, response.text)

    for path in (
        f"{ORG_ROOT}/robot-models/missing-model",
        f"{REGION_ROOT}/robots/missing-robot/bootstrap",
        f"{REGION_ROOT}/components/missing-component",
        f"{REGION_ROOT}/calibration-sets/missing-set",
        f"{ORG_ROOT}/stream-schemas/missing-schema",
    ):
        response = await client.get(path, headers=headers())
        assert response.status_code == 404, (path, response.text)

    invalid = await client.post(
        f"{ORG_ROOT}/robot-models",
        headers=headers(idem="negative-invalid-model"),
        json={"manufacturer": "", "model_code": "FX", "display_name": "Invalid"},
    )
    assert invalid.status_code == 422

    duplicate = await client.post(
        f"{REGION_ROOT}/robots",
        headers=headers(idem="negative-duplicate-robot"),
        json={
            "display_name": "Duplicate",
            "serial_no": "FIXTURE-ROBOT-01",
            "robot_model_id": model_id,
        },
    )
    assert duplicate.status_code == 409

    stale = await client.patch(
        f"{REGION_ROOT}/robots/{robot_id}",
        headers=headers(idem="negative-stale", etag='"rv-0"'),
        json={"display_name": "Must not apply"},
    )
    assert stale.status_code == 412

    topology_conflict = await client.post(
        f"{REGION_ROOT}/robots/{robot_id}/components:preflight",
        headers=headers(idem="negative-topology"),
        json={
            "component_type": "LIDAR",
            "display_name": "Invalid topology",
            "topology_revision": "999",
            "valid_from": datetime.now(UTC).isoformat(),
        },
    )
    assert topology_conflict.status_code == 409

    preflight = assert_status(
        await client.post(
            f"{REGION_ROOT}/components/{component_id}:preflight-disable",
            headers=headers(idem="negative-expired-preflight", etag='"rv-1"'),
            json={},
        )
    )["data"]
    async with TEST_SESSIONS.begin() as session:
        row = await session.get(models.RoboticsPreflight, preflight["preflight_id"])
        assert row is not None
        row.expires_at = datetime(2020, 1, 1, tzinfo=UTC)
    expired = await client.post(
        f"{REGION_ROOT}/components/{component_id}:disable",
        headers=headers(
            idem="negative-expired-commit",
            etag='"rv-1"',
            token=preflight["preflight_token"],
        ),
        json={},
    )
    assert expired.status_code == 410

    cross_scope_headers = headers()
    cross_scope_headers["X-Project-Id"] = "different-project"
    scoped = await client.get(
        f"{REGION_ROOT}/robots/{robot_id}/bootstrap", headers=cross_scope_headers
    )
    assert scoped.status_code == 404
