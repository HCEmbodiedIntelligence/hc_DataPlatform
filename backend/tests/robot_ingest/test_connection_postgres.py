"""Connection claims survive process reconstruction in a disposable, migrated DB."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.object_storage.memory import InMemoryObjectStorage
from hc_data_platform.robot_ingest.connections import LocalRobot, RedeemConnection
from hc_data_platform.robot_ingest.models import CreateRobotIngestIdentity, IssueCredentialCommand
from hc_data_platform.robot_ingest.repository import PostgresRobotIngestRepository
from hc_data_platform.robot_ingest.service import RobotIngestService
from hc_data_platform.security.auth import AuthContext

DSN = os.environ.get("ROBOT_INGEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="disposable ROBOT_INGEST_POSTGRES_DSN required")


def test_durable_connection_claim_concurrent_retry_task_projection_and_old_credential():
    import psycopg

    key = uuid4().hex[:8]
    org, robot, model, version = [f"{name}-{key}" for name in ("org", "robot", "model", "version")]
    projects = [f"project-{key}-{letter}" for letter in "AB"]
    tasks = [f"task-{key}-{letter}" for letter in "AB"]
    with psycopg.connect(DSN) as db:
        db.execute("SELECT set_config('app.platform_admin','true',false)")
        for project, task in zip(projects, tasks, strict=True):
            db.execute(
                "INSERT INTO registry.organization_projects "
                "(organization_id,project_id,display_name) "
                "VALUES(%s,%s,%s)",
                (org, project, "Project " + project[-1]),
            )
            dataset = "dataset_" + project.replace("-", "_")
            document = {
                "dataset_id": dataset,
                "scope": {
                    "organization_id": org,
                    "project_id": project,
                    "region_code": "test-region",
                },
            }
            db.execute(
                """INSERT INTO dataset_registry.datasets
                (organization_id,project_id,region_code,dataset_id,name,description,availability,
                 owner_id,owner_display_name,asset_state,storage_class,version,dataset_document,created_at,updated_at,activity_at)
                VALUES(%s,%s,'test-region',%s,%s,'','ACTIVE','test','Test','READY','STANDARD',1,%s::jsonb,now(),now(),now())""",
                (org, project, dataset, task, json.dumps(document)),
            )
            db.execute(
                """INSERT INTO collection_tasks.collection_tasks
                (collection_task_id,organization_id,project_id,dataset_id,task_code,name,task_type,scenario,status,create_fingerprint,upload_region_code)
                VALUES(%s,%s,%s,%s,lpad(nextval('collection_tasks.task_code_sequence')::text,8,'0'),%s,'ROBOT_CAPTURE','test','ACTIVE',%s,'test-region')""",
                (task, org, project, dataset, "Pick " + project[-1], "a" * 64),
            )
        db.execute(
            """INSERT INTO registry.robot_models
            (organization_id,model_id,manufacturer,model_code,display_name)
            VALUES(%s,%s,'test',%s,'Test model')""",
            (org, model, model),
        )
        db.execute(
            """INSERT INTO registry.robot_model_versions
            (organization_id,version_id,robot_model_id,version_label,lifecycle,asset_availability,publish_readiness,etag)
            VALUES(%s,%s,%s,'v1','PUBLISHED','AVAILABLE','READY','test')""",
            (org, version, model),
        )
        db.execute(
            """INSERT INTO robotics.robot_assets
            (organization_id,robot_id,display_name,serial_no,lifecycle_status,connectivity_state,etag,topology_revision,robot_model_version_id,binding_id,binding_valid_from,binding_etag)
            VALUES(%s,%s,'Test robot',%s,'ACTIVE','OFFLINE','test','test',
                   %s,gen_random_uuid(),now(),'test')""",
            (org, robot, key, version),
        )
    auth = AuthContext(
        subject_id="test-admin",
        organization_ids=frozenset({org}),
        project_ids=frozenset(projects),
        region_codes=frozenset({"test-region"}),
        organization_scope_triples=frozenset((org, p, "test-region") for p in projects),
        organization_scoped_capabilities=frozenset(
            (org, p, c) for p in projects for c in ("ingest_source.manage", "ingest_source.read")
        ),
    )

    def service():
        repo = PostgresRobotIngestRepository.from_dsn(DSN, psycopg_connection_factory(DSN))
        return RobotIngestService(
            repo, InMemoryObjectStorage(), credential_hmac_key="test-connection-postgres-key"
        )

    s = service()
    identity = s.create_identity(
        auth=auth,
        organization_id=org,
        project_id=projects[0],
        command=CreateRobotIngestIdentity(robot_id=robot, allowed_formats=("MCAP",)),
    ).data
    old = s.issue_credential(
        auth=auth,
        organization_id=org,
        project_id=projects[0],
        ingest_identity_id=identity.ingest_identity_id,
        command=IssueCredentialCommand(),
    ).credential
    ticket = s.connections.issue(auth, org, projects[0], identity.ingest_identity_id)
    cmd = RedeemConnection(
        ticket=ticket["ticket"],
        request_id=uuid4().hex,
        local_robot=LocalRobot(
            local_robot_id="local", profile_id="actual-profile", config_fingerprint="b" * 64
        ),
    )
    with ThreadPoolExecutor(max_workers=6) as workers:
        results = list(workers.map(lambda _: service().connections.redeem(cmd), range(10)))
    assert len({r["token"] for r in results}) == 1
    assert len(s.repository.list_credentials(org, identity.ingest_identity_id)) == 2
    assert s.authenticate(old.token).authenticated_robot_id == robot
    machine = s.authenticate(results[0]["token"])
    page = s.connections.tasks(machine, "MCAP", page_size=1)
    assert page["total"] == 2 and page["items"][0]["project_name"] == "Project A"
    assert (
        s.connections.tasks(machine, "MCAP", q="Pick B")["items"][0]["collection_task_id"]
        == tasks[1]
    )
    assert s.connections.tasks(machine, "MCAP", project_id="other-project")["items"] == []
    with pytest.raises(ProblemException):
        s.connections.task(machine, "foreign-task", "MCAP")
    with psycopg.connect(DSN) as db:
        db.execute("SELECT set_config('app.platform_admin','true',false)")
        db.execute(
            "UPDATE collection_tasks.collection_tasks SET status='CLOSED' "
            "WHERE collection_task_id=%s",
            (tasks[0],),
        )
        documents = db.execute(
            "SELECT document::text FROM ingest.robot_connection_tickets WHERE organization_id=%s",
            (org,),
        ).fetchall()
    assert s.connections.tasks(machine, "MCAP")["total"] == 1
    assert ticket["ticket"] not in str(documents) and results[0]["token"] not in str(documents)
    with pytest.raises(ProblemException):
        s.connections.task(machine, tasks[0], "MCAP")
