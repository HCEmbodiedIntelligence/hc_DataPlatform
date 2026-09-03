from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, cast

import pytest
from pydantic import TypeAdapter

from hc_data_platform.cleaning_drafts.repository import (
    CleaningDraftFilters,
    PostgresCleaningDraftRepository,
)
from hc_data_platform.cleaning_drafts.service import CleaningDraftService
from hc_data_platform.cleaning_workbench.calculation import EMPTY_EDL_HASH
from hc_data_platform.cleaning_workbench.models import (
    CommitAcceptedEnvelope,
    CommitAcknowledgement,
    CommitCleaningDraftCommand,
    CreateCleaningPreviewCommand,
    ExcludeRangeOperation,
    PreviewAcceptedEnvelope,
    SaveCleaningEdlCommand,
)
from hc_data_platform.cleaning_workbench.repository import (
    DbApiConnection,
    PostgresCleaningWorkbenchRepository,
)
from hc_data_platform.cleaning_workbench.service import CleaningWorkbenchService
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
    DatasetPageContentSnapshot,
    DatasetPageEpisodeRecord,
    DatasetPageEpisodeRevision,
    DatasetPageEpisodeStream,
    DatasetPageManifestEntry,
    DatasetPageManifestSummary,
    DatasetPageMetadata,
    DatasetPageRecord,
    DatasetPageReturnReviewCommand,
    DatasetPageReturnReviewFindingCommand,
    DatasetPageReturnReviewMutationRecord,
    DatasetPageReviewChecksCommand,
    DatasetPageReviewingVersion,
    DatasetPageRevisionSnapshotReference,
    DatasetPageScope,
    DatasetPageVersionCapacityFacts,
    DatasetPageVersionContentProjection,
    DatasetPageVersionSchemaChannel,
    DatasetPageVersionSchemaDetail,
)
from hc_data_platform.dataset_registry.repository import PostgresDatasetPageRepository
from hc_data_platform.dataset_registry.service import DatasetPageService
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

PROJECT_ID = "p11-workbench-project"
OTHER_PROJECT_ID = "p11-workbench-other-project"
REGION_CODE = "p11-workbench-region"
ORGANIZATION_ID = "p11-workbench-organization"
DATASET_ID = "dataset_p11postgres"
SOURCE_VERSION_ID = "version_p11source"
EPISODE_ID = "episode_p11source"
REVISION_ID = "revision_p11source"
STREAM_ID = "stream_p11source"
APP_ROLE = "p11_workbench_operator"
APP_PASSWORD = "p11-test-only-operator-password"
NOW = datetime(2026, 8, 19, 23, 30, tzinfo=timezone.utc)


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
        "cleaning.create",
        "cleaning.read",
        "cleaning.edit",
        "cleaning.preview",
        "cleaning.submit",
        "dataset_version.review",
    }
    return AuthContext(
        subject_id="p11-workbench-operator",
        project_ids=frozenset({PROJECT_ID}),
        region_codes=frozenset({REGION_CODE}),
        scope_pairs=frozenset({(PROJECT_ID, REGION_CODE)}),
        scoped_capabilities=frozenset((PROJECT_ID, capability) for capability in capabilities),
    )


def _dataset() -> DatasetPageRecord:
    return DatasetPageRecord(
        scope=_scope(),
        dataset_id=DATASET_ID,
        name="P11 PostgreSQL Dataset",
        description="A durable non-destructive P11 workbench integration fixture",
        labels=("p11", "postgres"),
        availability="ACTIVE",
        owner=DatasetPageActor(id="p11-owner", display_name="P11 Owner"),
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
        display_version="v-p11-source",
        kind="RAW",
        created_at=NOW,
        etag=ResourceVersion(1).etag,
        version_token="p11-source-version-token-000001",
        source_draft_id="draft_p11source",
        delivery_status="NOT_STARTED",
        allowed_actions=(),
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
        started_at_ns="0",
        duration_ns="1000",
        streams=(
            DatasetPageEpisodeStream(
                episode_stream_id=STREAM_ID,
                channel_path="joint.position",
                kind="NUMERIC",
                t_start_ns="0",
                t_end_ns="1000",
            ),
        ),
    )


def _source_episode() -> DatasetPageEpisodeRecord:
    revision = _source_revision()
    return DatasetPageEpisodeRecord(
        scope=_scope(),
        dataset_id=DATASET_ID,
        version_id=SOURCE_VERSION_ID,
        episode_id=EPISODE_ID,
        selected_revision=DatasetPageRevisionSnapshotReference(
            episode_id=EPISODE_ID,
            revision_id=REVISION_ID,
            ordinal=0,
            content_sha256=revision.content_sha256,
        ),
        included=True,
        success_state="SUCCEEDED",
        task="pick",
        robot_id="robot_p11postgres",
        review_status="PENDING",
        review_finding_count="0",
        started_at=NOW,
        started_at_ns="0",
        has_finding=False,
        change_type="RAW",
    )


def _schema_reference() -> DatasetPageContentReference:
    return DatasetPageContentReference(
        reference_type="DATASET_SCHEMA",
        reference_id="schema_p11postgres",
        reference_version="schema-v1",
        sha256="a" * 64,
    )


def _source_content() -> DatasetPageVersionContentProjection:
    revision = _source_revision()
    return DatasetPageVersionContentProjection(
        scope=_scope(),
        dataset_id=DATASET_ID,
        version_id=SOURCE_VERSION_ID,
        content_snapshot=DatasetPageContentSnapshot(
            content_snapshot_id="snapshot_p11source",
            content_snapshot_hash="b" * 64,
            revision_refs=(
                DatasetPageRevisionSnapshotReference(
                    episode_id=EPISODE_ID,
                    revision_id=REVISION_ID,
                    ordinal=0,
                    content_sha256=revision.content_sha256,
                ),
            ),
            schema_ref=_schema_reference(),
        ),
        manifest=DatasetPageManifestSummary(
            manifest_id="manifest_p11source",
            format_version="v1",
            canonicalization="canonical-json",
            sha256="c" * 64,
            entry_count="1",
        ),
        operational_revision="p11-source-operational-revision",
    )


def _source_schema() -> DatasetPageVersionSchemaDetail:
    return DatasetPageVersionSchemaDetail(
        scope=_scope(),
        dataset_id=DATASET_ID,
        version_id=SOURCE_VERSION_ID,
        schema_snapshot=_schema_reference(),
        channel_count="1",
        channels=(
            DatasetPageVersionSchemaChannel(
                channel_id="channel_p11position",
                name="joint.position",
                data_type="float64",
                unit="rad",
            ),
        ),
    )


def _source_capacity() -> DatasetPageVersionCapacityFacts:
    return DatasetPageVersionCapacityFacts(
        scope=_scope(),
        dataset_id=DATASET_ID,
        version_id=SOURCE_VERSION_ID,
        state="SETTLED",
        source_bytes="1000",
        required_physical_bytes="1000",
        actual_oss_bytes="1000",
        calculated_at=NOW,
        basis_revision="p11-source-capacity-v1",
    )


def _source_manifest_entry() -> DatasetPageManifestEntry:
    return DatasetPageManifestEntry(
        entry_id="entry_p11source",
        episode_id=EPISODE_ID,
        revision_id=REVISION_ID,
        role="REVISION",
        size_bytes="1000",
        sha256="d" * 64,
        safe_locator="revision-p11-source",
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
            "GRANT SELECT, INSERT, UPDATE ON dataset_registry.datasets, "
            "dataset_registry.dataset_versions, "
            "dataset_registry.dataset_version_content_projections, "
            "dataset_registry.dataset_detail_facts, "
            "dataset_registry.dataset_version_episodes, "
            "dataset_registry.dataset_version_episode_revisions, "
            "dataset_registry.dataset_version_schema_details, "
            "dataset_registry.dataset_version_manifest_entries, "
            "dataset_registry.dataset_version_capacity_facts, "
            "dataset_registry.dataset_version_review_decisions, "
            "dataset_registry.dataset_version_review_findings, "
            "dataset_registry.dataset_version_successor_drafts TO " + APP_ROLE
        )
        cursor.execute(
            "GRANT SELECT, INSERT, UPDATE ON manual_cleaning.manual_issues, "
            "manual_cleaning.manual_issue_draft_links, "
            "manual_cleaning.cleaning_draft_ancestry, "
            "manual_cleaning.cleaning_workbench_drafts, "
            "manual_cleaning.cleaning_draft_edl_revisions, "
            "manual_cleaning.cleaning_draft_previews, "
            "manual_cleaning.cleaning_workbench_commits, "
            "manual_cleaning.cleaning_workbench_jobs, "
            "manual_cleaning.cleaning_commit_output_revisions TO " + APP_ROLE
        )
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
            "manual_cleaning.cleaning_draft_ancestry",
            "manual_cleaning.manual_issue_draft_links",
            "manual_cleaning.manual_issues",
            "dataset_registry.dataset_version_successor_drafts",
            "dataset_registry.dataset_version_review_findings",
            "dataset_registry.dataset_version_review_decisions",
            "dataset_registry.dataset_version_manifest_entries",
            "dataset_registry.dataset_version_capacity_facts",
            "dataset_registry.dataset_version_schema_details",
            "dataset_registry.dataset_version_content_projections",
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
            '["p11", "postgres"]',
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
    content = _source_content()
    schema = _source_schema()
    capacity = _source_capacity()
    entry = _source_manifest_entry()
    cursor.execute(
        """
        INSERT INTO dataset_registry.dataset_versions (
            organization_id, project_id, region_code, dataset_id, version_id, display_version,
            version_kind, version_status, created_at, published_at, version_document
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s::jsonb)
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
        INSERT INTO dataset_registry.dataset_version_content_projections (
            organization_id, project_id, region_code, dataset_id, version_id,
            content_snapshot_id, content_snapshot_hash, manifest_id, manifest_sha256,
            operational_revision, content_document
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            content.scope.organization_id,
            content.scope.project_id,
            content.scope.region_code,
            content.dataset_id,
            content.version_id,
            content.content_snapshot.content_snapshot_id,
            content.content_snapshot.content_snapshot_hash,
            content.manifest.manifest_id,
            content.manifest.sha256,
            content.operational_revision,
            content.model_dump_json(),
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
    cursor.execute(
        """
        INSERT INTO dataset_registry.dataset_version_capacity_facts (
            organization_id, project_id, region_code, dataset_id, version_id, capacity_state,
            calculated_at, capacity_document
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            capacity.scope.organization_id,
            capacity.scope.project_id,
            capacity.scope.region_code,
            capacity.dataset_id,
            capacity.version_id,
            capacity.state,
            capacity.calculated_at,
            capacity.model_dump_json(),
        ),
    )
    cursor.execute(
        """
        INSERT INTO dataset_registry.dataset_version_manifest_entries (
            organization_id, project_id, region_code, dataset_id, version_id, entry_id,
            episode_id, revision_id, entry_role, size_bytes, entry_document
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            ORGANIZATION_ID,
            PROJECT_ID,
            REGION_CODE,
            DATASET_ID,
            SOURCE_VERSION_ID,
            entry.entry_id,
            entry.episode_id,
            entry.revision_id,
            entry.role,
            entry.size_bytes,
            entry.model_dump_json(),
        ),
    )


def _seed(dsn: str) -> None:
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO registry.organization_projects (organization_id, project_id) "
            "VALUES (%s, %s)",
            (ORGANIZATION_ID, PROJECT_ID),
        )
        _insert_dataset(cursor, _dataset())
        _insert_source_facts(cursor)


def _connection_factory(dsn: str) -> Callable[[], DbApiConnection]:
    return cast(Callable[[], DbApiConnection], psycopg_connection_factory(dsn))


_MANUAL_IDEMPOTENCY: TypeAdapter[ManualIssueMutationRecord | ManualIssueDraftMutationRecord] = (
    TypeAdapter(ManualIssueMutationRecord | ManualIssueDraftMutationRecord)
)
_WORKBENCH_IDEMPOTENCY: TypeAdapter[PreviewAcceptedEnvelope | CommitAcceptedEnvelope] = TypeAdapter(
    PreviewAcceptedEnvelope | CommitAcceptedEnvelope
)
_REVIEW_IDEMPOTENCY: TypeAdapter[DatasetPageReturnReviewMutationRecord] = TypeAdapter(
    DatasetPageReturnReviewMutationRecord
)


def _manual_issue_service(dsn: str) -> ManualIssueService:
    factory = _connection_factory(dsn)
    return ManualIssueService(
        PostgresManualIssueRepository(factory),
        cursor_secret="p11-postgres-manual-issue-cursor-secret",
        idempotency=PsycopgIdempotencyStore(
            factory,
            response_decoder=_MANUAL_IDEMPOTENCY.validate_python,
        ),
        clock=lambda: NOW,
    )


def _workbench_service(dsn: str) -> CleaningWorkbenchService:
    factory = _connection_factory(dsn)
    return CleaningWorkbenchService(
        PostgresCleaningWorkbenchRepository(factory),
        idempotency=PsycopgIdempotencyStore(
            factory,
            response_decoder=_WORKBENCH_IDEMPOTENCY.validate_python,
        ),
        clock=lambda: NOW,
    )


def _review_service(dsn: str) -> DatasetPageService:
    factory = _connection_factory(dsn)
    return DatasetPageService(
        PostgresDatasetPageRepository(factory),
        cursor_secret="p11-postgres-review-cursor-secret",
        idempotency=PsycopgIdempotencyStore(
            factory,
            response_decoder=_REVIEW_IDEMPOTENCY.validate_python,
        ),
        clock=lambda: NOW,
    )


def _draft_projection_service(dsn: str) -> CleaningDraftService:
    return CleaningDraftService(
        PostgresCleaningDraftRepository(_connection_factory(dsn)),
        cursor_secret="p11-postgres-p10-cursor-secret",
        clock=lambda: NOW,
    )


def _request_context(*, project_id: str = PROJECT_ID) -> RequestContext:
    return RequestContext(
        organization_id=ORGANIZATION_ID,
        project_id=project_id,
        region_code=REGION_CODE,
        subject_id="p11-workbench-operator",
        request_id="p11-workbench-postgres-integration",
    )


def _version_etag(dsn: str, version_id: str) -> str:
    factory = _connection_factory(dsn)
    connection = factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT version_document::text
              FROM dataset_registry.dataset_versions
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND dataset_id = %s AND version_id = %s
            """,
            (ORGANIZATION_ID, PROJECT_ID, REGION_CODE, DATASET_ID, version_id),
        )
        row = cursor.fetchone()
        assert row is not None
        return str(json.loads(row[0])["etag"])
    finally:
        cursor.close()
        connection.close()


def test_postgres_p11_p09_handoff_commit_and_p07_return_successor_are_durable() -> None:
    superuser_dsn = _superuser_dsn()
    asyncio.run(apply_migrations(superuser_dsn))
    _cleanup(superuser_dsn)
    _prepare_app_role(superuser_dsn)
    try:
        _seed(superuser_dsn)
        token = bind_request_context(_request_context())
        try:
            app_dsn = _app_dsn(superuser_dsn)
            manual_issues = _manual_issue_service(app_dsn)
            created_issue = manual_issues.create_issue(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                command=CreateManualIssueCommand(
                    origin_dataset_version_id=SOURCE_VERSION_ID,
                    episode_id=EPISODE_ID,
                    episode_revision_id=REVISION_ID,
                    episode_stream_id=STREAM_ID,
                    start_ns="100",
                    end_ns="200",
                    issue_type="TIMESTAMP_DRIFT",
                    severity="HIGH",
                    note="P11 durable handoff fixture.",
                ),
                idempotency_key="p11-postgres-create-issue",
                request_id="p11-postgres-create-issue",
            )
            issue = created_issue.record.issue
            handoff = manual_issues.create_draft_from_issue(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                issue_id=issue.id,
                command=CreateDraftFromIssueCommand(),
                if_match=issue.etag,
                idempotency_key="p11-postgres-handoff",
                request_id="p11-postgres-handoff",
            )
            draft_id = handoff.record.result.draft_id

            workbench = _workbench_service(app_dsn)
            bootstrap = workbench.bootstrap(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                draft_id=draft_id,
                request_id="p11-postgres-bootstrap",
            )
            assert bootstrap.data.draft.origin.origin_type == "ISSUE_DERIVED"
            assert bootstrap.data.base.revision_id == REVISION_ID
            assert bootstrap.data.edl.edl_revision == "0"
            assert bootstrap.data.edl.operation_hash == EMPTY_EDL_HASH

            save_command = SaveCleaningEdlCommand(
                expected_edl_revision="0",
                expected_operation_hash=EMPTY_EDL_HASH,
                operations=(
                    ExcludeRangeOperation(
                        id="operation_p11postgres",
                        sequence_no=0,
                        enabled=True,
                        start_ns="100",
                        end_ns="200",
                        reason="P11 postgres exclusion",
                    ),
                ),
                client_mutation_id="mutation_p11postgres",
            )
            saved = workbench.save_edl(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                draft_id=draft_id,
                if_match=bootstrap.data.draft.etag,
                command=save_command,
                request_id="p11-postgres-save",
            )
            assert saved.replayed is False
            assert saved.envelope.data.edl.edl_revision == "1"
            assert saved.envelope.data.edl.summary.output_duration_ns == "900"
            saved_replay = workbench.save_edl(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                draft_id=draft_id,
                if_match=bootstrap.data.draft.etag,
                command=save_command,
                request_id="p11-postgres-save-replay",
            )
            assert saved_replay.replayed is True
            assert saved_replay.envelope.data.edl == saved.envelope.data.edl

            preview_command = CreateCleaningPreviewCommand(
                base_revision_id=REVISION_ID,
                edl_revision=saved.envelope.data.edl.edl_revision,
                operation_hash=saved.envelope.data.edl.operation_hash,
            )
            preview = workbench.create_preview(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                draft_id=draft_id,
                if_match=saved.envelope.data.draft.etag,
                idempotency_key="p11-postgres-preview",
                command=preview_command,
                request_id="p11-postgres-preview",
            )
            assert preview.replayed is False
            assert preview.envelope.preview.status == "READY"
            assert preview.envelope.preview.viewer_manifest is not None
            assert "object_locator" not in preview.envelope.model_dump(mode="json")
            preview_replay = workbench.create_preview(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                draft_id=draft_id,
                if_match=saved.envelope.data.draft.etag,
                idempotency_key="p11-postgres-preview",
                command=preview_command,
                request_id="p11-postgres-preview-replay",
            )
            assert preview_replay.replayed is True
            assert preview_replay.envelope == preview.envelope

            commit_command = CommitCleaningDraftCommand(
                preview_id=preview.envelope.preview.preview_id,
                base_revision_id=REVISION_ID,
                edl_revision=saved.envelope.data.edl.edl_revision,
                operation_hash=saved.envelope.data.edl.operation_hash,
                acknowledgement=CommitAcknowledgement(
                    reviewed_summary=True,
                    compared_preview=True,
                ),
            )
            committed = workbench.commit(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                draft_id=draft_id,
                if_match=saved.envelope.data.draft.etag,
                idempotency_key="p11-postgres-commit",
                command=commit_command,
                request_id="p11-postgres-commit",
            )
            assert committed.replayed is False
            assert committed.envelope.commit.status == "SUCCEEDED"
            assert committed.envelope.commit.materialization_status == "NOT_STARTED"
            output_version_id = committed.envelope.commit.output_version.version_id
            output_revision_id = committed.envelope.commit.output_revisions[0].revision_id
            assert output_revision_id != REVISION_ID
            commit_replay = workbench.commit(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                draft_id=draft_id,
                if_match=saved.envelope.data.draft.etag,
                idempotency_key="p11-postgres-commit",
                command=commit_command,
                request_id="p11-postgres-commit-replay",
            )
            assert commit_replay.replayed is True
            assert commit_replay.envelope == committed.envelope

            with psycopg.connect(superuser_dsn) as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT workbench.status, workbench.workbench_version,
                           commit.materialization_status, output.version_status,
                           source.revision_document::text, changed.revision_document::text
                      FROM manual_cleaning.cleaning_workbench_drafts AS workbench
                      JOIN manual_cleaning.cleaning_workbench_commits AS commit
                        ON commit.organization_id = workbench.organization_id
                       AND commit.project_id = workbench.project_id
                       AND commit.region_code = workbench.region_code
                       AND commit.draft_id = workbench.draft_id
                      JOIN dataset_registry.dataset_versions AS output
                        ON output.organization_id = workbench.organization_id
                       AND output.project_id = workbench.project_id
                       AND output.region_code = workbench.region_code
                       AND output.dataset_id = workbench.dataset_id
                       AND output.version_id = commit.output_version_id
                      JOIN dataset_registry.dataset_version_episode_revisions AS source
                        ON source.organization_id = workbench.organization_id
                       AND source.project_id = workbench.project_id
                       AND source.region_code = workbench.region_code
                       AND source.dataset_id = workbench.dataset_id
                       AND source.version_id = %s
                       AND source.revision_id = %s
                      JOIN dataset_registry.dataset_version_episode_revisions AS changed
                        ON changed.organization_id = workbench.organization_id
                       AND changed.project_id = workbench.project_id
                       AND changed.region_code = workbench.region_code
                       AND changed.dataset_id = workbench.dataset_id
                       AND changed.version_id = commit.output_version_id
                       AND changed.revision_id = %s
                     WHERE workbench.organization_id = %s AND workbench.project_id = %s
                       AND workbench.region_code = %s AND workbench.draft_id = %s
                    """,
                    (
                        SOURCE_VERSION_ID,
                        REVISION_ID,
                        output_revision_id,
                        ORGANIZATION_ID,
                        PROJECT_ID,
                        REGION_CODE,
                        draft_id,
                    ),
                )
                row = cursor.fetchone()
                assert row is not None
                (
                    status,
                    version,
                    materialization,
                    output_status,
                    source_document,
                    changed_document,
                ) = row
                assert status == "COMMITTED"
                assert version == 3
                assert materialization == "NOT_STARTED"
                assert output_status == "REVIEWING"
                assert json.loads(source_document)["revision_id"] == REVISION_ID
                assert json.loads(source_document)["content_sha256"] == "d" * 64
                assert json.loads(changed_document)["revision_id"] == output_revision_id
                assert json.loads(changed_document)["content_sha256"] != "d" * 64

            review = _review_service(app_dsn)
            output_etag = _version_etag(app_dsn, output_version_id)
            checks = review.review_checks(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                dataset_id=DATASET_ID,
                version_id=output_version_id,
                command=DatasetPageReviewChecksCommand(expected_status="REVIEWING"),
                if_match=output_etag,
                request_id="p11-postgres-review-checks",
            )
            assert checks.data.blockers == ()
            assert [target.output_revision_id for target in checks.data.eligible_targets] == [
                output_revision_id
            ]
            return_command = DatasetPageReturnReviewCommand(
                expected_status="REVIEWING",
                review_token=checks.data.review_token,
                finding_catalog_version=checks.data.finding_catalog.version,
                findings=(
                    DatasetPageReturnReviewFindingCommand(
                        output_revision_id=output_revision_id,
                        episode_stream_id=STREAM_ID,
                        start_ns="10",
                        end_ns="20",
                        finding_type="RANGE_QUALITY",
                        severity="HIGH",
                        note="P11 output requires a revision-only follow-up.",
                    ),
                ),
            )
            returned = review.return_review(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                dataset_id=DATASET_ID,
                version_id=output_version_id,
                command=return_command,
                if_match=output_etag,
                idempotency_key="p11-postgres-review-return",
                request_id="p11-postgres-review-return",
            )
            assert returned.replayed is False
            assert returned.record.output_version_id == output_version_id
            assert returned.record.supersedes_draft_id == draft_id

            feedback = workbench.review_findings(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                draft_id=draft_id,
                request_id="p11-postgres-review-feedback",
            )
            assert feedback.data.feedback.review_decision.immutable is True
            assert feedback.data.feedback.findings[0].immutable is True
            assert feedback.data.feedback.successor_draft_id == returned.record.successor_draft_id

            successor = workbench.bootstrap(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                draft_id=returned.record.successor_draft_id,
                request_id="p11-postgres-successor-bootstrap",
            )
            assert successor.data.origin.origin_type == "REVIEW_RETURN"
            assert successor.data.base.version_id == output_version_id
            assert successor.data.base.revision_id == output_revision_id
            assert successor.data.successor_composition is not None
            assert (
                successor.data.successor_composition.editable_base_revision_id == output_revision_id
            )
            assert successor.data.review_feedback is not None
            assert successor.data.review_feedback.findings[0].id == returned.record.findings[0].id

            queue = _draft_projection_service(app_dsn).list_drafts(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                filters=CleaningDraftFilters(scope="all", status="active"),
                sort="updated_at:desc,id:desc",
                after=None,
                before=None,
                limit=20,
                request_id="p11-postgres-p10-projection",
            )
            drafts_by_id = {item.draft_id: item for item in queue.items}
            assert set(drafts_by_id) == {draft_id, returned.record.successor_draft_id}
            returned_source = drafts_by_id[draft_id]
            assert returned_source.origin.origin_type == "ISSUE_DERIVED"
            assert returned_source.output_version_status == "RETURNED"
            assert returned_source.allowed_actions == ("VIEW", "VIEW_EVENTS", "OPEN_SUCCESSOR")
            returned_successor = drafts_by_id[returned.record.successor_draft_id]
            assert returned_successor.origin.origin_type == "REVIEW_RETURN"
            assert returned_successor.manual_issue_count == "0"
            assert returned_successor.allowed_actions == ("VIEW", "VIEW_EVENTS", "EDIT")

            foreign_token = bind_request_context(_request_context(project_id=OTHER_PROJECT_ID))
            try:
                foreign_scope = successor.scope.model_copy(update={"project_id": OTHER_PROJECT_ID})
                repository = PostgresCleaningWorkbenchRepository(_connection_factory(app_dsn))
                assert repository.get_state(scope=foreign_scope, draft_id=draft_id) is None
            finally:
                reset_request_context(foreign_token)
        finally:
            reset_request_context(token)
    finally:
        _cleanup(superuser_dsn)
        _drop_app_role(superuser_dsn)
