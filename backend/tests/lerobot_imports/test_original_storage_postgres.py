import os

import pytest

from hc_data_platform.capture_schemes.transactions import bind_raw_snapshot
from hc_data_platform.ingest.raw_sources import PostgresRawSourceRepository
from hc_data_platform.lerobot_imports import processing

from .test_original_storage import SCOPE, store_source


@pytest.mark.integration
def test_original_storage_graph_has_no_dispatch_event(monkeypatch: pytest.MonkeyPatch) -> None:
    dsn = os.environ.get("HC_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    import psycopg

    def connect():
        return psycopg.connect(dsn)

    with connect() as connection:
        connection.execute(
            """INSERT INTO registry.organization_projects
            (organization_id, project_id, display_name) VALUES ('org-a','project-a','Raw test')
            ON CONFLICT DO NOTHING"""
        )
    monkeypatch.setattr(processing, "connections", lambda: connect)
    repository = PostgresRawSourceRepository(connect, capture_snapshot_writer=bind_raw_snapshot)
    for format_name, path in (("MCAP", "original.mcap"), ("ROSBAG", "original.bag")):
        _, _, raw = store_source(
            {path: b"unaltered original"}, source_format=format_name, raw_sources=repository
        )
        rows = processing.list_progress(**SCOPE, dataset_id="dataset-a")
        stored = next(row for row in rows if row.import_id == raw.raw_source_id)
        assert stored.status == "RAW_COMMITTED" and stored.file_count == 1
        assert stored.source_format == format_name and stored.episode_count == 0
        with connect() as connection:
            assert (
                connection.execute(
                    "SELECT count(*) FROM core.outbox_events WHERE envelope->>'aggregate_id'=%s",
                    (raw.raw_source_id,),
                ).fetchone()[0]
                == 0
            )
            assert (
                connection.execute(
                    "SELECT count(*) FROM ingest.raw_source_episodes WHERE raw_source_id=%s",
                    (raw.raw_source_id,),
                ).fetchone()[0]
                == 0
            )
        assert repository.get_source(**SCOPE, raw_source_id=raw.raw_source_id) == raw


@pytest.mark.integration
def test_mcap_commit_dispatches_once_with_the_frozen_binding() -> None:
    from types import SimpleNamespace
    from uuid import uuid4

    import psycopg

    from hc_data_platform.capture_schemes.common import digest
    from hc_data_platform.lerobot_imports.mcap_processing import dispatch_bound_source
    from tests.capture_schemes.processing_fixture import snapshot
    from tests.capture_schemes.test_mcap_processing import config, contract

    dsn = os.environ.get("HC_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    scope = tuple(SCOPE.values())
    task = "task-" + uuid4().hex
    with psycopg.connect(dsn) as db:
        db.execute(
            """INSERT INTO registry.organization_projects
            (organization_id,project_id,display_name) VALUES (%s,%s,'MCAP test')
            ON CONFLICT DO NOTHING""",
            scope[:2],
        )
    receipt = snapshot(
        contract(),
        scope=scope,
        raw={
            "dataset_id": "dataset-a",
            "collection_task_id": task,
            "robot_id": "x1",
            "source_format": "MCAP",
        },
    )
    receipt["effective_adapter"] = {
        "source_format": "MCAP",
        "parser_version": "native-mcap-recording/3",
        "configuration": config(),
    }
    receipt["snapshot_sha256"] = digest(
        {k: v for k, v in receipt.items() if k != "snapshot_sha256"}
    )
    repository = PostgresRawSourceRepository(
        lambda: psycopg.connect(dsn),
        capture_snapshot_writer=bind_raw_snapshot,
        mcap_dispatcher=dispatch_bound_source,
    )
    _, _, raw = store_source(
        {"recording.mcap": b"original"},
        raw_sources=repository,
        capture_plans=SimpleNamespace(prepare=lambda *a, **kw: receipt),
        source_format="MCAP",
        processing_mode="PROCESS",
        robot_id="x1",
        collection_task_id=task,
    )
    assert (raw.collection_task_id, raw.robot_id, raw.processing_status.value) == (
        task,
        "x1",
        "PENDING",
    )
    with psycopg.connect(dsn) as db:
        rows = db.execute(
            """SELECT m.status,m.config_reference,e.event_id
            FROM ingest.mcap_processing_files m JOIN core.outbox_events e
              ON e.envelope->'payload'->>'mcap_processing_id'=m.processing_id
            WHERE m.raw_source_id=%s""",
            (raw.raw_source_id,),
        ).fetchall()
        assert len(rows) == 1 and rows[0][:2] == ("PENDING", receipt)
