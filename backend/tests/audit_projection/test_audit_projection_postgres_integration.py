from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen
from uuid import uuid4

import pytest

from hc_data_platform.audit_projection.governance import (
    AuditExportOutboxHandler,
    AuditGovernanceService,
    PostgresAuditGovernanceRepository,
    S3AuditArtifactStore,
)
from hc_data_platform.audit_projection.models import AuditScope
from hc_data_platform.audit_projection.repository import (
    AuditQueryFilters,
    PostgresAuditProjectionRepository,
)
from hc_data_platform.audit_projection.service import AuditProjectionService
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.core.migrations import apply_migrations
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.outbox import OutboxDispatcher, PostgresOutboxDeliveryRepository

psycopg = pytest.importorskip("psycopg")

pytestmark = pytest.mark.integration

PROJECT_ID = "project-p19-postgres"
FOREIGN_PROJECT_ID = "project-p19-postgres-foreign"
REGION_CODE = "region-p19-postgres"
ORGANIZATION_ID = "org-p19-postgres"
OTHER_ORGANIZATION_ID = "org-p19-postgres-other"
APP_ROLE = "p19_audit_reader"
APP_PASSWORD = "p19-audit-reader-test-password"
NOW = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)


def _superuser_dsn() -> str:
    value = os.getenv("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


def _app_dsn(superuser_dsn: str) -> str:
    _credentials, separator, address = superuser_dsn.rpartition("@")
    assert separator
    return f"postgresql://{APP_ROLE}:{APP_PASSWORD}@{address}"


def _drop_role(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,))
        if cursor.fetchone() is None:
            return
        cursor.execute(psycopg.sql.SQL("DROP OWNED BY {}").format(psycopg.sql.Identifier(APP_ROLE)))
        cursor.execute(
            psycopg.sql.SQL("DROP ROLE IF EXISTS {}").format(psycopg.sql.Identifier(APP_ROLE))
        )


def _prepare_role(dsn: str) -> None:
    _drop_role(dsn)
    with psycopg.connect(dsn, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute(
            psycopg.sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER").format(
                psycopg.sql.Identifier(APP_ROLE), psycopg.sql.Literal(APP_PASSWORD)
            )
        )
        cursor.execute("GRANT USAGE ON SCHEMA core TO " + APP_ROLE)
        cursor.execute("GRANT SELECT ON core.audit_events TO " + APP_ROLE)
        cursor.execute("GRANT SELECT ON core.audit_integrity_heads TO " + APP_ROLE)
        cursor.execute("GRANT SELECT ON core.audit_integrity_entries TO " + APP_ROLE)
        cursor.execute(
            "GRANT SELECT, INSERT, UPDATE ON core.audit_retention_policies TO " + APP_ROLE
        )
        cursor.execute("GRANT SELECT, INSERT, UPDATE ON core.audit_legal_holds TO " + APP_ROLE)
        cursor.execute("GRANT SELECT, INSERT, UPDATE ON core.audit_export_jobs TO " + APP_ROLE)
        cursor.execute("GRANT INSERT ON core.audit_events TO " + APP_ROLE)
        cursor.execute("GRANT SELECT, INSERT, UPDATE ON core.outbox_events TO " + APP_ROLE)


def _cleanup(dsn: str) -> None:
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM core.audit_export_jobs WHERE project_id IN (%s, %s)",
            (PROJECT_ID, FOREIGN_PROJECT_ID),
        )
        cursor.execute(
            "DELETE FROM core.audit_legal_holds WHERE project_id IN (%s, %s)",
            (PROJECT_ID, FOREIGN_PROJECT_ID),
        )
        cursor.execute(
            "DELETE FROM core.audit_retention_policies WHERE project_id IN (%s, %s)",
            (PROJECT_ID, FOREIGN_PROJECT_ID),
        )
        cursor.execute(
            "DELETE FROM core.outbox_events WHERE project_id IN (%s, %s)",
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
            "DELETE FROM registry.organization_projects "
            "WHERE organization_id IN (%s, %s) AND project_id IN (%s, %s)",
            (ORGANIZATION_ID, OTHER_ORGANIZATION_ID, PROJECT_ID, FOREIGN_PROJECT_ID),
        )


def _seed(dsn: str) -> None:
    rows = (
        (
            "00000000-0000-4000-8000-000000000103",
            ORGANIZATION_ID,
            PROJECT_ID,
            REGION_CODE,
            "actor-p19-postgres",
            "cleaning.draft.updated",
            "CLEANING_DRAFT",
            "draft-p19-postgres",
            "request-p19-postgres-03",
            "a" * 64,
            "b" * 64,
            NOW - timedelta(minutes=1),
        ),
        (
            "00000000-0000-4000-8000-000000000102",
            ORGANIZATION_ID,
            PROJECT_ID,
            REGION_CODE,
            "actor-p19-postgres",
            "access.grant.revoked",
            "ACCESS_GRANT",
            "grant-p19-postgres",
            "request-p19-postgres-02",
            None,
            None,
            NOW - timedelta(minutes=2),
        ),
        (
            "00000000-0000-4000-8000-000000000101",
            ORGANIZATION_ID,
            PROJECT_ID,
            REGION_CODE,
            "actor-p19-postgres-other",
            "manual_issue.created",
            "MANUAL_ISSUE",
            "issue-p19-postgres",
            "request-p19-postgres-01",
            None,
            None,
            NOW - timedelta(minutes=3),
        ),
        (
            "00000000-0000-4000-8000-000000000201",
            ORGANIZATION_ID,
            FOREIGN_PROJECT_ID,
            REGION_CODE,
            "actor-p19-postgres-foreign",
            "access.grant.created",
            "ACCESS_GRANT",
            "grant-p19-postgres-foreign",
            "request-p19-postgres-foreign",
            None,
            None,
            NOW - timedelta(minutes=1),
        ),
        (
            "00000000-0000-4000-8000-000000000104",
            OTHER_ORGANIZATION_ID,
            PROJECT_ID,
            REGION_CODE,
            "actor-p19-postgres-other-org",
            "access.grant.created",
            "ACCESS_GRANT",
            "grant-p19-postgres-other-org",
            "request-p19-postgres-other-org",
            None,
            None,
            NOW - timedelta(seconds=30),
        ),
    )
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.executemany(
            """INSERT INTO registry.organization_projects (organization_id, project_id)
               VALUES (%s, %s) ON CONFLICT DO NOTHING""",
            (
                (ORGANIZATION_ID, PROJECT_ID),
                (ORGANIZATION_ID, FOREIGN_PROJECT_ID),
                (OTHER_ORGANIZATION_ID, PROJECT_ID),
            ),
        )
        cursor.executemany(
            """
            INSERT INTO core.audit_events (
                audit_id, organization_id, project_id, region_code, actor_id,
                action, resource_type,
                resource_id, request_id, before_hash, after_hash, details, occurred_at
            ) VALUES (
                %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                '{}'::jsonb, %s
            )
            """,
            rows,
        )


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    dsn = _superuser_dsn()
    asyncio.run(apply_migrations(dsn))
    _cleanup(dsn)
    _prepare_role(dsn)
    _seed(dsn)
    try:
        yield dsn
    finally:
        _cleanup(dsn)
        _drop_role(dsn)


def _auth() -> AuthContext:
    return AuthContext(
        subject_id="actor-p19-reader",
        project_ids=frozenset({PROJECT_ID}),
        region_codes=frozenset({REGION_CODE}),
        capability_revision=7,
        scope_pairs=frozenset({(PROJECT_ID, REGION_CODE)}),
        scoped_capabilities=frozenset({(PROJECT_ID, "audit.read")}),
        organization_ids=frozenset({ORGANIZATION_ID}),
        organization_scope_triples=frozenset({(ORGANIZATION_ID, PROJECT_ID, REGION_CODE)}),
        organization_scoped_capabilities=frozenset({(ORGANIZATION_ID, PROJECT_ID, "audit.read")}),
    )


def _governance_auth() -> AuthContext:
    return AuthContext(
        subject_id="actor-p19-governor",
        project_ids=frozenset({PROJECT_ID}),
        region_codes=frozenset({REGION_CODE}),
        capability_revision=8,
        scope_pairs=frozenset({(PROJECT_ID, REGION_CODE)}),
        scoped_capabilities=frozenset({(PROJECT_ID, "audit.read"), (PROJECT_ID, "audit.export")}),
        organization_ids=frozenset({ORGANIZATION_ID}),
        organization_scope_triples=frozenset({(ORGANIZATION_ID, PROJECT_ID, REGION_CODE)}),
        organization_scoped_capabilities=frozenset(
            {
                (ORGANIZATION_ID, PROJECT_ID, "audit.read"),
                (ORGANIZATION_ID, PROJECT_ID, "audit.export"),
            }
        ),
    )


def _service(dsn: str) -> AuditProjectionService:
    return AuditProjectionService(
        PostgresAuditProjectionRepository(psycopg_connection_factory(_app_dsn(dsn))),
        cursor_secret="p19-postgres-cursor-secret",
        clock=lambda: NOW,
    )


@contextmanager
def _within_request() -> Iterator[None]:
    token = bind_request_context(
        RequestContext(
            request_id="request-p19-postgres",
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            subject_id="actor-p19-reader",
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


@contextmanager
def _within_governance_request(organization_id: str = ORGANIZATION_ID) -> Iterator[None]:
    token = bind_request_context(
        RequestContext(
            request_id="request-p19-governance",
            organization_id=organization_id,
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            subject_id="actor-p19-governor",
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


def test_p19_postgres_reader_applies_migration_rls_redaction_and_keyset_cursor(
    postgres_dsn: str,
) -> None:
    service = _service(postgres_dsn)
    filters = AuditQueryFilters(NOW - timedelta(days=1), NOW)
    with _within_request():
        page = service.list_events(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            filters=filters,
            after=None,
            before=None,
            limit=20,
            request_id="request-p19-postgres",
        )
        bootstrap = service.bootstrap(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            occurred_from=NOW - timedelta(days=1),
            occurred_to=NOW,
            request_id="request-p19-postgres",
        )
        facets = service.facets(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            filters=filters,
            request_id="request-p19-postgres",
        )

    assert [item.event_id for item in page.items] == [
        "00000000-0000-4000-8000-000000000103",
        "00000000-0000-4000-8000-000000000102",
        "00000000-0000-4000-8000-000000000101",
    ]
    assert all(item.scope.project_id == PROJECT_ID for item in page.items)
    assert all(item.integrity.status == "UNKNOWN" for item in page.items)
    assert all(item.change is None for item in page.items)
    assert bootstrap.data.metrics == {
        "today": "3",
        "high_risk": "1",
        "failed": "0",
        "active_actors": "2",
    }
    assert facets.data.actor_ids == ()
    assert facets.data.resource_types == ("ACCESS_GRANT", "CLEANING_DRAFT", "MANUAL_ISSUE")

    with _within_request():
        integrity = service.integrity(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            request_id="request-p19-postgres",
        )
    assert integrity.data.status == "PASSED"
    assert integrity.data.checked_event_count == 3
    assert integrity.data.checked_chain_count == 1
    assert integrity.data.verified_through == NOW - timedelta(minutes=1)

    end_cursor = page.page_info.end_cursor
    assert end_cursor is not None
    with _within_request():
        second = service.list_events(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            filters=filters,
            after=end_cursor,
            before=None,
            limit=20,
            request_id="request-p19-postgres",
        )
    assert second.items == ()

    with _within_request(), pytest.raises(ProblemException) as foreign:
        service.list_events(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=FOREIGN_PROJECT_ID,
            region_code=REGION_CODE,
            filters=filters,
            after=None,
            before=None,
            limit=20,
            request_id="request-p19-postgres",
        )
    assert foreign.value.problem.status == 403
    assert foreign.value.problem.code == "ORGANIZATION_SCOPE_DENIED"


def test_p19_audit_keyset_index_is_present(postgres_dsn: str) -> None:
    with psycopg.connect(postgres_dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT indexname
              FROM pg_indexes
             WHERE schemaname = 'core'
               AND tablename = 'audit_events'
               AND indexname = 'audit_events_p19_organization_scope_keyset_idx'
            """
        )
        assert cursor.fetchone() == ("audit_events_p19_organization_scope_keyset_idx",)


@pytest.mark.integration
def test_p19_governance_persists_an_organization_scoped_minio_export(
    postgres_dsn: str,
) -> None:
    endpoint = os.getenv("HC_MINIO_ENDPOINT")
    bucket = os.getenv("HC_MINIO_BUCKET")
    access_key = os.getenv("HC_MINIO_ACCESS_KEY")
    secret_key = os.getenv("HC_MINIO_SECRET_KEY")
    if not all((endpoint, bucket, access_key, secret_key)):
        pytest.skip("HC_MINIO_ENDPOINT/BUCKET/ACCESS_KEY/SECRET_KEY are required")
    assert endpoint is not None
    assert bucket is not None
    assert access_key is not None
    assert secret_key is not None
    boto3 = pytest.importorskip("boto3")
    botocore_config = pytest.importorskip("botocore.config")
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
        config=botocore_config.Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    try:
        s3.head_bucket(Bucket=bucket)
    except Exception:
        s3.create_bucket(Bucket=bucket)

    suffix = uuid4().hex
    prefix = f"p19-integration-{suffix}"
    scope = AuditScope(
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
    )
    object_key: str | None = None
    try:
        with _within_governance_request():
            service = AuditGovernanceService(
                PostgresAuditGovernanceRepository(
                    psycopg_connection_factory(_app_dsn(postgres_dsn))
                ),
                _service(postgres_dsn),
                S3AuditArtifactStore(s3, bucket, prefix=prefix),
                clock=lambda: NOW,
            )
            auth = _governance_auth()
            policy = service.get_policy(auth=auth, scope=scope)
            updated = service.put_policy(
                auth=auth,
                scope=scope,
                standard_days=730,
                security_days=2555,
                if_match=policy.etag,
                request_id=f"request-p19-policy-{suffix}",
            )
            assert updated.policy_version == 2
            job = service.create_export(
                auth=auth,
                scope=scope,
                occurred_from=NOW - timedelta(days=1),
                occurred_to=NOW,
                idempotency_key=f"p19-export-{suffix}",
                request_id=f"request-p19-export-{suffix}",
            )
            with psycopg.connect(postgres_dsn) as connection, connection.cursor() as cursor:
                cursor.execute(
                    """SELECT count(*) FROM core.outbox_events
                         WHERE organization_id=%s AND project_id=%s AND region_code=%s
                           AND event_type='audit.export.requested.v1'
                           AND envelope->'payload'->>'job_id'=%s""",
                    (ORGANIZATION_ID, PROJECT_ID, REGION_CODE, job.job_id),
                )
                assert cursor.fetchone() == (1,)
            dispatcher = OutboxDispatcher(
                PostgresOutboxDeliveryRepository(
                    psycopg_connection_factory(_app_dsn(postgres_dsn))
                ),
                {AuditExportOutboxHandler.EVENT_TYPE: AuditExportOutboxHandler(service)},
                worker_id=f"p19-minio-{suffix}",
            )
            assert asyncio.run(
                dispatcher.dispatch_one(
                    organization_id=ORGANIZATION_ID,
                    project_id=PROJECT_ID,
                    region_code=REGION_CODE,
                )
            )
            completed = service.get_export(auth=auth, scope=scope, job_id=job.job_id)
            assert completed.status == "SUCCEEDED"
            assert completed.artifact is not None
            object_key = f"{prefix}/{job.job_id}/events.jsonl"
            stored = s3.get_object(Bucket=bucket, Key=object_key)["Body"].read()
            assert hashlib.sha256(stored).hexdigest() == completed.artifact.sha256
            assert b"actor-p19-postgres" not in stored
            assert b"before_hash" not in stored

            authorization = service.authorize_download(
                auth=auth,
                scope=scope,
                job_id=job.job_id,
                request_id=f"request-p19-download-{suffix}",
            )
        with urlopen(authorization.download_url) as response:  # noqa: S310 - service grant
            assert response.headers["Content-Type"] == "application/x-ndjson"
            assert response.headers["Cache-Control"] == "no-store"
            assert response.read() == stored

        with _within_governance_request("org-p19-other"):
            foreign = PostgresAuditGovernanceRepository(
                psycopg_connection_factory(_app_dsn(postgres_dsn))
            )
            assert foreign.get_policy(scope) is None
            assert foreign.get_export(scope, job.job_id) is None
    finally:
        if object_key is not None:
            s3.delete_object(Bucket=bucket, Key=object_key)


def test_p19_integrity_verifier_detects_source_tampering(postgres_dsn: str) -> None:
    with psycopg.connect(postgres_dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE core.audit_events
               SET action = 'cleaning.draft.updated.tampered'
             WHERE audit_id = '00000000-0000-4000-8000-000000000103'::uuid
               AND organization_id = %s
            """,
            (ORGANIZATION_ID,),
        )
        cursor.execute(
            """
            SELECT count(*)
              FROM core.audit_events
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
            """,
            (ORGANIZATION_ID, PROJECT_ID, REGION_CODE),
        )
        expected_checked_event_count = int(cursor.fetchone()[0])

    with _within_request():
        result = _service(postgres_dsn).integrity(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            request_id="request-p19-tamper",
        )
    assert result.data.status == "FAILED"
    assert result.data.checked_event_count == expected_checked_event_count
    assert result.data.checked_chain_count == 1
