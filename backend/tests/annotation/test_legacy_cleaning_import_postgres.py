from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any, cast
from urllib.parse import quote, urlsplit
from uuid import uuid4

import asyncpg
import psycopg
import pytest
from psycopg import sql

from hc_data_platform.annotation import (
    AnnotationActor,
    AnnotationService,
    PostgresAnnotationRepository,
    RevisionOrigin,
)
from hc_data_platform.annotation.postgres import DbApiConnection
from hc_data_platform.core import migrations as migration_module
from hc_data_platform.core.dbapi import normalize_postgres_dsn
from hc_data_platform.core.migrations import apply_migrations, load_migrations

pytestmark = pytest.mark.integration

_IMPORT_VERSION = "annotation/0008_scoped_legacy_cleaning_import.sql"
_NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _source_dsn() -> str:
    value = os.environ.get("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    return normalize_postgres_dsn(value)


def _database_dsn(base_dsn: str, database_name: str) -> str:
    parsed = urlsplit(base_dsn)
    return parsed._replace(path=f"/{quote(database_name, safe='')}").geturl()


@pytest.fixture
def isolated_legacy_import_dsn() -> Iterator[str]:
    base_dsn = _source_dsn()
    database_name = f"hc_legacy_import_{uuid4().hex[:12]}"
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


async def _bootstrap_before_import(dsn: str) -> None:
    migrations = load_migrations()
    import_index = next(
        index for index, migration in enumerate(migrations) if migration.version == _IMPORT_VERSION
    )
    connection = await asyncpg.connect(normalize_postgres_dsn(dsn))
    try:
        async with connection.transaction():
            await connection.execute(migration_module._TRACKING_TABLE_SQL)
        for migration in migrations[:import_index]:
            await migration_module._apply_pending_migration(connection, migration)
    finally:
        await connection.close()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _seed_scope(
    cursor: psycopg.Cursor[Any],
    *,
    organization_id: str,
    project_id: str,
    dataset_id: str,
    rollout_id: str,
    draft_ids: tuple[str, ...],
) -> None:
    region_code = "cn-test"
    version_id = "version_lance_1"
    episode_id = "episode_source"
    revision_id = "revision_source"
    stream_id = "stream_camera"
    storage_commit_id = _digest(f"{project_id}:storage")
    source_sha256 = _digest(f"{project_id}:source")

    cursor.execute(
        "INSERT INTO registry.organization_projects (organization_id, project_id) VALUES (%s, %s)",
        (organization_id, project_id),
    )
    scope = {
        "organization_id": organization_id,
        "project_id": project_id,
        "region_code": region_code,
    }
    cursor.execute(
        """
        INSERT INTO dataset_registry.datasets (
            organization_id, project_id, region_code, dataset_id, name, description,
            labels, availability, owner_id, owner_display_name, asset_state,
            storage_class, channels, episode_count, pending_review_version_count,
            returned_version_count, actionable_draft_count, version, dataset_document,
            created_at, updated_at, activity_at
        ) VALUES (
            %s, %s, %s, %s, %s, '', '[]'::jsonb, 'ACTIVE', 'owner', 'Owner',
            'READY', 'HOT', '["camera"]'::jsonb, 1, 0, 0, 0, 1, %s::jsonb,
            %s, %s, %s
        )
        """,
        (
            organization_id,
            project_id,
            region_code,
            dataset_id,
            f"Dataset {project_id}",
            json.dumps({"scope": scope, "dataset_id": dataset_id}),
            _NOW,
            _NOW,
            _NOW,
        ),
    )
    cursor.execute(
        """
        INSERT INTO dataset_registry.dataset_versions (
            organization_id, project_id, region_code, dataset_id, version_id,
            display_version, version_kind, version_status, created_at, published_at,
            version_document
        ) VALUES (%s, %s, %s, %s, %s, 'v1', 'RAW', 'READY', %s, %s, %s::jsonb)
        """,
        (
            organization_id,
            project_id,
            region_code,
            dataset_id,
            version_id,
            _NOW,
            _NOW,
            json.dumps(
                {
                    "scope": scope,
                    "dataset_id": dataset_id,
                    "version_id": version_id,
                    "kind": "RAW",
                    "status": "READY",
                }
            ),
        ),
    )
    cursor.execute(
        """
        INSERT INTO dataset_registry.dataset_version_episodes (
            organization_id, project_id, region_code, dataset_id, version_id,
            episode_id, revision_id, ordinal, started_at, started_at_ns, included,
            success_state, review_finding_count, has_finding, episode_document
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, 0, %s, 0, true,
            'SUCCEEDED', 0, false, %s::jsonb
        )
        """,
        (
            organization_id,
            project_id,
            region_code,
            dataset_id,
            version_id,
            episode_id,
            revision_id,
            _NOW,
            json.dumps(
                {
                    "scope": scope,
                    "dataset_id": dataset_id,
                    "version_id": version_id,
                    "episode_id": episode_id,
                    "selected_revision": {
                        "episode_id": episode_id,
                        "revision_id": revision_id,
                        "ordinal": 0,
                        "content_sha256": "a" * 64,
                    },
                }
            ),
        ),
    )
    revision_document = {
        "scope": scope,
        "dataset_id": dataset_id,
        "version_id": version_id,
        "episode_id": episode_id,
        "revision_id": revision_id,
        "ordinal": 0,
        "content_sha256": "a" * 64,
        "started_at_ns": "0",
        "duration_ns": "1000000000",
        "streams": [
            {
                "episode_stream_id": stream_id,
                "channel_path": "camera",
                "kind": "RGB_VIDEO",
                "t_start_ns": "0",
                "t_end_ns": "1000000000",
                "aligned_media_binding": {
                    "rollout_id": rollout_id,
                    "dataset_version": 7,
                    "artifact_id": "aligned-media-legacy-import",
                    "camera_id": "camera",
                    "fps": 30,
                    "start_step": 0,
                    "end_step": 100,
                },
            }
        ],
    }
    cursor.execute(
        """
        INSERT INTO dataset_registry.dataset_version_episode_revisions (
            organization_id, project_id, region_code, dataset_id, version_id,
            episode_id, revision_id, ordinal, revision_document
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s::jsonb)
        """,
        (
            organization_id,
            project_id,
            region_code,
            dataset_id,
            version_id,
            episode_id,
            revision_id,
            json.dumps(revision_document),
        ),
    )
    cursor.execute(
        """
        INSERT INTO lance_schema_snapshots (
            organization_id, project_id, dataset_id, schema_snapshot_id,
            frequency_hz, fields_json, fingerprint, snapshot_json
        ) VALUES (%s, %s, %s, 'schema_1', 100, '[]'::jsonb, %s, '{}'::jsonb)
        """,
        (organization_id, project_id, dataset_id, "b" * 64),
    )
    cursor.execute(
        """
        INSERT INTO lance_datasets (
            organization_id, project_id, dataset_id, schema_snapshot_id, frequency_hz,
            fingerprint, dataset_uri, current_version, created_at, updated_at
        ) VALUES (%s, %s, %s, 'schema_1', 100, %s, %s, 1, %s, %s)
        """,
        (
            organization_id,
            project_id,
            dataset_id,
            "b" * 64,
            f"s3://bucket/{project_id}",
            _NOW,
            _NOW,
        ),
    )
    cursor.execute(
        """
        INSERT INTO lance_dataset_versions (
            organization_id, project_id, dataset_id, version, schema_snapshot_id,
            frequency_hz, fingerprint, content_hash, dataset_uri, lance_version,
            storage_commit_id, rollout_id, source_sha256, converter_version,
            committed_rollouts, receipt_json, created_at
        ) VALUES (
            %s, %s, %s, 1, 'schema_1', 100, %s, %s, %s, 7, %s, %s, %s,
            'converter-1', %s::jsonb, '{}'::jsonb, %s
        )
        """,
        (
            organization_id,
            project_id,
            dataset_id,
            "b" * 64,
            "c" * 64,
            f"s3://bucket/{project_id}",
            storage_commit_id,
            rollout_id,
            source_sha256,
            json.dumps([rollout_id]),
            _NOW,
        ),
    )
    cursor.execute(
        """
        INSERT INTO lance_rollout_lineage (
            organization_id, project_id, dataset_id, rollout_id, version_added,
            source_sha256, converter_version, schema_snapshot_id, fingerprint,
            fragment_uri, fragment_content_hash, step_count, storage_commit_id, created_at
        ) VALUES (
            %s, %s, %s, %s, 1, %s, 'converter-1', 'schema_1', %s, %s, %s, 100, %s, %s
        )
        """,
        (
            organization_id,
            project_id,
            dataset_id,
            rollout_id,
            source_sha256,
            "b" * 64,
            f"s3://bucket/{project_id}/fragment",
            _digest(f"{project_id}:fragment"),
            storage_commit_id,
            _NOW,
        ),
    )

    for draft_index, draft_id in enumerate(draft_ids):
        created_at = _NOW.replace(minute=draft_index)
        cursor.execute(
            """
            INSERT INTO manual_cleaning.cleaning_workbench_drafts (
                organization_id, project_id, region_code, draft_id, origin_type,
                source_issue_id, dataset_id, base_version_id, episode_id,
                base_revision_id, selected_stream_id, start_ns, end_ns,
                schema_snapshot_id, status, workbench_version, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, 'ISSUE_DERIVED', %s, %s, %s, %s, %s, %s,
                0, 1000000000, 'schema_1', 'EDITING', 1, %s, %s
            )
            """,
            (
                organization_id,
                project_id,
                region_code,
                draft_id,
                f"issue_{draft_id}",
                dataset_id,
                version_id,
                episode_id,
                revision_id,
                stream_id,
                created_at,
                created_at,
            ),
        )
        cursor.execute(
            """
            INSERT INTO manual_cleaning.cleaning_draft_edl_revisions (
                organization_id, project_id, region_code, draft_id, edl_revision,
                client_mutation_id, operation_hash, operations_document, actor_id, created_at
            ) VALUES (%s, %s, %s, %s, 0, 'system:seed', %s, '[]'::jsonb, 'system', %s)
            """,
            (
                organization_id,
                project_id,
                region_code,
                draft_id,
                "sha256:" + "0" * 64,
                created_at,
            ),
        )
        operations = [
            {
                "schema_version": "1",
                "id": f"op_{draft_id}",
                "sequence_no": 0,
                "enabled": True,
                "type": "EXCLUDE_RANGE",
                "start_ns": "100000000",
                "end_ns": "200000000",
                "reason": "historic exclusion",
            }
        ]
        cursor.execute(
            """
            INSERT INTO manual_cleaning.cleaning_draft_edl_revisions (
                organization_id, project_id, region_code, draft_id, edl_revision,
                client_mutation_id, operation_hash, operations_document, actor_id, created_at
            ) VALUES (%s, %s, %s, %s, 1, %s, %s, %s::jsonb, 'legacy-user', %s)
            """,
            (
                organization_id,
                project_id,
                region_code,
                draft_id,
                f"mutation_{draft_id}",
                "sha256:" + "1" * 64,
                json.dumps(operations),
                created_at.replace(second=1),
            ),
        )


async def _reapply_import_sql(dsn: str) -> None:
    migration = next(
        migration for migration in load_migrations() if migration.version == _IMPORT_VERSION
    )
    connection = await asyncpg.connect(normalize_postgres_dsn(dsn))
    try:
        async with connection.transaction():
            await connection.execute(migration.sql)
    finally:
        await connection.close()


def _repository_connection(
    dsn: str,
    *,
    organization_id: str,
    project_id: str,
) -> DbApiConnection:
    connection = cast(Any, psycopg.connect(dsn))
    connection.execute(
        """
        SELECT set_config('app.organization_id', %s, false),
               set_config('app.project_id', %s, false),
               set_config('app.region_code', 'cn-test', false),
               set_config('app.subject_id', 'legacy-import-verifier', false),
               set_config('app.request_id', 'legacy-import-verifier', false)
        """,
        (organization_id, project_id),
    )
    return cast(DbApiConnection, connection)


def test_existing_scoped_cleaning_rows_import_once_and_unresolved_lineage_fails_closed(
    isolated_legacy_import_dsn: str,
) -> None:
    dsn = isolated_legacy_import_dsn
    asyncio.run(_bootstrap_before_import(dsn))
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        _seed_scope(
            cursor,
            organization_id="org_alpha",
            project_id="project_alpha",
            dataset_id="dataset_alpha",
            rollout_id="rollout_alpha",
            draft_ids=("draft_shared", "draft_second"),
        )
        _seed_scope(
            cursor,
            organization_id="org_beta",
            project_id="project_beta",
            dataset_id="dataset_beta",
            rollout_id="rollout_beta",
            draft_ids=("draft_shared",),
        )

    migrations = load_migrations()
    import_index = next(
        index for index, migration in enumerate(migrations) if migration.version == _IMPORT_VERSION
    )
    assert asyncio.run(apply_migrations(dsn)) == [
        migration.version for migration in migrations[import_index:]
    ]
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            "SELECT current_revision, state_version FROM annotation.annotation_tasks "
            "WHERE source_workflow_id LIKE 'legacy-cleaning-import:v1:%' "
            "ORDER BY organization_id"
        ).fetchall() == [(4, 4), (2, 2)]
        assert connection.execute(
            "SELECT count(*) FROM annotation.legacy_cleaning_migrations"
        ).fetchone() == (6,)
        assert connection.execute(
            "SELECT count(DISTINCT organization_id) "
            "FROM annotation.legacy_cleaning_migrations "
            "WHERE source_draft_id = 'draft_shared'"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT count(*) FROM annotation.annotation_operations"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM core.audit_events "
            "WHERE action = 'annotation.legacy_cleaning.imported'"
        ).fetchone() == (6,)
        source_payload = connection.execute(
            """
            SELECT legacy_audit -> 'source_payload'
            FROM annotation.annotation_revisions
            WHERE origin = 'LEGACY_CLEANING'
              AND legacy_audit ->> 'draft_id' = 'draft_second'
              AND (legacy_audit ->> 'source_revision')::bigint = 1
            """
        ).fetchone()[0]
        assert source_payload["migration_mode"] == "PRESERVED_NANOSECOND_EDL"
        assert source_payload["operations"][0]["start_ns"] == "100000000"

    service = AnnotationService(
        PostgresAnnotationRepository(
            lambda: _repository_connection(
                dsn,
                organization_id="org_alpha",
                project_id="project_alpha",
            )
        ),
        cursor_secret="legacy-import-filter-secret",
    )
    actor = AnnotationActor(
        actor_id="legacy-import-verifier",
        capabilities=frozenset(
            {
                "annotation_task.read",
                "annotation_task.claim",
                "annotation_task.assign",
                "annotation.edit",
                "annotation.save",
                "annotation.submit",
            }
        ),
        project_ids=frozenset({"project_alpha"}),
    )
    for draft_id in ("draft_shared", "draft_second"):
        page = service.list_revision_threads(
            project_id="project_alpha",
            region_code="cn-test",
            actor=actor,
            request_id=f"filter-{draft_id}",
            origin=RevisionOrigin.LEGACY_CLEANING,
            legacy_draft_id=draft_id,
            limit=10,
        )
        assert len(page.items) == 1
        assert page.items[0].legacy_draft_id == draft_id
        assert page.items[0].latest_revision.revision == 4

    asyncio.run(_reapply_import_sql(dsn))
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            "SELECT count(*) FROM annotation.legacy_cleaning_migrations"
        ).fetchone() == (6,)
        assert connection.execute(
            "SELECT count(*) FROM core.audit_events "
            "WHERE action = 'annotation.legacy_cleaning.imported'"
        ).fetchone() == (6,)

        created_at = _NOW.replace(hour=13)
        connection.execute(
            """
            INSERT INTO manual_cleaning.cleaning_workbench_drafts (
                organization_id, project_id, region_code, draft_id, origin_type,
                source_issue_id, dataset_id, base_version_id, episode_id,
                base_revision_id, selected_stream_id, start_ns, end_ns,
                schema_snapshot_id, status, workbench_version, created_at, updated_at
            ) VALUES (
                'org_alpha', 'project_alpha', 'cn-test', 'draft_unmapped',
                'ISSUE_DERIVED', 'issue_unmapped', 'dataset_alpha', 'version_lance_1',
                'episode_source', 'revision_source', 'stream_missing', 0, 1000000000,
                'schema_1', 'EDITING', 1, %s, %s
            )
            """,
            (created_at, created_at),
        )
        connection.execute(
            """
            INSERT INTO manual_cleaning.cleaning_draft_edl_revisions (
                organization_id, project_id, region_code, draft_id, edl_revision,
                client_mutation_id, operation_hash, operations_document, actor_id, created_at
            ) VALUES (
                'org_alpha', 'project_alpha', 'cn-test', 'draft_unmapped', 0,
                'system:seed', %s, '[]'::jsonb, 'system', %s
            )
            """,
            ("sha256:" + "0" * 64, created_at),
        )

    with pytest.raises(asyncpg.RaiseError, match="one exact selected-stream Lance lineage"):
        asyncio.run(_reapply_import_sql(dsn))
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            "SELECT count(*) FROM annotation.legacy_cleaning_migrations"
        ).fetchone() == (6,)
        assert connection.execute(
            "SELECT count(*) FROM annotation.annotation_tasks "
            "WHERE source_workflow_id LIKE 'legacy-cleaning-import:v1:%'"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT count(*) FROM core.audit_events "
            "WHERE action = 'annotation.legacy_cleaning.imported'"
        ).fetchone() == (6,)
