from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime, timezone

import pytest

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.data_sources.models import (
    CreateDataSourceCommand,
    DataSourceMutationRecord,
)
from hc_data_platform.data_sources.models import (
    TestConnectionCommand as ConnectionTestCommand,
)
from hc_data_platform.data_sources.repository import PostgresDataSourceRepository
from hc_data_platform.data_sources.service import DataSourceService
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.psycopg import PsycopgIdempotencyStore

psycopg = pytest.importorskip("psycopg")

pytestmark = pytest.mark.integration

PROJECT_ID = "p02-data-source-integration-project"
REGION_CODE = "p02-data-source-integration-region"
ORGANIZATION_ID = "p02-data-source-integration-organization"
FOREIGN_PROJECT_ID = "p02-data-source-foreign-project"
FOREIGN_ORGANIZATION_ID = "p02-data-source-foreign-organization"
SAME_PROJECT_FOREIGN_ORGANIZATION_ID = "p02-data-source-same-project-foreign-organization"
ROBOT_ID = "p02-data-source-integration-robot"
APP_ROLE = "p02_data_source_writer"
APP_PASSWORD = "p02-test-only-writer-password"
CREDENTIAL_KEY = "p02-postgres-credential-key"
NOW = datetime(2026, 8, 19, 12, tzinfo=timezone.utc)


def _superuser_dsn() -> str:
    value = os.getenv("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


def _app_dsn(superuser_dsn: str) -> str:
    _credentials, separator, address = superuser_dsn.rpartition("@")
    assert separator
    return f"postgresql://{APP_ROLE}:{APP_PASSWORD}@{address}"


def _auth() -> AuthContext:
    return AuthContext(
        subject_id="p02-data-source-writer",
        organization_ids=frozenset({ORGANIZATION_ID}),
        project_ids=frozenset({PROJECT_ID}),
        region_codes=frozenset({REGION_CODE}),
        roles=frozenset(),
        scope_pairs=frozenset({(PROJECT_ID, REGION_CODE)}),
        organization_scope_triples=frozenset({(ORGANIZATION_ID, PROJECT_ID, REGION_CODE)}),
        scoped_capabilities=frozenset(
            {
                (PROJECT_ID, "ingest_source.read"),
                (PROJECT_ID, "ingest_source.manage"),
            }
        ),
        organization_scoped_capabilities=frozenset(
            {
                (ORGANIZATION_ID, PROJECT_ID, "ingest_source.read"),
                (ORGANIZATION_ID, PROJECT_ID, "ingest_source.manage"),
            }
        ),
    )


def _prepare_app_role(dsn: str) -> None:
    _drop_app_role(dsn)
    with psycopg.connect(dsn, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute(
            psycopg.sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER").format(
                psycopg.sql.Identifier(APP_ROLE), psycopg.sql.Literal(APP_PASSWORD)
            )
        )
        cursor.execute(
            "GRANT USAGE ON SCHEMA core, ingest, registry, robotics TO p02_data_source_writer"
        )
        cursor.execute("GRANT SELECT ON registry.organization_projects TO p02_data_source_writer")
        cursor.execute(
            "GRANT SELECT ON robotics.robot_assets, robotics.project_robot_assignments "
            "TO p02_data_source_writer"
        )
        cursor.execute(
            "GRANT SELECT, INSERT, UPDATE ON ingest.data_sources TO p02_data_source_writer"
        )
        cursor.execute(
            "GRANT SELECT, INSERT ON ingest.data_source_credentials TO p02_data_source_writer"
        )
        cursor.execute(
            "GRANT SELECT, INSERT ON ingest.data_source_connection_test_jobs "
            "TO p02_data_source_writer"
        )
        cursor.execute("GRANT INSERT ON core.audit_events TO p02_data_source_writer")
        cursor.execute(
            "GRANT SELECT, INSERT, UPDATE ON core.idempotency_records TO p02_data_source_writer"
        )


def _drop_app_role(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,))
        if cursor.fetchone() is None:
            return
        cursor.execute(psycopg.sql.SQL("DROP OWNED BY {}").format(psycopg.sql.Identifier(APP_ROLE)))
        cursor.execute(
            psycopg.sql.SQL("DROP ROLE IF EXISTS {}").format(psycopg.sql.Identifier(APP_ROLE))
        )


def _cleanup(dsn: str) -> None:
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM core.audit_integrity_entries WHERE project_id IN (%s, %s)",
            (PROJECT_ID, FOREIGN_PROJECT_ID),
        )
        cursor.execute(
            "DELETE FROM core.audit_events WHERE project_id IN (%s, %s)",
            (PROJECT_ID, FOREIGN_PROJECT_ID),
        )
        cursor.execute(
            "DELETE FROM core.audit_integrity_heads WHERE project_id IN (%s, %s)",
            (PROJECT_ID, FOREIGN_PROJECT_ID),
        )
        cursor.execute(
            "DELETE FROM core.idempotency_records WHERE project_id = %s AND region_code = %s",
            (PROJECT_ID, REGION_CODE),
        )
        cursor.execute(
            "DELETE FROM ingest.data_source_connection_test_jobs WHERE project_id IN (%s, %s)",
            (PROJECT_ID, FOREIGN_PROJECT_ID),
        )
        cursor.execute(
            "DELETE FROM ingest.data_source_credentials WHERE project_id IN (%s, %s)",
            (PROJECT_ID, FOREIGN_PROJECT_ID),
        )
        cursor.execute(
            "DELETE FROM ingest.data_sources WHERE project_id IN (%s, %s)",
            (PROJECT_ID, FOREIGN_PROJECT_ID),
        )
        cursor.execute(
            "DELETE FROM robotics.project_robot_assignments "
            "WHERE project_id = %s AND region_code = %s",
            (PROJECT_ID, REGION_CODE),
        )
        cursor.execute(
            "DELETE FROM robotics.robot_assets WHERE organization_id = %s AND robot_id = %s",
            (ORGANIZATION_ID, ROBOT_ID),
        )
        cursor.execute(
            "DELETE FROM registry.organization_projects WHERE organization_id = ANY(%s)",
            (
                [
                    ORGANIZATION_ID,
                    FOREIGN_ORGANIZATION_ID,
                    SAME_PROJECT_FOREIGN_ORGANIZATION_ID,
                ],
            ),
        )


def _connection_factory(dsn: str) -> Callable[[], object]:
    return psycopg_connection_factory(dsn)


def _service(dsn: str) -> DataSourceService:
    connection_factory = _connection_factory(dsn)
    return DataSourceService(
        PostgresDataSourceRepository(connection_factory),
        cursor_secret="p02-postgres-cursor-secret",
        credential_key=CREDENTIAL_KEY,
        idempotency=PsycopgIdempotencyStore(
            connection_factory,
            response_decoder=DataSourceMutationRecord.model_validate,
        ),
        clock=lambda: NOW,
    )


def _request_context() -> RequestContext:
    return RequestContext(
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
        subject_id="p02-data-source-writer",
        request_id="p02-data-source-postgres-integration",
    )


def _command() -> CreateDataSourceCommand:
    return CreateDataSourceCommand.model_validate(
        {
            "name": "P02 PostgreSQL 数据源",
            "source_type": "ROBOT",
            "source_format": "MCAP",
            "binding": {"kind": "ROBOT", "robot_id": ROBOT_ID},
            "configuration": {"kind": "ROBOT", "transport": "HTTPS"},
            "upload_policy_code": "STANDARD",
            "credential_input": {"kind": "TOKEN", "token": "p02-postgres-secret"},
        }
    )


def test_postgres_data_source_is_rls_scoped_encrypted_audited_and_replayable() -> None:
    superuser_dsn = _superuser_dsn()
    _cleanup(superuser_dsn)
    _prepare_app_role(superuser_dsn)
    try:
        with psycopg.connect(superuser_dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO registry.organization_projects "
                "(organization_id, project_id, display_name) VALUES (%s, %s, %s)",
                (ORGANIZATION_ID, PROJECT_ID, "P02 集成项目"),
            )
            cursor.execute(
                """
                INSERT INTO robotics.robot_assets (
                    organization_id, robot_id, display_name, serial_no,
                    lifecycle_status, connectivity_state, etag, topology_revision, allowed_actions
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '[]'::jsonb)
                """,
                (
                    ORGANIZATION_ID,
                    ROBOT_ID,
                    "P02 PostgreSQL 机器人",
                    "P02-PG-SN",
                    "ACTIVE",
                    "ONLINE",
                    '"p02-robot:1"',
                    "p02-topology:1",
                ),
            )
            cursor.execute(
                """
                INSERT INTO robotics.project_robot_assignments (
                    organization_id, project_id, region_code, robot_id, assigned_by
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (ORGANIZATION_ID, PROJECT_ID, REGION_CODE, ROBOT_ID, "p02-test"),
            )
            cursor.execute(
                "INSERT INTO registry.organization_projects "
                "(organization_id, project_id, display_name) VALUES (%s, %s, %s)",
                (FOREIGN_ORGANIZATION_ID, FOREIGN_PROJECT_ID, "P02 外部项目"),
            )
            cursor.execute(
                "INSERT INTO registry.organization_projects "
                "(organization_id, project_id, display_name) VALUES (%s, %s, %s)",
                (SAME_PROJECT_FOREIGN_ORGANIZATION_ID, PROJECT_ID, "P02 同名外部项目"),
            )
            cursor.execute(
                """
                INSERT INTO ingest.data_sources (
                    organization_id, project_id, region_code, source_id, name, source_type,
                    source_format, administrative_state, connectivity_state, credential_state,
                    version, source_document, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, 'foreign-source', 'Foreign source', 'EDGE_AGENT', 'MCAP',
                    'ENABLED', 'UNKNOWN', 'MISSING', 1,
                    jsonb_build_object(
                        'id', 'foreign-source',
                        'source_type', 'EDGE_AGENT',
                        'administrative_state', 'ENABLED',
                        'binding', jsonb_build_object('kind', 'EDGE_AGENT'),
                        'configuration', jsonb_build_object('kind', 'EDGE_AGENT')
                    ),
                    %s, %s
                )
                """,
                (FOREIGN_ORGANIZATION_ID, FOREIGN_PROJECT_ID, "foreign-region", NOW, NOW),
            )
            cursor.execute(
                """
                INSERT INTO ingest.data_sources (
                    organization_id, project_id, region_code, source_id, name, source_type,
                    source_format, administrative_state, connectivity_state, credential_state,
                    version, source_document, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, 'same-project-foreign-source', 'Same project foreign source',
                    'EDGE_AGENT', 'MCAP', 'ENABLED', 'UNKNOWN', 'MISSING', 1,
                    jsonb_build_object(
                        'id', 'same-project-foreign-source',
                        'source_type', 'EDGE_AGENT',
                        'administrative_state', 'ENABLED',
                        'binding', jsonb_build_object('kind', 'EDGE_AGENT'),
                        'configuration', jsonb_build_object('kind', 'EDGE_AGENT')
                    ),
                    %s, %s
                )
                """,
                (
                    SAME_PROJECT_FOREIGN_ORGANIZATION_ID,
                    PROJECT_ID,
                    REGION_CODE,
                    NOW,
                    NOW,
                ),
            )

        token = bind_request_context(_request_context())
        try:
            service = _service(_app_dsn(superuser_dsn))
            created = service.create_source(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                command=_command(),
                idempotency_key="p02-postgres-create",
                request_id="p02-postgres-create",
            )
            source = created.record.source
            assert source is not None
            assert created.replayed is False

            replay = service.create_source(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                command=_command(),
                idempotency_key="p02-postgres-create",
                request_id="p02-postgres-create-replay",
            )
            assert replay.replayed is True
            assert replay.record.source == source

            tested = service.test_connection(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                source_id=source.id,
                command=ConnectionTestCommand(
                    observed_config_version=source.config_version,
                    observed_credential_version=source.credential_version,
                ),
                if_match=source.etag,
                idempotency_key="p02-postgres-test",
                request_id="p02-postgres-test",
            )
            assert tested.record.async_job is not None
            assert tested.record.async_job.status == "SUCCEEDED"

            page = service.list_sources(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                query=None,
                source_types=(),
                administrative_states=(),
                connectivity_states=(),
                credential_states=(),
                sort="updated_at:desc,id:desc",
                after=None,
                before=None,
                limit=20,
                request_id="p02-postgres-list",
            )
            assert [item.id for item in page.items] == [source.id]
        finally:
            reset_request_context(token)

        with psycopg.connect(superuser_dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT source_document::text,
                       (SELECT count(*) FROM ingest.data_source_credentials WHERE project_id = %s),
                       (
                           SELECT count(*)
                           FROM ingest.data_source_connection_test_jobs
                           WHERE project_id = %s
                       ),
                       (SELECT count(*) FROM core.audit_events WHERE project_id = %s)
                  FROM ingest.data_sources
                 WHERE project_id = %s AND region_code = %s
                """,
                (PROJECT_ID, PROJECT_ID, PROJECT_ID, PROJECT_ID, REGION_CODE),
            )
            source_document, credential_count, job_count, audit_count = cursor.fetchone()
            assert "p02-postgres-secret" not in source_document
            assert "credential_input" not in source_document
            assert credential_count == 1
            assert job_count == 1
            assert audit_count >= 3

        token = bind_request_context(_request_context())
        try:
            connection = _connection_factory(_app_dsn(superuser_dsn))()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT count(*) FROM ingest.data_sources")
                    # This includes an identically scoped project/region row under a
                    # different organization.  It must remain invisible to the app role.
                    assert cursor.fetchone()[0] == 1
            finally:
                connection.close()
        finally:
            reset_request_context(token)
    finally:
        _cleanup(superuser_dsn)
        _drop_app_role(superuser_dsn)
