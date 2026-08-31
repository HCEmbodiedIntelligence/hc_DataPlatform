from __future__ import annotations

import os

import pytest

psycopg = pytest.importorskip("psycopg")
pytestmark = pytest.mark.integration


def test_migrated_database_has_no_project_robot_tables() -> None:
    dsn = os.getenv("HC_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_schema || '.' || table_name
              FROM information_schema.tables
             WHERE (table_schema, table_name) IN (
                ('robotics', 'robot_instances'),
                ('robotics', 'robot_components'),
                ('robotics', 'component_frames'),
                ('robotics', 'component_channels'),
                ('registry', 'robot_model_bindings')
             )
            """
        )
        assert cursor.fetchall() == []
        cursor.execute("SELECT to_regclass('robotics.robot_assets')")
        assert cursor.fetchone() == ("robotics.robot_assets",)
