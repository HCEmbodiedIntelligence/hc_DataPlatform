from __future__ import annotations

import asyncio
from uuid import uuid4

import psycopg
import pytest

from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.ingest.raw_sources import PostgresRawSourceRepository
from hc_data_platform.lerobot_imports import processing, resolutions
from hc_data_platform.lerobot_imports.dispatch import LeRobotImportOutboxHandler
from hc_data_platform.lerobot_imports.pipeline import LeRobotPipeline
from hc_data_platform.lerobot_imports.resolutions import ResolveEpisode
from hc_data_platform.workflow.lerobot_workflow import NativeStateUpdate
from hc_data_platform.workflow.models import JobRecord, JobStatus
from tests.dashboard.test_dashboard_postgres import (
    NOW,
    isolated_dsn,  # noqa: F401
    organization_for,
    request_scope,
    seed_scope,
)


@pytest.fixture
def conflict(isolated_dsn, monkeypatch):  # noqa: F811
    prefix = f"decision-{uuid4().hex[:8]}"
    project, region = f"resolution-{prefix}", "cn-east"
    seed_scope(isolated_dsn, project, region, prefix)
    raw_id = uuid4().hex
    scope = (organization_for(project), project, region, raw_id)
    rollout = f"rollout-{prefix}-qc"
    connect = psycopg_connection_factory(isolated_dsn)
    monkeypatch.setattr(resolutions, "connections", lambda: connect)
    monkeypatch.setattr(processing, "connections", lambda: connect)
    with psycopg.connect(isolated_dsn) as connection:
        connection.execute(
            """INSERT INTO ingest.raw_sources
            (organization_id, project_id, region_code, raw_source_id, upload_id, dataset_id,
             collection_task_id, robot_id, source_format, source_format_version, manifest_key,
             storage_prefix, content_hash, file_count, total_bytes, raw_status, processing_status,
             created_at, committed_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,'dataset-native','task-decision-qc','robot-decision-qc',
            'LEROBOT_V3','3.0',%s,'raw/shared',%s,4,100,'COMMITTED',
            'PARTIALLY_FAILED',%s,%s,%s)""",
            (*scope, raw_id, f"raw/{raw_id}/manifest.json", "a" * 64, NOW, NOW, NOW),
        )
        for index, episode, status in [
            (0, "sibling-ready", "READY"),
            (1, rollout, "FAILED"),
            (2, "sibling-failed", "FAILED"),
        ]:
            connection.execute(
                """INSERT INTO ingest.raw_source_episodes
                (organization_id, project_id, region_code, raw_source_id, episode_id,
                 source_episode_index,status,dataset_version,lance_version,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    *scope,
                    episode,
                    index,
                    status,
                    1 if status == "READY" else None,
                    1 if status == "READY" else None,
                    NOW,
                    NOW,
                ),
            )
        connection.execute(
            """INSERT INTO ingest.raw_ingest_jobs
            (organization_id,project_id,region_code,raw_source_id,job_id,workflow_id,job_type,
             adapter_name,status,created_at,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,'LEROBOT_IMPORT','lerobot_v3','PARTIALLY_FAILED',%s,%s)""",
            (*scope, raw_id, f"import-{raw_id}", NOW, NOW),
        )
        connection.execute(
            """INSERT INTO aligned_fragment_attempts
            (organization_id,project_id,region_code,rollout_id,source_sha256,converter_version,
             attempt_id,content_sha256,schema_sha256,row_count,staging_uri,staging_format,status)
            VALUES (%s,%s,%s,%s,%s,'v1','original-attempt',%s,%s,2,'staging/old',
            'arrow-ipc/v1','READY')""",
            (*scope[:3], rollout, "a" * 64, "b" * 64, "c" * 64),
        )
        connection.execute(
            """INSERT INTO workflow.jobs
            (job_id,workflow_id,organization_id,project_id,resource_id,job_type,status,
             error_code,created_at,updated_at)
            VALUES (%s,%s,%s,%s,%s,'IngestRolloutWorkflow','TECHNICAL_FAILED',
            'ALIGNMENT_ATTEMPT_IMMUTABLE',%s,%s)""",
            (uuid4(), f"child-{raw_id}", *scope[:2], rollout, NOW, NOW),
        )
    with request_scope(project, region):
        yield scope, connect, rollout


@pytest.mark.integration
@pytest.mark.parametrize("action", ["REPROCESS", "DISCARD"])
def test_decision_is_scoped_idempotent_and_preserves_shared_raw(conflict, action):
    scope, connect, rollout = conflict
    command = ResolveEpisode(
        request_id=uuid4(), action=action, expected_attempt_id="original-attempt"
    )
    assert resolutions.get_resolution(*scope, 1).available
    with pytest.raises(ProblemException, match="EPISODE_NOT_FOUND"):
        resolutions.resolve_episode(*(*scope[:2], "other-region", scope[3]), 1, command, "actor")
    with pytest.raises(ProblemException, match="EPISODE_RESOLUTION_STALE"):
        resolutions.resolve_episode(
            *scope, 1, command.model_copy(update={"expected_attempt_id": "stale"}), "actor"
        )
    result = resolutions.resolve_episode(*scope, 1, command, "actor")
    assert result.status == ("PENDING" if action == "REPROCESS" else "DISCARDED")
    assert resolutions.resolve_episode(*scope, 1, command, "actor") == result
    with pytest.raises(ProblemException, match="EPISODE_RESOLUTION_UNAVAILABLE"):
        resolutions.resolve_episode(
            *scope, 1, command.model_copy(update={"request_id": uuid4()}), "actor"
        )
    with connect() as connection:
        rows = connection.execute(
            "SELECT episode_id,status FROM ingest.raw_source_episodes "
            "WHERE raw_source_id=%s ORDER BY source_episode_index",
            (scope[3],),
        ).fetchall()
        assert rows == [
            ("sibling-ready", "READY"),
            (rollout, result.status),
            ("sibling-failed", "FAILED"),
        ]
        assert connection.execute(
            "SELECT manifest_key,content_hash FROM ingest.raw_sources WHERE raw_source_id=%s",
            (scope[3],),
        ).fetchone() == (f"raw/{scope[3]}/manifest.json", "a" * 64)
        assert connection.execute(
            "SELECT actor_id,previous_attempt_id FROM ingest.episode_resolutions "
            "WHERE raw_source_id=%s",
            (scope[3],),
        ).fetchall() == [("actor", "original-attempt")]
        events = connection.execute(
            "SELECT envelope FROM core.outbox_events WHERE envelope->>'aggregate_id'=%s "
            "AND event_type='lerobot.import.requested.v1'",
            (scope[3],),
        ).fetchall()
    if action == "DISCARD":
        assert events == []
        processing.retry_processing(*scope)
        with connect() as connection:
            assert connection.execute(
                "SELECT status FROM ingest.raw_source_episodes "
                "WHERE raw_source_id=%s AND source_episode_index=1",
                (scope[3],),
            ).fetchone() == ("DISCARDED",)
        return
    assert len(events) == 1
    launched = []

    class Launcher:
        async def start(self, **kwargs):
            launched.append(kwargs)

    asyncio.run(
        LeRobotImportOutboxHandler(Launcher(), PostgresRawSourceRepository(connect))(
            DomainEventEnvelope.model_validate(events[0][0])
        )
    )
    workflow_input = launched[0]["workflow_input"]
    assert workflow_input.only_episode == 1
    task = workflow_input.task
    assert task.source.processing_attempt_id == str(command.request_id)
    pipeline = object.__new__(LeRobotPipeline)
    pipeline.connections = connect
    pipeline.source = lambda task: None
    pipeline.update_state(NativeStateUpdate(task=task, status="RUNNING"))
    assert resolutions.get_resolution(*scope, 1).status == "RUNNING"
    job = JobRecord(
        job_id=str(uuid4()),
        workflow_id="resolved-child",
        project_id=scope[1],
        resource_id=rollout,
        job_type="IngestRolloutWorkflow",
        status=JobStatus.SUCCEEDED,
        created_at=NOW,
        updated_at=NOW,
        result={"derived": {"step_count": 2, "dataset_version": 1, "lance_version": 1}},
    )
    pipeline.update_state(NativeStateUpdate(task=task, status="EPISODE_DONE", episode_job=job))
    pipeline.update_state(NativeStateUpdate(task=task, status="SUCCEEDED"))
    assert resolutions.get_resolution(*scope, 1).status == "SUCCEEDED"
    # The unrelated failed sibling keeps the aggregate partially failed.
    assert processing.get_progress(*scope).status == "PARTIALLY_FAILED"
    assert processing.get_progress(*scope).ready == 2


@pytest.mark.integration
def test_active_batch_and_ready_episode_cannot_be_discarded(conflict):
    scope, connect, _ = conflict
    command = ResolveEpisode(
        request_id=uuid4(), action="DISCARD", expected_attempt_id="original-attempt"
    )
    with pytest.raises(ProblemException, match="EPISODE_RESOLUTION_UNAVAILABLE"):
        resolutions.resolve_episode(*scope, 0, command, "actor")
    with connect() as connection:
        connection.execute(
            "UPDATE ingest.raw_ingest_jobs SET status='RUNNING' WHERE raw_source_id=%s", (scope[3],)
        )
    assert not resolutions.get_resolution(*scope, 1).available
    with pytest.raises(ProblemException, match="EPISODE_RESOLUTION_UNAVAILABLE"):
        resolutions.resolve_episode(*scope, 1, command, "actor")


@pytest.mark.integration
def test_batch_retry_uses_fresh_attempt_and_redelivery_keeps_identity(conflict):
    scope, connect, _ = conflict
    launched = []

    class Launcher:
        async def start(self, **kwargs):
            launched.append(kwargs["workflow_input"].task.source)

    handler = LeRobotImportOutboxHandler(Launcher(), PostgresRawSourceRepository(connect))
    for _ in range(2):
        processing.retry_processing(*scope)
        with connect() as connection:
            envelope = connection.execute(
                "SELECT envelope FROM core.outbox_events WHERE envelope->>'aggregate_id'=%s "
                "AND event_type='lerobot.import.requested.v1' ORDER BY occurred_at DESC LIMIT 1",
                (scope[3],),
            ).fetchone()[0]
        event = DomainEventEnvelope.model_validate(envelope)
        asyncio.run(handler(event))
        asyncio.run(handler(event))
        with connect() as connection:
            connection.execute(
                "UPDATE ingest.raw_ingest_jobs SET status='PARTIALLY_FAILED' "
                "WHERE raw_source_id=%s",
                (scope[3],),
            )
    assert launched[0].import_attempt_id == launched[1].import_attempt_id
    assert launched[2].import_attempt_id == launched[3].import_attempt_id
    assert launched[0].import_attempt_id != launched[2].import_attempt_id
    assert all(source.processing_attempt_id is None for source in launched)
