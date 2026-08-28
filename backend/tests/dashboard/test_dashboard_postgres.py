from __future__ import annotations

import asyncio
import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import sql  # noqa: E402
from psycopg.conninfo import conninfo_to_dict, make_conninfo  # noqa: E402

from hc_data_platform.core.context import (  # noqa: E402
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import (  # noqa: E402
    normalize_postgres_dsn,
    psycopg_connection_factory,
)
from hc_data_platform.core.errors import ProblemException  # noqa: E402
from hc_data_platform.core.migrations import apply_migrations  # noqa: E402
from hc_data_platform.dashboard.models import (  # noqa: E402
    DashboardPendingItemType,
    DashboardSectionStatus,
)
from hc_data_platform.dashboard.postgres import (  # noqa: E402
    SIGNAL_PIPELINE_QUERY,
    PostgresDashboardRepository,
    _activity_query,
    _pending_query,
)
from hc_data_platform.dashboard.repository import (  # noqa: E402
    DashboardScope,
    DashboardWindow,
)
from hc_data_platform.dashboard.service import DashboardService  # noqa: E402
from hc_data_platform.ingest.device_facts import (  # noqa: E402
    DeviceCaptureFactService,
    PostgresDeviceCaptureFactRepository,
)
from hc_data_platform.ingest.models import (  # noqa: E402
    DeviceCaptureEventType,
    DeviceCaptureFactRequest,
)
from hc_data_platform.security.auth import AuthContext  # noqa: E402
from hc_data_platform.security.capabilities import (  # noqa: E402
    CAPABILITY_ANNOTATION_REVIEW,
    CAPABILITY_DASHBOARD_READ,
    CAPABILITY_DATASETS_PUBLISH,
    CAPABILITY_DATASETS_READ,
    CAPABILITY_INGEST_UPLOAD,
)
from hc_data_platform.workflow.models import (  # noqa: E402
    JobRecord,
    JobStatus,
    WorkflowJobPersistenceActivityInput,
)
from hc_data_platform.workflow.postgres import (  # noqa: E402
    PostgresWorkflowJobRepository,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 18, 8, tzinfo=timezone.utc)
START = NOW - timedelta(hours=24)


def organization_for(project_id: str) -> str:
    return f"dashboard-organization-{project_id}"


def source_dsn() -> str:
    value = os.getenv("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    return normalize_postgres_dsn(value)


def migration_uri(base_dsn: str, database_name: str) -> str:
    """Keep asyncpg on the same isolated database used by psycopg fixtures."""

    parsed = urlsplit(base_dsn)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("HC_TEST_POSTGRES_DSN must use a PostgreSQL URI for migrations")
    return parsed._replace(path=f"/{quote(database_name, safe='')}").geturl()


@pytest.fixture(scope="module")
def isolated_dsn() -> str:
    base_dsn = source_dsn()
    parameters = conninfo_to_dict(base_dsn)
    database_name = f"hc_br01_dashboard_{uuid4().hex[:12]}"
    with psycopg.connect(base_dsn, autocommit=True) as admin:
        try:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        except psycopg.errors.InsufficientPrivilege:
            pytest.skip("HC_TEST_POSTGRES_DSN role cannot create an isolated database")
    dsn = make_conninfo(**{**parameters, "dbname": database_name})
    try:
        # The production dashboard is composed over the complete schema, not
        # just the tables that existed when the dashboard module was created.
        # Applying the manifest catches later source-table constraints before
        # this integration fixture can claim a production-compatible result.
        asyncio.run(apply_migrations(migration_uri(base_dsn, database_name)))
        seed_scope(dsn, "project-a", "cn-east", "a")
        seed_scope(dsn, "project-a", "cn-west", "west")
        seed_scope(dsn, "project-b", "cn-east", "other")
        yield dsn
    finally:
        with psycopg.connect(base_dsn, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (database_name,),
            )
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))


def actor(
    project_id: str,
    region_code: str,
    *,
    capabilities: tuple[str, ...] = (),
) -> AuthContext:
    return AuthContext(
        subject_id="dashboard-postgres-principal",
        project_ids=frozenset({project_id}),
        region_codes=frozenset({region_code}),
        roles=frozenset(),
        scope_pairs=frozenset({(project_id, region_code)}),
        scoped_capabilities=frozenset(
            (project_id, capability) for capability in (CAPABILITY_DASHBOARD_READ, *capabilities)
        ),
    )


@contextmanager
def request_scope(project_id: str, region_code: str) -> Any:
    token = bind_request_context(
        RequestContext(
            organization_id=organization_for(project_id),
            project_id=project_id,
            region_code=region_code,
            subject_id="dashboard-postgres-principal",
            request_id=f"request-{uuid4()}",
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


@contextmanager
def worker_scope(project_id: str, region_code: str) -> Any:
    token = bind_request_context(
        RequestContext(
            organization_id=organization_for(project_id),
            project_id=project_id,
            region_code=region_code,
            subject_id="hc-data-worker",
            request_id=f"worker-{uuid4()}",
            service_identity=True,
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


@contextmanager
def device_scope(project_id: str, region_code: str, subject_id: str) -> Any:
    token = bind_request_context(
        RequestContext(
            organization_id=organization_for(project_id),
            project_id=project_id,
            region_code=region_code,
            subject_id=subject_id,
            request_id=f"device-{uuid4()}",
            service_identity=True,
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


def seed_rollout(
    connection: Any,
    *,
    project_id: str,
    region_code: str,
    suffix: str,
    at: datetime,
    upload_status: str = "RAW_COMMITTED",
    committed: bool = False,
) -> tuple[str, UUID]:
    job_id = f"job-{suffix}"
    rollout_id = f"rollout-{suffix}"
    session_id = uuid4()
    digest = hashlib.sha256(suffix.encode()).hexdigest()
    task_code = str(int(digest[:16], 16) % 100_000_000).zfill(8)
    connection.execute(
        """
        INSERT INTO collection_tasks.collection_tasks (
            collection_task_id, project_id, dataset_id, task_code, name, task_type,
            scenario, description, target_json, quality_threshold, status,
            version, create_fingerprint, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, 'ROBOT', 'dashboard-integration', '',
                  '{"package_count": 1}'::jsonb, NULL, 'ACTIVE', 1, %s, %s, %s)
        ON CONFLICT (organization_id, project_id, collection_task_id) DO NOTHING
        """,
        (
            f"task-{suffix}",
            project_id,
            f"dataset_task_{digest[:32]}",
            task_code,
            f"Dashboard task {suffix}",
            digest,
            at,
            at,
        ),
    )
    connection.execute(
        """
        INSERT INTO ingest.collection_jobs (
            project_id, region_code, task_id, collection_job_id, robot_id,
            status, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, 'COLLECTING', %s, %s)
        """,
        (project_id, region_code, f"task-{suffix}", job_id, f"robot-{suffix}", at, at),
    )
    connection.execute(
        """
        INSERT INTO ingest.rollouts (
            project_id, region_code, collection_job_id, rollout_id, sequence_no,
            robot_id, source_sha256, status, created_at, updated_at,
            collection_session_id, recording_request_id, data_package_id
        ) VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            project_id,
            region_code,
            job_id,
            rollout_id,
            f"robot-{suffix}",
            digest,
            "FAILED" if upload_status == "FAILED" else "RAW_COMMITTED",
            at,
            at,
            f"collection-{suffix}",
            f"recording-{suffix}",
            f"package-{suffix}",
        ),
    )
    connection.execute(
        """
        INSERT INTO ingest.upload_sessions (
            session_id, project_id, region_code, rollout_id, object_key,
            multipart_upload_id, expected_sha256, expected_size, expected_crc64,
            manifest_fingerprint, status, failure_code, created_at, updated_at,
            completed_at, data_package_id, source_type
        ) VALUES (%s, %s, %s, %s, %s, NULL, %s, 100, 1, %s, %s, %s, %s, %s, %s, %s,
                  'OBJECT_STORAGE_REFERENCE')
        """,
        (
            session_id,
            project_id,
            region_code,
            rollout_id,
            f"raw/{suffix}/source.mcap",
            digest,
            digest,
            upload_status,
            "UPLOAD_FAILED" if upload_status == "FAILED" else None,
            at,
            at,
            at if committed else None,
            f"package-{suffix}",
        ),
    )
    if committed:
        connection.execute(
            """
            INSERT INTO ingest.rollout_objects (
                project_id, region_code, rollout_id, object_key, manifest_key,
                source_sha256, crc64, file_size, status, committed_at, data_package_id
            ) VALUES (%s, %s, %s, %s, %s, %s, 1, 100, 'COMMITTED', %s, %s)
            """,
            (
                project_id,
                region_code,
                rollout_id,
                f"raw/{suffix}/committed.mcap",
                f"raw/{suffix}/manifest.json",
                digest,
                at,
                f"package-{suffix}",
            ),
        )
    return rollout_id, session_id


def seed_annotation(
    connection: Any,
    *,
    project_id: str,
    rollout_id: str,
    suffix: str,
    state: str,
    at: datetime,
) -> str:
    task_id = f"annotation-{suffix}"
    connection.execute(
        """
        INSERT INTO annotation.annotation_tasks (
            task_id, project_id, dataset_id, dataset_version, assignee_id,
            current_revision, state_version, status, submitted_revision, submitted_by,
            approved_revision, approved_review_id, etag, rollout_id, created_at, updated_at,
            base_lance_version
        ) VALUES (%s, %s, %s, 1, NULL, 0, 0, 'DRAFT', NULL, NULL, NULL, NULL, %s, %s, %s, %s, 1)
        """,
        (task_id, project_id, f"dataset-{suffix}", f"etag-{suffix}", rollout_id, at, at),
    )
    connection.execute(
        """
        INSERT INTO annotation.annotation_revisions (
            task_id, revision, parent_revision, author_id, client_mutation_id, created_at,
            base_lance_version
        ) VALUES (%s, 0, NULL, 'author', %s, %s, 1)
        """,
        (task_id, f"mutation-{suffix}", at),
    )
    if state == "SUBMITTED":
        connection.execute(
            """
            UPDATE annotation.annotation_tasks
            SET status = 'SUBMITTED', submitted_revision = 0, submitted_by = 'author',
                updated_at = %s
            WHERE task_id = %s
            """,
            (at, task_id),
        )
    elif state == "APPROVED":
        review_id = f"review-{suffix}"
        connection.execute(
            """
            INSERT INTO annotation.annotation_reviews (
                review_id, task_id, revision, reviewer_id, decision, comment, created_at
            ) VALUES (%s, %s, 0, 'reviewer', 'APPROVE', '', %s)
            """,
            (review_id, task_id, at),
        )
        connection.execute(
            """
            UPDATE annotation.annotation_tasks
            SET status = 'APPROVED', submitted_revision = 0, submitted_by = 'author',
                approved_revision = 0, approved_review_id = %s, updated_at = %s
            WHERE task_id = %s
            """,
            (review_id, at, task_id),
        )
    return task_id


def seed_scope(dsn: str, project_id: str, region_code: str, prefix: str) -> None:
    with psycopg.connect(dsn) as connection:
        connection.execute(
            """
            INSERT INTO registry.organization_projects (organization_id, project_id)
            VALUES (%s, %s) ON CONFLICT DO NOTHING
            """,
            (organization_for(project_id), project_id),
        )
        connection.execute(
            """
            SELECT set_config('app.organization_id', %s, false),
                   set_config('app.project_id', %s, false),
                   set_config('app.region_code', %s, false),
                   set_config('app.subject_id', 'dashboard-seed', false)
            """,
            (organization_for(project_id), project_id, region_code),
        )
        failed_rollout, _ = seed_rollout(
            connection,
            project_id=project_id,
            region_code=region_code,
            suffix=f"{prefix}-failed",
            at=NOW - timedelta(minutes=20),
            upload_status="FAILED",
        )
        del failed_rollout
        qc_rollout, _ = seed_rollout(
            connection,
            project_id=project_id,
            region_code=region_code,
            suffix=f"{prefix}-qc",
            at=NOW - timedelta(minutes=10),
            committed=True,
        )
        device_fact_id = uuid4()
        device_source_hash = hashlib.sha256(f"device-{prefix}".encode()).hexdigest()
        connection.execute(
            """
            INSERT INTO ingest.device_capture_facts (
                fact_id, project_id, region_code, source_event_id, event_type,
                collection_task_id, collection_job_id, recording_request_id,
                data_package_id, robot_id, device_id, device_sequence_no,
                capture_started_at, capture_ended_at, saved_at,
                local_artifact_size, local_artifact_sha256, recorder_version,
                occurred_at, received_at, producer_subject_id, source_fingerprint, fact_json
            ) VALUES (
                %s, %s, %s, %s, 'SAVED', %s, %s, %s, %s, %s, %s, 1,
                %s, %s, %s, 100, %s, 'recorder-integration', %s, %s,
                'device-agent', %s, '{}'::jsonb
            )
            """,
            (
                device_fact_id,
                project_id,
                region_code,
                f"device-event-{prefix}",
                f"task-{prefix}-qc",
                f"job-{prefix}-qc",
                f"recording-{prefix}-qc",
                f"package-{prefix}-qc",
                f"robot-{prefix}-qc",
                f"device-{prefix}",
                NOW - timedelta(seconds=60),
                NOW,
                NOW,
                device_source_hash,
                NOW,
                NOW,
                device_source_hash,
            ),
        )
        review_rollout, _ = seed_rollout(
            connection,
            project_id=project_id,
            region_code=region_code,
            suffix=f"{prefix}-review",
            at=NOW - timedelta(minutes=30),
        )
        publish_rollout, _ = seed_rollout(
            connection,
            project_id=project_id,
            region_code=region_code,
            suffix=f"{prefix}-publish",
            at=NOW - timedelta(minutes=40),
        )
        published_rollout, _ = seed_rollout(
            connection,
            project_id=project_id,
            region_code=region_code,
            suffix=f"{prefix}-published",
            at=NOW - timedelta(minutes=50),
        )

        profile_hash = hashlib.sha256(f"profile-{prefix}".encode()).hexdigest()
        report_hash = hashlib.sha256(f"report-{prefix}".encode()).hexdigest()
        connection.execute(
            """
            INSERT INTO quality_profiles (
                project_id, profile_id, profile_version, schema_version,
                profile_sha256, profile_json, created_at
            ) VALUES (%s, %s, 1, 'quality-profile/v1', %s, '{}'::jsonb, %s)
            """,
            (project_id, f"profile-{prefix}", profile_hash, NOW - timedelta(minutes=12)),
        )
        source_hash = hashlib.sha256(f"{prefix}-qc".encode()).hexdigest()
        connection.execute(
            """
            INSERT INTO qc_reports (
                report_sha256, project_id, region_code, rollout_id, source_sha256,
                profile_id, profile_version, profile_sha256, engine_version,
                schema_version, status, report_json, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, 1, %s, 'engine-v1', 'qc-report/v1',
                      'REJECT', '{}'::jsonb, %s)
            """,
            (
                report_hash,
                project_id,
                region_code,
                qc_rollout,
                source_hash,
                f"profile-{prefix}",
                profile_hash,
                NOW - timedelta(minutes=9),
            ),
        )
        connection.execute(
            """
            INSERT INTO quality_rollout_summaries (
                project_id, region_code, rollout_id, source_sha256, profile_id,
                profile_version, engine_version, status, report_sha256, updated_at
            ) VALUES (%s, %s, %s, %s, %s, 1, 'engine-v1', 'REJECT', %s, %s)
            """,
            (
                project_id,
                region_code,
                qc_rollout,
                source_hash,
                f"profile-{prefix}",
                report_hash,
                NOW - timedelta(minutes=8),
            ),
        )
        seed_annotation(
            connection,
            project_id=project_id,
            rollout_id=review_rollout,
            suffix=f"{prefix}-review",
            state="SUBMITTED",
            at=NOW - timedelta(minutes=30),
        )
        seed_annotation(
            connection,
            project_id=project_id,
            rollout_id=publish_rollout,
            suffix=f"{prefix}-publish",
            state="APPROVED",
            at=NOW - timedelta(minutes=40),
        )
        seed_annotation(
            connection,
            project_id=project_id,
            rollout_id=published_rollout,
            suffix=f"{prefix}-published",
            state="APPROVED",
            at=NOW - timedelta(minutes=50),
        )

        publication_hash = hashlib.sha256(f"publication-{prefix}".encode()).hexdigest()
        publication_manifest = json.dumps(
            {"rollouts": [{"rollout_id": published_rollout}]},
            sort_keys=True,
            separators=(",", ":"),
        )
        connection.execute(
            """
            INSERT INTO publishing.dataset_versions (
                project_id, dataset_id, dataset_version, base_lance_version,
                content_hash, manifest_json, created_at
            ) VALUES (%s, %s, '1', 'v1', %s, %s::jsonb, %s)
            """,
            (
                project_id,
                f"dataset-{prefix}-published",
                publication_hash,
                publication_manifest,
                NOW - timedelta(minutes=5),
            ),
        )
        connection.execute(
            """
            INSERT INTO publishing.rollout_publication_lineage (
                project_id, region_code, rollout_id, dataset_id, dataset_version,
                base_lance_version, publication_identity, published_at, lineage_source
            ) VALUES (%s, %s, %s, %s, '1', 'v1', %s, %s, 'FORWARD')
            """,
            (
                project_id,
                region_code,
                published_rollout,
                f"dataset-{prefix}-published",
                publication_hash,
                NOW - timedelta(minutes=5),
            ),
        )


def test_postgres_exact_scope_event_pending_cursor_audit_lineage_and_indexes(
    isolated_dsn: str,
) -> None:
    capabilities = (
        CAPABILITY_INGEST_UPLOAD,
        CAPABILITY_DATASETS_READ,
        CAPABILITY_ANNOTATION_REVIEW,
        CAPABILITY_DATASETS_PUBLISH,
    )
    auth = actor("project-a", "cn-east", capabilities=capabilities)
    repository = PostgresDashboardRepository(
        psycopg_connection_factory(isolated_dsn),
        statement_timeout_ms=5000,
    )
    service = DashboardService(repository, cursor_secret="postgres-cursor", clock=lambda: NOW)
    common = {
        "auth": auth,
        "project_id": "project-a",
        "region_code": "cn-east",
        "range_start": START,
        "range_end": NOW,
        "timezone_name": "Asia/Shanghai",
    }
    with request_scope("project-a", "cn-east"):
        first_activity = service.activity(**common, cursor=None, limit=2)
        assert first_activity.activity.page_info is not None
        assert first_activity.activity.page_info.has_next_page is True
        assert first_activity.activity.page_info.end_cursor is not None
        second_activity = service.activity(
            **common,
            cursor=first_activity.activity.page_info.end_cursor,
            limit=100,
        )
        activity = (*first_activity.activity.items, *second_activity.activity.items)
        assert len({item.event_id for item in activity}) == len(activity)
        assert {item.event_type.value for item in activity} == {
            "UPLOAD_COMMITTED",
            "QC_COMPLETED",
            "TAG_REVIEW_DECIDED",
            "DATASET_PUBLISHED",
        }
        assert all(
            "west" not in item.source_id and "other" not in item.source_id for item in activity
        )

        first_pending = service.pending_items(**common, cursor=None, limit=2)
        assert first_pending.pending_items.page_info is not None
        assert first_pending.pending_items.page_info.end_cursor is not None
        second_pending = service.pending_items(
            **common,
            cursor=first_pending.pending_items.page_info.end_cursor,
            limit=100,
        )
        pending = (*first_pending.pending_items.items, *second_pending.pending_items.items)
        assert [item.item_type for item in pending] == [
            DashboardPendingItemType.QC_ANOMALY,
            DashboardPendingItemType.UPLOAD_FAILED,
            DashboardPendingItemType.TAG_REVIEW_PENDING,
            DashboardPendingItemType.PUBLICATION_PENDING,
        ]
        assert all(item.target.deep_link is not None for item in pending)
        assert not any("published" in item.source_id for item in pending)

        snapshot = service.snapshot(**common)
        assert [item.count for item in snapshot.sections.signal_pipeline.stage_counts] == [
            5,
            1,
            1,
            0,
            0,
            3,
            2,
            1,
        ]
        published = snapshot.sections.signal_pipeline.published_region
        assert published.status is DashboardSectionStatus.READY
        assert published.lineage_count == 1
        assert published.publication_count == 1

        task_list = service.task_status(
            auth=auth,
            project_id="project-a",
            region_code="cn-east",
        )
        assert len(task_list.tasks) == 5
        assert task_list.selected is None
        assert task_list.pipeline.task_count == 5
        assert task_list.pipeline.package_count == 5
        assert task_list.pipeline.stages[0].succeeded == 5
        selected_task = service.task_status(
            auth=auth,
            project_id="project-a",
            region_code="cn-east",
            task_id="task-a-qc",
        )
        assert selected_task.selected is not None
        assert selected_task.selected.task.lifecycle.value == "ACTIVE"
        assert selected_task.selected.qc.rejected == 1
        assert selected_task.selected.standardization.blocked_by_quality == 0
        assert selected_task.selected.standardization.isolated_by_quality == 1
        assert selected_task.selected.blocker_count == 0
        assert selected_task.selected.actions[0].action == "VIEW_QC_ANOMALIES"
        assert selected_task.selected.task.device_progress.captured_count == 1
        assert selected_task.selected.task.device_progress.saved_count == 1
        assert selected_task.selected.task.device_progress.confirmed_duration_seconds == 60

        observations = repository.collection_observations(
            auth=auth,
            scope=DashboardScope(auth.subject_id, "project-a", "cn-east"),
            window=DashboardWindow(START, NOW, "Asia/Shanghai"),
            limit=100,
        )
        assert len(observations.items) == 5
        assert {item.project_id for item in observations.items} == {"project-a"}
        assert {item.region_code for item in observations.items} == {"cn-east"}

        with pytest.raises(ProblemException) as cross_region:
            repository.business_events(
                auth=actor("project-a", "cn-west", capabilities=capabilities),
                scope=DashboardScope(auth.subject_id, "project-a", "cn-east"),
                window=DashboardWindow(START, NOW, "UTC"),
                limit=100,
            )
        assert cross_region.value.problem.code == "REGION_SCOPE_DENIED"

    with psycopg.connect(isolated_dsn) as connection:
        audits = connection.execute(
            """
            SELECT actor_id, region_code, action, resource_id, details
            FROM core.audit_events
            WHERE project_id = 'project-a' AND action = 'dashboard.query'
            ORDER BY occurred_at, audit_id
            """
        ).fetchall()
        assert {row[3] for row in audits} == {
            "activity",
            "pending-items",
            "snapshot",
            "task-status",
        }
        assert all(row[0] == auth.subject_id and row[1] == "cn-east" for row in audits)
        assert all(
            set(row[4]) == {"endpoint", "query_fingerprint", "result_status"} for row in audits
        )
        index_names = {
            row[0]
            for row in connection.execute(
                """
                SELECT indexname FROM pg_indexes
                WHERE indexname LIKE 'dashboard_%'
                   OR indexname = 'rollout_publication_lineage_scope_published_idx'
                """
            ).fetchall()
        }
        assert {
            "dashboard_rollout_objects_scope_committed_idx",
            "dashboard_rollouts_scope_created_idx",
            "dashboard_upload_sessions_state_opened_idx",
            "dashboard_qc_reports_scope_event_idx",
            "dashboard_quality_pending_scope_idx",
            "dashboard_annotation_tasks_state_opened_idx",
            "dashboard_annotation_reviews_event_idx",
            "rollout_publication_lineage_scope_published_idx",
        } <= index_names


def test_postgres_capability_intersection_and_half_open_boundaries(isolated_dsn: str) -> None:
    upload_actor = actor("project-a", "cn-east", capabilities=(CAPABILITY_INGEST_UPLOAD,))
    repository = PostgresDashboardRepository(
        psycopg_connection_factory(isolated_dsn), statement_timeout_ms=5000
    )
    with request_scope("project-a", "cn-east"):
        page = repository.pending_facts(
            auth=upload_actor,
            scope=DashboardScope(upload_actor.subject_id, "project-a", "cn-east"),
            window=DashboardWindow(START, NOW, "UTC"),
            allowed_types=(DashboardPendingItemType.UPLOAD_FAILED,),
            limit=100,
        )
        assert [item.item_type for item in page.items] == [DashboardPendingItemType.UPLOAD_FAILED]
        at_exclusive_end = repository.business_events(
            auth=upload_actor,
            scope=DashboardScope(upload_actor.subject_id, "project-a", "cn-east"),
            window=DashboardWindow(NOW, NOW + timedelta(hours=1), "UTC"),
            limit=100,
        )
        assert at_exclusive_end.items == ()


def test_postgres_workflow_job_writer_is_idempotent_and_keeps_latest_state(
    isolated_dsn: str,
) -> None:
    repository = PostgresWorkflowJobRepository(psycopg_connection_factory(isolated_dsn))
    base = JobRecord(
        workflow_id="ingest-rollout:v1:project-a:writer-test",
        workflow_run_id="run-writer-test",
        job_type="IngestRolloutWorkflow",
        project_id="project-a",
        resource_id="writer-test",
        status=JobStatus.RUNNING,
        stage="alignment",
        attempt=1,
        created_at=NOW - timedelta(minutes=1),
        updated_at=NOW - timedelta(seconds=1),
    )
    terminal = base.model_copy(
        update={
            "status": JobStatus.TECHNICAL_FAILED,
            "stage": "lance_commit",
            "error_code": "LANCE_WRITE_FAILED",
            "updated_at": NOW,
        }
    )
    with worker_scope("project-a", "cn-east"):
        repository.put_job(
            WorkflowJobPersistenceActivityInput(
                organization_id=organization_for("project-a"),
                region_code="cn-east",
                job=base,
            )
        )
        repository.put_job(
            WorkflowJobPersistenceActivityInput(
                organization_id=organization_for("project-a"),
                region_code="cn-east",
                job=terminal,
            )
        )

    with psycopg.connect(isolated_dsn) as connection:
        connection.execute(
            """
            SELECT set_config('app.organization_id', %s, false),
                   set_config('app.project_id', 'project-a', false),
                   set_config('app.region_code', 'cn-east', false),
                   set_config('app.subject_id', 'dashboard-postgres-principal', false),
                   set_config('app.service_identity', 'true', false)
            """,
            (organization_for("project-a"),),
        )
        row = connection.execute(
            """
            SELECT organization_id, status, stage, error_code, count(*) OVER ()
            FROM workflow.jobs WHERE workflow_id = %s
            """,
            (terminal.workflow_id,),
        ).fetchone()
    assert row == (
        organization_for("project-a"),
        "TECHNICAL_FAILED",
        "lance_commit",
        "LANCE_WRITE_FAILED",
        1,
    )


def test_postgres_device_fact_writer_is_idempotent_and_rejects_changed_retry(
    isolated_dsn: str,
) -> None:
    subject_id = "device-agent:dashboard-integration"
    auth = AuthContext(
        subject_id=subject_id,
        project_ids=frozenset({"project-a"}),
        region_codes=frozenset({"cn-east"}),
        roles=frozenset({"uploader"}),
        service_identity=True,
        organization_ids=frozenset({organization_for("project-a")}),
        organization_scope_triples=frozenset(
            {(organization_for("project-a"), "project-a", "cn-east")}
        ),
    )
    service = DeviceCaptureFactService(
        PostgresDeviceCaptureFactRepository(psycopg_connection_factory(isolated_dsn))
    )
    request = DeviceCaptureFactRequest(
        schema_version="device-capture-fact/v1",
        source_event_id="postgres-device-event",
        event_type=DeviceCaptureEventType.SAVED,
        collection_task_id="task-a-failed",
        collection_job_id="job-a-failed",
        recording_request_id="recording-device-extra",
        data_package_id="package-device-extra",
        robot_id="robot-a-failed",
        device_id="device-dashboard-integration",
        device_sequence_no=2,
        capture_started_at=NOW - timedelta(seconds=15),
        capture_ended_at=NOW - timedelta(seconds=5),
        saved_at=NOW - timedelta(seconds=4),
        local_artifact_size=1024,
        local_artifact_sha256="a" * 64,
        recorder_version="recorder-integration",
        occurred_at=NOW - timedelta(seconds=3),
    )
    call = {
        "auth": auth,
        "organization_id": organization_for("project-a"),
        "project_id": "project-a",
        "region_code": "cn-east",
    }
    with device_scope("project-a", "cn-east", subject_id):
        first = service.record(**call, request=request)
        retried = service.record(**call, request=request)
        assert first == retried
        with pytest.raises(ProblemException) as changed:
            service.record(
                **call,
                request=request.model_copy(update={"recorder_version": "recorder-changed"}),
            )
    assert changed.value.problem.code == "DEVICE_CAPTURE_FACT_IMMUTABLE"


def test_postgres_explain_business_feeds_keep_scope_range_order_and_limit(
    isolated_dsn: str,
) -> None:
    scope_params: tuple[object, ...] = (
        "dashboard-postgres-principal",
        "project-a",
        "cn-east",
        START,
        NOW,
        "dashboard-postgres-principal",
    )
    with psycopg.connect(isolated_dsn) as connection:
        connection.execute(
            """
            SELECT set_config('app.organization_id', %s, false),
                   set_config('app.project_id', 'project-a', false),
                   set_config('app.region_code', 'cn-east', false),
                   set_config('app.subject_id', 'dashboard-postgres-principal', false),
                   set_config('enable_seqscan', 'off', false)
            """,
            (organization_for("project-a"),),
        )
        signal_plan = connection.execute(
            "EXPLAIN (FORMAT JSON) " + SIGNAL_PIPELINE_QUERY,
            scope_params,
        ).fetchone()
        activity_plan = connection.execute(
            "EXPLAIN (FORMAT JSON) " + _activity_query(True),
            (*scope_params, None, None, None, None, 101),
        ).fetchone()
        pending_plan = connection.execute(
            "EXPLAIN (FORMAT JSON) " + _pending_query(True),
            (
                *scope_params,
                [item.value for item in DashboardPendingItemType],
                None,
                None,
                None,
                None,
                101,
            ),
        ).fetchone()

    assert signal_plan is not None
    assert activity_plan is not None
    assert pending_plan is not None
    signal_json = json.dumps(signal_plan[0], sort_keys=True)
    activity_json = json.dumps(activity_plan[0], sort_keys=True)
    pending_json = json.dumps(pending_plan[0], sort_keys=True)
    assert '"Node Type": "Limit"' in activity_json
    assert '"Node Type": "Limit"' in pending_json
    assert "dashboard_rollouts_scope_created_idx" in signal_json
    assert "dashboard_rollout_objects_scope_committed_idx" in activity_json
    assert "dashboard_quality_pending_scope_idx" in pending_json
