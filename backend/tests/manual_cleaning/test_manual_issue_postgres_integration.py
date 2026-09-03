from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, cast

import pytest
from pydantic import TypeAdapter

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.core.migrations import apply_migrations
from hc_data_platform.dataset_registry.models import (
    DatasetPageActor,
    DatasetPageContentReference,
    DatasetPageContentSnapshot,
    DatasetPageEpisodeRecord,
    DatasetPageEpisodeRevision,
    DatasetPageEpisodeStream,
    DatasetPageManifestSummary,
    DatasetPageMetadata,
    DatasetPageReadyVersion,
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
    ResolveManualIssueCommand,
)
from hc_data_platform.manual_cleaning.repository import (
    DbApiConnection,
    ManualIssueFilters,
    PostgresManualIssueRepository,
)
from hc_data_platform.manual_cleaning.service import ManualIssueService
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.psycopg import PsycopgIdempotencyStore
from hc_data_platform.security.versioning import ResourceVersion

psycopg = pytest.importorskip("psycopg")

pytestmark = pytest.mark.integration

PROJECT_ID = "p09-manual-cleaning-project"
REGION_CODE = "p09-manual-cleaning-region"
ORGANIZATION_ID = "p09-manual-cleaning-organization"
DATASET_ID = "dataset_p09postgres"
SOURCE_VERSION_ID = "version_p09source"
OUTPUT_VERSION_ID = "version_p09ready"
EPISODE_ID = "episode_p09source"
REVISION_ID = "revision_p09source"
STREAM_ID = "stream_p09source"
APP_ROLE = "p09_manual_issue_writer"
APP_PASSWORD = "p09-test-only-writer-password"
NOW = datetime(2026, 8, 19, 21, tzinfo=timezone.utc)


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


def _auth() -> AuthContext:
    capabilities = {
        "manual_issue.read",
        "manual_issue.create",
        "manual_issue.triage",
        "manual_issue.resolve",
        "cleaning.create",
    }
    return AuthContext(
        subject_id="p09-manual-issue-writer",
        project_ids=frozenset({PROJECT_ID}),
        region_codes=frozenset({REGION_CODE}),
        scope_pairs=frozenset({(PROJECT_ID, REGION_CODE)}),
        scoped_capabilities=frozenset((PROJECT_ID, capability) for capability in capabilities),
    )


def _dataset() -> DatasetPageRecord:
    return DatasetPageRecord(
        scope=_scope(),
        dataset_id=DATASET_ID,
        name="P09 PostgreSQL Dataset",
        description="Durable P09 source facts and issue handoff fixture",
        labels=("p09", "postgres"),
        availability="ACTIVE",
        owner=DatasetPageActor(id="p09-owner", display_name="P09 Owner"),
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
        display_version="v-source",
        kind="RAW",
        created_at=NOW,
        etag=ResourceVersion(1).etag,
        version_token="p09-source-version-token-000001",
        source_draft_id="draft_p09source",
        delivery_status="NOT_STARTED",
        allowed_actions=(),
    )


def _ready_version() -> DatasetPageReadyVersion:
    schema = DatasetPageContentReference(
        reference_type="DATASET_SCHEMA",
        reference_id="schema_p09postgres",
        reference_version="schema-v1",
        sha256="a" * 64,
    )
    return DatasetPageReadyVersion(
        scope=_scope(),
        dataset_id=DATASET_ID,
        version_id=OUTPUT_VERSION_ID,
        display_version="v-ready",
        kind="CLEANED",
        created_at=NOW,
        etag=ResourceVersion(1).etag,
        version_token="p09-ready-version-token-000002",
        published_at=NOW,
        content_snapshot=DatasetPageContentSnapshot(
            content_snapshot_id="content_p09ready",
            content_snapshot_hash="b" * 64,
            schema_ref=schema,
        ),
        manifest=DatasetPageManifestSummary(
            manifest_id="manifest_p09ready",
            format_version="v1",
            canonicalization="rfc8785",
            sha256="c" * 64,
            entry_count="0",
        ),
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
        robot_id="robot_p09postgres",
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
            reference_id="schema_p09postgres",
            reference_version="schema-v1",
            sha256="a" * 64,
        ),
        channel_count="1",
        channels=(
            DatasetPageVersionSchemaChannel(
                channel_id="channel_p09position",
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
            "GRANT SELECT ON dataset_registry.dataset_versions, "
            "dataset_registry.dataset_version_episode_revisions, "
            "dataset_registry.dataset_version_schema_details TO " + APP_ROLE
        )
        cursor.execute(
            "GRANT SELECT, INSERT, UPDATE ON manual_cleaning.manual_issues, "
            "manual_cleaning.manual_issue_draft_links, "
            "manual_cleaning.cleaning_draft_ancestry, "
            "manual_cleaning.cleaning_workbench_drafts, "
            "manual_cleaning.cleaning_draft_edl_revisions TO " + APP_ROLE
        )
        cursor.execute(
            "GRANT SELECT ON manual_cleaning.cleaning_draft_previews, "
            "manual_cleaning.cleaning_workbench_commits TO " + APP_ROLE
        )
        cursor.execute("GRANT INSERT ON core.audit_events TO " + APP_ROLE)
        cursor.execute("GRANT SELECT, INSERT, UPDATE ON core.idempotency_records TO " + APP_ROLE)


def _cleanup(dsn: str) -> None:
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        # The integration role cannot mutate append-only evidence.  The
        # disposable superuser fixture removes only its exact test scope while
        # replication mode is local to this cleanup transaction.
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
            "manual_cleaning.cleaning_draft_ancestry",
            "manual_cleaning.manual_issue_draft_links",
            "manual_cleaning.manual_issues",
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
            '["p09", "postgres"]',
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


def _insert_version(
    cursor: Any, version: DatasetPageReviewingVersion | DatasetPageReadyVersion
) -> None:
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
            version.published_at if isinstance(version, DatasetPageReadyVersion) else None,
            version.model_dump_json(),
        ),
    )


def _insert_source_facts(cursor: Any) -> None:
    episode = _source_episode()
    revision = _source_revision()
    schema = _source_schema()
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
            (
                "INSERT INTO registry.organization_projects "
                "(organization_id, project_id) VALUES (%s, %s)"
            ),
            (ORGANIZATION_ID, PROJECT_ID),
        )
        _insert_dataset(cursor, _dataset())
        _insert_version(cursor, _source_version())
        _insert_version(cursor, _ready_version())
        _insert_source_facts(cursor)


def _connection_factory(dsn: str) -> Callable[[], DbApiConnection]:
    return cast(Callable[[], DbApiConnection], psycopg_connection_factory(dsn))


_IDEMPOTENCY_RESPONSE: TypeAdapter[ManualIssueMutationRecord | ManualIssueDraftMutationRecord] = (
    TypeAdapter(ManualIssueMutationRecord | ManualIssueDraftMutationRecord)
)


def _service(dsn: str) -> ManualIssueService:
    factory = _connection_factory(dsn)
    return ManualIssueService(
        PostgresManualIssueRepository(factory),
        cursor_secret="p09-postgres-cursor-secret",
        idempotency=PsycopgIdempotencyStore(
            factory,
            response_decoder=_IDEMPOTENCY_RESPONSE.validate_python,
        ),
        clock=lambda: NOW,
    )


def _request_context() -> RequestContext:
    return RequestContext(
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
        subject_id="p09-manual-issue-writer",
        request_id="p09-manual-issue-postgres-integration",
    )


def _create_command() -> CreateManualIssueCommand:
    return CreateManualIssueCommand(
        origin_dataset_version_id=SOURCE_VERSION_ID,
        episode_id=EPISODE_ID,
        episode_revision_id=REVISION_ID,
        episode_stream_id=STREAM_ID,
        start_ns="300",
        end_ns="500",
        issue_type="TIMESTAMP_DRIFT",
        severity="HIGH",
        note="PostgreSQL source-bound ManualIssue fixture",
    )


def _insert_successful_commit(superuser_dsn: str, *, draft_id: str) -> None:
    with psycopg.connect(superuser_dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO manual_cleaning.cleaning_workbench_commits (
                organization_id, project_id, region_code, commit_id, draft_id,
                preview_id, job_id, output_version_id, operation_hash, status,
                materialization_status, commit_document, created_at, completed_at
            ) VALUES (
                %s, %s, %s, %s, %s, 'preview_p09durable', 'job_p09durable', %s,
                %s, 'SUCCEEDED', 'SUCCEEDED', %s::jsonb, %s, %s
            )
            """,
            (
                ORGANIZATION_ID,
                PROJECT_ID,
                REGION_CODE,
                "commit_p09durable",
                draft_id,
                OUTPUT_VERSION_ID,
                "sha256:" + "a" * 64,
                '{"commit_id":"commit_p09durable","draft_id":"' + draft_id + '"}',
                NOW,
                NOW,
            ),
        )


def test_postgres_p09_issue_handoff_and_resolution_are_durable_and_rls_scoped() -> None:
    superuser_dsn = _superuser_dsn()
    asyncio.run(apply_migrations(superuser_dsn))
    _cleanup(superuser_dsn)
    _prepare_app_role(superuser_dsn)
    try:
        _seed(superuser_dsn)
        token = bind_request_context(_request_context())
        try:
            service = _service(_app_dsn(superuser_dsn))
            created = service.create_issue(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                command=_create_command(),
                idempotency_key="p09-postgres-create",
                request_id="p09-postgres-create",
            )
            assert created.replayed is False
            assert created.record.issue.dataset_id == DATASET_ID
            assert created.record.issue.episode_revision_id == REVISION_ID

            replay = service.create_issue(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                command=_create_command(),
                idempotency_key="p09-postgres-create",
                request_id="p09-postgres-create-replay",
            )
            assert replay.replayed is True
            assert replay.record == created.record

            listed = service.list_issues(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                filters=ManualIssueFilters(),
                sort="updated_at:desc,id:desc",
                after=None,
                before=None,
                limit=20,
                request_id="p09-postgres-list",
            )
            assert [item.id for item in listed.items] == [created.record.issue.id]

            with pytest.raises(ProblemException) as unresolved:
                service.resolve_issue(
                    auth=_auth(),
                    organization_id=ORGANIZATION_ID,
                    project_id=PROJECT_ID,
                    region_code=REGION_CODE,
                    issue_id=created.record.issue.id,
                    command=ResolveManualIssueCommand(
                        resolution_version_id=OUTPUT_VERSION_ID,
                        resolution_note="No durable successful commit exists yet.",
                    ),
                    if_match=created.record.issue.etag,
                    idempotency_key="p09-postgres-before-commit",
                    request_id="p09-postgres-before-commit",
                )
            assert unresolved.value.problem.code == "MANUAL_ISSUE_NOT_IN_PROGRESS"

            draft = service.create_draft_from_issue(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                issue_id=created.record.issue.id,
                command=CreateDraftFromIssueCommand(),
                if_match=created.record.issue.etag,
                idempotency_key="p09-postgres-draft",
                request_id="p09-postgres-draft",
            )
            assert draft.replayed is False
            assert draft.record.result.disposition == "CREATED"
            draft_id = draft.record.result.draft_id

            draft_replay = service.create_draft_from_issue(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                issue_id=created.record.issue.id,
                command=CreateDraftFromIssueCommand(),
                if_match=created.record.issue.etag,
                idempotency_key="p09-postgres-draft",
                request_id="p09-postgres-draft-replay",
            )
            assert draft_replay.replayed is True
            assert draft_replay.record == draft.record

            with pytest.raises(ProblemException) as unqualified:
                service.resolve_issue(
                    auth=_auth(),
                    organization_id=ORGANIZATION_ID,
                    project_id=PROJECT_ID,
                    region_code=REGION_CODE,
                    issue_id=created.record.issue.id,
                    command=ResolveManualIssueCommand(
                        resolution_version_id=OUTPUT_VERSION_ID,
                        resolution_note="A READY version alone is not a qualified resolution.",
                    ),
                    if_match=draft.record.issue_etag,
                    idempotency_key="p09-postgres-no-commit",
                    request_id="p09-postgres-no-commit",
                )
            assert unqualified.value.problem.code == "RESOLUTION_VERSION_INELIGIBLE"

            _insert_successful_commit(superuser_dsn, draft_id=draft_id)
            resolution_note = "READY output, successful commit, and root ancestry are durable."
            resolved = service.resolve_issue(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                issue_id=created.record.issue.id,
                command=ResolveManualIssueCommand(
                    resolution_version_id=OUTPUT_VERSION_ID,
                    resolution_note=resolution_note,
                ),
                if_match=draft.record.issue_etag,
                idempotency_key="p09-postgres-resolve",
                request_id="p09-postgres-resolve",
            )
            assert resolved.replayed is False
            assert resolved.record.issue.status == "RESOLVED"
            assert resolved.record.issue.resolution_version is not None
            assert resolved.record.issue.resolution_version.producer_draft_id == draft_id

            resolved_replay = service.resolve_issue(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                issue_id=created.record.issue.id,
                command=ResolveManualIssueCommand(
                    resolution_version_id=OUTPUT_VERSION_ID,
                    resolution_note=resolution_note,
                ),
                if_match=draft.record.issue_etag,
                idempotency_key="p09-postgres-resolve",
                request_id="p09-postgres-resolve-replay",
            )
            assert resolved_replay.replayed is True
            assert resolved_replay.record == resolved.record

            with psycopg.connect(superuser_dsn) as connection, connection.cursor() as cursor:
                with pytest.raises(
                    psycopg.DatabaseError,
                    match="ManualIssue source facts are immutable",
                ):
                    cursor.execute(
                        """
                        UPDATE manual_cleaning.manual_issues
                           SET start_ns = start_ns + 1
                         WHERE project_id = %s AND manual_issue_id = %s
                        """,
                        (PROJECT_ID, created.record.issue.id),
                    )
                connection.rollback()
                with pytest.raises(
                    psycopg.DatabaseError,
                    match="Cleaning Draft ancestry is immutable",
                ):
                    cursor.execute(
                        """
                        UPDATE manual_cleaning.cleaning_draft_ancestry
                           SET recorded_at = recorded_at + interval '1 second'
                         WHERE project_id = %s AND root_draft_id = %s
                        """,
                        (PROJECT_ID, draft_id),
                    )
                connection.rollback()

            connection = _connection_factory(_app_dsn(superuser_dsn))()
            try:
                cursor = connection.cursor()
                try:
                    for table in (
                        "manual_cleaning.manual_issues",
                        "manual_cleaning.cleaning_workbench_drafts",
                        "manual_cleaning.manual_issue_draft_links",
                        "manual_cleaning.cleaning_draft_ancestry",
                        "manual_cleaning.cleaning_workbench_commits",
                    ):
                        cursor.execute(f"SELECT count(*) FROM {table}")
                        row = cursor.fetchone()
                        assert isinstance(row, tuple)
                        assert row[0] == 1
                finally:
                    cursor.close()
            finally:
                connection.close()
        finally:
            reset_request_context(token)

        with psycopg.connect(superuser_dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT issue_document::text,
                       (SELECT count(*) FROM core.idempotency_records WHERE project_id = %s),
                       (SELECT count(*) FROM core.audit_events WHERE project_id = %s)
                  FROM manual_cleaning.manual_issues
                 WHERE project_id = %s AND region_code = %s
                """,
                (PROJECT_ID, PROJECT_ID, PROJECT_ID, REGION_CODE),
            )
            document, idempotency_count, audit_count = cursor.fetchone()
            assert "credential" not in document
            assert "secret" not in document
            assert idempotency_count == 3
            assert audit_count >= 4
    finally:
        _cleanup(superuser_dsn)
        _drop_app_role(superuser_dsn)
