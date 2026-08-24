from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import cast

import pytest

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.dataset_registry.models import (
    CreateDatasetCommand,
    DatasetPageMutationRecord,
    DatasetPageRecord,
)
from hc_data_platform.dataset_registry.repository import (
    DatasetPageFilters,
    DbApiConnection,
    PostgresDatasetPageRepository,
)
from hc_data_platform.dataset_registry.service import DatasetPageService
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.psycopg import PsycopgIdempotencyStore

psycopg = pytest.importorskip("psycopg")

pytestmark = pytest.mark.integration

PROJECT_ID = "p05-dataset-page-integration-project"
REGION_CODE = "p05-dataset-page-integration-region"
ORGANIZATION_ID = "p05-dataset-page-integration-organization"
FOREIGN_PROJECT_ID = "p05-dataset-page-foreign-project"
FOREIGN_ORGANIZATION_ID = "p05-dataset-page-foreign-organization"
APP_ROLE = "p05_dataset_page_writer"
APP_PASSWORD = "p05-test-only-writer-password"
NOW = datetime(2026, 8, 19, 15, tzinfo=timezone.utc)


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
        subject_id="p05-dataset-page-writer",
        project_ids=frozenset({PROJECT_ID}),
        region_codes=frozenset({REGION_CODE}),
        roles=frozenset(),
        scope_pairs=frozenset({(PROJECT_ID, REGION_CODE)}),
        scoped_capabilities=frozenset(
            {(PROJECT_ID, "dataset.read"), (PROJECT_ID, "dataset.create")}
        ),
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


def _prepare_app_role(dsn: str) -> None:
    _drop_app_role(dsn)
    with psycopg.connect(dsn, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute(
            psycopg.sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER").format(
                psycopg.sql.Identifier(APP_ROLE), psycopg.sql.Literal(APP_PASSWORD)
            )
        )
        cursor.execute(
            "GRANT USAGE ON SCHEMA core, registry, dataset_registry TO p05_dataset_page_writer"
        )
        cursor.execute("GRANT SELECT ON registry.organization_projects TO p05_dataset_page_writer")
        cursor.execute(
            "GRANT SELECT, INSERT ON dataset_registry.datasets TO p05_dataset_page_writer"
        )
        cursor.execute("GRANT INSERT ON core.audit_events TO p05_dataset_page_writer")
        cursor.execute(
            "GRANT SELECT, INSERT, UPDATE ON core.idempotency_records TO p05_dataset_page_writer"
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
            "DELETE FROM dataset_registry.datasets WHERE project_id IN (%s, %s)",
            (PROJECT_ID, FOREIGN_PROJECT_ID),
        )
        cursor.execute(
            "DELETE FROM registry.organization_projects WHERE organization_id IN (%s, %s)",
            (ORGANIZATION_ID, FOREIGN_ORGANIZATION_ID),
        )


def _connection_factory(dsn: str) -> Callable[[], DbApiConnection]:
    return cast(Callable[[], DbApiConnection], psycopg_connection_factory(dsn))


def _service(dsn: str) -> DatasetPageService:
    connection_factory = _connection_factory(dsn)
    return DatasetPageService(
        PostgresDatasetPageRepository(connection_factory),
        cursor_secret="p05-postgres-cursor-secret",
        idempotency=PsycopgIdempotencyStore(
            connection_factory,
            response_decoder=DatasetPageMutationRecord.model_validate,
        ),
        clock=lambda: NOW,
    )


def _request_context() -> RequestContext:
    return RequestContext(
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
        subject_id="p05-dataset-page-writer",
        request_id="p05-dataset-page-postgres-integration",
    )


def _command() -> CreateDatasetCommand:
    return CreateDatasetCommand(
        name="P05 PostgreSQL Dataset",
        description="P05 durable dataset-page aggregate",
        labels=("p05", "postgres"),
    )


def test_postgres_dataset_page_is_rls_scoped_audited_and_replayable() -> None:
    superuser_dsn = _superuser_dsn()
    _cleanup(superuser_dsn)
    _prepare_app_role(superuser_dsn)
    try:
        with psycopg.connect(superuser_dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO registry.organization_projects (organization_id, project_id)
                VALUES (%s, %s)
                """,
                (ORGANIZATION_ID, PROJECT_ID),
            )
            cursor.execute(
                """
                INSERT INTO registry.organization_projects (organization_id, project_id)
                VALUES (%s, %s)
                """,
                (FOREIGN_ORGANIZATION_ID, FOREIGN_PROJECT_ID),
            )
            foreign = DatasetPageRecord.model_validate(
                {
                    "scope": {
                        "organization_id": FOREIGN_ORGANIZATION_ID,
                        "project_id": FOREIGN_PROJECT_ID,
                        "region_code": "p05-foreign-region",
                    },
                    "dataset_id": "dataset_p05foreign",
                    "name": "P05 Foreign Dataset",
                    "description": "foreign control row",
                    "labels": [],
                    "availability": "ACTIVE",
                    "owner": {"id": "foreign-owner", "display_name": "Foreign Owner"},
                    "created_at": NOW,
                    "updated_at": NOW,
                    "activity_at": NOW,
                    "etag": '"v1"',
                    "metadata": {"asset_state": "EMPTY", "storage_class": "STANDARD"},
                }
            )
            cursor.execute(
                """
                INSERT INTO dataset_registry.datasets (
                    organization_id, project_id, region_code, dataset_id, name, description,
                    labels, availability, owner_id, owner_display_name, asset_state, storage_class,
                    channels, episode_count, pending_review_version_count, returned_version_count,
                    actionable_draft_count, version, dataset_document, created_at, updated_at,
                    activity_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, '[]'::jsonb, %s, %s, %s, %s, %s, '[]'::jsonb,
                    0, 0, 0, 0, 1, %s::jsonb, %s, %s, %s
                )
                """,
                (
                    FOREIGN_ORGANIZATION_ID,
                    FOREIGN_PROJECT_ID,
                    "p05-foreign-region",
                    foreign.dataset_id,
                    foreign.name,
                    foreign.description,
                    foreign.availability,
                    foreign.owner.id,
                    foreign.owner.display_name,
                    foreign.metadata.asset_state,
                    foreign.metadata.storage_class,
                    foreign.model_dump_json(),
                    NOW,
                    NOW,
                    NOW,
                ),
            )

        token = bind_request_context(_request_context())
        try:
            service = _service(_app_dsn(superuser_dsn))
            created = service.create_dataset(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                command=_command(),
                idempotency_key="p05-postgres-create",
                request_id="p05-postgres-create",
            )
            assert created.replayed is False
            assert created.record.dataset.dataset_id.startswith("dataset_")

            replay = service.create_dataset(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                command=_command(),
                idempotency_key="p05-postgres-create",
                request_id="p05-postgres-create-replay",
            )
            assert replay.replayed is True
            assert replay.record.dataset == created.record.dataset

            page = service.list_datasets(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                filters=DatasetPageFilters(),
                sort="activity_at:desc,dataset_id:desc",
                after=None,
                before=None,
                limit=20,
                request_id="p05-postgres-list",
            )
            assert [item.dataset_id for item in page.items] == [created.record.dataset.dataset_id]
        finally:
            reset_request_context(token)

        with psycopg.connect(superuser_dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT dataset_document::text,
                       (SELECT count(*) FROM core.idempotency_records WHERE project_id = %s),
                       (SELECT count(*) FROM core.audit_events WHERE project_id = %s)
                  FROM dataset_registry.datasets
                 WHERE project_id = %s AND region_code = %s
                """,
                (PROJECT_ID, PROJECT_ID, PROJECT_ID, REGION_CODE),
            )
            document, idempotency_count, audit_count = cursor.fetchone()
            assert "credential_input" not in document
            assert "secret" not in document
            assert idempotency_count == 1
            assert audit_count >= 2

        token = bind_request_context(_request_context())
        try:
            connection = _connection_factory(_app_dsn(superuser_dsn))()
            try:
                cursor = connection.cursor()
                try:
                    cursor.execute("SELECT count(*) FROM dataset_registry.datasets")
                    result = cursor.fetchone()
                    assert isinstance(result, tuple)
                    assert result[0] == 1
                finally:
                    cursor.close()
            finally:
                connection.close()
        finally:
            reset_request_context(token)
    finally:
        _cleanup(superuser_dsn)
        _drop_app_role(superuser_dsn)
