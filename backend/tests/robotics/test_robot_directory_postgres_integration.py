from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from hc_data_platform.robot_assets.repository import PostgresOrganizationRobotAssetRepository
from hc_data_platform.robotics.models import CreateRobotRequest

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


def test_postgres_deletes_an_unused_active_robot() -> None:
    dsn = os.getenv("HC_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    organization_id = "robot-provisional-delete-integration"
    robot_id = "robot-provisional-delete"
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    try:
        with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO robotics.robot_assets (
                    organization_id, robot_id, display_name, serial_no,
                    lifecycle_status, connectivity_state, etag, topology_revision,
                    allowed_actions, revision, created_at, updated_at
                ) VALUES (
                    %s, %s, '未使用机器人', 'TEMP-DELETE-001', 'ACTIVE', 'OFFLINE',
                    '"robot-provisional-delete:1"', 'robot-provisional-delete:1',
                    '[]'::jsonb, 1, %s, %s
                )
                """,
                (organization_id, robot_id, now, now),
            )
        repository = PostgresOrganizationRobotAssetRepository(lambda: psycopg.connect(dsn))
        assert repository.delete_robot(
            organization_id=organization_id,
            robot_id=robot_id,
            actor_id="integration-test",
            request_id="provisional-delete",
            occurred_at=now,
        )
        assert not repository.delete_robot(
            organization_id=organization_id,
            robot_id=robot_id,
            actor_id="integration-test",
            request_id="provisional-delete-replay",
            occurred_at=now,
        )
    finally:
        with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM robotics.robot_asset_audit_events WHERE organization_id = %s",
                (organization_id,),
            )
            cursor.execute(
                "DELETE FROM robotics.robot_assets WHERE organization_id = %s",
                (organization_id,),
            )


def test_postgres_reports_a_serial_conflict_instead_of_leaking_unique_violation() -> None:
    dsn = os.getenv("HC_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    organization_id = "robot-serial-conflict-integration"
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    try:
        with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO robotics.robot_assets (
                    organization_id, robot_id, display_name, serial_no,
                    lifecycle_status, connectivity_state, etag, topology_revision,
                    allowed_actions, revision, created_at, updated_at
                ) VALUES (
                    %s, 'robot-existing', '已有机器人', 'SERIAL-001', 'ACTIVE', 'OFFLINE',
                    '"robot-existing:1"', 'robot-existing:1', '[]'::jsonb, 1, %s, %s
                )
                """,
                (organization_id, now, now),
            )
        repository = PostgresOrganizationRobotAssetRepository(lambda: psycopg.connect(dsn))
        with pytest.raises(ValueError, match="serial number already exists"):
            repository.create_robot(
                organization_id=organization_id,
                robot_id="robot-duplicate",
                command=CreateRobotRequest(display_name="重复机器人", serial_no="SERIAL-001"),
                idempotency_key="duplicate-serial",
                request_fingerprint="fingerprint",
                actor_id="integration-test",
                request_id="serial-conflict",
                occurred_at=now,
            )
    finally:
        with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM robotics.robot_asset_command_receipts WHERE organization_id = %s",
                (organization_id,),
            )
            cursor.execute(
                "DELETE FROM robotics.robot_asset_audit_events WHERE organization_id = %s",
                (organization_id,),
            )
            cursor.execute(
                "DELETE FROM robotics.robot_assets WHERE organization_id = %s",
                (organization_id,),
            )
