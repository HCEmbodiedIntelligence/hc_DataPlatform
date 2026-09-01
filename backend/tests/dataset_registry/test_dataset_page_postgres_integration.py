from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, cast

import pytest

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.dataset_registry.models import (
    CreateDatasetCommand,
    DatasetPageActor,
    DatasetPageEpisodeRecord,
    DatasetPageMetadata,
    DatasetPageMutationRecord,
    DatasetPageRecord,
    DatasetPageRevisionSnapshotReference,
    DatasetPageScope,
)
from hc_data_platform.dataset_registry.repository import (
    DatasetPageFilters,
    DbApiConnection,
    InMemoryDatasetPageRepository,
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
            "GRANT USAGE ON SCHEMA core, registry, dataset_registry, collection_tasks "
            "TO p05_dataset_page_writer"
        )
        cursor.execute("GRANT SELECT ON registry.organization_projects TO p05_dataset_page_writer")
        cursor.execute(
            "GRANT SELECT, INSERT ON dataset_registry.datasets TO p05_dataset_page_writer"
        )
        cursor.execute(
            "GRANT SELECT ON dataset_registry.dataset_version_episodes TO p05_dataset_page_writer"
        )
        cursor.execute(
            "GRANT SELECT ON collection_tasks.collection_tasks TO p05_dataset_page_writer"
        )
        cursor.execute("GRANT INSERT ON core.audit_events TO p05_dataset_page_writer")
        cursor.execute(
            "GRANT SELECT, INSERT, UPDATE ON core.idempotency_records TO p05_dataset_page_writer"
        )


def _cleanup(dsn: str) -> None:
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM dataset_registry.dataset_version_episodes WHERE project_id IN (%s, %s)",
            (PROJECT_ID, FOREIGN_PROJECT_ID),
        )
        cursor.execute(
            "DELETE FROM dataset_registry.dataset_versions WHERE project_id IN (%s, %s)",
            (PROJECT_ID, FOREIGN_PROJECT_ID),
        )
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


def _filter_record(
    *,
    dataset_id: str,
    name: str,
    task: str,
    pending: str = "0",
    returned: str = "0",
    draft: str = "0",
) -> DatasetPageRecord:
    scope = DatasetPageScope(
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
    )
    return DatasetPageRecord(
        scope=scope,
        dataset_id=dataset_id,
        name=name,
        description="PostgreSQL filter semantics",
        availability="ACTIVE",
        owner=DatasetPageActor(id="filter-owner", display_name="Filter Owner"),
        created_at=NOW,
        updated_at=NOW,
        activity_at=NOW,
        etag='"v1"',
        metadata=DatasetPageMetadata(
            robot_model_id="model-filter",
            robot_id=f"robot-{dataset_id}",
            task=task,
            scene="lab",
            asset_state="READY",
            storage_class="STANDARD",
        ),
        pending_review_version_count=pending,
        returned_version_count=returned,
        actionable_draft_count=draft,
    )


def _filter_episode(*, dataset_id: str, episode_id: str, ordinal: int) -> DatasetPageEpisodeRecord:
    return DatasetPageEpisodeRecord(
        scope=DatasetPageScope(
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
        ),
        dataset_id=dataset_id,
        version_id="version_filterepisode",
        episode_id=episode_id,
        selected_revision=DatasetPageRevisionSnapshotReference(
            episode_id=episode_id,
            revision_id=f"revision_filterepisode{ordinal}",
            ordinal=ordinal,
            content_sha256=str(ordinal + 1) * 64,
        ),
        included=True,
        success_state="SUCCEEDED",
        task="robot-picking-task",
    )


def _insert_filter_record(cursor: Any, record: DatasetPageRecord) -> None:
    cursor.execute(
        """
        INSERT INTO dataset_registry.datasets (
            organization_id, project_id, region_code, dataset_id, name, description,
            labels, availability, owner_id, owner_display_name, robot_model_id, robot_id,
            task, scene, asset_state, storage_class, channels, episode_count,
            pending_review_version_count, returned_version_count, actionable_draft_count,
            version, dataset_document, created_at, updated_at, activity_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, '[]'::jsonb, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, '[]'::jsonb, %s, %s, %s, %s, 1, %s::jsonb, %s, %s, %s
        )
        """,
        (
            record.scope.organization_id,
            record.scope.project_id,
            record.scope.region_code,
            record.dataset_id,
            record.name,
            record.description,
            record.availability,
            record.owner.id,
            record.owner.display_name,
            record.metadata.robot_model_id,
            record.metadata.robot_id,
            record.metadata.task,
            record.metadata.scene,
            record.metadata.asset_state,
            record.metadata.storage_class,
            int(record.episode_count),
            int(record.pending_review_version_count),
            int(record.returned_version_count),
            int(record.actionable_draft_count),
            record.model_dump_json(),
            record.created_at,
            record.updated_at,
            record.activity_at,
        ),
    )


def _insert_filter_episode(cursor: Any, episode: DatasetPageEpisodeRecord) -> None:
    version_document = {
        "scope": episode.scope.model_dump(mode="json"),
        "dataset_id": episode.dataset_id,
        "version_id": episode.version_id,
        "display_version": "v1",
        "kind": "RAW",
        "status": "REVIEWING",
    }
    cursor.execute(
        """
        INSERT INTO dataset_registry.dataset_versions (
            organization_id, project_id, region_code, dataset_id, version_id,
            display_version, version_kind, version_status, created_at, version_document
        ) VALUES (%s, %s, %s, %s, %s, 'v1', 'RAW', 'REVIEWING', %s, %s::jsonb)
        ON CONFLICT DO NOTHING
        """,
        (
            episode.scope.organization_id,
            episode.scope.project_id,
            episode.scope.region_code,
            episode.dataset_id,
            episode.version_id,
            NOW,
            psycopg.types.json.Jsonb(version_document),
        ),
    )
    cursor.execute(
        """
        INSERT INTO dataset_registry.dataset_version_episodes (
            organization_id, project_id, region_code, dataset_id, version_id, episode_id,
            revision_id, ordinal, started_at_ns, included, success_state, task,
            review_finding_count, has_finding, episode_document
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s, 0, false, %s::jsonb
        )
        """,
        (
            episode.scope.organization_id,
            episode.scope.project_id,
            episode.scope.region_code,
            episode.dataset_id,
            episode.version_id,
            episode.episode_id,
            episode.selected_revision.revision_id,
            episode.selected_revision.ordinal,
            episode.included,
            episode.success_state,
            episode.task,
            episode.model_dump_json(),
        ),
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


def test_postgres_and_memory_dataset_filters_have_identical_task_and_workflow_semantics() -> None:
    superuser_dsn = _superuser_dsn()
    _cleanup(superuser_dsn)
    _prepare_app_role(superuser_dsn)
    records = (
        _filter_record(
            dataset_id="dataset_filterpending",
            name="Filter pending",
            task="PickBox",
            pending="1",
        ),
        _filter_record(
            dataset_id="dataset_filterepisode",
            name="Filter episode",
            task="unrelated",
            returned="2",
        ),
        _filter_record(
            dataset_id="dataset_filterliteral",
            name="Filter literal",
            task="literal-%_task",
            draft="3",
        ),
    )
    episodes = (
        _filter_episode(
            dataset_id="dataset_filterepisode",
            episode_id="episode_filterepisode0",
            ordinal=0,
        ),
        _filter_episode(
            dataset_id="dataset_filterepisode",
            episode_id="episode_filterepisode1",
            ordinal=1,
        ),
    )
    try:
        with psycopg.connect(superuser_dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO registry.organization_projects (organization_id, project_id) "
                "VALUES (%s, %s)",
                (ORGANIZATION_ID, PROJECT_ID),
            )
            for record in records:
                _insert_filter_record(cursor, record)
            for episode in episodes:
                _insert_filter_episode(cursor, episode)

        scope = records[0].scope
        memory = InMemoryDatasetPageRepository(records=records, episodes=episodes)
        token = bind_request_context(_request_context())
        try:
            postgres = PostgresDatasetPageRepository(_connection_factory(_app_dsn(superuser_dsn)))
            filters_to_compare = (
                DatasetPageFilters(task="PICK"),
                DatasetPageFilters(task="%_"),
                DatasetPageFilters(workflow_state="pendingReview"),
                DatasetPageFilters(workflow_state="returned"),
                DatasetPageFilters(workflow_state="actionableDraft"),
                DatasetPageFilters(
                    task="pick",
                    workflow_state="pendingReview",
                    asset_state="READY",
                    created_from=NOW.date(),
                    created_to=NOW.date(),
                ),
            )
            for filters in filters_to_compare:
                memory_ids = {
                    record.dataset_id
                    for record in memory.list_records(scope=scope, filters=filters)
                }
                postgres_records = postgres.list_records(scope=scope, filters=filters)
                postgres_ids = [record.dataset_id for record in postgres_records]
                assert set(postgres_ids) == memory_ids
                assert len(postgres_ids) == len(set(postgres_ids))
        finally:
            reset_request_context(token)
    finally:
        _cleanup(superuser_dsn)
        _drop_app_role(superuser_dsn)
