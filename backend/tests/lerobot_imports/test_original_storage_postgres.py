import os

import pytest

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
    repository = PostgresRawSourceRepository(connect)
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
