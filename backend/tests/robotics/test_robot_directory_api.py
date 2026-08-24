from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.robotics.models import (
    ChannelReference,
    Connectivity,
    EffectiveModelBinding,
    FrameReference,
    RobotBootstrap,
    RobotComponent,
    RobotRecord,
)
from hc_data_platform.robotics.repository import InMemoryRoboticsRepository
from hc_data_platform.robotics.router import configure_robotics, router
from hc_data_platform.robotics.service import RoboticsService
from hc_data_platform.security.auth import AuthContext

PROJECT_ID = "project-a"
REGION_CODE = "cn-shanghai-01"
ORGANIZATION_ID = "org-a"


def _auth(
    *, region_code: str = REGION_CODE, can_read: bool = True, can_manage: bool = False
) -> AuthContext:
    return AuthContext(
        subject_id="robot-reader",
        project_ids=frozenset({PROJECT_ID}),
        organization_ids=frozenset({ORGANIZATION_ID}),
        region_codes=frozenset({region_code}),
        roles=frozenset(),
        scope_pairs=frozenset({(PROJECT_ID, region_code)}),
        organization_scope_triples=frozenset({(ORGANIZATION_ID, PROJECT_ID, region_code)}),
        scoped_capabilities=(
            (frozenset({(PROJECT_ID, "robot.read")}))
            | (frozenset({(PROJECT_ID, "robot.manage")}) if can_manage else frozenset())
            if can_read
            else (frozenset({(PROJECT_ID, "robot.manage")}) if can_manage else frozenset())
        ),
        organization_scoped_capabilities=(
            (frozenset({(ORGANIZATION_ID, PROJECT_ID, "robot.read")}))
            | (
                frozenset({(ORGANIZATION_ID, PROJECT_ID, "robot.manage")})
                if can_manage
                else frozenset()
            )
            if can_read
            else (
                frozenset({(ORGANIZATION_ID, PROJECT_ID, "robot.manage")})
                if can_manage
                else frozenset()
            )
        ),
    )


def _service() -> tuple[RoboticsService, InMemoryRoboticsRepository]:
    robot = RobotRecord(
        id="robot-a",
        display_name="星舟 017",
        serial_no="SN-017",
        lifecycle_status="ACTIVE",
        connectivity=Connectivity(
            state="ONLINE",
            observed_at=datetime(2026, 8, 19, 12, tzinfo=timezone.utc),
            source="fleet-heartbeat",
            reason_code=None,
        ),
    )
    repository = InMemoryRoboticsRepository(
        robots=(
            (
                PROJECT_ID,
                REGION_CODE,
                RobotBootstrap(
                    robot=robot,
                    etag='"robot-a:7"',
                    topology_revision="topology:7",
                    effective_model_binding=EffectiveModelBinding(
                        id="binding-a",
                        scope_type="ROBOT_INSTANCE",
                        scope_id="robot-a",
                        robot_model_version_id="model-version-a",
                        valid_from=datetime(2026, 8, 1, tzinfo=timezone.utc),
                        valid_to=None,
                        etag='"binding-a:1"',
                    ),
                    allowed_actions=("VIEW",),
                ),
            ),
            (
                PROJECT_ID,
                REGION_CODE,
                RobotBootstrap(
                    robot=RobotRecord(
                        id="robot-b",
                        display_name="星舟 118",
                        serial_no="SN-118",
                        lifecycle_status="ACTIVE",
                        connectivity=Connectivity(state="OFFLINE"),
                    ),
                    etag='"robot-b:1"',
                    topology_revision="topology:b:1",
                    allowed_actions=("VIEW",),
                ),
            ),
            (
                PROJECT_ID,
                REGION_CODE,
                RobotBootstrap(
                    robot=RobotRecord(
                        id="robot-c",
                        display_name="星舟 119",
                        serial_no="SN-119",
                        lifecycle_status="MAINTENANCE",
                        connectivity=Connectivity(state="DEGRADED"),
                    ),
                    etag='"robot-c:1"',
                    topology_revision="topology:c:1",
                    allowed_actions=("VIEW",),
                ),
            ),
        ),
        components=(
            (
                PROJECT_ID,
                REGION_CODE,
                RobotComponent(
                    id="camera-a",
                    robot_id="robot-a",
                    parent_component_id=None,
                    component_model_id="camera-model-a",
                    component_type="CAMERA",
                    display_name="前视相机",
                    serial_no="CAM-017",
                    lifecycle_status="ACTIVE",
                    sort_order="0",
                ),
            ),
        ),
        frames=(
            (
                PROJECT_ID,
                REGION_CODE,
                "camera-a",
                FrameReference(
                    id="frame-a",
                    name="camera_link",
                    parent_frame="base_link",
                    source="CALIBRATION",
                    calibration_set_id="calibration-a",
                    status="ACTIVE",
                    valid_from=datetime(2026, 8, 1, tzinfo=timezone.utc),
                    valid_to=None,
                ),
            ),
        ),
        channels=(
            (
                PROJECT_ID,
                REGION_CODE,
                "camera-a",
                ChannelReference(
                    id="channel-a",
                    canonical_path="/camera/front/image",
                    display_name="前视图像",
                    modality="IMAGE",
                    schema_id="image-schema",
                    schema_version="1",
                    role="PRIMARY",
                    unit=None,
                    frequency_hz="30",
                    frame_id="frame-a",
                    clock_id="clock-a",
                    status="ACTIVE",
                ),
            ),
        ),
    )
    return RoboticsService(
        repository, clock=lambda: datetime(2026, 8, 19, 12, tzinfo=timezone.utc)
    ), repository


def _app(current: dict[str, AuthContext | None]) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(ProblemException)
    async def handle_problem(_request: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(exc.problem.model_dump(mode="json"), status_code=exc.problem.status)

    @app.middleware("http")
    async def install_auth(request: Request, call_next: Any) -> Any:
        request.state.auth_context = current["value"]
        request.state.request_id = "robotics-router-test"
        return await call_next(request)

    app.include_router(router)
    return app


def test_robot_directory_read_scope_and_stable_cross_page_references() -> None:
    service, repository = _service()
    configure_robotics(service)
    current: dict[str, AuthContext | None] = {"value": _auth()}
    client = TestClient(_app(current), headers={"X-Organization-Id": ORGANIZATION_ID})
    root = f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}"
    headers = {"Authorization": "Bearer test"}

    listing = client.get(f"{root}/robots", params={"q": "017"}, headers=headers)
    assert listing.status_code == 200
    assert listing.headers["cache-control"] == "private, no-store"
    assert listing.json()["items"][0]["id"] == "robot-a"

    lifecycle_filtered = client.get(
        f"{root}/robots",
        params={"lifecycle_status": "MAINTENANCE", "connectivity_state": "ONLINE"},
        headers=headers,
    )
    assert lifecycle_filtered.status_code == 200
    assert lifecycle_filtered.json()["items"] == []
    assert (
        client.get(
            f"{root}/robots",
            params={"lifecycle_status": "UNKNOWN"},
            headers=headers,
        ).status_code
        == 422
    )

    bootstrap = client.get(f"{root}/robots/robot-a/bootstrap", headers=headers)
    assert bootstrap.status_code == 200
    assert bootstrap.headers["etag"] == '"robot-a:7"'
    assert (
        bootstrap.json()["data"]["effective_model_binding"]["robot_model_version_id"]
        == "model-version-a"
    )
    assert bootstrap.json()["data"]["allowed_actions"] == ["VIEW"]

    components = client.get(f"{root}/robots/robot-a/components", headers=headers)
    assert components.status_code == 200
    assert components.json()["topology_revision"] == "topology:7"
    assert components.json()["items"][0]["id"] == "camera-a"

    frames = client.get(f"{root}/components/camera-a/frames", headers=headers)
    channels = client.get(f"{root}/components/camera-a/channels", headers=headers)
    assert frames.json()["items"][0]["calibration_set_id"] == "calibration-a"
    assert channels.json()["items"][0]["schema_version"] == "1"
    assert len(repository.audit_events) == 6

    foreign = client.get(
        f"/api/v1/projects/{PROJECT_ID}/regions/us-east-01/robots", headers=headers
    )
    assert foreign.status_code == 403
    assert foreign.json()["code"] == "ORGANIZATION_SCOPE_DENIED"

    same_project_other_organization = client.get(
        f"{root}/robots",
        headers={"Authorization": "Bearer test", "X-Organization-Id": "org-foreign"},
    )
    assert same_project_other_organization.status_code == 403
    assert same_project_other_organization.json()["code"] == "ORGANIZATION_SCOPE_DENIED"

    missing = client.get(f"{root}/components/unknown/frames", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["code"] == "ROBOT_COMPONENT_NOT_FOUND"


def test_robot_directory_rejects_missing_auth_and_capability() -> None:
    service, _repository = _service()
    configure_robotics(service)
    current: dict[str, AuthContext | None] = {"value": None}
    client = TestClient(_app(current), headers={"X-Organization-Id": ORGANIZATION_ID})
    path = f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}/robots"
    assert client.get(path).status_code == 401

    current["value"] = _auth(can_read=False)
    denied = client.get(path, headers={"Authorization": "Bearer test"})
    assert denied.status_code == 403
    assert denied.json()["code"] == "CAPABILITY_REQUIRED"


def test_global_robot_search_is_scoped_cursor_bound_and_audited_without_query_text() -> None:
    service, repository = _service()
    configure_robotics(service)
    current: dict[str, AuthContext | None] = {"value": _auth()}
    client = TestClient(_app(current), headers={"X-Organization-Id": ORGANIZATION_ID})
    root = f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}"
    headers = {"Authorization": "Bearer test"}

    first = client.get(f"{root}/search", params={"q": "星舟", "limit": 2}, headers=headers)
    assert first.status_code == 200
    assert first.headers["cache-control"] == "private, no-store"
    assert [item["robot"]["id"] for item in first.json()["items"]] == [
        "robot-a",
        "robot-b",
    ]
    assert {item["entity_type"] for item in first.json()["items"]} == {"ROBOT"}
    cursor = first.json()["page_info"]["end_cursor"]
    assert isinstance(cursor, str)

    second = client.get(
        f"{root}/search", params={"q": "星舟", "cursor": cursor, "limit": 2}, headers=headers
    )
    assert second.status_code == 200
    assert [item["robot"]["id"] for item in second.json()["items"]] == ["robot-c"]
    assert second.json()["page_info"]["has_next_page"] is False

    mismatch = client.get(f"{root}/search", params={"q": "SN", "cursor": cursor}, headers=headers)
    assert mismatch.status_code == 400
    assert mismatch.json()["code"] == "INVALID_CURSOR"
    assert client.get(f"{root}/search", params={"q": "   "}, headers=headers).status_code == 422
    assert (
        client.get(f"{root}/search", params={"q": "星舟", "limit": 21}, headers=headers).status_code
        == 422
    )

    event = repository.audit_events[-1]
    assert event.action == "robot.search.executed"
    assert event.details == {
        "entity_type": "ROBOT",
        "query_length": 2,
        "result_count": 1,
        "has_next_page": False,
    }
    assert "星舟" not in repr(event.details)

    forbidden = client.get(
        f"/api/v1/projects/{PROJECT_ID}/regions/us-east-01/search",
        params={"q": "星舟"},
        headers=headers,
    )
    assert forbidden.status_code == 403


def test_robot_management_writes_are_idempotent_cas_protected_and_audited() -> None:
    service, repository = _service()
    configure_robotics(service)
    current: dict[str, AuthContext | None] = {"value": _auth(can_manage=True)}
    client = TestClient(_app(current), headers={"X-Organization-Id": ORGANIZATION_ID})
    root = f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}"
    manager_bootstrap = client.get(
        f"{root}/robots/robot-a/bootstrap", headers={"Authorization": "Bearer test"}
    )
    assert manager_bootstrap.status_code == 200
    assert manager_bootstrap.json()["data"]["allowed_actions"] == [
        "VIEW",
        "EDIT",
        "TRANSITION",
    ]
    headers = {"Authorization": "Bearer test", "Idempotency-Key": "create-robot-1"}
    created = client.post(
        f"{root}/robots",
        headers=headers,
        json={
            "display_name": "星舟 018",
            "serial_no": "SN-018",
            "lifecycle_status": "DRAFT",
            "connectivity_state": "OFFLINE",
        },
    )
    assert created.status_code == 201
    assert created.headers["cache-control"] == "private, no-store"
    robot_id = created.json()["data"]["robot"]["id"]
    created_etag = created.headers["etag"]
    assert robot_id.startswith("robot-")

    replay = client.post(
        f"{root}/robots",
        headers=headers,
        json={
            "display_name": "星舟 018",
            "serial_no": "SN-018",
            "lifecycle_status": "DRAFT",
            "connectivity_state": "OFFLINE",
        },
    )
    assert replay.status_code == 201
    assert replay.json()["data"]["robot"]["id"] == robot_id
    reused = client.post(
        f"{root}/robots",
        headers=headers,
        json={"display_name": "不同机器人", "serial_no": "SN-019"},
    )
    assert reused.status_code == 409
    assert reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"

    updated = client.patch(
        f"{root}/robots/{robot_id}",
        headers={
            "Authorization": "Bearer test",
            "Idempotency-Key": "update-robot-1",
            "If-Match": created_etag,
        },
        json={"display_name": "星舟 018A", "connectivity_state": "ONLINE"},
    )
    assert updated.status_code == 200
    updated_etag = updated.headers["etag"]
    assert updated_etag != created_etag
    assert updated.json()["data"]["robot"]["connectivity"]["state"] == "ONLINE"
    stale = client.patch(
        f"{root}/robots/{robot_id}",
        headers={
            "Authorization": "Bearer test",
            "Idempotency-Key": "update-robot-stale",
            "If-Match": created_etag,
        },
        json={"display_name": "不会写入"},
    )
    assert stale.status_code == 412
    assert stale.json()["code"] == "ROBOT_ETAG_MISMATCH"

    invalid_transition = client.post(
        f"{root}/robots/{robot_id}:transition",
        headers={
            "Authorization": "Bearer test",
            "Idempotency-Key": "transition-invalid",
            "If-Match": updated_etag,
        },
        json={"lifecycle_status": "DISABLED", "reason": "未先启用"},
    )
    assert invalid_transition.status_code == 409
    assert invalid_transition.json()["code"] == "ROBOT_LIFECYCLE_TRANSITION_INVALID"

    activated = client.post(
        f"{root}/robots/{robot_id}:transition",
        headers={
            "Authorization": "Bearer test",
            "Idempotency-Key": "transition-active",
            "If-Match": updated_etag,
        },
        json={"lifecycle_status": "ACTIVE", "reason": "现场验收通过"},
    )
    assert activated.status_code == 200
    assert activated.json()["data"]["robot"]["lifecycle_status"] == "ACTIVE"

    maintenance = client.post(
        f"{root}/robots/{robot_id}/maintenance-records",
        headers={"Authorization": "Bearer test", "Idempotency-Key": "maintenance-1"},
        json={"summary": "更换镜头防尘盖", "details": "工单 M-018"},
    )
    assert maintenance.status_code == 201
    history = client.get(
        f"{root}/robots/{robot_id}/maintenance-records",
        headers={"Authorization": "Bearer test"},
    )
    assert history.status_code == 200
    assert {item["event_type"] for item in history.json()["items"]} == {
        "MAINTENANCE",
        "LIFECYCLE_TRANSITION",
    }
    assert {event.action for event in repository.audit_events} >= {
        "robot.created",
        "robot.updated",
        "robot.lifecycle.transitioned",
        "robot.maintenance.recorded",
    }

    current["value"] = _auth()
    denied = client.post(
        f"{root}/robots",
        headers={"Authorization": "Bearer test", "Idempotency-Key": "read-only-create"},
        json={"display_name": "禁止", "serial_no": "SN-NO"},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "CAPABILITY_REQUIRED"


def test_robot_component_management_preserves_topology_and_retires_without_breaking_facts() -> None:
    service, repository = _service()
    configure_robotics(service)
    current: dict[str, AuthContext | None] = {"value": _auth(can_manage=True)}
    client = TestClient(_app(current), headers={"X-Organization-Id": ORGANIZATION_ID})
    root = f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}"
    headers = {
        "Authorization": "Bearer test",
        "Idempotency-Key": "create-arm-component",
        "If-Match": '"robot-a:7"',
    }
    command = {
        "component_model_id": "arm-model-a",
        "component_type": "ARM",
        "display_name": "主机械臂",
        "serial_no": "ARM-017",
        "lifecycle_status": "DRAFT",
        "sort_order": 1,
    }
    created = client.post(f"{root}/robots/robot-a/components", headers=headers, json=command)
    assert created.status_code == 201
    assert created.headers["cache-control"] == "private, no-store"
    component_id = created.json()["data"]["component"]["id"]
    created_etag = created.headers["etag"]
    assert component_id.startswith("component-")

    replay = client.post(f"{root}/robots/robot-a/components", headers=headers, json=command)
    assert replay.status_code == 201
    assert replay.json()["data"]["component"]["id"] == component_id
    reused = client.post(
        f"{root}/robots/robot-a/components",
        headers=headers,
        json={**command, "display_name": "不同命令"},
    )
    assert reused.status_code == 409
    assert reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"

    updated = client.patch(
        f"{root}/components/{component_id}",
        headers={
            "Authorization": "Bearer test",
            "Idempotency-Key": "update-arm-component",
            "If-Match": created_etag,
        },
        json={"display_name": "主机械臂 A"},
    )
    assert updated.status_code == 200
    updated_etag = updated.headers["etag"]
    assert updated.json()["data"]["component"]["display_name"] == "主机械臂 A"
    stale = client.patch(
        f"{root}/components/{component_id}",
        headers={
            "Authorization": "Bearer test",
            "Idempotency-Key": "stale-arm-component",
            "If-Match": created_etag,
        },
        json={"display_name": "不能写入"},
    )
    assert stale.status_code == 412
    assert stale.json()["code"] == "ROBOT_ETAG_MISMATCH"

    activated = client.post(
        f"{root}/components/{component_id}:transition",
        headers={
            "Authorization": "Bearer test",
            "Idempotency-Key": "activate-arm-component",
            "If-Match": updated_etag,
        },
        json={"lifecycle_status": "ACTIVE", "reason": "安装验收"},
    )
    assert activated.status_code == 200
    active_etag = activated.headers["etag"]
    assert activated.json()["data"]["component"]["lifecycle_status"] == "ACTIVE"

    child = client.post(
        f"{root}/robots/robot-a/components",
        headers={
            "Authorization": "Bearer test",
            "Idempotency-Key": "create-gripper-component",
            "If-Match": active_etag,
        },
        json={
            "parent_component_id": component_id,
            "component_model_id": "gripper-model-a",
            "component_type": "GRIPPER",
            "display_name": "末端夹具",
            "serial_no": "GRIP-017",
        },
    )
    assert child.status_code == 201
    child_id = child.json()["data"]["component"]["id"]
    child_created_etag = child.headers["etag"]

    blocked_retirement = client.post(
        f"{root}/components/{component_id}:transition",
        headers={
            "Authorization": "Bearer test",
            "Idempotency-Key": "retire-parent-before-child",
            "If-Match": child_created_etag,
        },
        json={"lifecycle_status": "RETIRED", "reason": "不得孤立活动子组件"},
    )
    assert blocked_retirement.status_code == 409
    assert blocked_retirement.json()["code"] == "ROBOT_COMPONENT_TOPOLOGY_CONFLICT"

    child_retired = client.post(
        f"{root}/components/{child_id}:transition",
        headers={
            "Authorization": "Bearer test",
            "Idempotency-Key": "retire-child-component",
            "If-Match": child_created_etag,
        },
        json={"lifecycle_status": "RETIRED", "reason": "夹具已拆除"},
    )
    assert child_retired.status_code == 200
    parent_retired = client.post(
        f"{root}/components/{component_id}:transition",
        headers={
            "Authorization": "Bearer test",
            "Idempotency-Key": "retire-parent-component",
            "If-Match": child_retired.headers["etag"],
        },
        json={"lifecycle_status": "RETIRED", "reason": "机械臂退役，历史引用保留"},
    )
    assert parent_retired.status_code == 200

    history = client.get(
        f"{root}/robots/robot-a/maintenance-records",
        headers={"Authorization": "Bearer test"},
    )
    assert any(
        item["component_id"] == component_id
        and item["event_type"] == "COMPONENT_LIFECYCLE_TRANSITION"
        for item in history.json()["items"]
    )
    assert {event.action for event in repository.audit_events} >= {
        "robot.component.created",
        "robot.component.updated",
        "robot.component.lifecycle.transitioned",
    }
