"""C-only PostgreSQL/MinIO/Temporal tests.

The real-source test deliberately reports B unavailable, never simulated READY.
The separately named orchestration test uses receipt doubles to test retries/QC.
"""

from __future__ import annotations

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from temporalio.client import Client
from temporalio.testing import ActivityEnvironment
from temporalio.worker import Worker

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.ingest.adapters import S3ObjectStorage
from hc_data_platform.robot_ingest import processing_api, processing_store
from hc_data_platform.robot_ingest.models import CreateRobotIngestIdentity, IssueCredentialCommand
from hc_data_platform.robot_ingest.processing_contract import (
    EpisodeReceipt,
    ProcessingFailure,
    SourceEpisode,
)
from hc_data_platform.robot_ingest.processing_store import ProcessingStore
from hc_data_platform.robot_ingest.processing_worker import (
    RobotProcessingActivities,
    RobotProcessingOutboxHandler,
)
from hc_data_platform.robot_ingest.processing_workflow import RobotIngestProcessingWorkflow
from hc_data_platform.robot_ingest.repository import PostgresRobotIngestRepository
from hc_data_platform.robot_ingest.router import configure_robot_ingest, router
from hc_data_platform.robot_ingest.service import RobotIngestService
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.outbox import OutboxDispatcher, PostgresOutboxDeliveryRepository
from hc_data_platform.workflow.outbox_worker import serve_outbox

DSN = os.environ.get("ROBOT_INGEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="C isolated infrastructure required")
G0 = Path(os.environ.get("OPENARM_G0", "/g0"))
ORG, PROJECT_A, REGION_A = "robot-c-org", "robot-c-project", "robot-c-region"
ROBOT, TASK_A = "robot-c-synthetic", "robot-c-task"


def _admin():
    return AuthContext(
        subject_id="robot-c-test",
        organization_ids=frozenset({ORG}),
        project_ids=frozenset({PROJECT_A}),
        region_codes=frozenset({REGION_A}),
        scope_pairs=frozenset({(PROJECT_A, REGION_A)}),
        organization_scope_triples=frozenset({(ORG, PROJECT_A, REGION_A)}),
        organization_scoped_capabilities=frozenset(
            (ORG, PROJECT_A, cap) for cap in ("ingest_source.read", "ingest_source.manage")
        ),
    )


def _setup_database(dsn):
    import psycopg

    with psycopg.connect(dsn) as connection:
        connection.execute("SELECT set_config('app.platform_admin','true',false)")
        connection.execute(
            """INSERT INTO registry.organization_projects
            (organization_id,project_id,display_name) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
            (ORG, PROJECT_A, PROJECT_A),
        )
        connection.execute(
            """INSERT INTO robotics.robot_assets
            (organization_id,robot_id,display_name,serial_no,lifecycle_status,
             connectivity_state,etag,topology_revision)
            VALUES (%s,%s,%s,%s,'ACTIVE','ONLINE','test','test') ON CONFLICT DO NOTHING""",
            (ORG, ROBOT, ROBOT, ROBOT),
        )
        connection.execute(
            """INSERT INTO collection_tasks.collection_tasks
            (collection_task_id,organization_id,project_id,dataset_id,task_code,name,
             task_type,scenario,status,create_fingerprint,upload_region_code)
            VALUES (%s,%s,%s,'dataset_robot_c','92000001','C synthetic',
                    'ROBOT_CAPTURE','synthetic','ACTIVE',%s,%s) ON CONFLICT DO NOTHING""",
            (TASK_A, ORG, PROJECT_A, "a" * 64, REGION_A),
        )


@pytest.fixture
def system():
    assert DSN
    _setup_database(DSN)
    scope = bind_request_context(
        RequestContext(
            organization_id=ORG,
            project_id=PROJECT_A,
            region_code=REGION_A,
            service_identity=True,
        )
    )
    connections = psycopg_connection_factory(DSN)
    storage = S3ObjectStorage.from_boto3(
        "hc-data-local",
        endpoint_url="http://minio:9000",
        aws_access_key_id="minio",
        aws_secret_access_key="minio-local-only",
        region_name="us-east-1",
    )
    service = RobotIngestService(
        PostgresRobotIngestRepository.from_dsn(DSN, connections),
        storage,
        credential_hmac_key="robot-processing-test-key",
    )
    identity = service.create_identity(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        command=CreateRobotIngestIdentity(
            robot_id=ROBOT, allowed_formats=("LEROBOT_V3",), allowed_transports=("HTTP", "HTTPS")
        ),
    ).data
    # Existing regression identity may already exist with a different format policy.
    from hc_data_platform.robot_ingest.models import UpdateRobotIngestIdentity

    service.update_identity(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=identity.ingest_identity_id,
        command=UpdateRobotIngestIdentity(
            allowed_formats=("LEROBOT_V3",),
            allowed_transports=("HTTP", "HTTPS"),
            upload_policy=identity.upload_policy,
        ),
    )
    credential = service.issue_credential(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=identity.ingest_identity_id,
        command=IssueCredentialCommand(),
    ).credential
    store = ProcessingStore(connections)
    configure_robot_ingest(service)
    processing_api.configure_processing(store)
    app = FastAPI()

    @app.exception_handler(ProblemException)
    async def error(_request: Request, exc: ProblemException):
        return JSONResponse(exc.problem.model_dump(mode="json"), status_code=exc.problem.status)

    app.include_router(router)
    app.include_router(processing_api.router)
    with TestClient(app) as api:
        yield service, store, storage, api, {"Authorization": f"Bearer {credential.token}"}
    reset_request_context(scope)


def uploaded(system):
    _, _, _, api, headers = system
    manifest = json.loads((G0 / "fixtures/robot-ingest-manifest.json").read_text())
    manifest.update(client_upload_id=str(uuid4()), collection_task_id=TASK_A, robot_id=ROBOT)
    response = api.post("/api/v1/robot-ingest/uploads", json=manifest, headers=headers)
    assert response.status_code == 201, response.text
    upload_id = response.json()["data"]["upload_id"]
    root = f"/api/v1/robot-ingest/uploads/{upload_id}"
    for asset in manifest["assets"]:
        asset_root = f"{root}/assets/{asset['asset_id']}"
        grant = api.post(
            asset_root + ":authorize-parts", json={"part_numbers": [1]}, headers=headers
        )
        assert grant.status_code == 200, grant.text
        url = grant.json()["authorizations"][0]["url"]
        body = (G0 / "fixtures/valid-openarm" / asset["path"]).read_bytes()
        put = httpx.put(url, content=body, timeout=20, trust_env=False)
        assert put.status_code == 200
        complete = api.post(
            asset_root + ":complete",
            headers=headers,
            json={"parts": [{"part_number": 1, "etag": put.headers["etag"]}]},
        )
        assert complete.status_code == 200, complete.text
    return upload_id, root


def commit(system):
    upload_id, root = uploaded(system)
    result = system[3].post(root + ":commit", headers=system[4])
    assert result.status_code == 200, result.text
    token = system[4]["Authorization"].split()[1]
    upload = system[0].get_upload(token=token, upload_id=upload_id).data
    return upload, root


def evidence(name, data):
    print("C_EVIDENCE " + json.dumps({"test": name, **data}, sort_keys=True))
    if os.environ.get("C_EVIDENCE_DIR"):
        (Path(os.environ["C_EVIDENCE_DIR"]) / (name + ".json")).write_text(
            json.dumps(data, indent=2) + "\n"
        )


def assert_g0_result_contract(result):
    # Compare wire keys/enums against E's frozen schema, without maintaining a C copy.
    schema = json.loads((G0 / "schemas/processing-result-proposed.schema.json").read_text())
    assert set(result) == set(schema["required"]) == set(schema["properties"])
    assert result["schema_version"] == schema["properties"]["schema_version"]["const"]
    for key in ("processing_status", "quality_status"):
        assert result[key] in schema["properties"][key]["enum"]
    episode_schema = schema["properties"]["episodes"]["items"]
    for ep in result["episodes"]:
        assert set(ep) == set(episode_schema["required"]) == set(episode_schema["properties"])
        for key in ("status", "quality_status", "next_action"):
            assert ep[key] in episode_schema["properties"][key]["enum"]
        assert ep["source_episode_id"] and ep["source_episode_index"] >= 0


@pytest.mark.asyncio
async def test_real_raw_outbox_temporal_missing_b_and_recovery(system, monkeypatch):
    service, store, storage, api, headers = system
    upload_id, root = uploaded(system)
    assert api.get(root + "/processing", headers=headers).status_code == 409
    original = processing_store.enqueue

    def fail_enqueue(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected failure before commit")

    with monkeypatch.context() as patch:
        patch.setattr(processing_store, "enqueue", fail_enqueue)
        with pytest.raises(RuntimeError, match="injected failure"):
            api.post(root + ":commit", headers=headers)
    with store.connections() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM core.outbox_events WHERE envelope#>>'{payload,upload_id}'=%s",
                (upload_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM ingest.raw_sources WHERE upload_id=%s", (upload_id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM ingest.robot_processing WHERE upload_id=%s", (upload_id,)
            ).fetchone()[0]
            == 0
        )
    assert api.get(root, headers=headers).json()["data"]["state"] == "READY_TO_COMMIT"
    # Response loss: ignore the successful response, then reconcile GET and retry commit.
    api.post(root + ":commit", headers=headers)
    token = headers["Authorization"].split()[1]
    upload = service.get_upload(token=token, upload_id=upload_id).data
    task, doc = store.read(upload)
    assert task and doc.phase == "PENDING"
    assert api.post(root + ":commit", headers=headers).json()["resumed"]
    client = await Client.connect("temporal:7233")
    queue = "robot-c-test-" + uuid4().hex
    outbox = PostgresOutboxDeliveryRepository(store.connections)
    handler = RobotProcessingOutboxHandler(client, store, task_queue=queue)
    with store.connections() as connection:
        row = connection.execute(
            "SELECT envelope FROM core.outbox_events WHERE event_id=%s",
            (str(uuid5(NAMESPACE_URL, task.workflow_id)),),
        ).fetchone()
    from hc_data_platform.core.events import DomainEventEnvelope

    event = DomainEventEnvelope.model_validate(row[0])
    # Claim survives dispatcher death. Start Temporal but deliberately omit acknowledgement.
    now = datetime.now(timezone.utc)
    # Use the real dispatcher for recovery; isolate this test's events from prior runs.
    with store.connections() as connection:
        connection.execute(
            """UPDATE core.outbox_events SET available_at=clock_timestamp()+interval '1 day'
            WHERE event_type=%s AND envelope->>'aggregate_id'<>%s AND published_at IS NULL""",
            (processing_store.EVENT_TYPE, task.raw_source_id),
        )
    claim = outbox.claim_next(
        organization_id=ORG,
        project_id=PROJECT_A,
        region_code=REGION_A,
        worker_id="before-restart",
        now=now,
        claimed_until=now + timedelta(milliseconds=1),
    )
    assert claim and claim.event.aggregate_id == task.raw_source_id
    await handler(claim.event)
    await asyncio.sleep(0.02)
    dispatcher = OutboxDispatcher(
        outbox, {processing_store.EVENT_TYPE: handler}, worker_id="after-restart"
    )
    assert await dispatcher.dispatch_one(
        organization_id=ORG, project_id=PROJECT_A, region_code=REGION_A
    )
    # Worker was offline at commit/start. A new real Worker consumes the persisted work.
    # Stop the first workflow Worker after it schedules discovery but before any
    # activity Worker exists. The replacement must replay durable Temporal history.
    async with Worker(client, task_queue=queue, workflows=[RobotIngestProcessingWorkflow]):
        for _ in range(100):
            history = await client.get_workflow_handle(task.workflow_id).fetch_history()
            if any(
                event.HasField("activity_task_scheduled_event_attributes")
                for event in history.events
            ):
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("workflow did not durably schedule discovery")
    activities = RobotProcessingActivities(store, storage)
    with ThreadPoolExecutor(max_workers=4) as executor:
        async with Worker(
            client,
            task_queue=queue,
            workflows=[RobotIngestProcessingWorkflow],
            activities=activities.activities,
            activity_executor=executor,
        ):
            await asyncio.wait_for(client.get_workflow_handle(task.workflow_id).result(), 45)
    result = api.get(root + "/processing", headers=headers)
    assert result.status_code == 200, result.text
    assert_g0_result_contract(result.json())
    assert result.json()["processing_status"] == "FAILED"
    assert result.json()["terminal"] and result.json()["poll_after_seconds"] == 0
    assert result.json()["episodes"] == []  # No fabricated source mapping or success.
    diagnostics = api.get(root + "/processing/diagnostics", headers=headers).json()
    assert diagnostics["error_code"] == "ROBOT_PROCESSOR_UNAVAILABLE" and diagnostics["retryable"]
    with store.connections() as connection:
        assert connection.execute(
            "SELECT status,last_error_code FROM ingest.raw_ingest_jobs WHERE raw_source_id=%s",
            (task.raw_source_id,),
        ).fetchone() == ("FAILED", "ROBOT_PROCESSOR_UNAVAILABLE")
    # Explicit retry survives lost response; the same request id never schedules twice.
    command = {"request_id": str(uuid4())}
    assert api.post(root + ":retry-processing", json=command, headers=headers).status_code == 200
    assert api.post(root + ":retry-processing", json=command, headers=headers).status_code == 200
    next_task, doc = store.read(upload)
    assert next_task.generation == 1 and next_task.workflow_id != task.workflow_id
    assert doc.phase == "PENDING"
    with pytest.raises(ProcessingFailure, match="STALE_ATTEMPT"):
        store.change(task, lambda d: d)
    # The normal continuous outbox loop dispatches the retry automatically.
    dispatch_loop = asyncio.create_task(
        serve_outbox(
            dispatcher,
            scopes=(f"{ORG}/{PROJECT_A}/{REGION_A}",),
            poll_interval_seconds=0.05,
            batch_size=1,
        )
    )
    for _ in range(100):
        with store.connections() as connection:
            published = connection.execute(
                "SELECT published_at FROM core.outbox_events WHERE event_id=%s",
                (str(uuid5(NAMESPACE_URL, next_task.workflow_id)),),
            ).fetchone()[0]
        if published:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("automatic outbox dispatch did not acknowledge retry")
    with ThreadPoolExecutor(max_workers=4) as executor:
        async with Worker(
            client,
            task_queue=queue,
            workflows=[RobotIngestProcessingWorkflow],
            activities=RobotProcessingActivities(store, storage).activities,
            activity_executor=executor,
        ):
            await asyncio.wait_for(client.get_workflow_handle(next_task.workflow_id).result(), 45)
    dispatch_loop.cancel()
    with suppress(asyncio.CancelledError):
        await dispatch_loop
    await handler(event)  # obsolete delivery is harmless after an explicit retry
    with store.connections() as connection:
        counts = [
            connection.execute(
                f"SELECT count(*) FROM {table} WHERE raw_source_id=%s", (task.raw_source_id,)
            ).fetchone()[0]
            for table in (
                "ingest.raw_sources",
                "ingest.raw_ingest_jobs",
                "ingest.raw_source_episodes",
            )
        ]
    assert counts == [1, 1, 0]
    evidence(
        "real-infrastructure",
        dict(
            upload_id=upload_id,
            raw_source_id=task.raw_source_id,
            workflow_ids=[task.workflow_id, next_task.workflow_id],
            counts=counts,
            result=api.get(root + "/processing", headers=headers).json(),
            diagnostics=diagnostics,
            assertions=[
                "commit rollback",
                "response-loss reconciliation",
                "outbox lease recovery",
                "duplicate Temporal start",
                "worker offline at commit",
                "Worker restart after activity scheduled; Temporal history replay",
                "automatic serve_outbox retry dispatch",
                "new Worker consumes retry",
                "stale generation rejected",
                "real S3 byte read",
                "B unavailable persisted",
            ],
            openarm_success_verified=False,
        ),
    )


def test_robot_processing_identity_isolation(system):
    service, store, _, api, headers = system
    upload, root = commit(system)
    assert api.get(root + "/processing", headers=headers).status_code == 200
    other_robot = "robot-other-" + uuid4().hex
    with store.connections() as connection:
        connection.execute(
            """INSERT INTO robotics.robot_assets
        (organization_id,robot_id,display_name,serial_no,lifecycle_status,
         connectivity_state,etag,topology_revision)
        VALUES (%s,%s,%s,%s,'ACTIVE','ONLINE','test','test')""",
            (ORG, other_robot, other_robot, other_robot),
        )
    identity = service.create_identity(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        command=CreateRobotIngestIdentity(robot_id=other_robot, allowed_formats=("LEROBOT_V3",)),
    ).data
    credential = service.issue_credential(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=identity.ingest_identity_id,
        command=IssueCredentialCommand(),
    ).credential
    foreign = {"Authorization": f"Bearer {credential.token}"}
    for suffix in ("/processing", "/processing/diagnostics"):
        assert api.get(root + suffix, headers=foreign).status_code == 404
        assert (
            api.get(root + suffix, headers={"Authorization": "Bearer user-session"}).status_code
            == 401
        )
    assert (
        api.post(
            root + ":retry-processing", headers=foreign, json={"request_id": str(uuid4())}
        ).status_code
        == 404
    )
    assert store.read(upload)[0].generation == 0


class ReceiptDouble:
    """Only for state-machine tests: explicitly NOT a B adapter or data-pipeline proof."""

    def __init__(self, quality="PASS"):
        self.calls = []
        self.fail_second = True
        self.quality = quality

    def discover(self, source):
        source.read("meta/info.json")
        return tuple(
            SourceEpisode(source_episode_id=f"source-{i}", source_episode_index=i) for i in range(2)
        )

    def process(self, source, episode, *, episode_id, attempt_id):
        self.calls.append((episode.source_episode_index, episode_id, attempt_id))
        if episode.source_episode_index == 1 and self.fail_second:
            raise ProcessingFailure("TEST_TRANSIENT_FAILURE", retryable=True)
        return EpisodeReceipt(
            quality_status=self.quality,
            frame_count=6,
            sample_count=6,
            dataset_version=1 if self.quality == "PASS" else None,
            lance_version=1 if self.quality == "PASS" else None,
            qc_report_id="test-qc",
        )


@pytest.mark.parametrize("quality", ["PASS", "RISK", "REJECT"])
def test_orchestration_receipt_double_partial_retry_atomicity(system, quality, monkeypatch):
    _, store, storage, api, headers = system
    upload, root = commit(system)
    task, _ = store.read(upload)
    processor = ReceiptDouble(quality)
    activities = RobotProcessingActivities(store, storage, processor)
    env = ActivityEnvironment()
    assert env.run(activities.discover, {"task": task.model_dump()}) == [0, 1]
    env.run(activities.episode, {"task": task.model_dump(), "index": 0})
    env.run(
        activities.fail,
        {
            "task": task.model_dump(),
            "index": 1,
            "error_code": "TEST_TRANSIENT_FAILURE",
            "retryable": True,
        },
    )
    env.run(activities.finish, {"task": task.model_dump()})
    result = api.get(root + "/processing", headers=headers).json()
    assert result["processing_status"] == "PARTIALLY_FAILED" and result["terminal"]
    assert result["episodes"][0]["status"] == "READY"
    # A projection failure after all SQL writes rolls back every projection.
    write = store._write
    with monkeypatch.context() as patch:

        def fail_after_write(*args):
            write(*args)
            raise RuntimeError("projection rollback")

        patch.setattr(store, "_write", fail_after_write)
        with pytest.raises(RuntimeError):
            store.retry(upload, str(uuid4()))
    assert store.read(upload)[0].generation == 0
    assert api.get(root + "/processing", headers=headers).json() == result
    command = {"request_id": str(uuid4())}
    assert api.post(root + ":retry-processing", json=command, headers=headers).status_code == 200
    retry_task, _ = store.read(upload)
    processor.fail_second = False
    assert env.run(activities.discover, {"task": retry_task.model_dump()}) == [1]
    env.run(activities.episode, {"task": retry_task.model_dump(), "index": 1})
    # Activity redelivery after durable receipt must skip the processor.
    env.run(activities.episode, {"task": retry_task.model_dump(), "index": 1})
    env.run(activities.finish, {"task": retry_task.model_dump()})
    assert [call[0] for call in processor.calls] == [0, 1]
    done = api.get(root + "/processing", headers=headers).json()
    assert_g0_result_contract(done)
    assert done["processing_status"] == "READY" and done["quality_status"] == quality
    assert done["episodes"][0]["episode_id"] == result["episodes"][0]["episode_id"]
    assert done["episodes"][1]["episode_id"] == result["episodes"][1]["episode_id"]
    assert api.post(root + ":retry-processing", json=command, headers=headers).status_code == 200
    assert (
        api.post(
            root + ":retry-processing", json={"request_id": str(uuid4())}, headers=headers
        ).status_code
        == 409
    )
    with store.connections() as connection:
        counts = connection.execute(
            """SELECT verified_episode_count,verified_frame_count,qc_pass_episode_count,
            qc_risk_episode_count,qc_reject_episode_count
            FROM ingest.raw_sources WHERE raw_source_id=%s""",
            (task.raw_source_id,),
        ).fetchone()
        assert counts == (
            2,
            12,
            2 if quality == "PASS" else 0,
            2 if quality == "RISK" else 0,
            2 if quality == "REJECT" else 0,
        )
    evidence(
        "orchestration-double-" + quality.lower(),
        dict(
            result=done,
            counts=counts,
            processor_calls=[c[0] for c in processor.calls],
            openarm_success_verified=False,
        ),
    )


def test_explicit_historical_recovery_and_concurrent_commit(system, monkeypatch):
    service, store, _, api, headers = system
    upload_id, root = uploaded(system)
    token = headers["Authorization"].split()[1]
    from threading import Barrier

    barrier = Barrier(4)
    head = service.storage.head

    def synchronized_head(key):
        result = head(key)
        if result is None and key.endswith("/robot-ingest-manifest.json"):
            barrier.wait(timeout=15)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(service.storage, "head", synchronized_head)
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(
                executor.map(
                    lambda _: service.commit_upload(token=token, upload_id=upload_id), range(4)
                )
            )
    assert len({r.data.raw_source_id for r in results}) == 1
    upload = service.get_upload(token=token, upload_id=upload_id).data
    task, _ = store.read(upload)
    with store.connections() as connection:
        assert (
            connection.execute(
                """SELECT count(*) FROM core.outbox_events
                WHERE envelope->>'aggregate_id'=%s AND event_type=%s""",
                (task.raw_source_id, processing_store.EVENT_TYPE),
            ).fetchone()[0]
            == 1
        )
        # Recreate the actual baseline state: committed source/job with no processing row/event.
        connection.execute(
            "DELETE FROM core.outbox_events WHERE envelope->>'aggregate_id'=%s",
            (task.raw_source_id,),
        )
        connection.execute(
            "DELETE FROM ingest.robot_processing WHERE raw_source_id=%s", (task.raw_source_id,)
        )
        connection.execute(
            "UPDATE ingest.raw_ingest_jobs SET workflow_id=NULL WHERE raw_source_id=%s",
            (task.raw_source_id,),
        )
    assert store.read(upload)[0] is None
    pending_recovery = api.get(root + "/processing", headers=headers)
    assert pending_recovery.status_code == 409
    assert pending_recovery.json()["code"] == "ROBOT_PROCESSING_NOT_SCHEDULED"
    assert (
        api.get(root + "/processing/diagnostics", headers=headers).json()["error_code"]
        == "ROBOT_PROCESSING_NOT_SCHEDULED"
    )
    command = {"request_id": str(uuid4())}
    assert api.post(root + ":retry-processing", json=command, headers=headers).status_code == 200
    assert api.post(root + ":retry-processing", json=command, headers=headers).status_code == 200
    recovered, _ = store.read(upload)
    assert recovered.workflow_id == task.workflow_id
    _assert_processing_rls(task.raw_source_id)
    with store.connections() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM core.outbox_events WHERE envelope->>'aggregate_id'=%s",
                (task.raw_source_id,),
            ).fetchone()[0]
            == 1
        )


def _assert_processing_rls(raw_source_id):
    import psycopg
    from psycopg import sql

    role = "robot_c_rls_" + uuid4().hex[:16]
    with psycopg.connect(DSN) as connection:
        connection.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(role)))
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA ingest TO {}").format(sql.Identifier(role))
        )
        connection.execute(
            sql.SQL(
                "GRANT SELECT ON ingest.robot_processing, ingest.robot_processing_requests TO {}"
            ).format(sql.Identifier(role))
        )
        connection.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
        for organization, project, region, expected in (
            (ORG, PROJECT_A, REGION_A, 1),
            ("other", PROJECT_A, REGION_A, 0),
            (ORG, "other", REGION_A, 0),
            (ORG, PROJECT_A, "other", 0),
        ):
            connection.execute(
                """SELECT set_config('app.organization_id',%s,true),
                set_config('app.project_id',%s,true),set_config('app.region_code',%s,true)""",
                (organization, project, region),
            )
            for table in ("robot_processing", "robot_processing_requests"):
                row = connection.execute(
                    sql.SQL("SELECT count(*) FROM ingest.{} WHERE raw_source_id=%s").format(
                        sql.Identifier(table)
                    ),
                    (raw_source_id,),
                ).fetchone()
                assert row[0] == expected
        connection.rollback()  # test role and temporary grants disappear together
