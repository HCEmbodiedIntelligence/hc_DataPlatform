from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import sql  # noqa: E402

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
from hc_data_platform.ingest.models import (  # noqa: E402
    ManifestFileV1,
    RolloutManifestV1,
    UploadSourceType,
    UploadStatus,
    raw_object_key,
)
from hc_data_platform.ingest.persistence import IngestPersistencePort  # noqa: E402
from hc_data_platform.ingest.ports import InMemoryObjectStorage, crc64_ecma  # noqa: E402
from hc_data_platform.ingest.postgres import PostgresIngestPersistence  # noqa: E402
from hc_data_platform.ingest.service import UploadSessionService  # noqa: E402

pytestmark = pytest.mark.integration


def _database_dsn() -> str:
    value = os.getenv("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    return normalize_postgres_dsn(value)


@contextmanager
def _scope(project_id: str, region_code: str) -> Iterator[None]:
    token = bind_request_context(
        RequestContext(
            project_id=project_id,
            region_code=region_code,
            subject_id="ingest-postgres-integration",
            request_id=str(uuid4()),
            service_identity=True,
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


def _manifest(project_id: str, body: bytes) -> RolloutManifestV1:
    start = datetime(2026, 8, 17, tzinfo=timezone.utc)
    digest = hashlib.sha256(body).hexdigest()
    checksum = crc64_ecma(body)
    return RolloutManifestV1(
        project_id=project_id,
        task_id="task-postgres",
        collection_job_id="job-postgres",
        rollout_id="rollout-postgres",
        collection_session_id="session-postgres",
        recording_request_id="request-postgres",
        data_package_id="package-postgres",
        sequence_no=1,
        robot_id="robot-postgres",
        start_time=start,
        end_time=start + timedelta(seconds=1),
        cameras=[],
        topics=[],
        expected_topics=["/required/missing"],
        actual_topics=[],
        files=[
            ManifestFileV1(
                path="recording.mcap",
                size=len(body),
                sha256=digest,
                crc64=checksum,
            )
        ],
        file_size=len(body),
        sha256=digest,
        crc64=checksum,
        compression="none",
        recorder_version="postgres-integration",
    )


def _apply_migrations(dsn: str) -> None:
    root = Path(__file__).parents[2] / "migrations"
    scripts = (
        root / "security" / "001_core.sql",
        root / "ingest" / "001_ingest.sql",
        root / "ingest" / "002_package_manifest_uploads.sql",
        root / "ingest" / "003_automatic_workflow_trigger.sql",
        root / "security" / "003_outbox_dispatch.sql",
    )
    with psycopg.connect(dsn, autocommit=True) as connection:
        for path in scripts:
            connection.execute(path.read_text(encoding="utf-8"))


def _create_role(dsn: str, role: str) -> None:
    with psycopg.connect(dsn) as connection:
        connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA core, ingest TO {}").format(sql.Identifier(role))
        )
        connection.execute(
            sql.SQL("GRANT SELECT, INSERT, UPDATE ON core.idempotency_records TO {}").format(
                sql.Identifier(role)
            )
        )
        for table in ("audit_events", "outbox_events"):
            connection.execute(
                sql.SQL("GRANT SELECT, INSERT, UPDATE ON core.{} TO {}").format(
                    sql.Identifier(table), sql.Identifier(role)
                )
            )
        if connection.execute(
            "SELECT to_regclass('collection_tasks.collection_tasks') IS NOT NULL"
        ).fetchone() == (True,):
            connection.execute(
                sql.SQL("GRANT USAGE ON SCHEMA collection_tasks TO {}").format(sql.Identifier(role))
            )
            connection.execute(
                sql.SQL("GRANT SELECT, UPDATE ON collection_tasks.collection_tasks TO {}").format(
                    sql.Identifier(role)
                )
            )
        for table in (
            "collection_jobs",
            "rollouts",
            "upload_sessions",
            "upload_objects",
            "upload_parts",
            "rollout_objects",
            "manifest_discoveries",
            "workflow_triggers",
        ):
            connection.execute(
                sql.SQL("GRANT SELECT, INSERT, UPDATE ON ingest.{} TO {}").format(
                    sql.Identifier(table),
                    sql.Identifier(role),
                )
            )


def _restricted_factory(dsn: str, role: str) -> Callable[[], Any]:
    scoped = psycopg_connection_factory(dsn)

    def connect() -> Any:
        connection = scoped()
        connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        return connection

    return connect


def _cleanup(dsn: str, project_id: str, role: str) -> None:
    with psycopg.connect(dsn) as connection:
        for table in (
            "workflow_triggers",
            "manifest_discoveries",
            "upload_parts",
            "upload_objects",
            "rollout_objects",
            "upload_sessions",
            "rollouts",
            "collection_jobs",
        ):
            connection.execute(
                sql.SQL("DELETE FROM ingest.{} WHERE project_id = %s").format(
                    sql.Identifier(table)
                ),
                (project_id,),
            )
        connection.execute(
            "DELETE FROM core.idempotency_records WHERE project_id = %s",
            (project_id,),
        )
        connection.execute("DELETE FROM core.outbox_events WHERE project_id = %s", (project_id,))
        connection.execute("DELETE FROM core.audit_events WHERE project_id = %s", (project_id,))
        connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_postgres_resume_idempotency_manifest_discovery_and_rls() -> None:
    dsn = _database_dsn()
    _apply_migrations(dsn)
    suffix = uuid4().hex[:12]
    project_id = f"ingest-it-{suffix}"
    role = f"ingest_it_role_{suffix}"
    region = "cn-test"
    _create_role(dsn, role)
    storage = InMemoryObjectStorage()
    factory = _restricted_factory(dsn, role)
    persistence: IngestPersistencePort = PostgresIngestPersistence(factory)
    service = UploadSessionService(storage, persistence)
    manifest = _manifest(project_id, b"postgres-resume")
    try:
        services = [service, UploadSessionService(storage, PostgresIngestPersistence(factory))]

        def create_concurrently(index: int) -> Any:
            with _scope(project_id, region):
                return services[index].create_session(
                    manifest=manifest,
                    region_code=region,
                    idempotency_key=f"create-package-{index}",
                )

        with ThreadPoolExecutor(max_workers=2) as executor:
            concurrent_sessions = list(executor.map(create_concurrently, range(2)))
        created = concurrent_sessions[0]
        assert concurrent_sessions[1].session_id == created.session_id
        assert concurrent_sessions[1].multipart_upload_id == created.multipart_upload_id

        with _scope(project_id, region):
            replay = service.create_session(
                manifest=manifest,
                region_code=region,
                idempotency_key="create-package-0",
            )
            assert replay.session_id == created.session_id
            service.renew_part_authorizations(created.session_id, [1, 2])
            assert service.pause_upload(created.session_id).status is UploadStatus.PAUSED

            restarted = UploadSessionService(storage, PostgresIngestPersistence(factory))
            grant = restarted.resume_upload(created.session_id, [2])
            assert grant.session.status is UploadStatus.UPLOADING
            assert [part.part_number for part in restarted.list_parts(created.session_id)] == [1, 2]
            discovery = restarted.get_manifest_preflight(created.session_id)
            assert discovery.identifiers.data_package_id == manifest.data_package_id
            assert discovery.discovery.read_only is True
            assert discovery.discovery.missing_expected_topics == ("/required/missing",)

            external_body = b"postgres-object-reference"
            external_digest = hashlib.sha256(external_body).hexdigest()
            external_crc = crc64_ecma(external_body)
            external = manifest.model_copy(
                update={
                    "collection_job_id": "job-object-reference",
                    "rollout_id": "rollout-object-reference",
                    "collection_session_id": "session-object-reference",
                    "recording_request_id": "request-object-reference",
                    "data_package_id": "package-object-reference",
                    "sha256": external_digest,
                    "crc64": external_crc,
                    "file_size": len(external_body),
                    "files": [
                        ManifestFileV1(
                            path="recording.mcap",
                            size=len(external_body),
                            sha256=external_digest,
                            crc64=external_crc,
                        )
                    ],
                }
            )
            external_key = raw_object_key(external)
            storage.objects[external_key] = external_body
            external_grant = restarted.create_upload(
                manifest=external,
                region_code=region,
                idempotency_key="register-object-reference",
                object_storage_uri=f"memory://object/{external_key}",
            )
            assert external_grant.session.source_type is UploadSourceType.OBJECT_STORAGE_REFERENCE
            assert external_grant.session.multipart_upload_id is None
            with psycopg.connect(dsn) as administrator:
                administrator.execute(
                    sql.SQL("REVOKE INSERT ON core.outbox_events FROM {}").format(
                        sql.Identifier(role)
                    )
                )
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                restarted.commit_manifest(
                    session_id=external_grant.session.session_id,
                    manifest=external,
                )
            assert restarted.get_session(external_grant.session.session_id).status is (
                UploadStatus.MULTIPART_COMPLETED
            )
            assert restarted.persistence.get_committed(project_id, external.rollout_id) is None
            assert (
                restarted.persistence.get_workflow_trigger(external_grant.session.session_id)
                is None
            )
            with psycopg.connect(dsn) as administrator:
                administrator.execute(
                    sql.SQL("GRANT INSERT ON core.outbox_events TO {}").format(sql.Identifier(role))
                )
            committed = restarted.commit_manifest(
                session_id=external_grant.session.session_id,
                manifest=external,
                actor_id="integration-user",
                request_id="integration-request",
            )
            assert committed.data_package_id == "package-object-reference"
            assert committed.workflow is not None
            assert committed.workflow.workflow_id.endswith("cn-test%2Frollout-object-reference")
            assert (
                restarted.commit_manifest(
                    session_id=external_grant.session.session_id,
                    manifest=external,
                ).workflow
                == committed.workflow
            )
            with factory() as connection:
                assert connection.execute(
                    "SELECT count(*) FROM core.outbox_events WHERE project_id = %s",
                    (project_id,),
                ).fetchone() == (1,)
                assert connection.execute(
                    "SELECT count(*) FROM core.audit_events "
                    "WHERE project_id = %s AND action = 'ingest.workflow.staged'",
                    (project_id,),
                ).fetchone() == (1,)
            listed = restarted.list_sessions(project_id, region)
            assert listed.total == 2
            filtered = restarted.list_sessions(
                project_id,
                region,
                data_package_id="package-object-reference",
            )
            assert filtered.total == 1
            assert filtered.items[0].session_id == external_grant.session.session_id

        with _scope(f"other-{project_id}", region):
            with pytest.raises(ProblemException) as hidden:
                service.get_session(created.session_id)
            assert hidden.value.problem.code == "UPLOAD_SESSION_NOT_FOUND"
        with _scope(project_id, "other-region"):
            with pytest.raises(ProblemException) as hidden_region:
                service.get_session(created.session_id)
            assert hidden_region.value.problem.code == "UPLOAD_SESSION_NOT_FOUND"
    finally:
        _cleanup(dsn, project_id, role)
