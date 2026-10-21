"""HTTP serialization must retain the exact immutable payload used by clients.

Database setup uses synthetic sample evidence; these are contract/transaction
checks, not proof that source media have been decoded.
"""

import psycopg
from fastapi.testclient import TestClient

from hc_data_platform.capture_schemes import schemes
from hc_data_platform.capture_schemes.common import digest
from hc_data_platform.capture_schemes.components import CaptureServices
from hc_data_platform.capture_schemes.router import router
from hc_data_platform.robot_ingest.connection_api import router as connection_router
from hc_data_platform.robot_ingest.router import get_robot_ingest_service

from .test_application_dependencies import Samples, application, identity
from .test_entrypoint_transactions import robot_service, robot_token
from .test_transactions import associate, environment, publish, selection  # noqa: F401


def test_http_plan_and_recording_serialization_preserves_content_hashes(environment):  # noqa: F811
    dsn, scope, tasks = environment
    with psycopg.connect(dsn) as db:
        ref = publish(db, scope[0])
        associate(db, scope[0], "robot-1", ref)
        associate(db, scope[0], "robot-2", ref)
        published = schemes.published_version(db, scope[0], ref.scheme_id, ref.version)
    service = robot_service(dsn, scope)
    token = robot_token(dsn, scope, service)
    services = CaptureServices(lambda: psycopg.connect(dsn), Samples("unused"), lambda _: True)
    app = application(identity(scope, "robot.read", "upload.read", "upload.manage"), services)
    app.include_router(connection_router)
    app.dependency_overrides[get_robot_ingest_service] = lambda: service
    scheme_path = f"/api/v1/organizations/{scope[0]}/capture-schemes/{ref.scheme_id}"
    plan_path = f"/api/v1/projects/{scope[1]}/collection-tasks/{tasks[0]}/capture-plan"
    headers = {"X-Organization-Id": scope[0], "X-Region-Code": scope[2]}
    with TestClient(app) as client:
        version = client.get(scheme_path + "/versions/1")
        assert version.status_code == 200, version.text
        assert digest(version.json()) == digest(published)
        assert client.get(scheme_path + "/versions/99").status_code == 404
        assert (
            client.get(scheme_path.replace(scope[0], "another-org") + "/versions/1").status_code
            == 403
        )
        links = client.get(f"/api/v1/organizations/{scope[0]}/robots/robot-1/capture-schemes")
        assert links.status_code == 200, links.text
        assert links.json()["items"][0]["scheme"]["content_sha256"] == ref.content_sha256

        response = client.put(
            plan_path,
            headers=headers,
            json={"expected_revision": 0, "scheme": ref.model_dump(mode="json")},
        )
        assert response.status_code == 200, response.text
        plan = response.json()["plan"]
        assert plan["participants"] == []
        assert "change" not in plan
        assert (
            digest({k: v for k, v in plan.items() if k not in {"plan_sha256", "locked_at"}})
            == plan["plan_sha256"]
        )
        command = {
            "expected_plan_revision": 1,
            "recording_id": "http-recording",
            "source_format": "LEROBOT_V3",
        }
        prepare_path = f"/api/v1/robot-ingest/tasks/{tasks[0]}:prepare-recording"
        response = client.post(
            prepare_path, headers={"Authorization": f"Bearer {token}"}, json=command
        )
        assert response.status_code == 200, response.text
        snapshot = response.json()
        assert response.headers["cache-control"] == "no-store"
        assert (
            digest({k: v for k, v in snapshot.items() if k != "snapshot_sha256"})
            == snapshot["snapshot_sha256"]
        )
        with psycopg.connect(dsn) as db:
            saved = db.execute(
                "SELECT document FROM ingest.capture_recording_snapshots "
                "WHERE organization_id=%s AND robot_id='robot-1' AND recording_id='http-recording'",
                (scope[0],),
            ).fetchone()[0]
        assert digest(snapshot) == digest(saved)
        added = client.post(
            plan_path + ":add-participants",
            headers=headers,
            json={
                "expected_revision": 1,
                "participants": [{"robot_id": "robot-2", "mapping_version": 1}],
            },
        )
        assert added.status_code == 200, added.text
        plan = added.json()["plan"]
        assert plan["locked_at"] is not None and plan["revision"] == 2
        assert (
            digest({k: v for k, v in plan.items() if k not in {"plan_sha256", "locked_at"}})
            == plan["plan_sha256"]
        )
        repeated = client.post(
            prepare_path, headers={"Authorization": f"Bearer {token}"}, json=command
        )
        assert repeated.json() == snapshot
        with psycopg.connect(dsn) as db:
            schemes.archive(db, scope[0], ref.scheme_id, 1, "tester")
        assert client.get(scheme_path + "/versions/1").json() == published


def test_all_capture_routes_and_recording_preparation_publish_typed_responses():
    app = application(identity(("org", "project", "region"), "robot.read"))
    app.include_router(connection_router)
    document = app.openapi()
    expected = {route.operation_id for route in router.routes if hasattr(route, "operation_id")} | {
        "prepareRobotTaskRecording"
    }
    observed = set()
    for item in document["paths"].values():
        for operation in item.values():
            if operation.get("operationId") in expected:
                schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
                assert schema["$ref"].startswith("#/components/schemas/Capture"), schema
                observed.add(operation["operationId"])
    assert observed == expected
