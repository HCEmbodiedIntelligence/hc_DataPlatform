"""Committed web imports must appear in the same task facts as robot uploads."""

from uuid import uuid4

import pytest

from hc_data_platform.collection_tasks.captures import capture_counts, read_captures
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.ingest.raw_sources import (
    CommittedRawSourceGraph,
    PostgresRawSourceRepository,
    RawIngestJob,
    RawSource,
    RawSourceEpisode,
)
from tests.collection_tasks.test_collection_task_postgres import (
    cleanup,
    create_runtime_role,
    create_task,
    database_dsn,
    drop_runtime_role,
    ensure_organization_project,
    insert_job,
    insert_qc,
    insert_received,
    organization_for,
    psycopg,
    request_scope,
    service_for,
    set_scope,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def task_scope():
    dsn = database_dsn()
    project = "web-progress-" + uuid4().hex
    role = "web_progress_" + uuid4().hex[:12]
    ensure_organization_project(dsn, project)
    create_runtime_role(dsn, role)
    service = service_for(dsn, role)
    try:
        with request_scope(project, "cn-test"):
            task = create_task(service, project, "web-progress")
            yield dsn, project, service, task
    finally:
        with psycopg.connect(dsn) as connection:
            set_scope(connection, project)
            for table in ("raw_source_episodes", "raw_ingest_jobs", "raw_sources"):
                connection.execute(f"DELETE FROM ingest.{table} WHERE project_id=%s", (project,))
        cleanup(dsn, project)
        drop_runtime_role(dsn, role)


def register_web_import(dsn, project, task, *, status="READY", manifest_key=None):
    source_id = "web-" + uuid4().hex
    scope = dict(
        organization_id=organization_for(project), project_id=project, region_code="cn-test"
    )
    source = RawSource(
        **scope,
        raw_source_id=source_id,
        upload_id=source_id,
        collection_task_id=task.collection_task_id,
        dataset_id=task.dataset_id,
        robot_id="manifest-device",
        source_format="LEROBOT_V3",
        source_format_version="v3.0",
        manifest_key=manifest_key or f"raw/{source_id}/manifest.json",
        storage_prefix=f"raw/{source_id}/source",
        content_hash="a" * 64,
        file_count=7,
        total_bytes=800,
    )
    episodes = tuple(
        RawSourceEpisode(
            **scope,
            raw_source_id=source_id,
            episode_id=f"{source_id}-ep-{index}",
            source_episode_index=index,
            status=status,
            frame_count=203 if status == "READY" else None,
            dataset_version=1 if status == "READY" else None,
            lance_version=1 if status == "READY" else None,
        )
        for index in range(2)
    )
    PostgresRawSourceRepository(psycopg_connection_factory(dsn)).register_committed(
        CommittedRawSourceGraph(
            source=source,
            episodes=episodes,
            job=RawIngestJob(
                **scope,
                raw_source_id=source_id,
                job_id=source_id,
                job_type="LEROBOT_IMPORT",
                adapter_name="lerobot_v3",
                status="SUCCEEDED" if status == "READY" else "RUNNING",
            ),
        )
    )
    return source, episodes


@pytest.mark.parametrize(
    ("status", "qc", "stage", "ready", "failed"),
    [
        ("READY", "PASS", "READY", 2, 0),
        ("READY", "RISK", "FAILED", 0, 2),
        ("FAILED", None, "FAILED", 0, 2),
        ("PROCESSING", None, "PROCESSING", 0, 0),
        ("DISCARDED", None, "DISCARDED", 0, 0),
    ],
)
def test_web_import_counts_one_receipt_and_each_episode(
    task_scope, status, qc, stage, ready, failed
):
    dsn, project, service, task = task_scope
    source, episodes = register_web_import(dsn, project, task, status=status)
    if qc:
        for index, episode in enumerate(episodes, 1):
            insert_qc(dsn, project, episode.episode_id, qc, index)

    page = service.captures(
        source.organization_id, project, "cn-test", task_id=task.collection_task_id
    )
    assert page.total == 1
    item = page.items[0]
    assert (item.upload_id, item.source_import_id) == (source.upload_id, source.raw_source_id)
    assert item.received and item.stage == stage
    assert item.qc_outcome == qc
    assert page.counts.received_count == 1
    assert page.counts.episode_count == (0 if status == "DISCARDED" else 2)
    assert page.counts.ready_episode_count == ready
    assert page.counts.failed_episode_count == failed

    progress = service.progress(source.organization_id, project, task.collection_task_id, "cn-test")
    assert progress.received_package_count == 1
    assert progress.captures == page.counts
    assert progress.qc.pass_count == int(qc == "PASS")
    assert progress.qc.risk_count == int(qc == "RISK")
    assert progress.qc.pending_count == int(qc is None)

    # Explicit SQL scope must hold even for a connection that bypasses RLS.
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        for organization, region, task_id in (
            ("another-organization", "cn-test", task.collection_task_id),
            (source.organization_id, "another-region", task.collection_task_id),
            (source.organization_id, "cn-test", "another-task"),
        ):
            assert not read_captures(cursor, organization, project, region, task_id)


def test_same_bytes_in_independent_imports_are_counted_separately(task_scope):
    dsn, project, service, task = task_scope
    first, _ = register_web_import(dsn, project, task)
    second, _ = register_web_import(dsn, project, task)
    assert first.content_hash == second.content_hash
    progress = service.progress(first.organization_id, project, task.collection_task_id, "cn-test")
    assert progress.received_package_count == 2
    assert progress.captures.ready_episode_count == 4
    # READY alone must not fabricate a passing QC report.
    assert progress.qc.pass_count == 0
    assert progress.qc.pending_count == 2


def test_legacy_receipt_is_not_counted_again_as_a_web_import(task_scope):
    dsn, project, service, task = task_scope
    job, rollout = "job-" + uuid4().hex, "rollout-" + uuid4().hex
    insert_job(dsn, project, task.collection_task_id, job)
    insert_received(dsn, project, job, rollout, 1)
    source, _ = register_web_import(
        dsn, project, task, manifest_key=f"raw/{project}/{rollout}/manifest.json"
    )
    progress = service.progress(source.organization_id, project, task.collection_task_id, "cn-test")
    assert progress.received_package_count == 1
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        items = read_captures(cursor, source.organization_id, project, "cn-test")
    assert capture_counts(items).upload_count == 0
