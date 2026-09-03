from __future__ import annotations

import json
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Barrier
from typing import Any
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import sql  # noqa: E402

from hc_data_platform.collection_tasks.models import (  # noqa: E402
    CollectionTaskAttainmentStatus,
    CollectionTaskRecord,
    CollectionTaskStatus,
    CollectionTaskTargetMetricStatus,
    CreateCollectionTask,
    UpdateCollectionTask,
)
from hc_data_platform.collection_tasks.postgres import (  # noqa: E402
    PostgresCollectionTaskRepository,
)
from hc_data_platform.collection_tasks.service import CollectionTaskService  # noqa: E402
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
from hc_data_platform.security.psycopg import PsycopgIdempotencyStore  # noqa: E402

pytestmark = pytest.mark.integration


def database_dsn() -> str:
    value = os.getenv("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    return normalize_postgres_dsn(value)


def organization_for(project_id: str) -> str:
    return f"org-{project_id}"


def ensure_organization_project(dsn: str, project_id: str) -> str:
    organization_id = organization_for(project_id)
    with psycopg.connect(dsn) as connection:
        connection.execute(
            """
            INSERT INTO registry.organization_projects (organization_id, project_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (organization_id, project_id),
        )
    return organization_id


@contextmanager
def request_scope(
    project_id: str,
    region_code: str | None = None,
    *,
    subject_id: str = "collection-task-integration",
) -> Iterator[None]:
    token = bind_request_context(
        RequestContext(
            organization_id=organization_for(project_id),
            project_id=project_id,
            region_code=region_code,
            subject_id=subject_id,
            request_id=str(uuid4()),
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


def set_scope(connection: Any, project_id: str, region_code: str = "cn-test") -> None:
    organization_id = organization_for(project_id)
    connection.execute(
        """
        INSERT INTO registry.organization_projects (organization_id, project_id)
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING
        """,
        (organization_id, project_id),
    )
    connection.execute(
        """
        SELECT set_config('app.organization_id', %s, false),
               set_config('app.project_id', %s, false),
               set_config('app.region_code', %s, false),
               set_config('app.subject_id', 'collection-task-integration', false),
               set_config('app.request_id', %s, false)
        """,
        (organization_id, project_id, region_code, str(uuid4())),
    )


def restricted_connection_factory(dsn: str, role_name: str) -> Any:
    scoped_factory = psycopg_connection_factory(dsn)

    def connect() -> Any:
        connection = scoped_factory()
        connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role_name)))
        return connection

    return connect


def create_runtime_role(dsn: str, role_name: str) -> None:
    with psycopg.connect(dsn) as connection:
        connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role_name)))
        connection.execute(
            sql.SQL(
                "GRANT USAGE ON SCHEMA access_control, core, collection_tasks, ingest, "
                "registry, public TO {}"
            ).format(sql.Identifier(role_name))
        )
        for relation, privileges in (
            ("core.idempotency_records", "SELECT, INSERT, UPDATE"),
            ("core.audit_events", "INSERT"),
            ("access_control.account_notifications", "SELECT, INSERT"),
            ("collection_tasks.collection_tasks", "SELECT, INSERT, UPDATE"),
            ("registry.organization_projects", "SELECT"),
            ("ingest.collection_jobs", "SELECT"),
            ("ingest.rollouts", "SELECT"),
            ("ingest.rollout_objects", "SELECT"),
            ("ingest.manifest_discoveries", "SELECT"),
            ("quality_rollout_summaries", "SELECT"),
        ):
            connection.execute(
                sql.SQL("GRANT {} ON {} TO {}").format(
                    sql.SQL(privileges),
                    sql.SQL(relation),
                    sql.Identifier(role_name),
                )
            )
        connection.execute(
            sql.SQL(
                "GRANT USAGE, SELECT ON SEQUENCE collection_tasks.task_code_sequence TO {}"
            ).format(sql.Identifier(role_name))
        )


def drop_runtime_role(dsn: str, role_name: str) -> None:
    with psycopg.connect(dsn) as connection:
        connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role_name)))
        connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role_name)))


def service_for(dsn: str, role_name: str) -> CollectionTaskService:
    factory = restricted_connection_factory(dsn, role_name)
    return CollectionTaskService(
        PostgresCollectionTaskRepository(factory),
        PsycopgIdempotencyStore(
            factory,
            response_decoder=CollectionTaskRecord.model_validate,
        ),
        cursor_secret="postgres-integration",
    )


def create_task(
    service: CollectionTaskService,
    project_id: str,
    key: str,
    *,
    command: CreateCollectionTask | None = None,
    created_by: str | None = None,
) -> Any:
    return service.create(
        organization_id=organization_for(project_id),
        project_id=project_id,
        command=command
        or CreateCollectionTask(
            name="Postgres task",
            type="COLLECTION",
            scenario="integration",
        ),
        idempotency_key=key,
        created_by=created_by,
    ).record


def insert_job(dsn: str, project_id: str, task_id: str, job_id: str) -> None:
    with psycopg.connect(dsn) as connection:
        set_scope(connection, project_id)
        connection.execute(
            """
            INSERT INTO ingest.collection_jobs (
                project_id, region_code, task_id, collection_job_id, robot_id,
                status, created_at, updated_at
            ) VALUES (%s, 'cn-test', %s, %s, 'manifest-device', 'COLLECTING', now(), now())
            """,
            (project_id, task_id, job_id),
        )


def insert_received(
    dsn: str,
    project_id: str,
    job_id: str,
    rollout_id: str,
    sequence_no: int,
    duration_seconds: float | None = None,
) -> None:
    digest = f"{sequence_no:064x}"
    session_id = str(uuid4())
    with psycopg.connect(dsn) as connection:
        set_scope(connection, project_id)
        connection.execute(
            """
            INSERT INTO ingest.rollouts (
                project_id, region_code, collection_job_id, rollout_id, sequence_no,
                robot_id, source_sha256, status, created_at, updated_at,
                collection_session_id, recording_request_id, data_package_id
            ) VALUES (%s, 'cn-test', %s, %s, %s, 'manifest-device', %s,
                      'RAW_COMMITTED', now(), now(), %s, %s, %s)
            """,
            (
                project_id,
                job_id,
                rollout_id,
                sequence_no,
                digest,
                f"session-{job_id}",
                f"request-{rollout_id}",
                rollout_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO ingest.upload_sessions (
                session_id, project_id, region_code, rollout_id, object_key,
                multipart_upload_id, expected_sha256, expected_size, expected_crc64,
                manifest_fingerprint, status, etag, failure_code, created_at,
                updated_at, completed_at, data_package_id, source_type
            ) VALUES (%s, %s, 'cn-test', %s, %s, NULL, %s, 10, 1, %s,
                      'RAW_COMMITTED', NULL, NULL, now(), now(), now(), %s,
                      'OBJECT_STORAGE_REFERENCE')
            """,
            (
                session_id,
                project_id,
                rollout_id,
                f"upload/{project_id}/{rollout_id}",
                digest,
                digest,
                rollout_id,
            ),
        )
        preflight = {
            "identifiers": {
                "robot_id": "manifest-device",
                "pico_instance_id": f"pico-{sequence_no}",
            },
            "discovery": {
                "cameras": [{"camera_id": f"camera-{sequence_no % 2}"}],
                "topics": [{"name": "/camera/front"}],
            },
        }
        if duration_seconds is not None:
            start_time = datetime(2026, 8, 19, tzinfo=timezone.utc)
            preflight["time_range"] = {
                "start_time": start_time.isoformat().replace("+00:00", "Z"),
                "end_time": (start_time + timedelta(seconds=duration_seconds))
                .isoformat()
                .replace("+00:00", "Z"),
            }
        connection.execute(
            """
            INSERT INTO ingest.manifest_discoveries (
                session_id, project_id, region_code, data_package_id,
                manifest_fingerprint, preflight_json, created_at
            ) VALUES (%s, %s, 'cn-test', %s, %s, %s::jsonb, now())
            """,
            (session_id, project_id, rollout_id, digest, json.dumps(preflight)),
        )
        connection.execute(
            """
            INSERT INTO ingest.rollout_objects (
                project_id, region_code, rollout_id, object_key, manifest_key,
                source_sha256, crc64, file_size, status, committed_at, data_package_id
            ) VALUES (%s, 'cn-test', %s, %s, %s, %s, 1, 10, 'COMMITTED', now(), %s)
            """,
            (
                project_id,
                rollout_id,
                f"raw/{project_id}/{rollout_id}",
                f"raw/{project_id}/{rollout_id}/manifest.json",
                digest,
                rollout_id,
            ),
        )


def insert_qc(dsn: str, project_id: str, rollout_id: str, status: str, index: int) -> None:
    profile_sha = "a" * 64
    source_sha = f"{index:064x}"
    report_sha = f"{index + 1000:064x}"
    with psycopg.connect(dsn) as connection:
        set_scope(connection, project_id)
        connection.execute(
            """
            INSERT INTO quality_profiles (
                project_id, profile_id, profile_version, schema_version,
                profile_sha256, profile_json
            ) VALUES (%s, 'integration', 1, 'quality-profile/v1', %s, '{}'::jsonb)
            ON CONFLICT (project_id, profile_id, profile_version) DO NOTHING
            """,
            (project_id, profile_sha),
        )
        connection.execute(
            """
            INSERT INTO qc_reports (
                report_sha256, project_id, region_code, rollout_id, source_sha256,
                profile_id, profile_version, profile_sha256, engine_version,
                schema_version, status, report_json
            ) VALUES (%s, %s, 'cn-test', %s, %s, 'integration', 1, %s,
                      'integration', 'qc-report/v1', %s, '{}'::jsonb)
            """,
            (report_sha, project_id, rollout_id, source_sha, profile_sha, status),
        )
        connection.execute(
            """
            INSERT INTO quality_rollout_summaries (
                project_id, region_code, rollout_id, source_sha256, profile_id,
                profile_version, engine_version, status, report_sha256
            ) VALUES (%s, 'cn-test', %s, %s, 'integration', 1,
                      'integration', %s, %s)
            """,
            (project_id, rollout_id, source_sha, status, report_sha),
        )


def race_close(
    barrier: Barrier,
    dsn: str,
    role_name: str,
    project_id: str,
    task_id: str,
    version: int,
) -> str:
    barrier.wait()
    with request_scope(project_id):
        repository = PostgresCollectionTaskRepository(restricted_connection_factory(dsn, role_name))
        return repository.close(
            organization_id=organization_for(project_id),
            project_id=project_id,
            collection_task_id=task_id,
            expected_version=version,
        ).status.value


def race_insert(
    barrier: Barrier,
    dsn: str,
    project_id: str,
    job_id: str,
    rollout_id: str,
) -> str:
    barrier.wait()
    try:
        insert_received(dsn, project_id, job_id, rollout_id, 1)
        return "inserted"
    except psycopg.errors.CheckViolation as exc:
        assert "COLLECTION_TASK_CLOSED" in str(exc)
        return "closed"


def cleanup(dsn: str, project_id: str) -> None:
    with psycopg.connect(dsn) as connection:
        set_scope(connection, project_id)
        connection.execute(
            "DELETE FROM quality_rollout_summaries WHERE project_id = %s", (project_id,)
        )
        connection.execute("DELETE FROM qc_reports WHERE project_id = %s", (project_id,))
        connection.execute("DELETE FROM quality_profiles WHERE project_id = %s", (project_id,))
        connection.execute(
            "DELETE FROM ingest.rollout_objects WHERE project_id = %s", (project_id,)
        )
        connection.execute(
            "DELETE FROM ingest.manifest_discoveries WHERE project_id = %s", (project_id,)
        )
        connection.execute("DELETE FROM ingest.upload_parts WHERE project_id = %s", (project_id,))
        connection.execute("DELETE FROM ingest.upload_objects WHERE project_id = %s", (project_id,))
        connection.execute(
            "DELETE FROM ingest.upload_sessions WHERE project_id = %s", (project_id,)
        )
        connection.execute("DELETE FROM ingest.rollouts WHERE project_id = %s", (project_id,))
        connection.execute(
            "DELETE FROM ingest.collection_jobs WHERE project_id = %s", (project_id,)
        )
        connection.execute(
            "DELETE FROM collection_tasks.collection_tasks WHERE project_id = %s", (project_id,)
        )
        connection.execute(
            "DELETE FROM core.audit_integrity_entries WHERE project_id = %s", (project_id,)
        )
        connection.execute("DELETE FROM core.audit_events WHERE project_id = %s", (project_id,))
        connection.execute(
            "DELETE FROM core.audit_integrity_heads WHERE project_id = %s", (project_id,)
        )
        set_scope(connection, project_id, "")
        connection.execute(
            "DELETE FROM core.idempotency_records WHERE project_id = %s", (project_id,)
        )
        connection.execute(
            "DELETE FROM registry.organization_projects WHERE organization_id = %s",
            (organization_for(project_id),),
        )


def test_postgres_target_constraint_rejects_nonpositive_dimensions() -> None:
    dsn = database_dsn()
    project_id = f"collection-task-target-{uuid4().hex}"
    with psycopg.connect(dsn) as connection:
        set_scope(connection, project_id)
        with pytest.raises(
            psycopg.errors.CheckViolation,
            match="collection_tasks_target_positive_dimensions_check",
        ):
            connection.execute(
                """
                INSERT INTO collection_tasks.collection_tasks (
                    collection_task_id, project_id, dataset_id, task_code, name,
                    task_type, scenario,
                    target_json, create_fingerprint
                ) VALUES (
                    %s, %s, %s, %s, 'invalid target', 'COLLECTION', 'integration',
                    %s::jsonb, %s
                )
                """,
                (
                    f"invalid-target-{uuid4().hex}",
                    project_id,
                    f"dataset_task_{uuid4().hex}",
                    str(uuid4().int % 100_000_000).zfill(8),
                    json.dumps({"package_count": 0}),
                    "f" * 64,
                ),
            )


def test_postgres_duration_attainment_cancel_and_reopen() -> None:
    dsn = database_dsn()
    project_id = f"collection-task-rules-{uuid4().hex}"
    ensure_organization_project(dsn, project_id)
    role_name = f"collection_task_rules_{uuid4().hex[:12]}"
    create_runtime_role(dsn, role_name)
    service = service_for(dsn, role_name)
    try:
        with request_scope(project_id):
            task = create_task(
                service,
                project_id,
                "rules-create",
                command=CreateCollectionTask(
                    name="Attainment task",
                    type="COLLECTION",
                    scenario="integration",
                    target={"package_count": 2, "duration_seconds": 30},
                    quality_threshold=1,
                ),
            )

        job_id = f"job-{uuid4().hex}"
        insert_job(dsn, project_id, task.collection_task_id, job_id)
        first_rollout = f"rollout-{uuid4().hex}"
        second_rollout = f"rollout-{uuid4().hex}"
        insert_received(dsn, project_id, job_id, first_rollout, 1, 10)
        insert_received(dsn, project_id, job_id, second_rollout, 2, 20)
        insert_qc(dsn, project_id, first_rollout, "PASS", 1)
        insert_qc(dsn, project_id, second_rollout, "PASS", 2)

        with request_scope(project_id, "cn-test"):
            progress = service.progress(
                organization_for(project_id),
                project_id,
                task.collection_task_id,
                "cn-test",
            )
        assert progress.captured_duration_seconds == 30
        assert progress.duration_observed_package_count == 2
        assert progress.duration_unknown_package_count == 0
        assert progress.attainment.status is CollectionTaskAttainmentStatus.ATTAINED
        assert progress.attainment.package_count is not None
        assert progress.attainment.package_count.status is CollectionTaskTargetMetricStatus.MET
        assert progress.attainment.duration_seconds is not None
        assert progress.attainment.duration_seconds.status is CollectionTaskTargetMetricStatus.MET
        assert progress.status is CollectionTaskStatus.ACTIVE

        with request_scope(project_id):
            cancelled = service.cancel(
                organization_id=organization_for(project_id),
                project_id=project_id,
                collection_task_id=task.collection_task_id,
                if_match=service.etag(task),
                idempotency_key="rules-cancel",
            ).record
            assert cancelled.status is CollectionTaskStatus.CANCELLED

        with pytest.raises(psycopg.errors.CheckViolation, match="COLLECTION_TASK_CANCELLED"):
            insert_received(
                dsn,
                project_id,
                job_id,
                f"after-cancel-{uuid4().hex}",
                3,
                5,
            )

        with request_scope(project_id):
            reopened = service.reopen(
                organization_id=organization_for(project_id),
                project_id=project_id,
                collection_task_id=task.collection_task_id,
                if_match=service.etag(cancelled),
                idempotency_key="rules-reopen",
            ).record
            assert reopened.status is CollectionTaskStatus.ACTIVE
        insert_received(
            dsn,
            project_id,
            job_id,
            f"after-reopen-{uuid4().hex}",
            4,
            5,
        )
    finally:
        cleanup(dsn, project_id)
        drop_runtime_role(dsn, role_name)


def test_postgres_lifecycle_transitions_create_one_owner_notification_each() -> None:
    dsn = database_dsn()
    project_id = f"collection-task-notification-{uuid4().hex}"
    owner_id = str(uuid4())
    role_name = f"collection_task_notification_{uuid4().hex[:12]}"
    ensure_organization_project(dsn, project_id)
    with psycopg.connect(dsn) as connection:
        connection.execute(
            """
            INSERT INTO access_control.accounts (
                principal_id, canonical_username, display_username, display_name,
                password_hash, password_changed_at
            ) VALUES (%s::uuid, %s, %s, %s, %s, now())
            """,
            (
                owner_id,
                f"collection-task-owner-{uuid4().hex}",
                "Collection task owner",
                "Collection task owner",
                "scrypt$test-not-used-by-collection-task-notification",
            ),
        )
    create_runtime_role(dsn, role_name)
    service = service_for(dsn, role_name)
    try:
        with request_scope(project_id, subject_id=owner_id):
            task = create_task(
                service,
                project_id,
                "notification-create",
                created_by=owner_id,
            )
            closed = service.close(
                organization_id=organization_for(project_id),
                project_id=project_id,
                collection_task_id=task.collection_task_id,
                if_match=service.etag(task),
                idempotency_key="notification-close",
            ).record
            assert (
                service.close(
                    organization_id=organization_for(project_id),
                    project_id=project_id,
                    collection_task_id=task.collection_task_id,
                    if_match=service.etag(task),
                    idempotency_key="notification-close-repeat",
                ).record
                == closed
            )
            reopened = service.reopen(
                organization_id=organization_for(project_id),
                project_id=project_id,
                collection_task_id=task.collection_task_id,
                if_match=service.etag(closed),
                idempotency_key="notification-reopen",
            ).record
            cancelled = service.cancel(
                organization_id=organization_for(project_id),
                project_id=project_id,
                collection_task_id=task.collection_task_id,
                if_match=service.etag(reopened),
                idempotency_key="notification-cancel",
            ).record
            assert cancelled.status is CollectionTaskStatus.CANCELLED

        with psycopg.connect(dsn) as connection:
            rows = connection.execute(
                """
                SELECT kind, resource_type, resource_id, event_key, state
                FROM access_control.account_notifications
                WHERE recipient_id = %s::uuid
                ORDER BY created_at, notification_id
                """,
                (owner_id,),
            ).fetchall()
        assert [(str(row[0]), str(row[1]), str(row[2]), str(row[4])) for row in rows] == [
            ("COLLECTION_TASK_CLOSED", "COLLECTION_TASK", task.collection_task_id, "UNREAD"),
            ("COLLECTION_TASK_REOPENED", "COLLECTION_TASK", task.collection_task_id, "UNREAD"),
            ("COLLECTION_TASK_CANCELLED", "COLLECTION_TASK", task.collection_task_id, "UNREAD"),
        ]
        assert len({str(row[3]) for row in rows}) == 3
    finally:
        cleanup(dsn, project_id)
        with psycopg.connect(dsn) as connection:
            connection.execute(
                "DELETE FROM access_control.account_notifications WHERE recipient_id = %s::uuid",
                (owner_id,),
            )
            connection.execute(
                "DELETE FROM access_control.accounts WHERE principal_id = %s::uuid",
                (owner_id,),
            )
        drop_runtime_role(dsn, role_name)


def test_postgres_notification_failure_rolls_back_lifecycle_transition() -> None:
    """A notification failure must not leave a closed task or its audit behind.

    Lifecycle notifications are deliberately persisted in the task transaction instead
    of a best-effort outbox callback.  A role without notification INSERT privilege
    gives this integration test a database-enforced failure after the task row and
    audit insert have both been attempted.
    """

    dsn = database_dsn()
    project_id = f"collection-task-notification-rollback-{uuid4().hex}"
    owner_id = str(uuid4())
    role_name = f"collection_task_notification_rollback_{uuid4().hex[:12]}"
    ensure_organization_project(dsn, project_id)
    with psycopg.connect(dsn) as connection:
        connection.execute(
            """
            INSERT INTO access_control.accounts (
                principal_id, canonical_username, display_username, display_name,
                password_hash, password_changed_at
            ) VALUES (%s::uuid, %s, %s, %s, %s, now())
            """,
            (
                owner_id,
                f"collection-task-owner-rollback-{uuid4().hex}",
                "Collection task owner rollback",
                "Collection task owner rollback",
                "scrypt$test-not-used-by-collection-task-notification",
            ),
        )
    create_runtime_role(dsn, role_name)
    service = service_for(dsn, role_name)
    try:
        with request_scope(project_id, subject_id=owner_id):
            task = create_task(
                service,
                project_id,
                "notification-rollback-create",
                created_by=owner_id,
            )

        with psycopg.connect(dsn) as connection:
            connection.execute(
                sql.SQL("REVOKE INSERT ON access_control.account_notifications FROM {}").format(
                    sql.Identifier(role_name)
                )
            )

        with (
            request_scope(project_id, subject_id=owner_id),
            pytest.raises(psycopg.errors.InsufficientPrivilege),
        ):
            service.close(
                organization_id=organization_for(project_id),
                project_id=project_id,
                collection_task_id=task.collection_task_id,
                if_match=service.etag(task),
                idempotency_key="notification-rollback-close",
            )

        with request_scope(project_id, subject_id=owner_id):
            persisted = service.detail(
                organization_for(project_id), project_id, task.collection_task_id
            )
        assert persisted is not None
        assert persisted.status is CollectionTaskStatus.ACTIVE
        assert persisted.version == task.version

        with psycopg.connect(dsn) as connection:
            audit_count = connection.execute(
                """
                SELECT count(*)
                FROM core.audit_events
                WHERE project_id = %s
                  AND resource_type = 'collection_task'
                  AND resource_id = %s
                  AND action = 'collection_task.closed'
                """,
                (project_id, task.collection_task_id),
            ).fetchone()[0]
            notification_count = connection.execute(
                """
                SELECT count(*)
                FROM access_control.account_notifications
                WHERE recipient_id = %s::uuid
                  AND resource_type = 'COLLECTION_TASK'
                  AND resource_id = %s
                """,
                (owner_id, task.collection_task_id),
            ).fetchone()[0]
        assert audit_count == 0
        assert notification_count == 0
    finally:
        cleanup(dsn, project_id)
        with psycopg.connect(dsn) as connection:
            connection.execute(
                "DELETE FROM access_control.account_notifications WHERE recipient_id = %s::uuid",
                (owner_id,),
            )
            connection.execute(
                "DELETE FROM access_control.accounts WHERE principal_id = %s::uuid",
                (owner_id,),
            )
        drop_runtime_role(dsn, role_name)


def test_postgres_progress_scope_idempotency_and_close_race() -> None:
    dsn = database_dsn()
    project_id = f"collection-task-it-{uuid4().hex}"
    ensure_organization_project(dsn, project_id)
    role_name = f"collection_task_it_{uuid4().hex[:12]}"
    create_runtime_role(dsn, role_name)
    service = service_for(dsn, role_name)
    try:
        with request_scope(project_id):
            task = create_task(service, project_id, "aggregate-create")
            replay = create_task(service, project_id, "aggregate-create")
            assert task == replay
            task = service.update(
                organization_id=organization_for(project_id),
                project_id=project_id,
                collection_task_id=task.collection_task_id,
                command=UpdateCollectionTask(description="Audited update"),
                if_match=service.etag(task),
            )
            assert task.description == "Audited update"

        # Repository scope is fail-closed even if a caller supplies another tenant's ID.
        with request_scope(f"other-{project_id}"):
            hidden = service.repository.get(
                organization_for(project_id),
                project_id,
                task.collection_task_id,
            )
            assert hidden is None

        job_id = f"job-{uuid4().hex}"
        insert_job(dsn, project_id, task.collection_task_id, job_id)
        outcomes = ("PASS", "PASS", "RISK", "REJECT", None)
        for index, outcome in enumerate(outcomes, start=1):
            rollout_id = f"rollout-{index}-{uuid4().hex}"
            insert_received(dsn, project_id, job_id, rollout_id, index)
            if outcome is not None:
                insert_qc(dsn, project_id, rollout_id, outcome, index)

        with request_scope(project_id, "cn-test"):
            progress = service.progress(
                organization_for(project_id),
                project_id,
                task.collection_task_id,
                "cn-test",
            )
        assert progress.received_package_count == 5
        assert progress.qc.evaluated_count == 4
        assert progress.qc.pass_count == 2
        assert progress.qc.risk_count == 1
        assert progress.qc.reject_count == 1
        assert progress.qc.pending_count == 1
        assert progress.qc.pass_rate.value == 0.5
        assert progress.observed_sources.device_ids == (
            "manifest-device",
            "pico-1",
            "pico-2",
            "pico-3",
            "pico-4",
            "pico-5",
        )
        assert progress.observed_sources.camera_ids == ("camera-0", "camera-1")
        assert progress.observed_sources.topic_names == ("/camera/front",)

        # The database trigger closes the service pre-check race and the
        # repository maps it back to the stable public problem code.
        with request_scope(project_id, "another-region"), pytest.raises(ProblemException) as locked:
            service.repository.update(
                organization_id=organization_for(project_id),
                project_id=project_id,
                collection_task_id=task.collection_task_id,
                expected_version=task.version,
                changes={"dataset_id": "dataset_reassignment_blocked"},
            )
        assert locked.value.problem.code == "COLLECTION_TASK_DATASET_REASSIGNMENT_BLOCKED"

        with request_scope(project_id):
            race_task = create_task(service, project_id, "race-create")
        race_job = f"race-job-{uuid4().hex}"
        insert_job(dsn, project_id, race_task.collection_task_id, race_job)
        barrier = Barrier(2)
        rollout_id = f"race-rollout-{uuid4().hex}"
        with ThreadPoolExecutor(max_workers=2) as pool:
            close_result = pool.submit(
                race_close,
                barrier,
                dsn,
                role_name,
                project_id,
                race_task.collection_task_id,
                race_task.version,
            )
            insert_result = pool.submit(
                race_insert,
                barrier,
                dsn,
                project_id,
                race_job,
                rollout_id,
            )
            assert close_result.result(timeout=10) == "CLOSED"
            first_insert = insert_result.result(timeout=10)
            assert first_insert in {"inserted", "closed"}

        # A genuinely new package always loses after close.
        with pytest.raises(psycopg.errors.CheckViolation, match="COLLECTION_TASK_CLOSED"):
            insert_received(
                dsn,
                project_id,
                race_job,
                f"after-close-{uuid4().hex}",
                2,
            )

        # If the raced package won before close, its identical insert is an allowed retry.
        if first_insert == "inserted":
            with psycopg.connect(dsn) as connection:
                set_scope(connection, project_id)
                connection.execute(
                    """
                    INSERT INTO ingest.rollouts (
                        project_id, region_code, collection_job_id, rollout_id, sequence_no,
                        robot_id, source_sha256, status, created_at, updated_at,
                        collection_session_id, recording_request_id, data_package_id
                    ) VALUES (%s, 'cn-test', %s, %s, 1, 'manifest-device', %s,
                              'RAW_COMMITTED', now(), now(), %s, %s, %s)
                    ON CONFLICT (project_id, rollout_id) DO NOTHING
                    """,
                    (
                        project_id,
                        race_job,
                        rollout_id,
                        f"{1:064x}",
                        f"session-{race_job}",
                        f"request-{rollout_id}",
                        rollout_id,
                    ),
                )

        with psycopg.connect(dsn) as connection:
            set_scope(connection, project_id)
            actions = connection.execute(
                """
                SELECT action FROM core.audit_events
                WHERE project_id = %s AND resource_type = 'collection_task'
                ORDER BY occurred_at
                """,
                (project_id,),
            ).fetchall()
            assert [row[0] for row in actions] == [
                "collection_task.created",
                "collection_task.updated",
                "collection_task.created",
                "collection_task.closed",
            ]
    finally:
        cleanup(dsn, project_id)
        drop_runtime_role(dsn, role_name)
