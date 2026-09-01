from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, cast

import pytest
from pydantic import TypeAdapter

from hc_data_platform.cleaning_drafts.models import CleaningDraftScope
from hc_data_platform.cleaning_drafts.repository import (
    CleaningDraftFilters,
    DbApiConnection,
    PostgresCleaningDraftRepository,
)
from hc_data_platform.cleaning_drafts.service import CleaningDraftService
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.core.migrations import apply_migrations
from hc_data_platform.dataset_registry.models import (
    DatasetPageActor,
    DatasetPageContentReference,
    DatasetPageEpisodeRecord,
    DatasetPageEpisodeRevision,
    DatasetPageEpisodeStream,
    DatasetPageMetadata,
    DatasetPageRecord,
    DatasetPageReviewingVersion,
    DatasetPageRevisionSnapshotReference,
    DatasetPageScope,
    DatasetPageVersionSchemaChannel,
    DatasetPageVersionSchemaDetail,
)
from hc_data_platform.manual_cleaning.models import (
    CreateDraftFromIssueCommand,
    CreateManualIssueCommand,
    ManualIssueDraftMutationRecord,
    ManualIssueMutationRecord,
)
from hc_data_platform.manual_cleaning.repository import PostgresManualIssueRepository
from hc_data_platform.manual_cleaning.service import ManualIssueService
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.psycopg import PsycopgIdempotencyStore
from hc_data_platform.security.versioning import ResourceVersion

psycopg = pytest.importorskip("psycopg")

pytestmark = pytest.mark.integration

PROJECT_ID = "p10-cleaning-drafts-project"
OTHER_PROJECT_ID = "p10-cleaning-drafts-other-project"
REGION_CODE = "p10-cleaning-drafts-region"
ORGANIZATION_ID = "p10-cleaning-drafts-organization"
DATASET_ID = "dataset_p10postgres"
SOURCE_VERSION_ID = "version_p10source"
EPISODE_ID = "episode_p10source"
REVISION_ID = "revision_p10source"
STREAM_ID = "stream_p10source"
APP_ROLE = "p10_cleaning_draft_reader"
APP_PASSWORD = "p10-test-only-reader-password"
NOW = datetime(2026, 8, 19, 23, tzinfo=timezone.utc)


def _superuser_dsn() -> str:
    value = os.getenv("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


def _app_dsn(superuser_dsn: str) -> str:
    _credentials, separator, address = superuser_dsn.rpartition("@")
    assert separator
    return f"postgresql://{APP_ROLE}:{APP_PASSWORD}@{address}"


def _scope() -> DatasetPageScope:
    return DatasetPageScope(
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
    )


def _cleaning_scope() -> CleaningDraftScope:
    return CleaningDraftScope(
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
    )


def _auth() -> AuthContext:
    capabilities = {
        "manual_issue.read",
        "manual_issue.create",
        "manual_issue.triage",
        "manual_issue.resolve",
        "cleaning.create",
        "cleaning.read",
    }
    return AuthContext(
        subject_id="p10-cleaning-draft-reader",
        project_ids=frozenset({PROJECT_ID}),
        region_codes=frozenset({REGION_CODE}),
        scope_pairs=frozenset({(PROJECT_ID, REGION_CODE)}),
        scoped_capabilities=frozenset((PROJECT_ID, capability) for capability in capabilities),
    )


def _dataset() -> DatasetPageRecord:
    return DatasetPageRecord(
        scope=_scope(),
        dataset_id=DATASET_ID,
        name="P10 PostgreSQL Dataset",
        description="Durable P09 handoff used by the P10 read projection fixture",
        labels=("p10", "postgres"),
        availability="ACTIVE",
        owner=DatasetPageActor(id="p10-owner", display_name="P10 Owner"),
        created_at=NOW,
        updated_at=NOW,
        activity_at=NOW,
        etag=ResourceVersion(1).etag,
        metadata=DatasetPageMetadata(asset_state="READY", storage_class="STANDARD"),
    )


def _source_version() -> DatasetPageReviewingVersion:
    return DatasetPageReviewingVersion(
        scope=_scope(),
        dataset_id=DATASET_ID,
        version_id=SOURCE_VERSION_ID,
        display_version="v-p10-source",
        kind="RAW",
        created_at=NOW,
        etag=ResourceVersion(1).etag,
        version_token="p10-source-version-token-000001",
        source_draft_id="draft_p10source",
        delivery_status="NOT_STARTED",
        allowed_actions=(),
    )


def _source_episode() -> DatasetPageEpisodeRecord:
    revision = DatasetPageRevisionSnapshotReference(
        episode_id=EPISODE_ID,
        revision_id=REVISION_ID,
        ordinal=0,
        content_sha256="d" * 64,
    )
    return DatasetPageEpisodeRecord(
        scope=_scope(),
        dataset_id=DATASET_ID,
        version_id=SOURCE_VERSION_ID,
        episode_id=EPISODE_ID,
        selected_revision=revision,
        included=True,
        success_state="SUCCEEDED",
        task="pick",
        robot_id="robot_p10postgres",
        review_status="PENDING",
        review_finding_count="0",
        started_at=NOW,
        started_at_ns="100",
        has_finding=False,
        change_type="RAW",
    )


def _source_revision() -> DatasetPageEpisodeRevision:
    return DatasetPageEpisodeRevision(
        scope=_scope(),
        dataset_id=DATASET_ID,
        version_id=SOURCE_VERSION_ID,
        episode_id=EPISODE_ID,
        revision_id=REVISION_ID,
        ordinal=0,
        content_sha256="d" * 64,
        started_at_ns="100",
        duration_ns="900",
        streams=(
            DatasetPageEpisodeStream(
                episode_stream_id=STREAM_ID,
                channel_path="joint.position",
                kind="NUMERIC",
                t_start_ns="100",
                t_end_ns="1000",
            ),
        ),
    )


def _source_schema() -> DatasetPageVersionSchemaDetail:
    return DatasetPageVersionSchemaDetail(
        scope=_scope(),
        dataset_id=DATASET_ID,
        version_id=SOURCE_VERSION_ID,
        schema_snapshot=DatasetPageContentReference(
            reference_type="DATASET_SCHEMA",
            reference_id="schema_p10postgres",
            reference_version="schema-v1",
            sha256="a" * 64,
        ),
        channel_count="1",
        channels=(
            DatasetPageVersionSchemaChannel(
                channel_id="channel_p10position",
                name="joint.position",
                data_type="float64",
                unit="rad",
            ),
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
            "GRANT USAGE ON SCHEMA core, registry, dataset_registry, manual_cleaning TO " + APP_ROLE
        )
        cursor.execute("GRANT SELECT ON registry.organization_projects TO " + APP_ROLE)
        cursor.execute(
            "GRANT SELECT ON dataset_registry.datasets, dataset_registry.dataset_versions, "
            "dataset_registry.dataset_version_episodes, "
            "dataset_registry.dataset_version_episode_revisions, "
            "dataset_registry.dataset_version_schema_details, "
            "dataset_registry.dataset_version_review_decisions, "
            "dataset_registry.dataset_version_review_findings, "
            "dataset_registry.dataset_version_successor_drafts TO " + APP_ROLE
        )
        cursor.execute(
            "GRANT SELECT, INSERT, UPDATE ON manual_cleaning.manual_issues, "
            "manual_cleaning.cleaning_drafts, manual_cleaning.manual_issue_draft_links, "
            "manual_cleaning.cleaning_draft_ancestry, "
            "manual_cleaning.cleaning_workbench_drafts, "
            "manual_cleaning.cleaning_draft_edl_revisions, "
            "manual_cleaning.cleaning_draft_previews, "
            "manual_cleaning.cleaning_workbench_commits, "
            "manual_cleaning.cleaning_commit_output_revisions TO " + APP_ROLE
        )
        cursor.execute("GRANT SELECT ON manual_cleaning.cleaning_draft_commits TO " + APP_ROLE)
        cursor.execute("GRANT SELECT, INSERT ON core.audit_events TO " + APP_ROLE)
        cursor.execute("GRANT SELECT, INSERT, UPDATE ON core.idempotency_records TO " + APP_ROLE)


def _cleanup(dsn: str) -> None:
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        cursor.execute(
            "DELETE FROM core.audit_integrity_entries WHERE project_id = %s", (PROJECT_ID,)
        )
        cursor.execute("DELETE FROM core.audit_events WHERE project_id = %s", (PROJECT_ID,))
        cursor.execute(
            "DELETE FROM core.audit_integrity_heads WHERE project_id = %s", (PROJECT_ID,)
        )
        cursor.execute(
            "DELETE FROM core.idempotency_records WHERE project_id = %s AND region_code = %s",
            (PROJECT_ID, REGION_CODE),
        )
        for table in (
            "manual_cleaning.cleaning_commit_output_revisions",
            "manual_cleaning.cleaning_workbench_jobs",
            "manual_cleaning.cleaning_workbench_commits",
            "manual_cleaning.cleaning_draft_previews",
            "manual_cleaning.cleaning_draft_edl_revisions",
            "manual_cleaning.cleaning_workbench_drafts",
            "manual_cleaning.cleaning_draft_commits",
            "manual_cleaning.cleaning_draft_ancestry",
            "manual_cleaning.manual_issue_draft_links",
            "manual_cleaning.cleaning_drafts",
            "manual_cleaning.manual_issues",
            "dataset_registry.dataset_version_successor_drafts",
            "dataset_registry.dataset_version_review_findings",
            "dataset_registry.dataset_version_review_decisions",
            "dataset_registry.dataset_version_schema_details",
            "dataset_registry.dataset_version_episode_revisions",
            "dataset_registry.dataset_version_episodes",
            "dataset_registry.dataset_versions",
            "dataset_registry.datasets",
        ):
            cursor.execute(f"DELETE FROM {table} WHERE project_id = %s", (PROJECT_ID,))
        cursor.execute(
            "DELETE FROM registry.organization_projects WHERE organization_id = %s",
            (ORGANIZATION_ID,),
        )


def _insert_dataset(cursor: Any, record: DatasetPageRecord) -> None:
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
            '["p10", "postgres"]',
            record.availability,
            record.owner.id,
            record.owner.display_name,
            record.metadata.asset_state,
            record.metadata.storage_class,
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


def _insert_source_facts(cursor: Any) -> None:
    version = _source_version()
    episode = _source_episode()
    revision = _source_revision()
    schema = _source_schema()
    cursor.execute(
        """
        INSERT INTO dataset_registry.dataset_versions (
            organization_id, project_id, region_code, dataset_id, version_id, display_version,
            version_kind, version_status, created_at, published_at, version_document
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            version.scope.organization_id,
            version.scope.project_id,
            version.scope.region_code,
            version.dataset_id,
            version.version_id,
            version.display_version,
            version.kind,
            version.status,
            version.created_at,
            None,
            version.model_dump_json(),
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
            episode.scope.organization_id,
            episode.scope.project_id,
            episode.scope.region_code,
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
    cursor.execute(
        """
        INSERT INTO dataset_registry.dataset_version_episode_revisions (
            organization_id, project_id, region_code, dataset_id, version_id, episode_id,
            revision_id, ordinal, revision_document
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            revision.scope.organization_id,
            revision.scope.project_id,
            revision.scope.region_code,
            revision.dataset_id,
            revision.version_id,
            revision.episode_id,
            revision.revision_id,
            revision.ordinal,
            revision.model_dump_json(),
        ),
    )
    cursor.execute(
        """
        INSERT INTO dataset_registry.dataset_version_schema_details (
            organization_id, project_id, region_code, dataset_id, version_id, schema_snapshot_id,
            channel_count, schema_document
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            schema.scope.organization_id,
            schema.scope.project_id,
            schema.scope.region_code,
            schema.dataset_id,
            schema.version_id,
            schema.schema_snapshot.reference_id,
            int(schema.channel_count),
            schema.model_dump_json(),
        ),
    )


def _seed(dsn: str) -> None:
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO registry.organization_projects "
            "(organization_id, project_id) VALUES (%s, %s)",
            (ORGANIZATION_ID, PROJECT_ID),
        )
        _insert_dataset(cursor, _dataset())
        _insert_source_facts(cursor)


def _connection_factory(dsn: str) -> Callable[[], DbApiConnection]:
    return cast(Callable[[], DbApiConnection], psycopg_connection_factory(dsn))


_IDEMPOTENCY_RESPONSE: TypeAdapter[ManualIssueMutationRecord | ManualIssueDraftMutationRecord] = (
    TypeAdapter(ManualIssueMutationRecord | ManualIssueDraftMutationRecord)
)


def _manual_issue_service(dsn: str) -> ManualIssueService:
    factory = _connection_factory(dsn)
    return ManualIssueService(
        PostgresManualIssueRepository(factory),
        cursor_secret="p10-postgres-manual-issue-cursor-secret",
        idempotency=PsycopgIdempotencyStore(
            factory,
            response_decoder=_IDEMPOTENCY_RESPONSE.validate_python,
        ),
        clock=lambda: NOW,
    )


def _cleaning_draft_service(dsn: str) -> CleaningDraftService:
    return CleaningDraftService(
        PostgresCleaningDraftRepository(_connection_factory(dsn)),
        cursor_secret="p10-postgres-cleaning-draft-cursor-secret",
        clock=lambda: NOW,
    )


def _request_context(project_id: str = PROJECT_ID) -> RequestContext:
    return RequestContext(
        organization_id=ORGANIZATION_ID,
        project_id=project_id,
        region_code=REGION_CODE,
        subject_id="p10-cleaning-draft-reader",
        request_id="p10-cleaning-draft-postgres-integration",
    )


def _create_issue_command() -> CreateManualIssueCommand:
    return CreateManualIssueCommand(
        origin_dataset_version_id=SOURCE_VERSION_ID,
        episode_id=EPISODE_ID,
        episode_revision_id=REVISION_ID,
        episode_stream_id=STREAM_ID,
        start_ns="300",
        end_ns="500",
        issue_type="TIMESTAMP_DRIFT",
        severity="HIGH",
        note="PostgreSQL P09 handoff for the P10 read projection fixture",
    )


def test_postgres_p10_projects_real_p09_handoffs_with_rls_and_read_audit() -> None:
    superuser_dsn = _superuser_dsn()
    asyncio.run(apply_migrations(superuser_dsn))
    _cleanup(superuser_dsn)
    _prepare_app_role(superuser_dsn)
    try:
        _seed(superuser_dsn)
        token = bind_request_context(_request_context())
        try:
            manual_issues = _manual_issue_service(_app_dsn(superuser_dsn))
            issue = manual_issues.create_issue(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                command=_create_issue_command(),
                idempotency_key="p10-postgres-create-issue",
                request_id="p10-postgres-create-issue",
            )
            handoff = manual_issues.create_draft_from_issue(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                issue_id=issue.record.issue.id,
                command=CreateDraftFromIssueCommand(),
                if_match=issue.record.issue.etag,
                idempotency_key="p10-postgres-create-draft",
                request_id="p10-postgres-create-draft",
            )
            assert handoff.record.result.draft_id is not None
            draft_id = handoff.record.result.draft_id

            service = _cleaning_draft_service(_app_dsn(superuser_dsn))
            listed = service.list_drafts(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                filters=CleaningDraftFilters(scope="all", status="active"),
                sort="updated_at:desc,id:desc",
                after=None,
                before=None,
                limit=20,
                request_id="p10-postgres-list",
            )
            assert [item.draft_id for item in listed.items] == [draft_id]
            assert listed.items[0].etag == f'"{draft_id}:workbench:1"'
            assert listed.items[0].preview_status == "NONE"
            assert listed.items[0].allowed_actions == ("VIEW", "VIEW_EVENTS")

            detail = service.detail(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                draft_id=draft_id,
                request_id="p10-postgres-detail",
            )
            assert detail.data.draft.origin.origin_type == "ISSUE_DERIVED"
            assert detail.data.ordered_operations == ()
            assert detail.data.preview_summary is None
            assert detail.data.relationships.output_version_id is None

            events = service.events(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                draft_id=draft_id,
                after=None,
                before=None,
                limit=10,
                request_id="p10-postgres-events",
            )
            assert [event.event_type for event in events.items] == ["cleaning.draft.created"]
            assert events.items[0].safe_summary == "Draft created from a ManualIssue."
        finally:
            reset_request_context(token)

        other_token = bind_request_context(_request_context(OTHER_PROJECT_ID))
        try:
            repository = PostgresCleaningDraftRepository(
                _connection_factory(_app_dsn(superuser_dsn))
            )
            assert repository.list_drafts(scope=_cleaning_scope()) == ()
        finally:
            reset_request_context(other_token)

        with psycopg.connect(superuser_dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT action, resource_type, details::text
                  FROM core.audit_events
                 WHERE project_id = %s
                   AND action IN ('cleaning.draft.listed', 'cleaning.draft.viewed',
                                  'cleaning.draft.events_viewed')
                 ORDER BY occurred_at, audit_id
                """,
                (PROJECT_ID,),
            )
            audit_rows = cursor.fetchall()
            assert {row[0] for row in audit_rows} == {
                "cleaning.draft.listed",
                "cleaning.draft.viewed",
                "cleaning.draft.events_viewed",
            }
            assert all(row[1] == "CLEANING_DRAFT" for row in audit_rows)
            assert all("credential" not in row[2] and "secret" not in row[2] for row in audit_rows)
    finally:
        _cleanup(superuser_dsn)
        _drop_app_role(superuser_dsn)
