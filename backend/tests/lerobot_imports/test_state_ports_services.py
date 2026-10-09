"""Real transactions behind the injected native state/recovery ports."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from hc_data_platform.lerobot_imports import resolutions
from hc_data_platform.lerobot_imports.orchestration import build_import_plan
from hc_data_platform.lerobot_imports.pipeline_ports import NativeStateChange
from hc_data_platform.lerobot_imports.resolutions import ResolveEpisode
from hc_data_platform.lerobot_imports.state_store import PostgresNativeProcessingState
from hc_data_platform.workflow.result_queries import PostgresTerminalWorkflowResults
from tests.dashboard.test_dashboard_postgres import request_scope
from tests.lerobot_imports.test_episode_resolutions import conflict, isolated_dsn  # noqa: F401


def task_for(scope, attempt):
    task = build_import_plan(
        organization_id=scope[0],
        project_id=scope[1],
        region_code=scope[2],
        dataset_id="dataset-native",
        collection_task_id="task-decision-qc",
        robot_id="robot-decision-qc",
        raw_upload_id=scope[3],
        raw_manifest_key=f"raw/{scope[3]}/manifest.json",
        episode_count=3,
    ).episode_tasks[1]
    return task.model_copy(
        update={"source": task.source.model_copy(update={"processing_attempt_id": attempt})}
    )


def snapshot(connect, scope):
    with connect() as connection:
        return tuple(
            connection.execute(
                f"SELECT to_jsonb(row) FROM ingest.{table} row "
                "WHERE raw_source_id=%s ORDER BY to_jsonb(row)::text",
                (scope[3],),
            ).fetchall()
            for table in (
                "raw_sources",
                "raw_source_episodes",
                "raw_ingest_jobs",
                "episode_resolutions",
            )
        )


@pytest.mark.integration
def test_state_fault_rolls_back_episode_resolution_job_and_aggregate(conflict, monkeypatch):  # noqa: F811
    scope, connect, _ = conflict
    command = ResolveEpisode(
        request_id=uuid4(), action="REPROCESS", expected_attempt_id="original-attempt"
    )
    resolutions.resolve_episode(*scope, 1, command, "actor")
    task = task_for(scope, str(command.request_id))
    store = PostgresNativeProcessingState(connect)
    before = snapshot(connect, scope)
    refresh = resolutions.refresh_import_state

    def fail_after_writes(cursor, scope):
        refresh(cursor, scope)
        raise RuntimeError("injected after aggregate writes")

    with monkeypatch.context() as patch:
        patch.setattr(resolutions, "refresh_import_state", fail_after_writes)
        with pytest.raises(RuntimeError, match="injected"):
            store.update_state(
                NativeStateChange(task=task, status="FAILED", error_code="IO_FAILURE")
            )
    assert snapshot(connect, scope) == before
    store.update_state(NativeStateChange(task=task, status="FAILED", error_code="IO_FAILURE"))
    assert resolutions.get_resolution(*scope, 1).status == "FAILED"
    assert store.episode_ready(
        task.model_copy(update={"source": task.source.model_copy(update={"episode_index": 0})})
    )


@pytest.mark.integration
def test_obsolete_attempt_and_parallel_redelivery_preserve_latest_decision(conflict):  # noqa: F811
    scope, connect, _ = conflict
    command = ResolveEpisode(
        request_id=uuid4(), action="REPROCESS", expected_attempt_id="original-attempt"
    )
    resolutions.resolve_episode(*scope, 1, command, "actor")
    store = PostgresNativeProcessingState(connect)
    before = snapshot(connect, scope)
    foreign = task_for(scope, str(command.request_id)).model_copy(update={"region_code": "foreign"})
    with pytest.raises(ValueError, match="scope"):
        store.update_state(NativeStateChange(task=foreign, status="FAILED"))
    with pytest.raises(ValueError, match="scope"):
        store.episode_ready(foreign)
    store.update_state(
        NativeStateChange(
            task=task_for(scope, str(uuid4())),
            status="EPISODE_DONE",
            ready=True,
            derived={"step_count": 2, "dataset_version": 99, "lance_version": 99},
        )
    )
    assert snapshot(connect, scope) == before
    task = task_for(scope, str(command.request_id))

    def deliver(_):
        with request_scope(scope[1], scope[2]):
            store.update_state(NativeStateChange(task=task, status="RUNNING"))

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(deliver, range(4)))
    assert resolutions.get_resolution(*scope, 1).status == "RUNNING"
    with connect() as connection:
        assert connection.execute(
            "SELECT status FROM ingest.raw_ingest_jobs WHERE raw_source_id=%s", (scope[3],)
        ).fetchone() == ("RUNNING",)
        assert connection.execute(
            "SELECT status FROM ingest.raw_source_episodes "
            "WHERE raw_source_id=%s AND source_episode_index=0",
            (scope[3],),
        ).fetchone() == ("READY",)


@pytest.mark.integration
def test_recovery_query_only_reads_scoped_terminal_ingest(conflict):  # noqa: F811
    scope, connect, rollout = conflict
    query = PostgresTerminalWorkflowResults(connect)
    args = dict(
        organization_id=scope[0],
        project_id=scope[1],
        resource_id=rollout,
        job_type="IngestRolloutWorkflow",
    )
    assert query.latest_terminal(**args) is None
    with connect() as connection:
        connection.execute(
            "UPDATE workflow.jobs SET status='SUCCEEDED',result='{}'::jsonb WHERE workflow_id=%s",
            (f"child-{scope[3]}",),
        )
    assert query.latest_terminal(**args).status == "SUCCEEDED"
    assert query.latest_terminal(**{**args, "resource_id": "foreign-rollout"}) is None
    assert query.latest_terminal(**{**args, "job_type": "AnotherWorkflow"}) is None
    with pytest.raises(ValueError, match="scope"):
        query.latest_terminal(**{**args, "organization_id": "foreign-organization"})
