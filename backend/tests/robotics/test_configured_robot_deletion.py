"""Physical removal of unused instances; retained historical data stays immutable."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event
from uuid import uuid4

import psycopg
import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.registry.binding_queries import published_model_for_binding
from hc_data_platform.robot_assets.repository import PostgresOrganizationRobotAssetRepository
from hc_data_platform.robot_processing import service
from tests.robot_processing.test_configuration import draft, publish

pytestmark = pytest.mark.integration


@pytest.fixture
def robot():
    dsn = os.getenv("HC_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL required")
    org, rid = "delete-test-" + uuid4().hex, "robot"
    with psycopg.connect(dsn) as db:
        db.execute(
            "INSERT INTO registry.organization_projects VALUES(%s,'p','Delete tests')", (org,)
        )
        db.execute(
            """INSERT INTO robotics.robot_assets
            (organization_id,robot_id,display_name,serial_no,lifecycle_status,
             connectivity_state,etag,topology_revision)
            VALUES(%s,%s,'Disposable robot','serial','ACTIVE','OFFLINE','1','1')""",
            (org, rid),
        )
        service.save(db, org, rid, "scheme", draft(), "tester")
    repo = PostgresOrganizationRobotAssetRepository(
        lambda: psycopg.connect(dsn), published_models=published_model_for_binding
    )
    return dsn, org, rid, repo


def delete(robot):
    _, org, rid, repo = robot
    return repo.delete_robot(
        organization_id=org,
        robot_id=rid,
        actor_id="tester",
        request_id="delete-test",
        occurred_at=datetime.now(timezone.utc),
    )


def test_deletes_draft_live_and_deleted_identity_remnants_and_project_assignment(robot):
    dsn, org, rid, _ = robot
    ids = ["identity-" + uuid4().hex for _ in range(2)]
    with psycopg.connect(dsn) as db:
        for i, ident in enumerate(ids):
            db.execute(
                """INSERT INTO ingest.robot_ingest_identities
                (organization_id,ingest_identity_id,robot_id,state,identity_document,
                 created_at,updated_at,deleted_at)
                VALUES(%s,%s,%s,%s,'{}',now(),now(),CASE WHEN %s THEN now() END)""",
                (org, ident, rid, "DISABLED" if i else "ENABLED", bool(i)),
            )
            db.execute(
                """INSERT INTO ingest.robot_ingest_credentials
                (credential_id,organization_id,ingest_identity_id,credential_version,
                 token_prefix,token_digest,state,issued_at)
                VALUES(%s,%s,%s,1,%s,%s,'ACTIVE',now())""",
                ("cred-" + uuid4().hex, org, ident, "hcri_" + "a" * 32 + ".ABCDEFGH", "b" * 64),
            )
            db.execute(
                "INSERT INTO ingest.robot_connection_locals VALUES(%s,%s,'{}')", (org, ident)
            )
            db.execute(
                "INSERT INTO ingest.robot_credential_requests VALUES(%s,%s,'key',now())",
                (org, ident),
            )
        db.execute(
            """INSERT INTO robotics.project_robot_assignments
            (organization_id,project_id,region_code,robot_id,assigned_by)
            VALUES(%s,'p','region',%s,'tester')""",
            (org, rid),
        )
        db.execute(
            "INSERT INTO ingest.robot_connection_tickets VALUES(%s,%s,%s)",
            (uuid4().hex + uuid4().hex, org, json.dumps({"robot_id": rid})),
        )
    assert delete(robot)
    assert not delete(robot)
    with psycopg.connect(dsn) as db:
        for table in (
            "robotics.robot_assets",
            "ingest.robot_processing_schemes",
            "ingest.robot_ingest_identities",
            "ingest.robot_ingest_credentials",
            "ingest.robot_connection_locals",
            "ingest.robot_credential_requests",
            "ingest.robot_connection_tickets",
            "robotics.project_robot_assignments",
        ):
            assert (
                db.execute(
                    f"SELECT count(*) FROM {table} WHERE organization_id=%s", (org,)
                ).fetchone()[0]
                == 0
            )
        assert db.execute(
            """SELECT document->'last_draft' FROM ingest.robot_processing_audit
            WHERE organization_id=%s AND action='ROBOT_DELETED'""",
            (org,),
        ).fetchone()[0] == draft().model_dump(mode="json", exclude={"expected_revision"})
        assert (
            db.execute(
                "SELECT count(*) FROM robotics.robot_asset_audit_events "
                "WHERE organization_id=%s AND action='robot.asset.deleted'",
                (org,),
            ).fetchone()[0]
            == 1
        )
        with pytest.raises(ProblemException, match="ROBOT_NOT_FOUND"):
            service.save(db, org, rid, "scheme", draft(), "tester")


def test_published_version_blocks_delete_without_mutation_and_with_specific_reason(robot):
    dsn, org, rid, _ = robot
    with psycopg.connect(dsn) as db:
        before = publish(db, org, rid, "scheme")
    with pytest.raises(ValueError, match="已发布采集配置 1 条"):
        delete(robot)
    with psycopg.connect(dsn) as db:
        assert service.read(db, org, rid) == before


def test_delete_never_touches_same_robot_id_in_another_organization(robot):
    dsn, org, rid, repo = robot
    assert not repo.delete_robot(
        organization_id=org + "-other",
        robot_id=rid,
        actor_id="tester",
        request_id="foreign-delete",
        occurred_at=datetime.now(timezone.utc),
    )
    with psycopg.connect(dsn) as db:
        assert service.read(db, org, rid)["schemes"]
    assert delete(robot)


def test_publication_and_deletion_serialize_on_the_same_robot_lock(robot):
    dsn, org, rid, _ = robot
    started = Event()

    def deleting():
        started.set()
        return delete(robot)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with psycopg.connect(dsn) as db:
            service.lock(db, org, rid)
            future = pool.submit(deleting)
            assert started.wait(3)
            publish(db, org, rid, "scheme")
        with pytest.raises(ValueError, match="已发布采集配置 1 条"):
            future.result(timeout=5)
    with psycopg.connect(dsn) as db:
        assert len(service.read(db, org, rid)["versions"]) == 1
