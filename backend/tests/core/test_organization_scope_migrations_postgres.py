from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from urllib.parse import quote, urlsplit
from uuid import uuid4

import asyncpg
import psycopg
import pytest
from psycopg import sql

from hc_data_platform.core import migrations as migration_module
from hc_data_platform.core.dbapi import normalize_postgres_dsn
from hc_data_platform.core.migrations import apply_migrations, load_migrations

pytestmark = pytest.mark.integration

_CALIBRATION_ORGANIZATION_VERSION = "calibrations/0007_organization_scoped_calibrations.sql"
_ROBOTICS_ORGANIZATION_VERSION = "robotics/0004_organization_scoped_robotics.sql"
_CORE_ORGANIZATION_VERSION = "security/018_core_organization_scope.sql"
_PRODUCT_ORGANIZATION_VERSION = "security/019_product_organization_scope.sql"
_LEGACY_CLEANING_IMPORT_VERSION = "annotation/0008_scoped_legacy_cleaning_import.sql"
_PROJECT_ID = "organization-upgrade-project"
_ORGANIZATION_ID = "organization-upgrade-org"
_SECOND_ORGANIZATION_ID = "organization-upgrade-org-second"
_REGION_CODE = "cn-shanghai-upgrade"
_ROBOT_ID = "organization-upgrade-robot"
_COMPONENT_ID = "organization-upgrade-component"
_SET_ID = "organization-upgrade-calibration"
_ACCESS_AUDIT_ID = "1a000000-0000-4000-8000-000000000011"
_CORE_AUDIT_ID = "1a000000-0000-4000-8000-000000000012"
_SECOND_CORE_AUDIT_ID = "1a000000-0000-4000-8000-000000000013"
_CORE_OUTBOX_ID = "1a000000-0000-4000-8000-000000000014"


def _source_dsn() -> str:
    value = os.environ.get("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    return normalize_postgres_dsn(value)


def _database_dsn(base_dsn: str, database_name: str) -> str:
    parsed = urlsplit(base_dsn)
    return parsed._replace(path=f"/{quote(database_name, safe='')}").geturl()


@pytest.fixture
def isolated_organization_migration_dsn() -> Iterator[str]:
    base_dsn = _source_dsn()
    database_name = f"hc_org_scope_{uuid4().hex[:12]}"
    with psycopg.connect(base_dsn, autocommit=True) as admin:
        try:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        except psycopg.errors.InsufficientPrivilege:
            pytest.skip("HC_TEST_POSTGRES_DSN role cannot create an isolated database")
    dsn = _database_dsn(base_dsn, database_name)
    try:
        yield dsn
    finally:
        with psycopg.connect(base_dsn, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (database_name,),
            )
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))


async def _bootstrap_before_organization_identity(dsn: str) -> tuple[str, ...]:
    migrations = load_migrations()
    position = next(
        index
        for index, migration in enumerate(migrations)
        if migration.version == _CALIBRATION_ORGANIZATION_VERSION
    )
    connection = await asyncpg.connect(normalize_postgres_dsn(dsn))
    try:
        async with connection.transaction():
            await connection.execute(migration_module._TRACKING_TABLE_SQL)
        for migration in migrations[:position]:
            async with connection.transaction():
                await connection.execute(migration.sql)
                await connection.execute(
                    """
                    INSERT INTO core.schema_migrations (version, checksum_sha256)
                    VALUES ($1, $2)
                    """,
                    migration.version,
                    migration.checksum_sha256,
                )
    finally:
        await connection.close()
    return tuple(migration.version for migration in migrations[position:])


async def _bootstrap_before_core_organization_identity(dsn: str) -> tuple[str, ...]:
    migrations = load_migrations()
    position = next(
        index
        for index, migration in enumerate(migrations)
        if migration.version == _CORE_ORGANIZATION_VERSION
    )
    connection = await asyncpg.connect(normalize_postgres_dsn(dsn))
    try:
        async with connection.transaction():
            await connection.execute(migration_module._TRACKING_TABLE_SQL)
        for migration in migrations[:position]:
            await migration_module._apply_pending_migration(connection, migration)
    finally:
        await connection.close()
    return tuple(migration.version for migration in migrations[position:])


async def _bootstrap_before_product_organization_identity(dsn: str) -> tuple[str, ...]:
    migrations = load_migrations()
    position = next(
        index
        for index, migration in enumerate(migrations)
        if migration.version == _PRODUCT_ORGANIZATION_VERSION
    )
    connection = await asyncpg.connect(normalize_postgres_dsn(dsn))
    try:
        async with connection.transaction():
            await connection.execute(migration_module._TRACKING_TABLE_SQL)
        for migration in migrations[:position]:
            await migration_module._apply_pending_migration(connection, migration)
    finally:
        await connection.close()
    return tuple(migration.version for migration in migrations[position:])


def _seed_legacy_core_rows(dsn: str, *, organizations: tuple[str, ...]) -> str:
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO registry.organization_projects "
            "(organization_id, project_id) VALUES (%s, %s)",
            ((organization_id, _PROJECT_ID) for organization_id in organizations),
        )
        cursor.execute(
            """
            INSERT INTO core.idempotency_records (
                project_id, region_code, scope_key, idempotency_key,
                request_fingerprint, response_json, expires_at
            ) VALUES (%s, %s, 'core-upgrade', 'same-key', %s, '{}'::jsonb, now() + interval '1 day')
            """,
            (_PROJECT_ID, _REGION_CODE, "a" * 64),
        )
        cursor.execute(
            """
            INSERT INTO core.audit_events (
                audit_id, project_id, region_code, actor_id, action, resource_type,
                resource_id, request_id, details, occurred_at
            ) VALUES (
                %s, %s, %s, 'core-upgrade-actor', 'core.upgrade.seeded',
                'migration', 'core-upgrade-resource', 'core-upgrade-request',
                '{"legacy": true}'::jsonb, '2026-08-20T12:00:00Z'
            )
            """,
            (_CORE_AUDIT_ID, _PROJECT_ID, _REGION_CODE),
        )
        cursor.execute(
            """
            INSERT INTO core.outbox_events (
                event_id, project_id, region_code, event_type, envelope, occurred_at
            ) VALUES (
                %s, %s, %s, 'core.upgrade.seeded',
                '{"event_id": "legacy-envelope-without-organization"}'::jsonb,
                '2026-08-20T12:00:00Z'
            )
            """,
            (_CORE_OUTBOX_ID, _PROJECT_ID, _REGION_CODE),
        )
        return cursor.execute(
            "SELECT event_hash FROM core.audit_integrity_entries WHERE audit_id = %s",
            (_CORE_AUDIT_ID,),
        ).fetchone()[0]


def _seed_legacy_product_row(dsn: str, *, organizations: tuple[str, ...]) -> None:
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO registry.organization_projects "
            "(organization_id, project_id) VALUES (%s, %s)",
            ((organization_id, _PROJECT_ID) for organization_id in organizations),
        )
        cursor.execute(
            """
            INSERT INTO storage.inventory_snapshots (
                project_id, snapshot_id, observed_at,
                physical_total_bytes, physical_instance_count,
                candidate_business_total_bytes, candidate_logical_object_count,
                replica_overhead_bytes, temporary_bytes,
                duplicate_inventory_rows_ignored, content_digest, published_at
            ) VALUES (
                %s, 'legacy-product-snapshot', '2026-08-20T12:00:00Z',
                10, 1, 10, 1, 0, 0, 0, %s, '2026-08-20T12:00:00Z'
            )
            """,
            (_PROJECT_ID, "c" * 64),
        )


def _seed_legacy_robot_and_calibration(dsn: str, *, organizations: tuple[str, ...]) -> None:
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO registry.organization_projects "
            "(organization_id, project_id) VALUES (%s, %s)",
            ((organization_id, _PROJECT_ID) for organization_id in organizations),
        )
        cursor.execute(
            """
                INSERT INTO robotics.robot_instances (
                    project_id, region_code, robot_id, display_name, serial_no,
                    lifecycle_status, connectivity_state, etag, topology_revision, allowed_actions
                ) VALUES (%s, %s, %s, %s, %s, 'ACTIVE', 'ONLINE', %s, %s, '[]'::jsonb)
                """,
            (
                _PROJECT_ID,
                _REGION_CODE,
                _ROBOT_ID,
                "Legacy organization upgrade robot",
                "ORG-UPGRADE-SN",
                '"organization-upgrade-robot:1"',
                "organization-upgrade-topology:1",
            ),
        )
        cursor.execute(
            """
                INSERT INTO robotics.robot_components (
                    project_id, region_code, component_id, robot_id, component_model_id,
                    component_type, display_name, serial_no, lifecycle_status, sort_order
                ) VALUES (%s, %s, %s, %s, %s, 'CAMERA', %s, %s, 'ACTIVE', 0)
                """,
            (
                _PROJECT_ID,
                _REGION_CODE,
                _COMPONENT_ID,
                _ROBOT_ID,
                "organization-upgrade-camera-model",
                "Legacy organization upgrade camera",
                "ORG-UPGRADE-CAM",
            ),
        )
        cursor.execute(
            """
                INSERT INTO calibrations.calibration_sets (
                    project_id, region_code, set_id, robot_instance_id, component_id, version,
                    snapshot_status, availability, content_hash, validation_context_hash,
                    validation_status, validation_content_hash, validation_report_id, etag,
                    allowed_actions, blocked_reasons
                ) VALUES (
                    %s, %s, %s, %s, %s, 1, 'DRAFT', NULL, %s, %s, 'PASSED', %s, %s, %s,
                    '[]'::jsonb, '[]'::jsonb
                )
                """,
            (
                _PROJECT_ID,
                _REGION_CODE,
                _SET_ID,
                _ROBOT_ID,
                _COMPONENT_ID,
                "a" * 64,
                "b" * 64,
                "a" * 64,
                "organization-upgrade-report",
                '"organization-upgrade-calibration:1"',
            ),
        )
        cursor.execute(
            """
            INSERT INTO access_control.audit_events (
                event_id, scope_kind, project_id, actor_id, action,
                resource_type, resource_id, request_id, outcome, safe_details
            ) VALUES (
                %s, 'PROJECT', %s, 'organization-upgrade-actor',
                'access.membership.requested', 'membership_request',
                'organization-upgrade-membership', 'organization-upgrade-request',
                'SUCCEEDED', '{}'::jsonb
            )
            """,
            (_ACCESS_AUDIT_ID, _PROJECT_ID),
        )


def test_populated_legacy_calibration_and_robotics_upgrade_to_exact_organization_keys(
    isolated_organization_migration_dsn: str,
) -> None:
    dsn = isolated_organization_migration_dsn
    expected_pending = asyncio.run(_bootstrap_before_organization_identity(dsn))
    _seed_legacy_robot_and_calibration(dsn, organizations=(_ORGANIZATION_ID,))

    assert tuple(asyncio.run(apply_migrations(dsn))) == expected_pending
    assert asyncio.run(apply_migrations(dsn)) == []

    with psycopg.connect(dsn) as connection:
        organization_rows = connection.execute(
            """
            SELECT robot.organization_id, component.organization_id, calibration.organization_id
            FROM robotics.robot_instances robot
            JOIN robotics.robot_components component
              ON component.organization_id = robot.organization_id
             AND component.project_id = robot.project_id
             AND component.region_code = robot.region_code
             AND component.robot_id = robot.robot_id
            JOIN calibrations.calibration_sets calibration
              ON calibration.organization_id = component.organization_id
             AND calibration.project_id = component.project_id
             AND calibration.region_code = component.region_code
             AND calibration.component_id = component.component_id
             AND calibration.robot_instance_id = component.robot_id
            WHERE robot.robot_id = %s AND calibration.set_id = %s
            """,
            (_ROBOT_ID, _SET_ID),
        ).fetchall()
        assert organization_rows == [(_ORGANIZATION_ID, _ORGANIZATION_ID, _ORGANIZATION_ID)]
        assert connection.execute(
            """
            SELECT organization_id
            FROM access_control.audit_events
            WHERE event_id = %s
            """,
            (_ACCESS_AUDIT_ID,),
        ).fetchone() == (_ORGANIZATION_ID,)
        assert connection.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'calibrations.calibration_sets'::regclass
                  AND conname = 'calibration_sets_robot_component_organization_fk'
                  AND pg_get_constraintdef(oid) LIKE
                    'FOREIGN KEY (organization_id, project_id, region_code, component_id, '
                    || 'robot_instance_id)%'
            )
            """
        ).fetchone()[0]
        ledger = {
            row[0]
            for row in connection.execute(
                """
                SELECT version FROM core.schema_migrations
                WHERE version IN (%s, %s)
                """,
                (_CALIBRATION_ORGANIZATION_VERSION, _ROBOTICS_ORGANIZATION_VERSION),
            ).fetchall()
        }
        assert ledger == {_CALIBRATION_ORGANIZATION_VERSION, _ROBOTICS_ORGANIZATION_VERSION}

    with (
        psycopg.connect(dsn, autocommit=True) as connection,
        pytest.raises(psycopg.errors.RaiseException, match="access audit events are append-only"),
    ):
        connection.execute(
            """
            UPDATE access_control.audit_events
               SET safe_details = '{"tampered": true}'::jsonb
             WHERE event_id = %s
            """,
            (_ACCESS_AUDIT_ID,),
        )


def test_ambiguous_legacy_project_fails_before_organization_columns_or_ledger(
    isolated_organization_migration_dsn: str,
) -> None:
    dsn = isolated_organization_migration_dsn
    asyncio.run(_bootstrap_before_organization_identity(dsn))
    _seed_legacy_robot_and_calibration(
        dsn,
        organizations=(_ORGANIZATION_ID, _SECOND_ORGANIZATION_ID),
    )

    with pytest.raises(
        asyncpg.exceptions.RaiseError, match="requires exactly one registry organization"
    ):
        asyncio.run(apply_migrations(dsn))

    with psycopg.connect(dsn) as connection:
        assert not connection.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM core.schema_migrations
                WHERE version IN (%s, %s)
            )
            """,
            (_CALIBRATION_ORGANIZATION_VERSION, _ROBOTICS_ORGANIZATION_VERSION),
        ).fetchone()[0]
        assert not connection.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'calibrations'
                  AND table_name = 'calibration_sets'
                  AND column_name = 'organization_id'
            )
            """
        ).fetchone()[0]


def test_populated_core_upgrade_backfills_identity_rebuilds_chain_and_rekeys_ledgers(
    isolated_organization_migration_dsn: str,
) -> None:
    dsn = isolated_organization_migration_dsn
    expected_pending = asyncio.run(_bootstrap_before_core_organization_identity(dsn))
    legacy_event_hash = _seed_legacy_core_rows(dsn, organizations=(_ORGANIZATION_ID,))

    assert expected_pending[:3] == (
        _CORE_ORGANIZATION_VERSION,
        _PRODUCT_ORGANIZATION_VERSION,
        _LEGACY_CLEANING_IMPORT_VERSION,
    )
    assert tuple(asyncio.run(apply_migrations(dsn))) == expected_pending
    assert asyncio.run(apply_migrations(dsn)) == []

    with psycopg.connect(dsn) as connection:
        upgraded_row = connection.execute(
            """
            SELECT idempotency.organization_id, audit.organization_id,
                   outbox.organization_id, integrity.organization_id,
                   integrity.event_hash
            FROM core.idempotency_records idempotency
            CROSS JOIN core.audit_events audit
            CROSS JOIN core.outbox_events outbox
            JOIN core.audit_integrity_entries integrity
              ON integrity.audit_id = audit.audit_id
            WHERE idempotency.idempotency_key = 'same-key'
              AND audit.audit_id = %s
              AND outbox.event_id = %s
            """,
            (_CORE_AUDIT_ID, _CORE_OUTBOX_ID),
        ).fetchone()
        assert upgraded_row[:4] == (
            _ORGANIZATION_ID,
            _ORGANIZATION_ID,
            _ORGANIZATION_ID,
            _ORGANIZATION_ID,
        )
        assert upgraded_row[4] != legacy_event_hash

        rebuilt_event_hash = connection.execute(
            "SELECT event_hash FROM core.audit_integrity_entries WHERE audit_id = %s",
            (_CORE_AUDIT_ID,),
        ).fetchone()[0]
        assert rebuilt_event_hash != legacy_event_hash
        assert len(rebuilt_event_hash) == 64

        connection.execute(
            """
            INSERT INTO registry.organization_projects (organization_id, project_id)
            VALUES (%s, %s)
            """,
            (_SECOND_ORGANIZATION_ID, _PROJECT_ID),
        )
        connection.execute(
            """
            INSERT INTO core.idempotency_records (
                organization_id, project_id, region_code, scope_key, idempotency_key,
                request_fingerprint, response_json, expires_at
            ) VALUES (
                %s, %s, %s, 'core-upgrade', 'same-key', %s,
                '{"second": true}'::jsonb, now() + interval '1 day'
            )
            """,
            (_SECOND_ORGANIZATION_ID, _PROJECT_ID, _REGION_CODE, "b" * 64),
        )
        connection.execute(
            """
            INSERT INTO core.audit_events (
                audit_id, organization_id, project_id, region_code, actor_id, action,
                resource_type, resource_id, request_id, details, occurred_at
            ) VALUES (
                %s, %s, %s, %s, 'core-upgrade-actor-second',
                'core.upgrade.second_org', 'migration', 'core-upgrade-resource-second',
                'core-upgrade-request-second', '{}'::jsonb, '2026-08-20T12:01:00Z'
            )
            """,
            (_SECOND_CORE_AUDIT_ID, _SECOND_ORGANIZATION_ID, _PROJECT_ID, _REGION_CODE),
        )
        assert connection.execute(
            """
            SELECT organization_id, last_sequence
            FROM core.audit_integrity_heads
            WHERE project_id = %s AND region_code = %s
            ORDER BY organization_id
            """,
            (_PROJECT_ID, _REGION_CODE),
        ).fetchall() == [
            (_ORGANIZATION_ID, 1),
            (_SECOND_ORGANIZATION_ID, 1),
        ]
        assert connection.execute(
            """
            SELECT count(*), count(DISTINCT organization_id)
            FROM core.idempotency_records
            WHERE project_id = %s AND region_code = %s
              AND scope_key = 'core-upgrade' AND idempotency_key = 'same-key'
            """,
            (_PROJECT_ID, _REGION_CODE),
        ).fetchone() == (2, 2)

    with (
        psycopg.connect(dsn, autocommit=True) as connection,
        pytest.raises(
            psycopg.errors.CheckViolation,
            match="outbox_events_envelope_organization_matches",
        ),
    ):
        connection.execute(
            """
            INSERT INTO core.outbox_events (
                event_id, organization_id, project_id, region_code,
                event_type, envelope, occurred_at
            ) VALUES (
                %s, %s, %s, %s, 'core.upgrade.mismatched',
                jsonb_build_object('organization_id', %s::text), now()
            )
            """,
            (
                str(uuid4()),
                _ORGANIZATION_ID,
                _PROJECT_ID,
                _REGION_CODE,
                _SECOND_ORGANIZATION_ID,
            ),
        )


def test_ambiguous_legacy_core_project_rolls_back_columns_and_migration_ledger(
    isolated_organization_migration_dsn: str,
) -> None:
    dsn = isolated_organization_migration_dsn
    asyncio.run(_bootstrap_before_core_organization_identity(dsn))
    legacy_event_hash = _seed_legacy_core_rows(
        dsn,
        organizations=(_ORGANIZATION_ID, _SECOND_ORGANIZATION_ID),
    )

    with pytest.raises(
        asyncpg.exceptions.RaiseError,
        match="organization-scoped core upgrade requires exactly one registry organization",
    ):
        asyncio.run(apply_migrations(dsn))

    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM core.schema_migrations WHERE version = %s
            )
            """,
            (_CORE_ORGANIZATION_VERSION,),
        ).fetchone() == (False,)
        assert connection.execute(
            """
            SELECT count(*)
            FROM information_schema.columns
            WHERE table_schema = 'core'
              AND table_name IN (
                  'idempotency_records', 'audit_events', 'outbox_events',
                  'audit_integrity_heads', 'audit_integrity_entries'
              )
              AND column_name = 'organization_id'
            """
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT event_hash FROM core.audit_integrity_entries WHERE audit_id = %s",
            (_CORE_AUDIT_ID,),
        ).fetchone() == (legacy_event_hash,)


def test_product_tables_backfill_and_restrict_same_project_across_organizations(
    isolated_organization_migration_dsn: str,
) -> None:
    dsn = isolated_organization_migration_dsn
    expected_pending = asyncio.run(_bootstrap_before_product_organization_identity(dsn))
    _seed_legacy_product_row(dsn, organizations=(_ORGANIZATION_ID,))

    assert expected_pending[:2] == (
        _PRODUCT_ORGANIZATION_VERSION,
        _LEGACY_CLEANING_IMPORT_VERSION,
    )
    assert tuple(asyncio.run(apply_migrations(dsn))) == expected_pending
    assert asyncio.run(apply_migrations(dsn)) == []

    role_name = f"hc_product_scope_{uuid4().hex[:12]}"
    try:
        with psycopg.connect(dsn) as connection:
            assert connection.execute(
                """
                SELECT organization_id
                FROM storage.inventory_snapshots
                WHERE project_id = %s AND snapshot_id = 'legacy-product-snapshot'
                """,
                (_PROJECT_ID,),
            ).fetchone() == (_ORGANIZATION_ID,)
            assert connection.execute(
                """
                SELECT count(*)
                FROM information_schema.columns AS project_column
                JOIN information_schema.tables AS relation
                  ON relation.table_schema = project_column.table_schema
                 AND relation.table_name = project_column.table_name
                WHERE project_column.column_name = 'project_id'
                  AND relation.table_type = 'BASE TABLE'
                  AND project_column.table_schema NOT IN ('pg_catalog', 'information_schema')
                  AND NOT EXISTS (
                      SELECT 1
                      FROM information_schema.columns AS organization_column
                      WHERE organization_column.table_schema = project_column.table_schema
                        AND organization_column.table_name = project_column.table_name
                        AND organization_column.column_name = 'organization_id'
                  )
                """
            ).fetchone() == (0,)
            assert connection.execute(
                """
                SELECT policyname, permissive
                FROM pg_policies
                WHERE schemaname = 'storage'
                  AND tablename = 'inventory_snapshots'
                  AND policyname IN ('hc_scope_allow', 'hc_scope_isolation')
                ORDER BY policyname
                """
            ).fetchall() == [
                ("hc_scope_allow", "PERMISSIVE"),
                ("hc_scope_isolation", "RESTRICTIVE"),
            ]

            connection.execute(
                """
                INSERT INTO registry.organization_projects (organization_id, project_id)
                VALUES (%s, %s)
                """,
                (_SECOND_ORGANIZATION_ID, _PROJECT_ID),
            )
            connection.execute(
                """
                INSERT INTO storage.inventory_snapshots (
                    organization_id, project_id, snapshot_id, observed_at,
                    physical_total_bytes, physical_instance_count,
                    candidate_business_total_bytes, candidate_logical_object_count,
                    replica_overhead_bytes, temporary_bytes,
                    duplicate_inventory_rows_ignored, content_digest, published_at
                ) VALUES (
                    %s, %s, 'second-product-snapshot', '2026-08-20T12:01:00Z',
                    20, 1, 20, 1, 0, 0, 0, %s, '2026-08-20T12:01:00Z'
                )
                """,
                (_SECOND_ORGANIZATION_ID, _PROJECT_ID, "d" * 64),
            )
            connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role_name)))
            connection.execute(
                sql.SQL("GRANT USAGE ON SCHEMA storage TO {}").format(sql.Identifier(role_name))
            )
            connection.execute(
                sql.SQL("GRANT SELECT ON storage.inventory_snapshots TO {}").format(
                    sql.Identifier(role_name)
                )
            )

        for organization_id, expected_snapshot in (
            (_ORGANIZATION_ID, "legacy-product-snapshot"),
            (_SECOND_ORGANIZATION_ID, "second-product-snapshot"),
        ):
            with psycopg.connect(dsn) as connection:
                connection.execute(
                    """
                    SELECT set_config('app.organization_id', %s, false),
                           set_config('app.project_id', %s, false),
                           set_config('app.region_code', %s, false)
                    """,
                    (organization_id, _PROJECT_ID, _REGION_CODE),
                )
                connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role_name)))
                assert connection.execute(
                    """
                    SELECT snapshot_id
                    FROM storage.inventory_snapshots
                    ORDER BY snapshot_id
                    """
                ).fetchall() == [(expected_snapshot,)]
    finally:
        with psycopg.connect(dsn) as connection:
            connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role_name)))
            connection.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_name)))


def test_ambiguous_product_project_rolls_back_all_columns_and_ledger(
    isolated_organization_migration_dsn: str,
) -> None:
    dsn = isolated_organization_migration_dsn
    asyncio.run(_bootstrap_before_product_organization_identity(dsn))
    _seed_legacy_product_row(
        dsn,
        organizations=(_ORGANIZATION_ID, _SECOND_ORGANIZATION_ID),
    )

    with pytest.raises(
        asyncpg.exceptions.RaiseError,
        match="organization-scoped product upgrade requires exactly one registry organization",
    ):
        asyncio.run(apply_migrations(dsn))

    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM core.schema_migrations WHERE version = %s
            )
            """,
            (_PRODUCT_ORGANIZATION_VERSION,),
        ).fetchone() == (False,)
        assert connection.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'storage'
                  AND table_name = 'inventory_snapshots'
                  AND column_name = 'organization_id'
            )
            """
        ).fetchone() == (False,)
