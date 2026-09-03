from __future__ import annotations

import asyncio
import os
import uuid
from urllib.parse import urlparse

import asyncpg
import psycopg
import pytest

from hc_data_platform.core import migrations as migration_module
from hc_data_platform.core.dbapi import normalize_postgres_dsn
from hc_data_platform.core.migrations import apply_migrations, load_migrations, migration_status
from tests.system.wave2.cleanup import PostgresS3CleanupBackend
from tests.system.wave2.fixture import RunScope

pytestmark = pytest.mark.integration

HISTORICAL_COUNT = 20
ORIGINAL_MISSING = (
    "dashboard/0001_dashboard_query_indexes.sql",
    "security/003_outbox_dispatch.sql",
    "ingest/003_automatic_workflow_trigger.sql",
    "annotation/0003_automatic_tasks.sql",
    "publishing/0002_rollout_publication_region_lineage.sql",
    "dashboard/0002_dashboard_business_feed_indexes.sql",
)
FORWARD_REPAIR = (
    "storage/0002_seal_inventory_snapshots.sql",
    "annotation/0004_backfill_task_base_step_count.sql",
)
EXPECTED_NEW_OBJECTS = {
    "dashboard/0001_dashboard_query_indexes.sql": (
        "ingest.dashboard_rollout_objects_scope_committed_idx",
        "ingest.dashboard_rollouts_scope_created_idx",
    ),
    "security/003_outbox_dispatch.sql": ("core.outbox_events_organization_claimable_idx",),
    "ingest/003_automatic_workflow_trigger.sql": ("ingest.workflow_triggers",),
    "annotation/0003_automatic_tasks.sql": (
        "annotation.tag_schema_bindings",
        "annotation.annotation_task_triggers",
    ),
    "publishing/0002_rollout_publication_region_lineage.sql": (
        "publishing.rollout_publication_lineage",
    ),
    "dashboard/0002_dashboard_business_feed_indexes.sql": (
        "ingest.dashboard_upload_sessions_state_opened_idx",
        "public.dashboard_qc_reports_scope_event_idx",
        "public.dashboard_quality_pending_scope_idx",
        "annotation.dashboard_annotation_tasks_state_opened_idx",
        "annotation.dashboard_annotation_reviews_event_idx",
    ),
}


def _migrations_after_historical_baseline() -> tuple[str, ...]:
    """Keep the recovery gate current as additive domain migrations land."""

    return tuple(item.version for item in load_migrations()[HISTORICAL_COUNT:])


def _required_dsn(name: str) -> str:
    value = os.getenv(name)
    if not value:
        pytest.skip(f"{name} must point to a dedicated empty migration-test database")
    return value


async def _bootstrap_historical_twenty(dsn: str) -> None:
    migrations = load_migrations()
    connection = await asyncpg.connect(normalize_postgres_dsn(dsn))
    try:
        async with connection.transaction():
            await connection.execute(migration_module._TRACKING_TABLE_SQL)
            for migration in migrations[:HISTORICAL_COUNT]:
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


async def _seed_historical_rows(dsn: str) -> dict[str, int]:
    connection = await asyncpg.connect(normalize_postgres_dsn(dsn))
    try:
        async with connection.transaction():
            await connection.execute(
                """
                INSERT INTO storage.inventory_snapshots (
                    project_id, snapshot_id, observed_at, physical_total_bytes,
                    physical_instance_count, candidate_business_total_bytes,
                    candidate_logical_object_count, replica_overhead_bytes,
                    temporary_bytes, duplicate_inventory_rows_ignored, content_digest
                ) VALUES (
                    'mr01-history-project', 'mr01-history-snapshot', now(), 42,
                    1, 42, 1, 0, 0, 0, repeat('a', 64)
                )
                """
            )
            await connection.execute(
                """
                INSERT INTO storage.inventory_facts (
                    project_id, snapshot_id, physical_instance_id, logical_object_id,
                    physical_bytes, disposition, business_category, object_role, observed_at
                ) VALUES (
                    'mr01-history-project', 'mr01-history-snapshot', 'physical-1',
                    'logical-1', 42, 'PRIMARY', 'RAW', 'RAW', now()
                )
                """
            )
            await connection.execute(
                """
                INSERT INTO lance_schema_snapshots (
                    project_id, dataset_id, schema_snapshot_id, frequency_hz,
                    fields_json, fingerprint, snapshot_json
                ) VALUES (
                    'mr01-history-project', 'dataset-1', 'schema-1', 30,
                    '[]'::jsonb, repeat('b', 64), '{}'::jsonb
                );
                INSERT INTO lance_datasets (
                    project_id, dataset_id, schema_snapshot_id, frequency_hz,
                    fingerprint, dataset_uri, current_version
                ) VALUES (
                    'mr01-history-project', 'dataset-1', 'schema-1', 30,
                    repeat('b', 64), 's3://mr01-history/dataset-1', 1
                );
                INSERT INTO lance_dataset_versions (
                    project_id, dataset_id, version, schema_snapshot_id, frequency_hz,
                    fingerprint, content_hash, dataset_uri, lance_version,
                    storage_commit_id, rollout_id, source_sha256, converter_version,
                    committed_rollouts, receipt_json, created_at
                ) VALUES (
                    'mr01-history-project', 'dataset-1', 1, 'schema-1', 30,
                    repeat('b', 64), repeat('c', 64), 's3://mr01-history/dataset-1', 1,
                    repeat('d', 64), 'rollout-1', repeat('e', 64), 'converter-1',
                    '["rollout-1"]'::jsonb, '{}'::jsonb, now()
                );
                INSERT INTO lance_rollout_lineage (
                    project_id, dataset_id, rollout_id, version_added, source_sha256,
                    converter_version, schema_snapshot_id, fingerprint, fragment_uri,
                    fragment_content_hash, step_count, storage_commit_id
                ) VALUES (
                    'mr01-history-project', 'dataset-1', 'rollout-1', 1,
                    repeat('e', 64), 'converter-1', 'schema-1', repeat('b', 64),
                    's3://mr01-history/fragment-1', repeat('f', 64), 123, repeat('d', 64)
                )
                """
            )
            await connection.execute(
                """
                INSERT INTO annotation.annotation_tasks (
                    task_id, project_id, dataset_id, dataset_version, rollout_id,
                    current_revision, state_version, status, etag,
                    base_lance_version, base_step_count
                ) VALUES (
                    'task-1', 'mr01-history-project', 'dataset-1', 1, 'rollout-1',
                    0, 0, 'DRAFT', 'etag-1', 1, NULL
                );
                INSERT INTO annotation.annotation_revisions (
                    task_id, revision, parent_revision, author_id, client_mutation_id,
                    base_lance_version
                ) VALUES ('task-1', 0, NULL, 'author-1', 'mutation-1', 1)
                """
            )
        return {
            "storage.inventory_snapshots": 1,
            "storage.inventory_facts": 1,
            "annotation.annotation_tasks": 1,
            "annotation.annotation_revisions": 1,
            "public.lance_rollout_lineage": 1,
        }
    finally:
        await connection.close()


async def _exact_counts(dsn: str, relations: tuple[str, ...]) -> dict[str, int]:
    connection = await asyncpg.connect(normalize_postgres_dsn(dsn))
    try:
        counts: dict[str, int] = {}
        for relation in relations:
            counts[relation] = int(await connection.fetchval(f"SELECT count(*) FROM {relation}"))
        return counts
    finally:
        await connection.close()


async def _assert_current_objects(dsn: str) -> None:
    assert tuple(EXPECTED_NEW_OBJECTS) == ORIGINAL_MISSING
    connection = await asyncpg.connect(normalize_postgres_dsn(dsn))
    try:
        for migration, objects in EXPECTED_NEW_OBJECTS.items():
            for object_name in objects:
                assert await connection.fetchval("SELECT to_regclass($1)::text", object_name), (
                    migration,
                    object_name,
                )
        assert await connection.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'core' AND table_name = 'outbox_events'
                  AND column_name = 'claim_token'
            )
            """
        )
        assert await connection.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'storage' AND table_name = 'inventory_snapshots'
                  AND column_name = 'sealed' AND is_nullable = 'NO'
            )
            """
        )
        assert await connection.fetchval(
            """
            SELECT count(*) = 2 FROM pg_trigger
            WHERE NOT tgisinternal
              AND tgname IN (
                  'storage_inventory_snapshot_immutable',
                  'storage_inventory_fact_immutable'
              )
            """
        )
    finally:
        await connection.close()


def test_existing_twenty_migrations_upgrade_forward_without_data_loss() -> None:
    dsn = _required_dsn("HC_MIGRATION_RECOVERY_EXISTING_DSN")
    migrations = load_migrations()
    expected_missing = _migrations_after_historical_baseline()
    product_scope_index = next(
        index
        for index, migration in enumerate(migrations)
        if migration.version == "security/019_product_organization_scope.sql"
    )
    blocked_suffix = tuple(migration.version for migration in migrations[product_scope_index:])
    assert blocked_suffix[:2] == (
        "security/019_product_organization_scope.sql",
        "annotation/0008_scoped_legacy_cleaning_import.sql",
    )
    asyncio.run(_bootstrap_historical_twenty(dsn))
    expected_counts = asyncio.run(_seed_historical_rows(dsn))

    before = asyncio.run(migration_status(dsn))
    assert before == {
        "status": "not_current",
        "expected": HISTORICAL_COUNT + len(expected_missing),
        "applied": HISTORICAL_COUNT,
        "missing": sorted(expected_missing),
        "unknown": [],
        "checksum_drift": [],
    }

    with pytest.raises(
        asyncpg.exceptions.RaiseError,
        match="organization-scoped product upgrade requires exactly one registry organization",
    ):
        asyncio.run(apply_migrations(dsn))

    # Forward migrations before 019 commit independently.  The tenant-identity
    # preflight is the deliberate stop point: operators must register one exact
    # organization instead of letting the migration invent an owner for old data.
    stopped = asyncio.run(migration_status(dsn))
    assert stopped == {
        "status": "not_current",
        "expected": HISTORICAL_COUNT + len(expected_missing),
        "applied": product_scope_index,
        "missing": sorted(blocked_suffix),
        "unknown": [],
        "checksum_drift": [],
    }
    assert asyncio.run(_exact_counts(dsn, tuple(expected_counts))) == expected_counts

    with psycopg.connect(normalize_postgres_dsn(dsn)) as connection:
        connection.execute(
            """
            INSERT INTO registry.organization_projects (organization_id, project_id)
            VALUES ('mr01-history-organization', 'mr01-history-project')
            """
        )

    applied_now = asyncio.run(apply_migrations(dsn))
    assert tuple(applied_now) == blocked_suffix
    assert asyncio.run(migration_status(dsn))["status"] == "current"
    assert asyncio.run(apply_migrations(dsn)) == []
    assert asyncio.run(_exact_counts(dsn, tuple(expected_counts))) == expected_counts
    asyncio.run(_assert_current_objects(dsn))

    with psycopg.connect(normalize_postgres_dsn(dsn)) as connection:
        sealed, base_step_count, snapshot_organization, task_organization = connection.execute(
            """
            SELECT snapshot.sealed, task.base_step_count,
                   snapshot.organization_id, task.organization_id
            FROM storage.inventory_snapshots snapshot
            CROSS JOIN annotation.annotation_tasks task
            WHERE snapshot.snapshot_id = 'mr01-history-snapshot'
              AND task.task_id = 'task-1'
            """
        ).fetchone()
    assert sealed is True
    assert base_step_count == 123
    assert snapshot_organization == "mr01-history-organization"
    assert task_organization == "mr01-history-organization"


def test_fresh_database_upgrade_reupgrade_and_wave2_cleanup_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dsn = _required_dsn("HC_MIGRATION_RECOVERY_FRESH_DSN")
    expected_versions = [migration.version for migration in load_migrations()]
    assert asyncio.run(apply_migrations(dsn)) == expected_versions
    assert asyncio.run(migration_status(dsn)) == {
        "status": "current",
        "expected": len(expected_versions),
        "applied": len(expected_versions),
        "missing": [],
        "unknown": [],
        "checksum_drift": [],
    }
    assert asyncio.run(apply_migrations(dsn)) == []
    asyncio.run(_assert_current_objects(dsn))

    scope = RunScope.create("mr01-cleanup")
    outside_project = "be22-outside-control-p1"
    outside_organization = "be22-outside-control-org"
    with psycopg.connect(normalize_postgres_dsn(dsn)) as connection:
        organization_projects = (
            (scope.organization_id, scope.project_id),
            (scope.organization_id, scope.foreign_project_id),
            (outside_organization, outside_project),
        )
        for organization_id, project_id in organization_projects:
            connection.execute(
                """INSERT INTO registry.organization_projects (organization_id, project_id)
                   VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                (organization_id, project_id),
            )
        for organization_id, project_id in organization_projects:
            connection.execute(
                """
                INSERT INTO core.audit_events (
                    audit_id, organization_id, project_id, actor_id, action, resource_type,
                    resource_id, request_id, details, occurred_at
                ) VALUES (%s, %s, %s, 'mr01', 'TEST', 'migration', %s, %s, '{}'::jsonb, now())
                """,
                (
                    uuid.uuid4(),
                    organization_id,
                    project_id,
                    project_id,
                    f"request-{project_id}",
                ),
            )

    database = urlparse(normalize_postgres_dsn(dsn)).path.lstrip("/")
    monkeypatch.setenv("HC_ENVIRONMENT", "test")
    monkeypatch.setenv("HC_WAVE2_CLEANUP_ENABLED", "1")
    monkeypatch.setenv("HC_WAVE2_TEST_DATABASE_ACK", database)
    cleanup = PostgresS3CleanupBackend(postgres_dsn=dsn, s3_client=object(), bucket="unused")
    counts = cleanup.delete_database_scope(scope)
    assert counts["core.audit_events"] == 2

    with psycopg.connect(normalize_postgres_dsn(dsn)) as connection:
        remaining = connection.execute(
            "SELECT project_id FROM core.audit_events ORDER BY project_id"
        ).fetchall()
    assert remaining == [(outside_project,)]
