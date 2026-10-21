from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.registry.binding_queries import published_model_for_binding
from hc_data_platform.robot_assets.repository import PostgresOrganizationRobotAssetRepository
from hc_data_platform.robot_assets.router import configure_organization_robot_assets
from hc_data_platform.robot_assets.service import OrganizationRobotAssetService
from hc_data_platform.security.auth import AuthContext
from tests.robot_ingest.test_raw_derivation_services import uploaded_raw  # noqa: F401
from tests.robot_processing.test_configuration import publish
from tests.robotics.test_configured_robot_deletion import robot  # noqa: F401
from tests.robotics.test_robot_directory_api import _app

pytest_plugins = ["tests.episode_store.test_commit_services"]
pytestmark = pytest.mark.integration


def actor(org, project, region, *, read=True):
    return AuthContext(
        subject_id="deletion-check",
        organization_ids=frozenset({org}),
        project_ids=frozenset({project}),
        region_codes=frozenset({region}),
        organization_scope_triples=frozenset({(org, project, region)}),
        organization_scoped_capabilities=frozenset(
            (org, project, cap)
            for cap in (("robot.manage", "upload.read") if read else ("robot.manage",))
        ),
    )


def setup(uploaded):
    repository, _, raw, _, command, _ = uploaded
    scope = raw.target
    repo = PostgresOrganizationRobotAssetRepository(
        repository.connections,
        published_models=published_model_for_binding,
    )
    service = OrganizationRobotAssetService(repo)
    auth = actor(scope.organization_id, scope.project_id, scope.region_code)
    args = dict(auth=auth, organization_id=scope.organization_id, robot_id=command.robot_id)
    return service, args, repository


def test_report_identifies_real_task_file_and_upload_without_mutating_history(uploaded_raw):  # noqa: F811
    service, args, repository = setup(uploaded_raw)

    def snapshot():
        with repository.connections() as db:
            return {
                table: db.execute(
                    f"SELECT row_to_json(r) FROM {table} r WHERE organization_id=%s",
                    (args["organization_id"],),
                ).fetchall()
                for table in (
                    "ingest.raw_sources",
                    "ingest.robot_ingest_uploads",
                    "robotics.robot_assets",
                )
            }

    before = snapshot()
    report = service.deletion_check(**args)
    assert not report.can_delete
    assert {b.kind: b.count for b in report.blockers} == {"raw": 1, "upload": 1}
    assert report.total == 2 and report.restricted_count == 0
    assert {r.kind for r in report.items} == {"raw", "upload"}
    for item in report.items:
        assert item.task_name == "Synthetic Raw" and len(item.task_code) == 8
        assert item.collection_task_id == uploaded_raw[4].collection_task_id
        assert item.name == uploaded_raw[4].assets[0].path
        assert item.project_id == args["auth"].project_ids.__iter__().__next__()
    assert snapshot() == before
    assert report.items[0].raw_source_id == report.items[1].raw_source_id


def test_report_pages_and_kind_filter_do_not_hide_blockers(uploaded_raw):  # noqa: F811
    service, args, _ = setup(uploaded_raw)
    page1 = service.deletion_check(**args, limit=1)
    page2 = service.deletion_check(**args, limit=1, offset=1)
    assert page1.items[0].kind != page2.items[0].kind
    assert page1.total == page2.total == 2
    report = service.deletion_check(**args, kind="upload")
    assert report.total == 1 and report.items[0].kind == "upload"
    assert len(report.blockers) == 2
    assert service.deletion_check(**args, offset=100).items == []


@pytest.mark.parametrize("restriction", ["no_read", "other_region", "other_project"])
def test_robot_management_never_exposes_unauthorized_task_and_file_names(uploaded_raw, restriction):  # noqa: F811
    service, args, _ = setup(uploaded_raw)
    original = args["auth"]
    org, project, region = next(iter(original.organization_scope_triples))
    auth = actor(
        org,
        project if restriction != "other_project" else "elsewhere",
        region if restriction != "other_region" else "other-region",
        read=restriction != "no_read",
    )
    args["auth"] = auth
    report = service.deletion_check(**args)
    assert not report.can_delete and report.restricted_count == 2
    assert report.total == 0 and report.items == []
    assert "Synthetic Raw" not in report.model_dump_json()
    assert uploaded_raw[4].collection_task_id not in report.model_dump_json()


def test_endpoint_auth_missing_robot_and_parameters(uploaded_raw):  # noqa: F811
    service, args, _ = setup(uploaded_raw)
    configure_organization_robot_assets(service)
    current = {"value": args["auth"]}
    client = TestClient(_app(current))
    path = (
        f"/api/v1/organizations/{args['organization_id']}/robots/{args['robot_id']}/deletion-check"
    )
    headers = {"Authorization": "Bearer test"}
    response = client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert not response.json()["can_delete"]
    for params in ({"limit": 101}, {"offset": -1}, {"kind": "unknown"}):
        assert client.get(path, headers=headers, params=params).status_code == 422
    assert client.get(path.replace(args["robot_id"], "missing"), headers=headers).status_code == 404
    assert (
        client.get(
            path.replace(args["organization_id"], "foreign-org"), headers=headers
        ).status_code
        == 403
    )
    current["value"] = replace(args["auth"], organization_scoped_capabilities=frozenset())
    assert client.get(path, headers=headers).status_code == 403
    current["value"] = None
    assert client.get(path).status_code == 401


def test_published_version_is_identified_and_retention_is_explicit(robot):  # noqa: F811
    import psycopg

    dsn, org, rid, repo = robot
    with psycopg.connect(dsn) as db:
        publish(db, org, rid, "scheme")
    service = OrganizationRobotAssetService(repo)
    args = dict(auth=actor(org, "p", "region", read=False), organization_id=org, robot_id=rid)
    report = service.deletion_check(**args)
    assert not report.can_delete and report.items[0].scheme_id == "scheme"
    assert report.items[0].version == 1 and report.items[0].project_id is None
    assert "不可删除" in report.blockers[0].resolution
    with pytest.raises(ProblemException):
        service.delete_robot(**args, request_id="blocked")


def test_unused_robot_can_delete_after_a_read_only_preview(robot):  # noqa: F811
    _, org, rid, repo = robot
    service = OrganizationRobotAssetService(repo)
    args = dict(auth=actor(org, "p", "region"), organization_id=org, robot_id=rid)
    report = service.deletion_check(**args)
    assert report.can_delete and not report.blockers and not report.items
    service.delete_robot(**args, request_id="delete-empty")
    with pytest.raises(ProblemException):
        service.deletion_check(**args)


def test_component_calibration_and_source_references_are_named_and_scoped(robot):  # noqa: F811
    import json

    import psycopg

    dsn, org, rid, repo = robot
    with psycopg.connect(dsn) as db:
        db.execute(
            """INSERT INTO robotics.robot_asset_components
            (organization_id,component_id,robot_id,component_model_id,component_type,
             display_name,serial_no,lifecycle_status,sort_order)
            VALUES(%s,'camera',%s,'model','CAMERA','标定相机','serial','ACTIVE',0)""",
            (org, rid),
        )
        db.execute(
            """INSERT INTO calibrations.calibration_sets
            (organization_id,project_id,region_code,set_id,robot_instance_id,version,
             snapshot_status,etag)
            VALUES(%s,'p','region','calibration-set',%s,3,'PUBLISHED','1')""",
            (org, rid),
        )
        document = {
            "id": "source",
            "source_type": "ROBOT",
            "administrative_state": "DISABLED",
            "binding": {"kind": "ROBOT", "robot_id": rid},
            "configuration": {"kind": "ROBOT"},
        }
        db.execute(
            """INSERT INTO ingest.data_sources
            (organization_id,project_id,region_code,source_id,name,source_type,source_format,
             administrative_state,connectivity_state,credential_state,version,source_document,
             created_at,updated_at) VALUES(%s,'p','region','source','旧机器人数据源','ROBOT','MCAP',
             'DISABLED','OFFLINE','MISSING',1,%s::jsonb,now(),now())""",
            (org, json.dumps(document)),
        )
    service = OrganizationRobotAssetService(repo)
    auth = actor(org, "p", "region")
    auth = replace(
        auth,
        organization_scoped_capabilities=auth.organization_scoped_capabilities
        | frozenset({(org, "p", "calibration.read"), (org, "p", "ingest_source.read")}),
    )
    args = dict(auth=auth, organization_id=org, robot_id=rid)
    report = service.deletion_check(**args)
    assert {r.kind for r in report.items} == {"component", "calibration", "source"}
    assert {r.name for r in report.items} == {"标定相机", "calibration-set", "旧机器人数据源"}
    assert all(r.collection_task_id is None for r in report.items)
    assert report.restricted_count == 0 and not report.can_delete
    with pytest.raises(ProblemException):
        service.delete_robot(**args, request_id="preserve-linked-assets")


def test_production_deletion_check_rejects_anonymous_before_query_validation():
    from hc_data_platform.core.app import create_app
    from tests.security.test_public_api_security_gate import _settings

    with TestClient(create_app(settings=_settings())) as client:
        response = client.get(
            "/api/v1/organizations/other/robots/unknown/deletion-check?limit=invalid"
        )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
