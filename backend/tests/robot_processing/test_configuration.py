import copy
import json
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hc_data_platform.continuous_recordings.asset_models import RecordingConfigurationV1
from hc_data_platform.continuous_recordings.mcap_native import inspect, prepare, resolve
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.robot_assets.service import OrganizationRobotAssetService
from hc_data_platform.robot_processing import migration, service
from hc_data_platform.robot_processing.models import Draft, Publish
from hc_data_platform.robot_processing.router import get_organization_robot_asset_service, router
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.http import require_auth_context
from tests.continuous_recordings.test_mcap_native import synthetic_mcap

CONFIG = dict(
    collection_mode="JOINT_ONLY",
    time_source="STATE_LOG_TIME",
    state_topic="/joint_states",
    action_topic="/joint_cmd",
    joint_names=["joint"],
    joint_units=["rad"],
)
INVENTORY = {
    t: {
        "kind": "joints",
        "schema": "sensor_msgs/JointState",
        "joint_names": ["joint"],
        "message_count": 12,
    }
    for t in ("/joint_states", "/joint_cmd")
}


def test_candidates_never_assign_roles_or_intersect_joints():
    inv = copy.deepcopy(INVENTORY)
    inv["/action/joint_states"] = inv["/joint_states"]
    with pytest.raises(ValueError, match="missing state, action.*candidates"):
        resolve({}, inv)
    inv["/joint_cmd"]["joint_names"] = ["another"]
    for config in (CONFIG, {**CONFIG, "joint_names": [], "joint_units": []}):
        with pytest.raises(ValueError, match="JOINT_LAYOUT_MISMATCH"):
            resolve(config, inv)


@pytest.mark.parametrize("registered", [False, True])
def test_missing_rgb_and_zero_message_channels_are_data_loss(registered):
    inv = copy.deepcopy(INVENTORY)
    if registered:
        inv["/head"] = {"kind": "rgb", "schema": "sensor_msgs/msg/Image", "message_count": 0}
    with pytest.raises(ValueError, match="REQUIRED_RGB_MISSING.*message_count=0"):
        resolve(
            {
                **CONFIG,
                "collection_mode": "RGB",
                "time_source": "RGB_LOG_TIME",
                "primary_camera": "/head",
            },
            inv,
        )


def test_multiple_rgb_candidates_and_depth_units():
    inv = {**INVENTORY, "/head": {"kind": "rgb"}, "/wrist": {"kind": "rgb"}}
    with pytest.raises(ValueError, match="primary camera.*head.*wrist"):
        resolve({**CONFIG, "collection_mode": "RGB", "time_source": "RGB_LOG_TIME"}, inv)
    with pytest.raises(ValueError, match="DEPTH_UNITS_REQUIRED"):
        service.validate_configuration({**CONFIG, "depth_scale_m_per_unit": 0.001}, INVENTORY)


def test_joint_clock_retains_gaps_timestamps_and_no_video(tmp_path):
    path = synthetic_mcap(tmp_path / "gap.mcap", joint_only=True, missing=(3, 4, 5, 6, 7))
    inv = inspect(path)
    assert inv["/color"]["message_count"] == 0
    config, evidence = prepare(path, tmp_path / "derived", CONFIG)
    RecordingConfigurationV1.model_validate(config)
    assert not config["cameras"] and evidence["frames"] == 12 and evidence["invalid_frames"] >= 3
    assert not list((tmp_path / "derived").rglob("*.mp4"))
    assert evidence["duration_ns"] >= 400_000_000
    from mcap.reader import make_reader

    with (tmp_path / "derived/sensors/robot.mcap").open("rb") as f:
        values = [
            json.loads(m.data)
            for _, c, m in make_reader(f).iter_messages()
            if c.topic == "/observation/state"
        ]
    assert any(v["source_timestamp_ns"] is None for v in values)
    assert any(v["values"] == [None] for v in values)
    with pytest.raises(ValueError, match="legacy RGB"):
        RecordingConfigurationV1.model_validate(
            {**config, "collection_mode": "RGB", "schema_version": "recording-config/v1"}
        )


@pytest.fixture
def pg():
    dsn = os.environ.get("ROBOT_CONFIG_TEST_DSN")
    if not dsn:
        pytest.skip("isolated DB required")
    org = "cfg-" + uuid4().hex
    robot = "robot"
    with psycopg.connect(dsn) as db:
        db.execute(
            "INSERT INTO registry.organization_projects VALUES(%s,'p','Config tests')", (org,)
        )
        db.execute(
            (
                """INSERT INTO
                robotics.robot_assets(organization_id,robot_id,display_name,serial_no,lifecycle_status,connectivity_state,etag,topology_revision)
                VALUES(%s,%s,'Test','serial','ACTIVE','OFFLINE','1','1')"""
            ),
            (org, robot),
        )
    return dsn, org, robot


def draft(revision=0, config=None):
    return Draft(
        expected_revision=revision,
        name="Joint plan",
        software="fixture",
        configuration=config or CONFIG,
    )


def publish(db, org, robot, scheme="joints", revision=1):
    service.validate(db, org, robot, scheme, revision, INVENTORY, {"sample": "synthetic"}, "tester")
    return service.publish(db, org, robot, scheme, Publish(expected_revision=revision), "tester")


def test_draft_requires_real_evidence_publication_immutable_and_concurrent(pg):
    dsn, org, robot = pg

    def writer(_):
        try:
            with psycopg.connect(dsn) as db:
                service.save(db, org, robot, "joints", draft(), "writer")
            return "ok"
        except ProblemException:
            return "conflict"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(writer, range(8)))
    assert results.count("ok") == 1 and results.count("conflict") == 7
    with psycopg.connect(dsn) as db:
        with pytest.raises(ProblemException):
            service.publish(db, org, robot, "joints", Publish(expected_revision=1), "tester")
        first = publish(db, org, robot)["versions"][0]
        assert service.select_snapshot(db, (org, "p2", "region2"), robot) == first
        again = publish(db, org, robot)
        assert len(again["versions"]) == 1
        service.save(
            db, org, robot, "joints", draft(1, {**CONFIG, "maximum_time_error_ms": 25}), "tester"
        )
        latest = publish(db, org, robot, revision=2)["versions"][0]
        assert latest["version"] == 2 and latest != first
        assert (
            service.select_snapshot(
                db,
                (org, "p", "r"),
                robot,
                {k: first[k] for k in ("scheme_id", "version", "content_sha256")},
            )
            == first
        )
        assert (
            service.select_snapshot(
                db,
                ("foreign", "p", "r"),
                robot,
                {"scheme_id": "joints", "version": 1, "content_sha256": first["content_sha256"]},
            )["error"]
            == "ROBOT_PROCESSING_CONFIG_VERSION_UNKNOWN"
        )
    with psycopg.connect(dsn) as db, pytest.raises(psycopg.errors.RaiseException):
        db.execute(
            (
                "UPDATE ingest.robot_processing_versions SET "
                "document=document WHERE organization_id=%s"
            ),
            (org,),
        )


def test_task_visual_requirement_and_migration_conflicts_idempotent(pg):
    dsn, org, robot = pg
    with psycopg.connect(dsn) as db:
        for project, config in [
            ("p", CONFIG),
            ("p2", CONFIG),
            ("p3", {**CONFIG, "action_topic": "/other"}),
        ]:
            db.execute(
                "INSERT INTO ingest.mcap_robot_profiles VALUES(%s,%s,'r',%s,%s::jsonb,now())",
                (org, project, robot, json.dumps(config)),
            )
        before = db.execute("SELECT count(*) FROM core.outbox_events").fetchone()[0]
        report = migration.inventory(db)
        selected = [c for c in report["candidates"] if c["organization_id"] == org]
        assert len(selected) == 2 and all(c["conflict"] for c in selected)
        approval = {
            "inventory_sha256": report["inventory_sha256"],
            "candidate_ids": [c["candidate_id"] for c in selected],
        }
        assert all(
            r["result"] == "DRAFT_CREATED" for r in migration.apply(db, report, approval, "admin")
        )
        assert all(
            r["result"] == "ALREADY_APPLIED" for r in migration.apply(db, report, approval, "admin")
        )
        assert (
            service.select_snapshot(db, (org, "p", "r"), robot)["error"]
            == "ROBOT_PROCESSING_CONFIG_CONFLICT"
        )
        state = service.read(db, org, robot)
        assert not state["versions"] and all(not s["is_default"] for s in state["schemes"])
        assert db.execute("SELECT count(*) FROM core.outbox_events").fetchone()[0] == before
        service.save(db, org, robot, "approved", draft(), "admin")
        version = publish(db, org, robot, "approved")["versions"][0]
        db.execute(
            (
                "INSERT INTO ingest.robot_task_data_requirements "
                "VALUES(%s,'p','task',true,false,'admin',now())"
            ),
            (org,),
        )
        assert (
            service.check_task(db, (org, "p", "r"), "task", version)["error"]
            == "ROBOT_TASK_DATA_INCOMPATIBLE"
        )
        assert "error" not in service.check_task(db, (org, "p2", "r"), "task", version)


def test_config_route_does_not_grant_robot_management_to_upload_users():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_organization_robot_asset_service] = (
        OrganizationRobotAssetService.in_memory
    )
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(
        subject_id="u",
        region_codes=frozenset(),
        organization_ids=frozenset({"org"}),
        project_ids=frozenset({"p"}),
        organization_scoped_capabilities=frozenset({("org", "p", "upload.manage")}),
    )
    # Endpoint fails authorization before any persistence dependency runs.
    client = TestClient(app)
    from fastapi.responses import JSONResponse

    @app.exception_handler(ProblemException)
    async def handle_problem(request, exc):
        return JSONResponse(exc.problem.model_dump(mode="json"), status_code=exc.problem.status)

    response = client.put(
        "/api/v1/organizations/org/robots/robot/processing-config/s",
        json=draft().model_dump(mode="json"),
    )
    assert response.status_code == 403, response.text
    response = client.get("/api/v1/organizations/foreign/robots/robot/processing-config")
    assert response.status_code == 403, response.text


def test_configuration_and_batch_inspection_routes_require_authentication():
    from hc_data_platform.core.app import create_app
    from hc_data_platform.core.config import Settings

    app = create_app(
        settings=Settings(environment="test", runtime_backend="memory", _env_file=None)
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        for path, item in app.openapi()["paths"].items():
            if not any(
                p in path
                for p in (
                    "processing-config",
                    "robot-processing-templates",
                    "data-requirements",
                    "mcap-versions",
                    "mcap-channels",
                )
            ):
                continue
            import re

            concrete = re.sub(r"\{[^}]+\}", "test", path)
            for method in item:
                if method in {"get", "put", "post"}:
                    response = client.request(method, concrete, json={})
                    assert response.status_code == 401, (method, path, response.text)


def test_database_rls_is_organization_scoped_across_projects(pg):
    from psycopg import sql

    dsn, org, robot = pg
    role = "cfg_reader_" + uuid4().hex
    with psycopg.connect(dsn) as db:
        service.save(db, org, robot, "joints", draft(), "admin")
        publish(db, org, robot)
    # A transaction-local role is rolled back with all grants after the check.
    with psycopg.connect(dsn) as db:
        db.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(role)))
        db.execute(
            sql.SQL("GRANT USAGE ON SCHEMA ingest,core,robotics TO {}").format(sql.Identifier(role))
        )
        db.execute(
            sql.SQL(
                """GRANT SELECT ON
                ingest.robot_processing_schemes,ingest.robot_processing_versions,ingest.robot_processing_audit,robotics.robot_assets
                TO {}"""
            ).format(sql.Identifier(role))
        )
        db.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
        db.execute(
            (
                "SELECT set_config('app.organization_id',%s,true),set_config("
                "'app.platform_admin','false',true)"
            ),
            (org,),
        )
        for project in ("p", "another-project"):
            db.execute("SELECT set_config('app.project_id',%s,true)", (project,))
            assert {
                r[0]
                for r in db.execute("SELECT organization_id FROM ingest.robot_processing_versions")
            } == {org}
        db.execute("SELECT set_config('app.organization_id','foreign',true)")
        assert db.execute("SELECT count(*) FROM ingest.robot_processing_versions").fetchone() == (
            0,
        )
        db.rollback()


def test_concurrent_publications_have_one_default_and_idempotent_versions(pg):
    dsn, org, robot = pg
    with psycopg.connect(dsn) as db:
        for scheme in ("a", "b"):
            service.save(db, org, robot, scheme, draft(), "admin")
            service.validate(db, org, robot, scheme, 1, INVENTORY, {"sample": "fixture"}, "admin")

    def writer(scheme):
        with psycopg.connect(dsn) as db:
            service.publish(db, org, robot, scheme, Publish(expected_revision=1), "admin")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(writer, ["a", "b"] * 4))
    with psycopg.connect(dsn) as db:
        state = service.read(db, org, robot)
        assert len(state["versions"]) == 2
        assert sum(s["is_default"] for s in state["schemes"]) == 1


def test_migration_requires_exact_inventory_and_explicit_candidates(pg):
    dsn, org, robot = pg
    with psycopg.connect(dsn) as db:
        report = migration.inventory(db)
        for approval in (
            {"inventory_sha256": "changed"},
            {"inventory_sha256": report["inventory_sha256"], "candidate_ids": ["unknown"]},
        ):
            with pytest.raises(ValueError):
                migration.apply(db, report, approval, "admin")
        assert (
            migration.apply(db, report, {"inventory_sha256": report["inventory_sha256"]}, "admin")
            == []
        )
        assert service.read(db, org, robot)["schemes"] == []


def test_migration_cli_dry_run_and_approved_replay(pg, tmp_path):
    import subprocess
    import sys

    dsn, org, robot = pg
    with psycopg.connect(dsn) as db:
        db.execute(
            "INSERT INTO ingest.mcap_robot_profiles VALUES(%s,'p','r',%s,%s::jsonb,now())",
            (org, robot, json.dumps(CONFIG)),
        )
        before = db.execute("SELECT count(*) FROM core.outbox_events").fetchone()[0]
    env = {**os.environ, "HC_POSTGRES_DSN": dsn}

    def invoke(name, *arguments):
        output = tmp_path / name
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "hc_data_platform.robot_processing.migration",
                "--output",
                str(output),
                *arguments,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(output.read_text())

    report = invoke("dry-run.json")
    selected = [c["candidate_id"] for c in report["candidates"] if c["organization_id"] == org]
    assert len(selected) == 1 and "applied" not in report
    with psycopg.connect(dsn) as db:
        assert service.read(db, org, robot)["schemes"] == []
    approval = tmp_path / "approval.json"
    approval.write_text(
        json.dumps(
            {
                "inventory_sha256": report["inventory_sha256"],
                "candidate_ids": selected,
                "source_link_ids": [],
            }
        )
    )
    args = ("--approval", str(approval), "--actor", "isolated-cli-test")
    assert invoke("applied.json", *args)["applied"][0]["result"] == "DRAFT_CREATED"
    assert invoke("replayed.json", *args)["applied"][0]["result"] == "ALREADY_APPLIED"
    with psycopg.connect(dsn) as db:
        state = service.read(db, org, robot)
        assert not state["versions"] and len(state["schemes"]) == 1
        assert not state["schemes"][0]["is_default"]
        assert db.execute("SELECT count(*) FROM core.outbox_events").fetchone()[0] == before
        assert (
            db.execute(
                "SELECT configuration FROM ingest.mcap_robot_profiles WHERE organization_id=%s",
                (org,),
            ).fetchone()[0]
            == CONFIG
        )
