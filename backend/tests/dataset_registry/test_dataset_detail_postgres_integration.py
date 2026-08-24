from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import pytest

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.dataset_registry.models import (
    DatasetPageActor,
    DatasetPageContentReference,
    DatasetPageDetailFacts,
    DatasetPageDetailSummary,
    DatasetPageEpisodeRecord,
    DatasetPageMetadata,
    DatasetPageRecord,
    DatasetPageReviewingVersion,
    DatasetPageRevisionSnapshotReference,
    DatasetPageScope,
    DatasetPageSourceProvenance,
    DatasetPageVersionCapacityFacts,
    DatasetPageVersionSchemaSummary,
)
from hc_data_platform.dataset_registry.repository import (
    DatasetPageEpisodeFilters,
    DatasetPageSourceProvenanceFilters,
    DatasetPageVersionFilters,
    DbApiConnection,
    PostgresDatasetPageRepository,
)
from hc_data_platform.dataset_registry.service import DatasetPageService
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.versioning import ResourceVersion

psycopg = pytest.importorskip("psycopg")

pytestmark = pytest.mark.integration

PROJECT_ID = "p06-dataset-detail-integration-project"
REGION_CODE = "p06-dataset-detail-integration-region"
ORGANIZATION_ID = "p06-dataset-detail-integration-organization"
FOREIGN_PROJECT_ID = "p06-dataset-detail-foreign-project"
FOREIGN_REGION_CODE = "p06-dataset-detail-foreign-region"
FOREIGN_ORGANIZATION_ID = "p06-dataset-detail-foreign-organization"
DATASET_ID = "dataset_p06postgres"
FOREIGN_DATASET_ID = "dataset_p06foreign"
VERSION_ID = "version_p06postgres"
FOREIGN_VERSION_ID = "version_p06foreign"
APP_ROLE = "p06_dataset_detail_reader"
APP_PASSWORD = "p06-test-only-reader-password"
NOW = datetime(2026, 8, 19, 17, tzinfo=timezone.utc)


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
    capabilities = {
        "dataset.read",
        "dataset_version.read",
        "data_schema.read",
        "storage.overview.read",
        "episode.read",
    }
    return AuthContext(
        subject_id="p06-dataset-detail-reader",
        project_ids=frozenset({PROJECT_ID}),
        region_codes=frozenset({REGION_CODE}),
        roles=frozenset(),
        scope_pairs=frozenset({(PROJECT_ID, REGION_CODE)}),
        scoped_capabilities=frozenset((PROJECT_ID, capability) for capability in capabilities),
        organization_ids=frozenset({ORGANIZATION_ID}),
        organization_scope_triples=frozenset({(ORGANIZATION_ID, PROJECT_ID, REGION_CODE)}),
        organization_scoped_capabilities=frozenset(
            (ORGANIZATION_ID, PROJECT_ID, capability) for capability in capabilities
        ),
    )


def _scope(*, foreign: bool = False) -> DatasetPageScope:
    return DatasetPageScope(
        organization_id=FOREIGN_ORGANIZATION_ID if foreign else ORGANIZATION_ID,
        project_id=FOREIGN_PROJECT_ID if foreign else PROJECT_ID,
        region_code=FOREIGN_REGION_CODE if foreign else REGION_CODE,
    )


def _dataset(*, foreign: bool = False) -> DatasetPageRecord:
    scope = _scope(foreign=foreign)
    return DatasetPageRecord(
        scope=scope,
        dataset_id=FOREIGN_DATASET_ID if foreign else DATASET_ID,
        name="P06 foreign dataset" if foreign else "P06 PostgreSQL dataset",
        description="durable P06 detail projection",
        labels=("p06", "postgres"),
        availability="ACTIVE",
        owner=DatasetPageActor(id="p06-owner", display_name="P06 Owner"),
        created_at=NOW - timedelta(days=1),
        updated_at=NOW,
        activity_at=NOW,
        etag=ResourceVersion(1).etag,
        metadata=DatasetPageMetadata(asset_state="READY", storage_class="STANDARD"),
        episode_count="1",
        pending_review_version_count="1",
        returned_version_count="0",
        actionable_draft_count="1",
    )


def _schema_reference(*, foreign: bool = False) -> DatasetPageContentReference:
    return DatasetPageContentReference(
        reference_type="DATASET_SCHEMA",
        reference_id="schema_p06foreign" if foreign else "schema_p06postgres",
        reference_version="schema-v1",
        sha256=("b" if foreign else "a") * 64,
    )


def _bundle(
    *, foreign: bool = False
) -> tuple[
    DatasetPageReviewingVersion,
    DatasetPageDetailFacts,
    DatasetPageVersionSchemaSummary,
    DatasetPageSourceProvenance,
    DatasetPageVersionCapacityFacts,
    DatasetPageEpisodeRecord,
]:
    scope = _scope(foreign=foreign)
    dataset_id = FOREIGN_DATASET_ID if foreign else DATASET_ID
    version_id = FOREIGN_VERSION_ID if foreign else VERSION_ID
    suffix = "foreign" if foreign else "postgres"
    episode_id = f"episode_p06{suffix}"
    revision_id = f"revision_p06{suffix}"
    revision = DatasetPageRevisionSnapshotReference(
        episode_id=episode_id,
        revision_id=revision_id,
        ordinal=0,
        content_sha256=("d" if foreign else "c") * 64,
    )
    version = DatasetPageReviewingVersion(
        scope=scope,
        dataset_id=dataset_id,
        version_id=version_id,
        display_version="v1",
        kind="CLEANED",
        created_at=NOW,
        etag='"p06-version-v1"',
        version_token=f"p06-{suffix}-version-token-0001",
        source_draft_id=f"draft_p06{suffix}",
        delivery_status="NOT_STARTED",
    )
    facts = DatasetPageDetailFacts(
        scope=scope,
        dataset_id=dataset_id,
        summary=DatasetPageDetailSummary(
            episode_count="1",
            effective_duration_ns="1000000000",
            source_bytes="1024",
            required_physical_bytes="2048",
            actual_oss_bytes="2048",
            pending_review_version_count="1",
            returned_version_count="0",
            actionable_draft_count="1",
            calculated_at=NOW,
            calculation_state="SETTLED",
        ),
    )
    schema = DatasetPageVersionSchemaSummary(
        scope=scope,
        dataset_id=dataset_id,
        version_id=version_id,
        schema_snapshot=_schema_reference(foreign=foreign),
        channel_count="2",
    )
    source = DatasetPageSourceProvenance(
        scope=scope,
        dataset_id=dataset_id,
        version_id=version_id,
        provenance_id=f"provenance_p06{suffix}",
        upload_id=f"upload_p06{suffix}",
        source_id=f"source_p06{suffix}",
        source_display_name=f"P06 {suffix} source",
        source_manifest_id=f"manifest_p06{suffix}",
        source_manifest_sha256=("e" if foreign else "f") * 64,
        verified_object_set_hash=("1" if foreign else "2") * 64,
        registered_at=NOW,
    )
    capacity = DatasetPageVersionCapacityFacts(
        scope=scope,
        dataset_id=dataset_id,
        version_id=version_id,
        state="SETTLED",
        source_bytes="1024",
        required_physical_bytes="2048",
        actual_oss_bytes="2048",
        calculated_at=NOW,
        basis_revision=f"capacity-p06-{suffix}-v1",
    )
    episode = DatasetPageEpisodeRecord(
        scope=scope,
        dataset_id=dataset_id,
        version_id=version_id,
        episode_id=episode_id,
        selected_revision=revision,
        included=True,
        success_state="SUCCEEDED",
        task="pick",
        robot_id=f"robot_p06{suffix}",
        review_status="HAS_FINDING",
        review_finding_count="1",
        started_at=NOW - timedelta(hours=1),
        started_at_ns="100",
        has_finding=True,
        change_type="CLEANED",
    )
    return version, facts, schema, source, capacity, episode


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
            "GRANT USAGE ON SCHEMA core, registry, dataset_registry TO p06_dataset_detail_reader"
        )
        cursor.execute(
            "GRANT SELECT ON registry.organization_projects TO p06_dataset_detail_reader"
        )
        cursor.execute("GRANT SELECT ON dataset_registry.datasets TO p06_dataset_detail_reader")
        cursor.execute(
            "GRANT SELECT ON dataset_registry.dataset_versions, "
            "dataset_registry.dataset_detail_facts, "
            "dataset_registry.dataset_version_schema_summaries, "
            "dataset_registry.dataset_version_source_provenance, "
            "dataset_registry.dataset_version_capacity_facts, "
            "dataset_registry.dataset_version_episodes TO p06_dataset_detail_reader"
        )
        cursor.execute("GRANT INSERT ON core.audit_events TO p06_dataset_detail_reader")


def _cleanup(dsn: str) -> None:
    projects = (PROJECT_ID, FOREIGN_PROJECT_ID)
    organizations = (ORGANIZATION_ID, FOREIGN_ORGANIZATION_ID)
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM core.audit_integrity_entries WHERE project_id IN (%s, %s)", projects
        )
        cursor.execute("DELETE FROM core.audit_events WHERE project_id IN (%s, %s)", projects)
        cursor.execute(
            "DELETE FROM core.audit_integrity_heads WHERE project_id IN (%s, %s)", projects
        )
        for table in (
            "dataset_registry.dataset_version_episodes",
            "dataset_registry.dataset_version_capacity_facts",
            "dataset_registry.dataset_version_source_provenance",
            "dataset_registry.dataset_version_schema_summaries",
            "dataset_registry.dataset_detail_facts",
            "dataset_registry.dataset_versions",
            "dataset_registry.datasets",
        ):
            cursor.execute(f"DELETE FROM {table} WHERE project_id IN (%s, %s)", projects)
        cursor.execute(
            "DELETE FROM registry.organization_projects WHERE organization_id IN (%s, %s)",
            organizations,
        )


def _insert_dataset(cursor: Any, record: DatasetPageRecord) -> None:
    metadata = record.metadata
    cursor.execute(
        """
        INSERT INTO dataset_registry.datasets (
            organization_id, project_id, region_code, dataset_id, name, description,
            labels, availability, owner_id, owner_display_name, asset_state, storage_class,
            channels, episode_count, pending_review_version_count, returned_version_count,
            actionable_draft_count, version, dataset_document, created_at, updated_at, activity_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, '[]'::jsonb,
            %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s
        )
        """,
        (
            record.scope.organization_id,
            record.scope.project_id,
            record.scope.region_code,
            record.dataset_id,
            record.name,
            record.description,
            '["p06", "postgres"]',
            record.availability,
            record.owner.id,
            record.owner.display_name,
            metadata.asset_state,
            metadata.storage_class,
            int(record.episode_count),
            int(record.pending_review_version_count),
            int(record.returned_version_count),
            int(record.actionable_draft_count),
            ResourceVersion.from_etag(record.etag).value,
            record.model_dump_json(),
            record.created_at,
            record.updated_at,
            record.activity_at,
        ),
    )


def _insert_bundle(cursor: Any, *, foreign: bool = False) -> None:
    version, facts, schema, source, capacity, episode = _bundle(foreign=foreign)
    scope = version.scope
    cursor.execute(
        """
        INSERT INTO dataset_registry.dataset_versions (
            organization_id, project_id, region_code, dataset_id, version_id, display_version,
            version_kind, version_status, created_at, published_at, version_document
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s::jsonb)
        """,
        (
            scope.organization_id,
            scope.project_id,
            scope.region_code,
            version.dataset_id,
            version.version_id,
            version.display_version,
            version.kind,
            version.status,
            version.created_at,
            version.model_dump_json(),
        ),
    )
    summary = facts.summary
    cursor.execute(
        """
        INSERT INTO dataset_registry.dataset_detail_facts (
            organization_id, project_id, region_code, dataset_id, episode_count,
            effective_duration_ns, source_bytes, required_physical_bytes, actual_oss_bytes,
            pending_review_version_count, returned_version_count, actionable_draft_count,
            calculation_state, calculated_at, fact_document
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
        )
        """,
        (
            scope.organization_id,
            scope.project_id,
            scope.region_code,
            facts.dataset_id,
            int(summary.episode_count),
            summary.effective_duration_ns,
            summary.source_bytes,
            summary.required_physical_bytes,
            summary.actual_oss_bytes,
            int(summary.pending_review_version_count),
            int(summary.returned_version_count),
            int(summary.actionable_draft_count),
            summary.calculation_state,
            summary.calculated_at,
            facts.model_dump_json(),
        ),
    )
    cursor.execute(
        """
        INSERT INTO dataset_registry.dataset_version_schema_summaries (
            organization_id, project_id, region_code, dataset_id, version_id, schema_snapshot_id,
            channel_count, schema_document
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            scope.organization_id,
            scope.project_id,
            scope.region_code,
            schema.dataset_id,
            schema.version_id,
            schema.schema_snapshot.reference_id,
            int(schema.channel_count or "0"),
            schema.model_dump_json(),
        ),
    )
    cursor.execute(
        """
        INSERT INTO dataset_registry.dataset_version_source_provenance (
            organization_id, project_id, region_code, dataset_id, version_id, provenance_id,
            upload_id, source_id, source_display_name, source_manifest_id, registered_at,
            provenance_document
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            scope.organization_id,
            scope.project_id,
            scope.region_code,
            source.dataset_id,
            source.version_id,
            source.provenance_id,
            source.upload_id,
            source.source_id,
            source.source_display_name,
            source.source_manifest_id,
            source.registered_at,
            source.model_dump_json(),
        ),
    )
    cursor.execute(
        """
        INSERT INTO dataset_registry.dataset_version_capacity_facts (
            organization_id, project_id, region_code, dataset_id, version_id, capacity_state,
            calculated_at, capacity_document
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            scope.organization_id,
            scope.project_id,
            scope.region_code,
            capacity.dataset_id,
            capacity.version_id,
            capacity.state,
            capacity.calculated_at,
            capacity.model_dump_json(),
        ),
    )
    cursor.execute(
        """
        INSERT INTO dataset_registry.dataset_version_episodes (
            organization_id, project_id, region_code, dataset_id, version_id, episode_id,
            revision_id, ordinal, started_at, started_at_ns, included, success_state, task,
            robot_id, review_status, review_finding_count, has_finding, change_type,
            episode_document
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s::jsonb
        )
        """,
        (
            scope.organization_id,
            scope.project_id,
            scope.region_code,
            episode.dataset_id,
            episode.version_id,
            episode.episode_id,
            episode.selected_revision.revision_id,
            episode.selected_revision.ordinal,
            episode.started_at,
            episode.started_at_ns,
            episode.included,
            episode.success_state,
            episode.task,
            episode.robot_id,
            episode.review_status,
            int(episode.review_finding_count or "0"),
            episode.has_finding,
            episode.change_type,
            episode.model_dump_json(),
        ),
    )


def _connection_factory(dsn: str) -> Callable[[], DbApiConnection]:
    return cast(Callable[[], DbApiConnection], psycopg_connection_factory(dsn))


def _service(dsn: str) -> DatasetPageService:
    return DatasetPageService(
        PostgresDatasetPageRepository(_connection_factory(dsn)),
        cursor_secret="p06-postgres-cursor-secret",
        clock=lambda: NOW,
    )


def _request_context() -> RequestContext:
    return RequestContext(
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
        subject_id="p06-dataset-detail-reader",
        request_id="p06-dataset-detail-postgres-integration",
    )


def test_postgres_p06_detail_projection_is_rls_scoped_and_audited() -> None:
    superuser_dsn = _superuser_dsn()
    _cleanup(superuser_dsn)
    _prepare_app_role(superuser_dsn)
    try:
        with psycopg.connect(superuser_dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO registry.organization_projects "
                "(organization_id, project_id) VALUES (%s, %s)",
                (ORGANIZATION_ID, PROJECT_ID),
            )
            cursor.execute(
                "INSERT INTO registry.organization_projects "
                "(organization_id, project_id) VALUES (%s, %s)",
                (FOREIGN_ORGANIZATION_ID, FOREIGN_PROJECT_ID),
            )
            _insert_dataset(cursor, _dataset())
            _insert_dataset(cursor, _dataset(foreign=True))
            _insert_bundle(cursor)
            _insert_bundle(cursor, foreign=True)

        token = bind_request_context(_request_context())
        try:
            service = _service(_app_dsn(superuser_dsn))
            bootstrap = service.bootstrap(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                dataset_id=DATASET_ID,
                request_id="p06-bootstrap",
            )
            assert bootstrap.data.summary.actual_oss_bytes == "2048"

            versions = service.list_versions(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                dataset_id=DATASET_ID,
                filters=DatasetPageVersionFilters(),
                sort="created_at:desc,version_id:desc",
                after=None,
                before=None,
                limit=10,
                request_id="p06-versions",
            )
            assert [item.version_id for item in versions.items] == [VERSION_ID]

            schema = service.schema_summary(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                dataset_id=DATASET_ID,
                version_id=VERSION_ID,
                request_id="p06-schema",
            )
            assert schema.data.channel_count == "2"

            sources = service.list_source_provenance(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                dataset_id=DATASET_ID,
                version_id=VERSION_ID,
                filters=DatasetPageSourceProvenanceFilters(),
                sort="registered_at:desc,provenance_id:desc",
                after=None,
                before=None,
                limit=10,
                request_id="p06-sources",
            )
            assert len(sources.items) == 1

            capacity = service.capacity(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                dataset_id=DATASET_ID,
                version_id=VERSION_ID,
                request_id="p06-capacity",
            )
            assert capacity.data.state == "SETTLED"

            episodes = service.list_episodes(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                dataset_id=DATASET_ID,
                version_id=VERSION_ID,
                filters=DatasetPageEpisodeFilters(),
                sort="ordinal:asc,episode_id:asc",
                after=None,
                before=None,
                limit=10,
                request_id="p06-episodes",
            )
            assert len(episodes.items) == 1

            connection = _connection_factory(_app_dsn(superuser_dsn))()
            try:
                cursor = connection.cursor()
                try:
                    for table in (
                        "dataset_registry.dataset_versions",
                        "dataset_registry.dataset_detail_facts",
                        "dataset_registry.dataset_version_schema_summaries",
                        "dataset_registry.dataset_version_source_provenance",
                        "dataset_registry.dataset_version_capacity_facts",
                        "dataset_registry.dataset_version_episodes",
                    ):
                        cursor.execute(f"SELECT count(*) FROM {table}")
                        result = cursor.fetchone()
                        assert isinstance(result, tuple)
                        assert result[0] == 1
                finally:
                    cursor.close()
            finally:
                connection.close()
        finally:
            reset_request_context(token)

        with psycopg.connect(superuser_dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT version_document::text,
                       (SELECT count(*) FROM core.audit_events WHERE project_id = %s)
                  FROM dataset_registry.dataset_versions
                 WHERE project_id = %s AND dataset_id = %s
                """,
                (PROJECT_ID, PROJECT_ID, DATASET_ID),
            )
            document, audit_count = cursor.fetchone()
            assert "credential" not in document
            assert "secret" not in document
            assert audit_count >= 6
    finally:
        _cleanup(superuser_dsn)
        _drop_app_role(superuser_dsn)
