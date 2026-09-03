from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import pytest
from pydantic import TypeAdapter

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.core.migrations import apply_migrations
from hc_data_platform.dataset_registry.models import (
    DatasetPageActor,
    DatasetPageApproveReviewCommand,
    DatasetPageApproveReviewMutationRecord,
    DatasetPageContentReference,
    DatasetPageContentSnapshot,
    DatasetPageEpisodeAlignedMediaBinding,
    DatasetPageEpisodeDataBinding,
    DatasetPageEpisodeRecord,
    DatasetPageEpisodeRevision,
    DatasetPageEpisodeStream,
    DatasetPageManifestEntry,
    DatasetPageManifestSummary,
    DatasetPageMetadata,
    DatasetPageReadyVersion,
    DatasetPageRecord,
    DatasetPageReturnReviewCommand,
    DatasetPageReturnReviewMutationRecord,
    DatasetPageReviewChecksCommand,
    DatasetPageReviewingVersion,
    DatasetPageRevisionSnapshotReference,
    DatasetPageScope,
    DatasetPageVersionCapacityFacts,
    DatasetPageVersionContentProjection,
    DatasetPageVersionManifestEntryRecord,
    DatasetPageVersionSchemaChannel,
    DatasetPageVersionSchemaDetail,
)
from hc_data_platform.dataset_registry.repository import (
    DatasetPageEpisodeFilters,
    DbApiConnection,
    PostgresDatasetPageRepository,
)
from hc_data_platform.dataset_registry.service import DatasetPageService
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.psycopg import PsycopgIdempotencyStore
from hc_data_platform.security.versioning import ResourceVersion

psycopg = pytest.importorskip("psycopg")

pytestmark = pytest.mark.integration

PROJECT_ID = "p07-dataset-postgres-project"
REGION_CODE = "p07-dataset-postgres-region"
ORGANIZATION_ID = "p07-dataset-postgres-organization"
FOREIGN_PROJECT_ID = "p07-dataset-postgres-foreign-project"
FOREIGN_REGION_CODE = "p07-dataset-postgres-foreign-region"
FOREIGN_ORGANIZATION_ID = "p07-dataset-postgres-foreign-organization"
DATASET_ID = "dataset_p07postgres"
FOREIGN_DATASET_ID = "dataset_p07foreign"
APPROVE_VERSION_ID = "version_p07approve"
RETURN_VERSION_ID = "version_p07return"
COMPARE_VERSION_ID = "version_p07compare"
APP_ROLE = "p07_dataset_detail_reader"
APP_PASSWORD = "p07-test-only-reader-password"
NOW = datetime(2026, 8, 19, 19, tzinfo=timezone.utc)


def _superuser_dsn() -> str:
    value = os.getenv("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


def _app_dsn(superuser_dsn: str) -> str:
    _credentials, separator, address = superuser_dsn.rpartition("@")
    assert separator
    return f"postgresql://{APP_ROLE}:{APP_PASSWORD}@{address}"


def _scope(*, foreign: bool = False) -> DatasetPageScope:
    return DatasetPageScope(
        organization_id=FOREIGN_ORGANIZATION_ID if foreign else ORGANIZATION_ID,
        project_id=FOREIGN_PROJECT_ID if foreign else PROJECT_ID,
        region_code=FOREIGN_REGION_CODE if foreign else REGION_CODE,
    )


def _auth() -> AuthContext:
    capabilities = {
        "dataset.read",
        "dataset_version.read",
        "data_schema.read",
        "episode.read",
        "dataset_version.review",
        "dataset_version.publish",
    }
    return AuthContext(
        subject_id="p07-dataset-detail-reader",
        project_ids=frozenset({PROJECT_ID}),
        region_codes=frozenset({REGION_CODE}),
        scope_pairs=frozenset({(PROJECT_ID, REGION_CODE)}),
        scoped_capabilities=frozenset((PROJECT_ID, capability) for capability in capabilities),
    )


def _record(*, foreign: bool = False) -> DatasetPageRecord:
    scope = _scope(foreign=foreign)
    return DatasetPageRecord(
        scope=scope,
        dataset_id=FOREIGN_DATASET_ID if foreign else DATASET_ID,
        name="P07 foreign dataset" if foreign else "P07 PostgreSQL dataset",
        description="durable P07 fixed-version projection",
        labels=("p07", "postgres"),
        availability="ACTIVE",
        owner=DatasetPageActor(id="p07-owner", display_name="P07 Owner"),
        created_at=NOW - timedelta(days=1),
        updated_at=NOW,
        activity_at=NOW,
        etag=ResourceVersion(1).etag,
        metadata=DatasetPageMetadata(asset_state="READY", storage_class="STANDARD"),
        episode_count="1" if foreign else "2",
        pending_review_version_count="1" if foreign else "2",
        returned_version_count="0",
        actionable_draft_count="1",
    )


def _schema_reference(marker: str) -> DatasetPageContentReference:
    return DatasetPageContentReference(
        reference_type="DATASET_SCHEMA",
        reference_id=f"schema_p07{marker}",
        reference_version="schema-v1",
        sha256=("a" if marker == "approve" else "b") * 64,
    )


@dataclass(frozen=True)
class ReviewBundle:
    version: DatasetPageReviewingVersion
    projection: DatasetPageVersionContentProjection
    capacity: DatasetPageVersionCapacityFacts
    episode: DatasetPageEpisodeRecord
    revision: DatasetPageEpisodeRevision
    schema: DatasetPageVersionSchemaDetail
    manifest_entries: tuple[DatasetPageVersionManifestEntryRecord, ...]


def _review_bundle(*, version_id: str, marker: str, foreign: bool = False) -> ReviewBundle:
    scope = _scope(foreign=foreign)
    dataset_id = FOREIGN_DATASET_ID if foreign else DATASET_ID
    suffix = f"{marker}foreign" if foreign else marker
    episode_id = f"episode_p07{suffix}"
    revision_id = f"revision_p07{suffix}"
    snapshot_sha = ("c" if marker == "approve" else "d") * 64
    schema_ref = _schema_reference(marker)
    revision_ref = DatasetPageRevisionSnapshotReference(
        episode_id=episode_id,
        revision_id=revision_id,
        ordinal=0,
        content_sha256=snapshot_sha,
    )
    version = DatasetPageReviewingVersion(
        scope=scope,
        dataset_id=dataset_id,
        version_id=version_id,
        display_version=f"v-{marker}",
        kind="CLEANED",
        created_at=NOW,
        etag=f'"p07-{marker}-v1"',
        version_token=f"p07-{marker}-version-token-00000001",
        source_draft_id=f"draft_p07{suffix}",
        delivery_status="NOT_STARTED",
        allowed_actions=(),
    )
    manifest = DatasetPageManifestSummary(
        manifest_id=f"manifest_p07{suffix}",
        format_version="v1",
        canonicalization="rfc8785",
        sha256=("e" if marker == "approve" else "f") * 64,
        entry_count="1",
    )
    projection = DatasetPageVersionContentProjection(
        scope=scope,
        dataset_id=dataset_id,
        version_id=version_id,
        content_snapshot=DatasetPageContentSnapshot(
            content_snapshot_id=f"content_p07{suffix}",
            content_snapshot_hash=snapshot_sha,
            revision_refs=(revision_ref,),
            schema_ref=schema_ref,
        ),
        manifest=manifest,
        operational_revision=f"operational-p07-{suffix}-v1",
    )
    episode = DatasetPageEpisodeRecord(
        scope=scope,
        dataset_id=dataset_id,
        version_id=version_id,
        episode_id=episode_id,
        selected_revision=revision_ref,
        included=True,
        success_state="SUCCEEDED",
        task="pick",
        robot_id=f"robot_p07{suffix}",
        review_status="PENDING",
        review_finding_count="0",
        started_at=NOW - timedelta(hours=1),
        started_at_ns="100",
        has_finding=False,
        change_type="CLEANED",
    )
    revision = DatasetPageEpisodeRevision(
        scope=scope,
        dataset_id=dataset_id,
        version_id=version_id,
        episode_id=episode_id,
        revision_id=revision_id,
        ordinal=0,
        content_sha256=snapshot_sha,
        started_at_ns="100",
        duration_ns="1000",
        streams=(
            DatasetPageEpisodeStream(
                episode_stream_id=f"stream_p07{suffix}",
                channel_path="/camera/front/image_raw",
                kind="RGB_VIDEO",
                t_start_ns="100",
                t_end_ns="1100",
                aligned_media_binding=DatasetPageEpisodeAlignedMediaBinding(
                    rollout_id=f"rollout_p07{suffix}",
                    dataset_version=7,
                    artifact_id=f"aligned-media-p07{suffix}",
                    camera_id="front-rgb",
                    fps=30,
                    start_step=0,
                    end_step=30,
                ),
            ),
            DatasetPageEpisodeStream(
                episode_stream_id=f"stream_p07joint{suffix}",
                channel_path="/joint_states/position",
                kind="JOINT_STATE",
                t_start_ns="100",
                t_end_ns="1100",
                data_binding=DatasetPageEpisodeDataBinding(
                    rollout_id=f"rollout_p07{suffix}",
                    lance_version=7,
                    modality_key="joint.position",
                    value_kind="VECTOR",
                    start_step=0,
                    end_step=30,
                ),
            ),
        ),
    )
    schema = DatasetPageVersionSchemaDetail(
        scope=scope,
        dataset_id=dataset_id,
        version_id=version_id,
        schema_snapshot=schema_ref,
        channel_count="1",
        channels=(
            DatasetPageVersionSchemaChannel(
                channel_id=f"channel_p07{suffix}",
                name="joint.position",
                data_type="float64",
                unit="rad",
            ),
        ),
    )
    entry = DatasetPageManifestEntry(
        entry_id=f"entry_p07{suffix}",
        episode_id=episode_id,
        revision_id=revision_id,
        role="REVISION",
        size_bytes="1024",
        sha256=snapshot_sha,
        safe_locator=f"revision-{suffix}",
    )
    return ReviewBundle(
        version=version,
        projection=projection,
        capacity=DatasetPageVersionCapacityFacts(
            scope=scope,
            dataset_id=dataset_id,
            version_id=version_id,
            state="SETTLED",
            source_bytes="1024",
            required_physical_bytes="2048",
            actual_oss_bytes="2048",
            calculated_at=NOW,
            basis_revision=f"capacity-p07-{suffix}-v1",
        ),
        episode=episode,
        revision=revision,
        schema=schema,
        manifest_entries=(
            DatasetPageVersionManifestEntryRecord(
                scope=scope,
                dataset_id=dataset_id,
                version_id=version_id,
                entry=entry,
            ),
        ),
    )


def _compare_version() -> tuple[DatasetPageReadyVersion, DatasetPageVersionContentProjection]:
    scope = _scope()
    schema_ref = _schema_reference("compare")
    revision_ref = DatasetPageRevisionSnapshotReference(
        episode_id="episode_p07approve",
        revision_id="revision_p07compare",
        ordinal=0,
        content_sha256="9" * 64,
    )
    manifest = DatasetPageManifestSummary(
        manifest_id="manifest_p07compare",
        format_version="v1",
        canonicalization="rfc8785",
        sha256="8" * 64,
        entry_count="0",
    )
    content = DatasetPageContentSnapshot(
        content_snapshot_id="content_p07compare",
        content_snapshot_hash="7" * 64,
        revision_refs=(revision_ref,),
        schema_ref=schema_ref,
    )
    return (
        DatasetPageReadyVersion(
            scope=scope,
            dataset_id=DATASET_ID,
            version_id=COMPARE_VERSION_ID,
            display_version="v-compare",
            kind="RAW",
            created_at=NOW - timedelta(days=1),
            etag='"p07-compare-v1"',
            version_token="p07-compare-version-token-000001",
            published_at=NOW - timedelta(hours=1),
            content_snapshot=content,
            manifest=manifest,
            allowed_actions=(),
        ),
        DatasetPageVersionContentProjection(
            scope=scope,
            dataset_id=DATASET_ID,
            version_id=COMPARE_VERSION_ID,
            content_snapshot=content,
            manifest=manifest,
            operational_revision="operational-p07-compare-v1",
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
            "GRANT SELECT, UPDATE ON dataset_registry.datasets, "
            "dataset_registry.dataset_versions, dataset_registry.dataset_version_episodes TO "
            + APP_ROLE
        )
        cursor.execute(
            "GRANT SELECT ON dataset_registry.dataset_version_capacity_facts, "
            "dataset_registry.dataset_version_content_projections, "
            "dataset_registry.dataset_version_episode_revisions, "
            "dataset_registry.dataset_version_schema_details, "
            "dataset_registry.dataset_version_manifest_entries, "
            "dataset_registry.dataset_version_required_storage, "
            "dataset_registry.dataset_version_operational_inventory TO " + APP_ROLE
        )
        cursor.execute(
            "GRANT SELECT, INSERT ON dataset_registry.dataset_version_review_decisions, "
            "dataset_registry.dataset_version_review_findings, "
            "dataset_registry.dataset_version_successor_drafts, "
            "dataset_registry.dataset_version_async_jobs TO " + APP_ROLE
        )
        cursor.execute(
            "GRANT SELECT, INSERT ON manual_cleaning.cleaning_workbench_drafts, "
            "manual_cleaning.cleaning_draft_edl_revisions TO " + APP_ROLE
        )
        cursor.execute("GRANT INSERT ON core.audit_events TO " + APP_ROLE)
        cursor.execute("GRANT SELECT, INSERT, UPDATE ON core.idempotency_records TO " + APP_ROLE)


def _cleanup(dsn: str) -> None:
    projects = (PROJECT_ID, FOREIGN_PROJECT_ID)
    organizations = (ORGANIZATION_ID, FOREIGN_ORGANIZATION_ID)
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        cursor.execute(
            "DELETE FROM core.audit_integrity_entries WHERE project_id IN (%s, %s)", projects
        )
        cursor.execute("DELETE FROM core.audit_events WHERE project_id IN (%s, %s)", projects)
        cursor.execute(
            "DELETE FROM core.audit_integrity_heads WHERE project_id IN (%s, %s)", projects
        )
        cursor.execute(
            "DELETE FROM core.idempotency_records WHERE project_id IN (%s, %s)", projects
        )
        for table in (
            "manual_cleaning.cleaning_commit_output_revisions",
            "manual_cleaning.cleaning_workbench_jobs",
            "manual_cleaning.cleaning_workbench_commits",
            "manual_cleaning.cleaning_draft_previews",
            "manual_cleaning.cleaning_draft_edl_revisions",
            "manual_cleaning.cleaning_workbench_drafts",
        ):
            cursor.execute(f"DELETE FROM {table} WHERE project_id IN (%s, %s)", projects)
        for table in (
            "dataset_registry.dataset_version_review_findings",
            "dataset_registry.dataset_version_successor_drafts",
            "dataset_registry.dataset_version_review_decisions",
            "dataset_registry.dataset_version_async_jobs",
            "dataset_registry.dataset_version_operational_inventory",
            "dataset_registry.dataset_version_required_storage",
            "dataset_registry.dataset_version_manifest_entries",
            "dataset_registry.dataset_version_schema_details",
            "dataset_registry.dataset_version_episode_revisions",
            "dataset_registry.dataset_version_content_projections",
            "dataset_registry.dataset_version_episodes",
            "dataset_registry.dataset_version_capacity_facts",
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
            '["p07", "postgres"]',
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


def _insert_bundle(cursor: Any, bundle: ReviewBundle) -> None:
    scope = bundle.version.scope
    cursor.execute(
        """
        INSERT INTO dataset_registry.dataset_version_content_projections (
            organization_id, project_id, region_code, dataset_id, version_id, content_snapshot_id,
            content_snapshot_hash, manifest_id, manifest_sha256, operational_revision,
            content_document
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            scope.organization_id,
            scope.project_id,
            scope.region_code,
            bundle.projection.dataset_id,
            bundle.projection.version_id,
            bundle.projection.content_snapshot.content_snapshot_id,
            bundle.projection.content_snapshot.content_snapshot_hash,
            bundle.projection.manifest.manifest_id,
            bundle.projection.manifest.sha256,
            bundle.projection.operational_revision,
            bundle.projection.model_dump_json(),
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
            bundle.capacity.dataset_id,
            bundle.capacity.version_id,
            bundle.capacity.state,
            bundle.capacity.calculated_at,
            bundle.capacity.model_dump_json(),
        ),
    )
    _insert_episode_revision(cursor, bundle.episode, bundle.revision)
    schema = bundle.schema
    cursor.execute(
        """
        INSERT INTO dataset_registry.dataset_version_schema_details (
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
            int(schema.channel_count),
            schema.model_dump_json(),
        ),
    )
    for item in bundle.manifest_entries:
        entry = item.entry
        cursor.execute(
            """
            INSERT INTO dataset_registry.dataset_version_manifest_entries (
                organization_id, project_id, region_code, dataset_id, version_id, entry_id,
                episode_id, revision_id, entry_role, size_bytes, entry_document
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                scope.organization_id,
                scope.project_id,
                scope.region_code,
                item.dataset_id,
                item.version_id,
                entry.entry_id,
                entry.episode_id,
                entry.revision_id,
                entry.role,
                entry.size_bytes,
                entry.model_dump_json(),
            ),
        )


def _insert_episode_revision(
    cursor: Any,
    episode: DatasetPageEpisodeRecord,
    revision: DatasetPageEpisodeRevision,
) -> None:
    assert episode.scope == revision.scope
    assert episode.dataset_id == revision.dataset_id
    assert episode.version_id == revision.version_id
    assert episode.episode_id == revision.episode_id
    assert episode.selected_revision.revision_id == revision.revision_id
    scope = episode.scope
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
    cursor.execute(
        """
        INSERT INTO dataset_registry.dataset_version_episode_revisions (
            organization_id, project_id, region_code, dataset_id, version_id, episode_id,
            revision_id, ordinal, revision_document
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            scope.organization_id,
            scope.project_id,
            scope.region_code,
            revision.dataset_id,
            revision.version_id,
            revision.episode_id,
            revision.revision_id,
            revision.ordinal,
            revision.model_dump_json(),
        ),
    )


def _insert_compare_projection(
    cursor: Any, projection: DatasetPageVersionContentProjection
) -> None:
    scope = projection.scope
    cursor.execute(
        """
        INSERT INTO dataset_registry.dataset_version_content_projections (
            organization_id, project_id, region_code, dataset_id, version_id, content_snapshot_id,
            content_snapshot_hash, manifest_id, manifest_sha256, operational_revision,
            content_document
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            scope.organization_id,
            scope.project_id,
            scope.region_code,
            projection.dataset_id,
            projection.version_id,
            projection.content_snapshot.content_snapshot_id,
            projection.content_snapshot.content_snapshot_hash,
            projection.manifest.manifest_id,
            projection.manifest.sha256,
            projection.operational_revision,
            projection.model_dump_json(),
        ),
    )


def _seed(dsn: str) -> tuple[ReviewBundle, ReviewBundle]:
    approve = _review_bundle(version_id=APPROVE_VERSION_ID, marker="approve")
    returned = _review_bundle(version_id=RETURN_VERSION_ID, marker="return")
    compare, compare_projection = _compare_version()
    compare_revision = approve.revision.model_copy(
        update={
            "version_id": COMPARE_VERSION_ID,
            "revision_id": "revision_p07compare",
            "content_sha256": "9" * 64,
        }
    )
    compare_episode = approve.episode.model_copy(
        update={
            "version_id": COMPARE_VERSION_ID,
            "selected_revision": DatasetPageRevisionSnapshotReference(
                episode_id=approve.episode.episode_id,
                revision_id=compare_revision.revision_id,
                ordinal=compare_revision.ordinal,
                content_sha256=compare_revision.content_sha256,
            ),
            "change_type": "RAW",
        }
    )
    foreign = _review_bundle(version_id="version_p07foreign", marker="return", foreign=True)
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            (
                "INSERT INTO registry.organization_projects "
                "(organization_id, project_id) VALUES (%s, %s)"
            ),
            (ORGANIZATION_ID, PROJECT_ID),
        )
        cursor.execute(
            (
                "INSERT INTO registry.organization_projects "
                "(organization_id, project_id) VALUES (%s, %s)"
            ),
            (FOREIGN_ORGANIZATION_ID, FOREIGN_PROJECT_ID),
        )
        _insert_dataset(cursor, _record())
        _insert_dataset(cursor, _record(foreign=True))
        _insert_version(cursor, approve.version)
        _insert_version(cursor, returned.version)
        _insert_version(cursor, compare)
        _insert_version(cursor, foreign.version)
        _insert_bundle(cursor, approve)
        _insert_bundle(cursor, returned)
        _insert_bundle(cursor, foreign)
        _insert_compare_projection(cursor, compare_projection)
        _insert_episode_revision(cursor, compare_episode, compare_revision)
    return approve, returned


def _connection_factory(dsn: str) -> Callable[[], DbApiConnection]:
    return cast(Callable[[], DbApiConnection], psycopg_connection_factory(dsn))


_IDEMPOTENCY_RESPONSE: TypeAdapter[
    DatasetPageApproveReviewMutationRecord | DatasetPageReturnReviewMutationRecord
] = TypeAdapter(DatasetPageApproveReviewMutationRecord | DatasetPageReturnReviewMutationRecord)


def _service(dsn: str) -> DatasetPageService:
    factory = _connection_factory(dsn)
    return DatasetPageService(
        PostgresDatasetPageRepository(factory),
        cursor_secret="p07-postgres-cursor-secret",
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
        subject_id="p07-dataset-detail-reader",
        request_id="p07-dataset-detail-postgres-integration",
    )


def test_postgres_p07_review_paths_are_rls_scoped_durable_and_idempotent() -> None:
    superuser_dsn = _superuser_dsn()
    asyncio.run(apply_migrations(superuser_dsn))
    _cleanup(superuser_dsn)
    _prepare_app_role(superuser_dsn)
    try:
        approve_bundle, return_bundle = _seed(superuser_dsn)
        token = bind_request_context(_request_context())
        try:
            service = _service(_app_dsn(superuser_dsn))
            approve_bootstrap = service.version_bootstrap(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                dataset_id=DATASET_ID,
                version_id=APPROVE_VERSION_ID,
                request_id="p07-approve-bootstrap",
            )
            assert approve_bootstrap.data.snapshot_token
            approve_revision = service.episode_revision(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                dataset_id=DATASET_ID,
                version_id=APPROVE_VERSION_ID,
                revision_id=approve_bundle.revision.revision_id,
                snapshot_token=approve_bootstrap.data.snapshot_token,
                request_id="p07-preview-binding-read",
            )
            assert approve_revision.data.streams[
                0
            ].aligned_media_binding == DatasetPageEpisodeAlignedMediaBinding(
                rollout_id="rollout_p07approve",
                dataset_version=7,
                artifact_id="aligned-media-p07approve",
                camera_id="front-rgb",
                fps=30,
                start_step=0,
                end_step=30,
            )
            assert approve_revision.data.streams[1].data_binding == DatasetPageEpisodeDataBinding(
                rollout_id="rollout_p07approve",
                lance_version=7,
                modality_key="joint.position",
                value_kind="VECTOR",
                start_step=0,
                end_step=30,
            )
            history = service.episode_revision_history(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                dataset_id=DATASET_ID,
                episode_id=approve_bundle.episode.episode_id,
                after=None,
                before=None,
                limit=10,
                request_id="p07-episode-history",
            )
            assert [item.version_id for item in history.items] == [
                APPROVE_VERSION_ID,
                COMPARE_VERSION_ID,
            ]
            assert [item.selected_revision.revision_id for item in history.items] == [
                approve_bundle.revision.revision_id,
                "revision_p07compare",
            ]
            assert history.items[1].version_status == "READY"
            approve_checks = service.review_checks(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                dataset_id=DATASET_ID,
                version_id=APPROVE_VERSION_ID,
                command=DatasetPageReviewChecksCommand(expected_status="REVIEWING"),
                if_match=approve_bundle.version.etag,
                request_id="p07-approve-checks",
            )
            approval = service.approve_review(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                dataset_id=DATASET_ID,
                version_id=APPROVE_VERSION_ID,
                command=DatasetPageApproveReviewCommand(
                    expected_status="REVIEWING",
                    review_token=approve_checks.data.review_token,
                ),
                if_match=approve_bundle.version.etag,
                idempotency_key="p07-postgres-approve-key",
                request_id="p07-approve",
            )
            assert approval.replayed is False
            assert approval.record.job.status == "SUCCEEDED"
            assert approval.record.job.result_ref == {
                "state": "CANDIDATE_READY",
                "manifest_id": approve_bundle.projection.manifest.manifest_id,
            }
            approval_replay = service.approve_review(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                dataset_id=DATASET_ID,
                version_id=APPROVE_VERSION_ID,
                command=DatasetPageApproveReviewCommand(
                    expected_status="REVIEWING",
                    review_token=approve_checks.data.review_token,
                ),
                if_match=approve_bundle.version.etag,
                idempotency_key="p07-postgres-approve-key",
                request_id="p07-approve-replay",
            )
            assert approval_replay.replayed is True
            assert approval_replay.record == approval.record

            return_checks = service.review_checks(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                dataset_id=DATASET_ID,
                version_id=RETURN_VERSION_ID,
                command=DatasetPageReviewChecksCommand(expected_status="REVIEWING"),
                if_match=return_bundle.version.etag,
                request_id="p07-return-checks",
            )
            stream = return_bundle.revision.streams[0]
            return_command = DatasetPageReturnReviewCommand(
                expected_status="REVIEWING",
                review_token=return_checks.data.review_token,
                finding_catalog_version=return_checks.data.finding_catalog.version,
                findings=(
                    {
                        "output_revision_id": return_bundle.revision.revision_id,
                        "episode_stream_id": stream.episode_stream_id,
                        "start_ns": "100",
                        "end_ns": "200",
                        "finding_type": "RANGE_QUALITY",
                        "severity": "HIGH",
                        "note": "PostgreSQL durable review finding.",
                    },
                ),
            )
            returned = service.return_review(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                dataset_id=DATASET_ID,
                version_id=RETURN_VERSION_ID,
                command=return_command,
                if_match=return_bundle.version.etag,
                idempotency_key="p07-postgres-return-key",
                request_id="p07-return",
            )
            assert returned.replayed is False
            assert returned.record.findings[0].immutable is True
            assert returned.record.successor_draft_id != returned.record.supersedes_draft_id
            returned_replay = service.return_review(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                dataset_id=DATASET_ID,
                version_id=RETURN_VERSION_ID,
                command=return_command,
                if_match=return_bundle.version.etag,
                idempotency_key="p07-postgres-return-key",
                request_id="p07-return-replay",
            )
            assert returned_replay.replayed is True
            assert returned_replay.record == returned.record

            refreshed = service.version_bootstrap(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                dataset_id=DATASET_ID,
                version_id=RETURN_VERSION_ID,
                request_id="p07-return-bootstrap",
            )
            assert refreshed.data.version.status == "RETURNED"
            episodes = service.list_episodes(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                dataset_id=DATASET_ID,
                version_id=RETURN_VERSION_ID,
                filters=DatasetPageEpisodeFilters(),
                sort="ordinal:asc,episode_id:asc",
                after=None,
                before=None,
                limit=20,
                snapshot_token=refreshed.data.snapshot_token,
                request_id="p07-return-episodes",
            )
            assert episodes.items[0].review_finding_count == "1"

            connection = _connection_factory(_app_dsn(superuser_dsn))()
            try:
                cursor = connection.cursor()
                try:
                    expected_counts = {
                        "dataset_registry.dataset_version_content_projections": 3,
                        "dataset_registry.dataset_version_review_decisions": 2,
                        "dataset_registry.dataset_version_review_findings": 1,
                        "dataset_registry.dataset_version_successor_drafts": 1,
                        "dataset_registry.dataset_version_async_jobs": 1,
                    }
                    for table, expected in expected_counts.items():
                        cursor.execute(f"SELECT count(*) FROM {table}")
                        row = cursor.fetchone()
                        assert isinstance(row, tuple)
                        assert row[0] == expected
                    cursor.execute(
                        "SELECT count(*) FROM dataset_registry.dataset_version_content_projections "
                        "WHERE dataset_id = %s",
                        (FOREIGN_DATASET_ID,),
                    )
                    row = cursor.fetchone()
                    assert isinstance(row, tuple)
                    assert row[0] == 0
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
                 WHERE project_id = %s AND dataset_id = %s AND version_id = %s
                """,
                (PROJECT_ID, PROJECT_ID, DATASET_ID, RETURN_VERSION_ID),
            )
            document, audit_count = cursor.fetchone()
            assert "credential" not in document
            assert "secret" not in document
            assert audit_count >= 6
    finally:
        _cleanup(superuser_dsn)
        _drop_app_role(superuser_dsn)
