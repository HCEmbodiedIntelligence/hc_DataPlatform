import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.platform_ops.maintenance import PostgresMaintenanceRepository
from hc_data_platform.robot_ingest.models import CreateRobotIngestIdentity, IssueCredentialCommand
from hc_data_platform.robot_ingest.router import get_robot_ingest_service
from hc_data_platform.robot_ingest.service import RobotIngestService
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.http import require_auth_context
from hc_data_platform.teleoperation.store import PresenceStore


@pytest.fixture
def endpoints():
    org = "xr-" + uuid4().hex
    auth = AuthContext(
        subject_id="op",
        project_ids=frozenset({"p"}),
        region_codes=frozenset(),
        organization_ids=frozenset({org}),
        organization_scope_triples=frozenset({(org, "p", None)}),
        organization_scoped_capabilities=frozenset(
            (org, "p", c) for c in ("robot.read", "teleoperation.connect", "ingest_source.manage")
        ),
    )
    service = RobotIngestService.in_memory()
    robot = "r1"
    identity = service.create_identity(
        auth=auth,
        organization_id=org,
        project_id="p",
        command=CreateRobotIngestIdentity(robot_id=robot, allowed_formats=("MCAP",)),
    ).data
    issued = service.issue_credential(
        auth=auth,
        organization_id=org,
        project_id="p",
        ingest_identity_id=identity.ingest_identity_id,
        command=IssueCredentialCommand(),
    ).credential
    dsn = os.getenv("TELEOP_TEST_POSTGRES_DSN")
    app = create_app(
        settings=Settings(environment="test", runtime_backend="memory"),
        maintenance_write_gate=PostgresMaintenanceRepository.from_dsn(dsn) if dsn else None,
    )
    if dsn:
        app.state.teleoperation = PresenceStore(psycopg_connection_factory(dsn))
    app.dependency_overrides[get_robot_ingest_service] = lambda: service
    app.dependency_overrides[require_auth_context] = lambda: auth
    with TestClient(app) as http:
        yield http, org, auth, {"Authorization": "Bearer " + issued.token}


SYNC = "/api/v1/robot-ingest/teleoperation/sync"


def test_robot_exchange_and_concurrent_reservation(endpoints):
    http, org, auth, headers = endpoints
    robots = f"/api/v1/organizations/{org}/teleoperation/robots"
    sessions = robots + "/r1/sessions"
    device = {"instance_id": str(uuid4()), "name": "lab-arm"}

    def robot(**kwargs):
        return http.post(SYNC, headers=headers, json=device | kwargs)

    assert http.get(robots).json() == []
    assert http.post(SYNC, json=device).status_code == 401
    assert robot().status_code == 200
    assert robot(instance_id=str(uuid4())).status_code == 409
    assert http.get(robots).json()[0]["robot_id"] == "r1"
    assert http.get(robots.replace(org, "elsewhere")).status_code == 403
    with ThreadPoolExecutor(2) as pool:
        reservations = list(pool.map(lambda _: http.post(sessions), range(2)))
    assert sorted(r.status_code for r in reservations) == [201, 409]
    sid = next(r for r in reservations if r.status_code == 201).json()["session_id"]
    path = f"{sessions}/{sid}"
    offer, answer = "v=0\r\ns=offer\r\n", "v=0\r\ns=answer\r\n"
    assert http.get(robots).json()[0]["busy"]
    assert http.post(path + "/sync", json={"offer": offer}).status_code == 200
    assert robot().json()["offer"] == offer
    assert robot(session_id=str(uuid4()), answer=answer).status_code == 409
    assert robot(session_id=sid, answer=answer).status_code == 200
    assert http.post(path + "/sync", json={}).json()["answer"] == answer
    http.app.dependency_overrides[require_auth_context] = lambda: replace(auth, subject_id="other")
    assert http.delete(path).status_code == 409
    http.app.dependency_overrides[require_auth_context] = lambda: auth
    assert http.delete(path).status_code == 204
    assert "session_id" not in robot().json()
    assert http.post(path + "/sync", json={}).status_code == 409


def test_lease_and_permission(endpoints, monkeypatch):
    from types import SimpleNamespace

    from hc_data_platform.teleoperation import router

    now = [1000.0]
    monkeypatch.setattr(router, "time", SimpleNamespace(time=lambda: now[0]))
    http, org, auth, headers = endpoints
    robots = f"/api/v1/organizations/{org}/teleoperation/robots"
    sessions = robots + "/r1/sessions"
    device = {"instance_id": str(uuid4()), "name": "VR endpoint"}
    assert http.post(SYNC, headers=headers, json=device).is_success
    http.app.dependency_overrides[require_auth_context] = lambda: replace(
        auth, organization_scoped_capabilities=frozenset({(org, "p", "robot.read")})
    )
    assert http.get(robots).status_code == 200
    assert http.post(sessions).status_code == 403

    def grant(robot_id):
        return replace(
            auth,
            organization_scoped_capabilities=frozenset(
                {(org, "p", "robot.read"), (org, "p", f"teleoperation.connect/{robot_id}")}
            ),
        )

    http.app.dependency_overrides[require_auth_context] = lambda: grant("unassigned")
    assert http.post(sessions).status_code == 403
    http.app.dependency_overrides[require_auth_context] = lambda: grant("r1")
    sid = http.post(sessions).json()["session_id"]
    now[0] += 12
    assert http.post(SYNC, headers=headers, json=device).is_success
    now[0] += 9
    assert http.get(robots).json()[0]["busy"] is False
    assert http.post(f"{sessions}/{sid}/sync", json={}).status_code == 409
    now[0] += 21
    assert http.get(robots).json() == []
    assert http.post(sessions).status_code == 409
    assert http.post(SYNC, headers=headers, json=device | {"instance_id": str(uuid4())}).is_success


def test_capture_round_trip_keeps_machine_receipt_and_rejects_stale_commands(endpoints):
    from dataclasses import replace
    from types import SimpleNamespace
    from unittest.mock import Mock

    from hc_data_platform.collection_tasks.router import get_collection_task_service

    http, org, auth, headers = endpoints
    base = f"/api/v1/organizations/{org}/teleoperation/robots/r1"
    device = {
        "instance_id": str(uuid4()),
        "name": "capture",
        "capture_project": "p",
        "capture_tasks": ["t1"],
    }
    assert http.post(SYNC, headers=headers, json=device).status_code == 200
    sid = http.post(base + "/sessions").json()["session_id"]
    cap = base + "/capture"
    command = {
        "session_id": sid,
        "request_id": str(uuid4()),
        "project_id": "p",
        "task_id": "t1",
        "action": "start",
        "revision": 0,
    }
    assert http.post(cap, json=command).status_code == 403
    grants = auth.organization_scoped_capabilities | {
        (org, "p", "collection_task.read"),
        (org, "p", "ingest.import"),
    }
    http.app.dependency_overrides[require_auth_context] = lambda: replace(
        auth, organization_scoped_capabilities=grants
    )
    tasks = Mock()
    tasks.detail.return_value.public.return_value = SimpleNamespace(
        status=SimpleNamespace(value="ACTIVE")
    )
    http.app.dependency_overrides[get_collection_task_service] = lambda: tasks

    def report(body):
        return http.post(SYNC, headers=headers, json=device | {"session_id": sid} | body)

    for action in ("start", "mark", "stop"):
        state = http.get(cap, params={"project_id": "p"})
        assert state.status_code == 200, state.text
        command.update(action=action, request_id=str(uuid4()), revision=state.json()["revision"])
        pending = http.post(cap, json=command)
        assert pending.status_code == 200, pending.text
        assert pending.json()["state"] == "pending"
        assert http.post(cap, json=command).json() == pending.json()
        receipt = {
            "request_id": command["request_id"],
            "state": "saved" if action == "stop" else "recording",
            "record_id": "episode-qa",
        }
        assert report({"capture_result": receipt}).status_code == 409
        assert report({}).json()["capture_command"] == command
        assert report({}).json()["capture_command"] == command
        assert report({"capture_result": receipt}).status_code == 200
        assert "capture_command" not in report({"capture_result": receipt}).json()
        stale = command | {"request_id": str(uuid4())}
        assert http.post(cap, json=stale).status_code == 409
    assert http.delete(base + "/sessions/" + sid).status_code == 204
    final = http.get(cap, params={"project_id": "p"}).json()
    assert (final["state"], final["record_id"]) == ("saved", "episode-qa")
    assert http.post(cap, json=command).status_code == 409
