from __future__ import annotations

import json
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from typing import Any
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import sql  # noqa: E402

from hc_data_platform.collection_tasks.models import (  # noqa: E402
    CollectionTaskRecord,
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
from hc_data_platform.security.psycopg import PsycopgIdempotencyStore  # noqa: E402

pytestmark = pytest.mark.integration


def database_dsn() -> str:
    value = os.getenv("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    return normalize_postgres_dsn(value)


@contextmanager
def request_scope(project_id: str, region_code: str | None = None) -> Iterator[None]:
    token = bind_request_context(
        RequestContext(
            project_id=project_id,
            region_code=region_code,
            subject_id="collection-task-integration",
            request_id=str(uuid4()),
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


def set_scope(connection: Any, project_id: str, region_code: str = "cn-test") -> None:
    connection.execute(
        """
        SELECT set_config('app.project_id', %s, false),
               set_config('app.region_code', %s, false),
               set_config('app.subject_id', 'collection-task-integration', false),
               set_config('app.request_id', %s, false)
        """,
        (project_id, region_code, str(uuid4())),
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
            sql.SQL("GRANT USAGE ON SCHEMA core, collection_tasks, ingest, public TO {}").format(
                sql.Identifier(role_name)
            )
        )
        for relation, privileges in (
            ("core.idempotency_records", "SELECT, INSERT, UPDATE"),
            ("core.audit_events", "INSERT"),
            ("collection_tasks.collection_tasks", "SELECT, INSERT, UPDATE"),
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


def create_task(service: CollectionTaskService, project_id: str, key: str) -> Any:
    return service.create(
        project_id=project_id,
        command=CreateCollectionTask(
            name="Postgres task",
            type="COLLECTION",
            scenario="integration",
        ),
        idempotency_key=key,
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
        connection.execute("DELETE FROM core.audit_events WHERE project_id = %s", (project_id,))
        set_scope(connection, project_id, "")
        connection.execute(
            "DELETE FROM core.idempotency_records WHERE project_id = %s", (project_id,)
        )


def test_postgres_progress_scope_idempotency_and_close_race() -> None:
    dsn = database_dsn()
    project_id = f"collection-task-it-{uuid4().hex}"
    role_name = f"collection_task_it_{uuid4().hex[:12]}"
    create_runtime_role(dsn, role_name)
    service = service_for(dsn, role_name)
    try:
        with request_scope(project_id):
            task = create_task(service, project_id, "aggregate-create")
            replay = create_task(service, project_id, "aggregate-create")
            assert task == replay
            task = service.update(
                project_id=project_id,
                collection_task_id=task.collection_task_id,
                command=UpdateCollectionTask(description="Audited update"),
                if_match=service.etag(task),
            )
            assert task.description == "Audited update"

        # Repository scope is fail-closed even if a caller supplies another tenant's ID.
        with request_scope(f"other-{project_id}"):
            hidden = service.repository.get(project_id, task.collection_task_id)
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
            progress = service.progress(project_id, task.collection_task_id, "cn-test")
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
