"""Run P12/P13/P19 Mock-off browser acceptance against disposable real services.

The runner owns a randomly named PostgreSQL database, a non-superuser runtime
role, short-lived signed browser identities, an exact-scope outbox dispatcher,
and one random MinIO prefix.  It reuses the already running local PostgreSQL,
MinIO, and Temporal dependencies but never changes the shared Worker process or
its ``HC_OUTBOX_SCOPES`` value.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import IO, Any
from urllib.error import URLError
from urllib.parse import quote, urlparse, urlunparse
from urllib.request import ProxyHandler, build_opener

import boto3
import jwt
import psycopg
from botocore.config import Config
from botocore.exceptions import ClientError
from psycopg import sql

from hc_data_platform.core.migrations import apply_migrations
from hc_data_platform.security.audit import canonical_hash

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"
PYTHON_BIN = BACKEND_ROOT / ".venv" / "bin" / "python"
API_BIN = BACKEND_ROOT / ".venv" / "bin" / "hc-data-api"
WORKER_BIN = BACKEND_ROOT / ".venv" / "bin" / "hc-data-worker"
AXE_CORE_PATH = (
    FRONTEND_ROOT
    / "node_modules"
    / ".pnpm"
    / "axe-core@4.13.0"
    / "node_modules"
    / "axe-core"
    / "axe.min.js"
)

P12_ORGANIZATION_ID = "p12p13-browser-organization"
P12_PROJECT_ID = "p12p13-browser-project"
P12_FOREIGN_PROJECT_ID = "p12p13-browser-foreign-project"
P12_REGION_CODE = "p12p13-browser-region"
P19_ORGANIZATION_ID = "p19-browser-organization"
P19_PROJECT_ID = "p19-browser-project"
P19_FOREIGN_PROJECT_ID = "p19-browser-foreign-project"
P19_REGION_CODE = "p19-browser-region"
P19_EVENT_ID = "00000000-0000-4000-8000-000000000301"
P12_OBJECT_ID = "object-browser-cache"
P12_OBJECT_BODY = b"p12-p13-real-browser-cache\n"
P13_CANDIDATE_ID = "object-browser-lifecycle-candidate"
P13_CANDIDATE_BODY = b"p13-real-browser-lifecycle-candidate\n"


@dataclass(frozen=True, slots=True)
class Names:
    suffix: str
    database: str
    role: str
    object_prefix: str
    temporal_queue: str


@dataclass(slots=True)
class OwnedProcess:
    name: str
    process: subprocess.Popen[bytes]
    log_path: Path
    log_stream: IO[bytes]


def _names() -> Names:
    suffix = secrets.token_hex(5)
    return Names(
        suffix=suffix,
        database=f"hc_browser_{suffix}",
        role=f"hc_browser_role_{suffix}",
        object_prefix=f"browser-e2e/{suffix}",
        temporal_queue=f"hc-browser-{suffix}",
    )


def _url_dsn(admin_dsn: str, *, database: str, user: str, password: str) -> str:
    parsed = urlparse(admin_dsn.replace("postgresql+asyncpg://", "postgresql://", 1))
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise RuntimeError("HC_E2E_ADMIN_POSTGRES_DSN must be a PostgreSQL URL")
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    authority = f"{quote(user, safe='')}:{quote(password, safe='')}@{host}"
    if parsed.port is not None:
        authority += f":{parsed.port}"
    return urlunparse(("postgresql", authority, f"/{database}", "", "", ""))


def _assert_owned_name(names: Names) -> None:
    if not re.fullmatch(r"hc_browser_[0-9a-f]{10}", names.database):
        raise RuntimeError("refusing to manage an unexpected database name")
    if not re.fullmatch(r"hc_browser_role_[0-9a-f]{10}", names.role):
        raise RuntimeError("refusing to manage an unexpected role name")


def _assert_port_free(port: int) -> None:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"required isolated port {port} is already in use")


def _create_database(admin_dsn: str, names: Names) -> None:
    _assert_owned_name(names)
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (names.database,)
        ).fetchone()
        if exists is not None:
            raise RuntimeError("random disposable database unexpectedly already exists")
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(names.database)))


def _create_role(admin_dsn: str, database: str, names: Names, password: str) -> None:
    _assert_owned_name(names)
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (names.role,)
        ).fetchone()
        if exists is not None:
            raise RuntimeError("random disposable role unexpectedly already exists")
        connection.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE").format(
                sql.Identifier(names.role), sql.Literal(password)
            )
        )
        connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(names.database), sql.Identifier(names.role)
            )
        )

    with psycopg.connect(database) as connection:
        role = sql.Identifier(names.role)
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA core, registry, storage TO {}").format(role)
        )
        connection.execute(
            sql.SQL(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA storage TO {}"
            ).format(role)
        )
        connection.execute(
            sql.SQL(
                "GRANT SELECT, INSERT, UPDATE ON "
                "core.audit_events, core.audit_integrity_heads, "
                "core.audit_integrity_entries, core.audit_retention_policies, "
                "core.audit_legal_holds, core.audit_export_jobs, "
                "core.outbox_events, core.idempotency_records TO {}"
            ).format(role)
        )
        connection.execute(sql.SQL("GRANT SELECT ON core.schema_migrations TO {}").format(role))
        connection.execute(
            sql.SQL("GRANT SELECT ON registry.organization_projects TO {}").format(role)
        )
        connection.execute(
            sql.SQL(
                "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA core, storage TO {}"
            ).format(role)
        )


def _executemany(
    connection: psycopg.Connection[Any],
    statement: str,
    rows: Any,
) -> None:
    with connection.cursor() as cursor:
        cursor.executemany(statement, rows)


def _insert_capacity_fixture(connection: psycopg.Connection[Any]) -> None:
    snapshot_rows = (
        (
            P12_ORGANIZATION_ID,
            P12_PROJECT_ID,
            "snapshot-browser-previous",
            datetime(2026, 8, 18, 2, tzinfo=timezone.utc),
            280,
            6,
            170,
            4,
            100,
            10,
            "1" * 64,
        ),
        (
            P12_ORGANIZATION_ID,
            P12_PROJECT_ID,
            "snapshot-browser-middle",
            datetime(2026, 8, 21, 2, tzinfo=timezone.utc),
            295,
            6,
            185,
            4,
            100,
            10,
            "2" * 64,
        ),
        (
            P12_ORGANIZATION_ID,
            P12_PROJECT_ID,
            "snapshot-browser-current",
            datetime(2026, 8, 24, 2, tzinfo=timezone.utc),
            310,
            6,
            200,
            4,
            100,
            10,
            "3" * 64,
        ),
        (
            P12_ORGANIZATION_ID,
            P12_FOREIGN_PROJECT_ID,
            "snapshot-browser-foreign",
            datetime(2026, 8, 24, 2, tzinfo=timezone.utc),
            999,
            1,
            999,
            1,
            0,
            0,
            "4" * 64,
        ),
    )
    _executemany(
        connection,
        """
        INSERT INTO storage.inventory_snapshots (
            organization_id, project_id, snapshot_id, observed_at,
            physical_total_bytes, physical_instance_count,
            candidate_business_total_bytes, candidate_logical_object_count,
            replica_overhead_bytes, temporary_bytes,
            duplicate_inventory_rows_ignored, content_digest, sealed
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s, false)
        """,
        snapshot_rows,
    )

    snapshots = (
        ("snapshot-browser-previous", datetime(2026, 8, 18, 2, tzinfo=timezone.utc), 20),
        ("snapshot-browser-middle", datetime(2026, 8, 21, 2, tzinfo=timezone.utc), 35),
        ("snapshot-browser-current", datetime(2026, 8, 24, 2, tzinfo=timezone.utc), 50),
    )
    fact_rows: list[tuple[object, ...]] = []
    for snapshot_id, observed_at, annotated_bytes in snapshots:
        fact_rows.extend(
            (
                (
                    P12_ORGANIZATION_ID,
                    P12_PROJECT_ID,
                    snapshot_id,
                    "physical-raw-primary",
                    "logical-raw",
                    100,
                    "PRIMARY",
                    "RAW",
                    "RAW",
                    observed_at,
                ),
                (
                    P12_ORGANIZATION_ID,
                    P12_PROJECT_ID,
                    snapshot_id,
                    "physical-raw-replica",
                    "logical-raw",
                    100,
                    "REPLICA",
                    "RAW",
                    "RAW",
                    observed_at,
                ),
                (
                    P12_ORGANIZATION_ID,
                    P12_PROJECT_ID,
                    snapshot_id,
                    "physical-annotation",
                    "logical-annotation",
                    annotated_bytes,
                    "PRIMARY",
                    "ANNOTATION_COMPLETE",
                    "REBUILDABLE_DERIVATIVE",
                    observed_at,
                ),
                (
                    P12_ORGANIZATION_ID,
                    P12_PROJECT_ID,
                    snapshot_id,
                    "physical-pending",
                    "logical-pending",
                    30,
                    "PRIMARY",
                    "PENDING_ANNOTATION",
                    "OTHER",
                    observed_at,
                ),
                (
                    P12_ORGANIZATION_ID,
                    P12_PROJECT_ID,
                    snapshot_id,
                    "physical-issue",
                    "logical-issue",
                    20,
                    "PRIMARY",
                    "ISSUE_DATA",
                    "OTHER",
                    observed_at,
                ),
                (
                    P12_ORGANIZATION_ID,
                    P12_PROJECT_ID,
                    snapshot_id,
                    "physical-temporary",
                    None,
                    10,
                    "TEMPORARY",
                    None,
                    "OTHER",
                    observed_at,
                ),
            )
        )
    fact_rows.append(
        (
            P12_ORGANIZATION_ID,
            P12_FOREIGN_PROJECT_ID,
            "snapshot-browser-foreign",
            "physical-foreign",
            "logical-foreign",
            999,
            "PRIMARY",
            "RAW",
            "RAW",
            datetime(2026, 8, 24, 2, tzinfo=timezone.utc),
        )
    )
    _executemany(
        connection,
        """
        INSERT INTO storage.inventory_facts (
            organization_id, project_id, snapshot_id, physical_instance_id,
            logical_object_id, physical_bytes, disposition, business_category,
            object_role, observed_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        fact_rows,
    )
    connection.execute(
        """
        UPDATE storage.inventory_snapshots
        SET sealed = true
        WHERE organization_id = %s
          AND project_id IN (%s, %s)
        """,
        (P12_ORGANIZATION_ID, P12_PROJECT_ID, P12_FOREIGN_PROJECT_ID),
    )


def _seed_database(database_dsn: str, names: Names) -> tuple[str, str]:
    object_key = f"{names.object_prefix}/managed/cache/demo.bin"
    candidate_key = f"{names.object_prefix}/managed/cache/lifecycle-candidate.bin"
    checksum = hashlib.sha256(P12_OBJECT_BODY).hexdigest()
    candidate_checksum = hashlib.sha256(P13_CANDIDATE_BODY).hexdigest()
    with psycopg.connect(database_dsn) as connection:
        _executemany(
            connection,
            """
            INSERT INTO registry.organization_projects (organization_id, project_id)
            VALUES (%s, %s)
            """,
            (
                (P12_ORGANIZATION_ID, P12_PROJECT_ID),
                (P12_ORGANIZATION_ID, P12_FOREIGN_PROJECT_ID),
                (P19_ORGANIZATION_ID, P19_PROJECT_ID),
                (P19_ORGANIZATION_ID, P19_FOREIGN_PROJECT_ID),
            ),
        )
        _insert_capacity_fixture(connection)
        _executemany(
            connection,
            """
            INSERT INTO storage.lifecycle_policies (
                organization_id, project_id, policy_id, name, business_category,
                object_role, action, minimum_age_days, priority, state, version,
                etag, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                (
                    P12_ORGANIZATION_ID,
                    P12_PROJECT_ID,
                    "policy-browser-enabled",
                    "P12P13 浏览器启用策略",
                    "ANNOTATION_COMPLETE",
                    "REBUILDABLE_DERIVATIVE",
                    "CLEAN_REBUILDABLE_CACHE",
                    30,
                    100,
                    "ENABLED",
                    1,
                    '"v1"',
                    datetime(2026, 6, 1, tzinfo=timezone.utc),
                    datetime(2026, 7, 1, tzinfo=timezone.utc),
                ),
                (
                    P12_ORGANIZATION_ID,
                    P12_FOREIGN_PROJECT_ID,
                    "policy-browser-foreign",
                    "P12P13 外部项目策略",
                    "ANNOTATION_COMPLETE",
                    "REBUILDABLE_DERIVATIVE",
                    "CLEAN_REBUILDABLE_CACHE",
                    30,
                    101,
                    "ENABLED",
                    1,
                    '"v1"',
                    datetime(2026, 6, 1, tzinfo=timezone.utc),
                    datetime(2026, 7, 1, tzinfo=timezone.utc),
                ),
            ),
        )
        _executemany(
            connection,
            """
            INSERT INTO storage.managed_objects (
                organization_id, project_id, object_id, display_key, object_key,
                original_object_key, physical_bytes, checksum_sha256,
                business_category, object_role, storage_tier, status,
                active_reference_count, retention_until, legal_hold,
                governance_hold, rebuild_source_id, recoverable_until,
                version, etag, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                'ANNOTATION_COMPLETE', 'REBUILDABLE_DERIVATIVE', 'HOT', 'ACTIVE',
                0, NULL, false, false, 'raw-source-browser', NULL,
                1, '"v1"', %s, %s
            )
            """,
            (
                (
                    P12_ORGANIZATION_ID,
                    P12_PROJECT_ID,
                    P12_OBJECT_ID,
                    "object-browser-cache",
                    object_key,
                    object_key,
                    len(P12_OBJECT_BODY),
                    checksum,
                    datetime(2026, 5, 1, tzinfo=timezone.utc),
                    datetime(2026, 6, 1, tzinfo=timezone.utc),
                ),
                (
                    P12_ORGANIZATION_ID,
                    P12_PROJECT_ID,
                    P13_CANDIDATE_ID,
                    "P13 lifecycle candidate",
                    candidate_key,
                    candidate_key,
                    len(P13_CANDIDATE_BODY),
                    candidate_checksum,
                    datetime(2026, 5, 1, tzinfo=timezone.utc),
                    datetime(2026, 6, 1, tzinfo=timezone.utc),
                ),
            ),
        )
        _executemany(
            connection,
            """
            INSERT INTO core.audit_events (
                audit_id, organization_id, project_id, region_code, actor_id,
                action, resource_type, resource_id, request_id,
                before_hash, after_hash, details, occurred_at
            ) VALUES (
                %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s::jsonb, %s
            )
            """,
            (
                (
                    P19_EVENT_ID,
                    P19_ORGANIZATION_ID,
                    P19_PROJECT_ID,
                    P19_REGION_CODE,
                    "p19-browser-sensitive-actor",
                    "cleaning.draft.updated",
                    "CLEANING_DRAFT",
                    "draft-p19-browser",
                    "request-p19-browser-301",
                    "a" * 64,
                    "b" * 64,
                    json.dumps(
                        {
                            "secret": "p19-browser-sensitive-details",
                            "before": "p19-browser-before-hash",
                            "after": "p19-browser-after-hash",
                        }
                    ),
                    datetime(2026, 8, 20, 12, tzinfo=timezone.utc),
                ),
                (
                    "00000000-0000-4000-8000-000000000302",
                    P19_ORGANIZATION_ID,
                    P19_FOREIGN_PROJECT_ID,
                    P19_REGION_CODE,
                    "p19-browser-foreign-actor",
                    "access.grant.created",
                    "ACCESS_GRANT",
                    "grant-p19-browser-foreign",
                    "request-p19-browser-302",
                    None,
                    None,
                    "{}",
                    datetime(2026, 8, 20, 12, 1, tzinfo=timezone.utc),
                ),
            ),
        )
    return object_key, candidate_key


def _s3_client(endpoint: str, access_key: str, secret_key: str) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _issue_token(
    signing_key: str,
    *,
    subject: str,
    organization_id: str,
    project_id: str,
    region_code: str,
    capabilities: tuple[str, ...],
) -> str:
    now = datetime.now(timezone.utc)
    return str(
        jwt.encode(
            {
                "iss": "https://be22.test.invalid/",
                "aud": "hc-data-platform",
                "sub": subject,
                "iat": now,
                "exp": now + timedelta(hours=1),
                "roles": [],
                "project_ids": [],
                "region_codes": [],
                "capabilities": [],
                "organization_scopes": [
                    {
                        "organization_id": organization_id,
                        "project_id": project_id,
                        "region_code": region_code,
                        "capabilities": list(capabilities),
                    }
                ],
                "capability_revision": 1,
                "service_identity": False,
            },
            signing_key,
            algorithm="HS256",
        )
    )


def _runtime_environment(
    *,
    app_dsn: str,
    names: Names,
    signing_key: str,
    cursor_secret: str,
    credential_key: str,
    object_endpoint: str,
    object_public_endpoint: str,
    bucket: str,
    access_key: str,
    secret_key: str,
) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "HC_ENVIRONMENT": "test",
            "HC_RUNTIME_BACKEND": "production",
            "HC_COMPONENT_ROLE": "api",
            "HC_API_HOST": "127.0.0.1",
            "HC_API_PORT": "5197",
            "HC_POSTGRES_DSN": app_dsn,
            "HC_TEMPORAL_TARGET": "127.0.0.1:7233",
            "HC_TEMPORAL_TASK_QUEUE": names.temporal_queue,
            "HC_OUTBOX_SCOPES": json.dumps(
                [
                    f"{P12_ORGANIZATION_ID}/{P12_PROJECT_ID}/{P12_REGION_CODE}",
                    f"{P19_ORGANIZATION_ID}/{P19_PROJECT_ID}/{P19_REGION_CODE}",
                ]
            ),
            "HC_OUTBOX_POLL_INTERVAL_SECONDS": "0.2",
            "HC_OUTBOX_BATCH_SIZE": "8",
            "HC_OBJECT_STORE_ENDPOINT": object_endpoint,
            "HC_OBJECT_STORE_PUBLIC_ENDPOINT": object_public_endpoint,
            "HC_OBJECT_STORE_BUCKET": bucket,
            "HC_OBJECT_STORE_ACCESS_KEY": access_key,
            "HC_OBJECT_STORE_SECRET_KEY": secret_key,
            "HC_OBJECT_STORE_REGION": "us-east-1",
            "HC_ARTIFACT_PREFIX": f"{names.object_prefix}/artifacts",
            "HC_JWT_ISSUER": "https://be22.test.invalid/",
            "HC_JWT_AUDIENCE": "hc-data-platform",
            "HC_JWT_ALGORITHMS": '["HS256"]',
            "HC_JWT_SIGNING_KEY": signing_key,
            "HC_CURSOR_SECRET": cursor_secret,
            "HC_DATA_SOURCE_CREDENTIAL_KEY": credential_key,
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
    )
    return environment


def _start_process(
    name: str,
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    log_dir: Path,
) -> OwnedProcess:
    log_path = log_dir / f"{name}.log"
    stream = log_path.open("wb")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdout=stream,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return OwnedProcess(name=name, process=process, log_path=log_path, log_stream=stream)


def _stop_process(owned: OwnedProcess) -> None:
    process = owned.process
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
    owned.log_stream.close()


def _tail_logs(processes: list[OwnedProcess]) -> None:
    for owned in processes:
        try:
            lines = owned.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        print(f"--- {owned.name} log tail ---")
        print("\n".join(lines[-80:]))


def _wait_http(url: str, process: OwnedProcess, *, timeout: float = 45) -> None:
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.process.poll() is not None:
            raise RuntimeError(f"{process.name} exited before {url} became ready")
        try:
            with opener.open(url, timeout=2) as response:  # noqa: S310 - fixed loopback URL
                if 200 <= response.status < 500:
                    return
        except (OSError, URLError):
            pass
        time.sleep(0.2)
    raise RuntimeError(f"timed out waiting for {url}")


def _playwright_environment(signing_key: str) -> dict[str, str]:
    environment = os.environ.copy()
    p12_capabilities = (
        "storage.overview.read",
        "storage.lifecycle.read",
        "storage.lifecycle.manage",
        "storage.lifecycle.execute",
    )
    approver_capabilities = (
        "storage.overview.read",
        "storage.lifecycle.read",
        "storage.lifecycle.approve",
    )
    environment.update(
        {
            "AXE_CORE_PATH": str(AXE_CORE_PATH),
            "HC_P12_P13_REAL_API_E2E_ENABLED": "1",
            "HC_P12_P13_REAL_API_E2E_BASE_URL": "http://127.0.0.1:5196",
            "HC_P12_P13_REAL_API_E2E_TOKEN": _issue_token(
                signing_key,
                subject="p12p13-browser-operator",
                organization_id=P12_ORGANIZATION_ID,
                project_id=P12_PROJECT_ID,
                region_code=P12_REGION_CODE,
                capabilities=p12_capabilities,
            ),
            "HC_P12_P13_REAL_API_E2E_APPROVER_TOKEN": _issue_token(
                signing_key,
                subject="p12p13-browser-approver",
                organization_id=P12_ORGANIZATION_ID,
                project_id=P12_PROJECT_ID,
                region_code=P12_REGION_CODE,
                capabilities=approver_capabilities,
            ),
            "HC_P12_P13_REAL_API_E2E_CAPACITY_DENIED_TOKEN": _issue_token(
                signing_key,
                subject="p12p13-browser-capacity-denied",
                organization_id=P12_ORGANIZATION_ID,
                project_id=P12_PROJECT_ID,
                region_code=P12_REGION_CODE,
                capabilities=("storage.lifecycle.read",),
            ),
            "HC_P12_P13_REAL_API_E2E_LIFECYCLE_DENIED_TOKEN": _issue_token(
                signing_key,
                subject="p12p13-browser-lifecycle-denied",
                organization_id=P12_ORGANIZATION_ID,
                project_id=P12_PROJECT_ID,
                region_code=P12_REGION_CODE,
                capabilities=("storage.overview.read",),
            ),
            "HC_P19_REAL_API_E2E_ENABLED": "1",
            "HC_P19_REAL_API_E2E_BASE_URL": "http://127.0.0.1:5196",
            "HC_P19_REAL_API_E2E_TOKEN": _issue_token(
                signing_key,
                subject="p19-browser-reader",
                organization_id=P19_ORGANIZATION_ID,
                project_id=P19_PROJECT_ID,
                region_code=P19_REGION_CODE,
                capabilities=("audit.read", "audit.export"),
            ),
            "HC_P19_REAL_API_E2E_FORBIDDEN_TOKEN": _issue_token(
                signing_key,
                subject="p19-browser-forbidden",
                organization_id=P19_ORGANIZATION_ID,
                project_id=P19_PROJECT_ID,
                region_code=P19_REGION_CODE,
                capabilities=(),
            ),
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
    )
    return environment


def _cancel_audit_exists(database_dsn: str) -> bool:
    with psycopg.connect(database_dsn) as connection:
        row = connection.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM core.audit_events
                WHERE organization_id = %s AND project_id = %s
                  AND action = 'audit.export.cancelled'
            )
            """,
            (P19_ORGANIZATION_ID, P19_PROJECT_ID),
        ).fetchone()
    return bool(row and row[0])


def _run_p19_and_start_worker_after_cancel(
    *,
    playwright_env: dict[str, str],
    database_dsn: str,
    start_worker: Any,
) -> OwnedProcess:
    process = subprocess.Popen(
        [
            "pnpm",
            "exec",
            "playwright",
            "test",
            "-c",
            "playwright.audit-real-api.config.ts",
        ],
        cwd=FRONTEND_ROOT,
        env=playwright_env,
    )
    deadline = time.monotonic() + 150
    worker: OwnedProcess | None = None
    while time.monotonic() < deadline:
        if _cancel_audit_exists(database_dsn):
            worker = start_worker()
            break
        return_code = process.poll()
        if return_code is not None:
            raise subprocess.CalledProcessError(return_code, process.args)
        time.sleep(0.1)
    if worker is None:
        process.terminate()
        process.wait(timeout=5)
        raise RuntimeError("P19 browser flow did not persist the cancellation checkpoint")
    return_code = process.wait()
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, process.args)
    return worker


def _run_playwright(config: str, environment: dict[str, str]) -> None:
    subprocess.run(
        ["pnpm", "exec", "playwright", "test", "-c", config],
        cwd=FRONTEND_ROOT,
        env=environment,
        check=True,
    )


def _verify_results(database_dsn: str, *, verify_p19: bool, verify_storage: bool) -> None:
    with psycopg.connect(database_dsn) as connection:
        if verify_p19:
            policy = connection.execute(
                """
                SELECT policy_version, standard_days, security_days
                FROM core.audit_retention_policies
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                """,
                (P19_ORGANIZATION_ID, P19_PROJECT_ID, P19_REGION_CODE),
            ).fetchone()
            if policy != (2, 730, 2555):
                raise RuntimeError(f"unexpected persisted P19 policy result: {policy!r}")
            hold_counts = connection.execute(
                """
                SELECT count(*) FILTER (WHERE status = 'ACTIVE'), count(*)
                FROM core.audit_legal_holds
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                """,
                (P19_ORGANIZATION_ID, P19_PROJECT_ID, P19_REGION_CODE),
            ).fetchone()
            if hold_counts != (0, 1):
                raise RuntimeError(f"unexpected persisted P19 legal holds: {hold_counts!r}")
            export = connection.execute(
                """
                SELECT status, artifact_sha256 IS NOT NULL, exported_event_count > 0
                FROM core.audit_export_jobs
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                """,
                (P19_ORGANIZATION_ID, P19_PROJECT_ID, P19_REGION_CODE),
            ).fetchone()
            if export != ("SUCCEEDED", True, True):
                raise RuntimeError(f"unexpected persisted P19 export result: {export!r}")
            actions = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT action FROM core.audit_events
                    WHERE organization_id = %s AND project_id = %s
                    """,
                    (P19_ORGANIZATION_ID, P19_PROJECT_ID),
                ).fetchall()
            }
            required = {
                "audit.retention.updated",
                "audit.legal_hold.created",
                "audit.legal_hold.released",
                "audit.export.queued",
                "audit.export.cancelled",
                "audit.export.retried",
                "audit.export.completed",
                "audit.export.download_authorized",
            }
            if not required.issubset(actions):
                raise RuntimeError(
                    f"P19 audit actions are incomplete: {sorted(required - actions)}"
                )

        if verify_storage:
            object_state = connection.execute(
                """
                SELECT status, version, object_key LIKE '_hc_governance/trash/%%'
                FROM storage.managed_objects
                WHERE organization_id = %s AND project_id = %s AND object_id = %s
                """,
                (P12_ORGANIZATION_ID, P12_PROJECT_ID, P12_OBJECT_ID),
            ).fetchone()
            if object_state != ("ACTIVE", 3, False):
                raise RuntimeError(f"unexpected P12 object result: {object_state!r}")
            candidate_state = connection.execute(
                """
                SELECT status, object_key LIKE '_hc_governance/trash/%%'
                FROM storage.managed_objects
                WHERE organization_id = %s AND project_id = %s AND object_id = %s
                """,
                (P12_ORGANIZATION_ID, P12_PROJECT_ID, P13_CANDIDATE_ID),
            ).fetchone()
            if candidate_state != ("TRASHED", True):
                raise RuntimeError(f"unexpected P13 candidate result: {candidate_state!r}")
            execution = connection.execute(
                """
                SELECT
                    execution.status,
                    (
                        SELECT count(*)
                        FROM storage.lifecycle_execution_items AS item
                        WHERE item.organization_id = execution.organization_id
                          AND item.project_id = execution.project_id
                          AND item.execution_id = execution.execution_id
                          AND item.status = 'PROCESSED'
                    ),
                    (
                        SELECT count(*)
                        FROM storage.lifecycle_execution_items AS item
                        WHERE item.organization_id = execution.organization_id
                          AND item.project_id = execution.project_id
                          AND item.execution_id = execution.execution_id
                    )
                FROM storage.lifecycle_executions AS execution
                WHERE execution.organization_id = %s AND execution.project_id = %s
                ORDER BY execution.created_at DESC LIMIT 1
                """,
                (P12_ORGANIZATION_ID, P12_PROJECT_ID),
            ).fetchone()
            if execution != ("COMPLETED", 1, 1):
                raise RuntimeError(f"unexpected P13 execution result: {execution!r}")
            policy = connection.execute(
                """
                SELECT state FROM storage.lifecycle_policies
                WHERE organization_id = %s AND project_id = %s
                  AND policy_id = 'policy-browser-enabled'
                """,
                (P12_ORGANIZATION_ID, P12_PROJECT_ID),
            ).fetchone()
            if policy != ("PAUSED",):
                raise RuntimeError(f"unexpected P13 policy result: {policy!r}")
            schedules = connection.execute(
                """
                SELECT count(*) FROM storage.lifecycle_schedules
                WHERE organization_id = %s AND project_id = %s
                """,
                (P12_ORGANIZATION_ID, P12_PROJECT_ID),
            ).fetchone()
            if schedules != (0,):
                raise RuntimeError(f"P13 schedule cleanup did not complete: {schedules!r}")


def _managed_object_keys(database_dsn: str) -> tuple[str, ...]:
    try:
        with psycopg.connect(database_dsn) as connection:
            return tuple(
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT object_key FROM storage.managed_objects
                    WHERE organization_id = %s AND project_id = %s
                    """,
                    (P12_ORGANIZATION_ID, P12_PROJECT_ID),
                ).fetchall()
            )
    except Exception:
        return ()


def _cleanup_s3(client: Any, bucket: str, names: Names, exact_keys: tuple[str, ...]) -> None:
    continuation: str | None = None
    keys: set[str] = set(exact_keys)
    while True:
        request: dict[str, object] = {"Bucket": bucket, "Prefix": names.object_prefix}
        if continuation is not None:
            request["ContinuationToken"] = continuation
        page = client.list_objects_v2(**request)
        keys.update(str(item["Key"]) for item in page.get("Contents", ()))
        if not page.get("IsTruncated"):
            break
        continuation = str(page["NextContinuationToken"])
    for key in sorted(keys):
        client.delete_object(Bucket=bucket, Key=key)
    residual = client.list_objects_v2(Bucket=bucket, Prefix=names.object_prefix, MaxKeys=1)
    if residual.get("KeyCount", 0):
        raise RuntimeError("disposable browser object prefix was not removed")
    for key in exact_keys:
        try:
            client.head_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
            if status == 404:
                continue
            raise
        raise RuntimeError(f"disposable managed object remains after cleanup: {key}")


def _drop_database_and_role(admin_dsn: str, names: Names) -> None:
    _assert_owned_name(names)
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        connection.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(names.database)
            )
        )
        connection.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(names.role)))
        database_exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (names.database,)
        ).fetchone()
        role_exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (names.role,)
        ).fetchone()
        if database_exists is not None or role_exists is not None:
            raise RuntimeError("disposable PostgreSQL identity remains after cleanup")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=("all", "p19", "storage"), default="all")
    arguments = parser.parse_args()
    run_p19 = arguments.suite in {"all", "p19"}
    run_storage = arguments.suite in {"all", "storage"}

    for required in (PYTHON_BIN, API_BIN, WORKER_BIN, AXE_CORE_PATH):
        if not required.exists():
            raise RuntimeError(f"required local runtime asset is missing: {required}")
    _assert_port_free(5196)
    _assert_port_free(5197)

    names = _names()
    admin_dsn = os.environ.get(
        "HC_E2E_ADMIN_POSTGRES_DSN",
        "postgresql://hc:hc@127.0.0.1:5432/postgres",
    )
    parsed_admin = urlparse(admin_dsn)
    admin_user = parsed_admin.username or "hc"
    admin_password = parsed_admin.password or "hc"
    database_dsn = _url_dsn(
        admin_dsn,
        database=names.database,
        user=admin_user,
        password=admin_password,
    )
    role_password = secrets.token_urlsafe(32)
    app_dsn = _url_dsn(
        admin_dsn,
        database=names.database,
        user=names.role,
        password=role_password,
    )
    object_endpoint = os.environ.get("HC_E2E_MINIO_ENDPOINT", "http://127.0.0.1:19000")
    object_public_endpoint = os.environ.get("HC_E2E_MINIO_PUBLIC_ENDPOINT", object_endpoint)
    bucket = os.environ.get("HC_E2E_MINIO_BUCKET", "hc-data-local")
    access_key = os.environ.get("HC_E2E_MINIO_ACCESS_KEY", "minio")
    secret_key = os.environ.get("HC_E2E_MINIO_SECRET_KEY", "minio-local-only")
    s3 = _s3_client(object_endpoint, access_key, secret_key)
    signing_key = secrets.token_urlsafe(48)
    cursor_secret = secrets.token_urlsafe(32)
    credential_key = secrets.token_urlsafe(32)
    processes: list[OwnedProcess] = []
    database_created = False
    role_created = False
    managed_keys: tuple[str, ...] = ()
    failed = False

    with tempfile.TemporaryDirectory(prefix="hc-p12-p19-browser-") as raw_log_dir:
        log_dir = Path(raw_log_dir)
        try:
            print(
                "Creating disposable database and applying the complete migration manifest "
                f"({names.suffix})."
            )
            _create_database(admin_dsn, names)
            database_created = True
            asyncio.run(apply_migrations(database_dsn))
            object_key, candidate_key = _seed_database(database_dsn, names)
            for key, body in (
                (object_key, P12_OBJECT_BODY),
                (candidate_key, P13_CANDIDATE_BODY),
            ):
                s3.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=body,
                    ContentType="application/octet-stream",
                    Metadata={"sha256": hashlib.sha256(body).hexdigest()},
                )
            _create_role(admin_dsn, database_dsn, names, role_password)
            role_created = True

            runtime_env = _runtime_environment(
                app_dsn=app_dsn,
                names=names,
                signing_key=signing_key,
                cursor_secret=cursor_secret,
                credential_key=credential_key,
                object_endpoint=object_endpoint,
                object_public_endpoint=object_public_endpoint,
                bucket=bucket,
                access_key=access_key,
                secret_key=secret_key,
            )
            api = _start_process(
                "api",
                [str(API_BIN)],
                cwd=BACKEND_ROOT,
                environment=runtime_env,
                log_dir=log_dir,
            )
            processes.append(api)
            _wait_http("http://127.0.0.1:5197/health/ready", api)

            vite_env = runtime_env.copy()
            vite_env.update(
                {
                    "VITE_MOCK_MODE": "off",
                    "VITE_DEV_PROXY_TARGET": "http://127.0.0.1:5197",
                }
            )
            vite = _start_process(
                "vite",
                [
                    "pnpm",
                    "exec",
                    "vite",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "5196",
                    "--strictPort",
                ],
                cwd=FRONTEND_ROOT,
                environment=vite_env,
                log_dir=log_dir,
            )
            processes.append(vite)
            _wait_http("http://127.0.0.1:5196/", vite)
            playwright_env = _playwright_environment(signing_key)

            worker: OwnedProcess | None = None

            def start_worker() -> OwnedProcess:
                nonlocal worker
                if worker is not None:
                    return worker
                worker_environment = runtime_env.copy()
                worker_environment["HC_COMPONENT_ROLE"] = "worker"
                worker = _start_process(
                    "worker",
                    [str(WORKER_BIN)],
                    cwd=BACKEND_ROOT,
                    environment=worker_environment,
                    log_dir=log_dir,
                )
                processes.append(worker)
                return worker

            if run_p19:
                print(
                    "Running P19 governance browser flow; Worker starts only after durable cancel."
                )
                worker = _run_p19_and_start_worker_after_cancel(
                    playwright_env=playwright_env,
                    database_dsn=database_dsn,
                    start_worker=start_worker,
                )
                if worker.process.poll() is not None:
                    raise RuntimeError("isolated Worker exited during P19 acceptance")
            if run_storage:
                start_worker()
                print("Running P12/P13 storage and lifecycle browser flows.")
                _run_playwright("playwright.storage-real-api.config.ts", playwright_env)
                assert worker is not None
                if worker.process.poll() is not None:
                    raise RuntimeError("isolated Worker exited during P12/P13 acceptance")

            _verify_results(
                database_dsn,
                verify_p19=run_p19,
                verify_storage=run_storage,
            )
            print("Browser acceptance and durable postconditions passed.")
        except BaseException:
            failed = True
            _tail_logs(processes)
            raise
        finally:
            for process in reversed(processes):
                _stop_process(process)
            if database_created:
                managed_keys = _managed_object_keys(database_dsn)
            trash_identity = canonical_hash(
                {
                    "project_id": P12_PROJECT_ID,
                    "object_id": P12_OBJECT_ID,
                    "original_object_key": (f"{names.object_prefix}/managed/cache/demo.bin"),
                    "kind": "trash",
                }
            )
            trash_key = f"_hc_governance/trash/{trash_identity}"
            exact_keys = tuple(sorted({*managed_keys, trash_key}))
            try:
                _cleanup_s3(s3, bucket, names, exact_keys)
            finally:
                if database_created or role_created:
                    _drop_database_and_role(admin_dsn, names)
            _assert_port_free(5196)
            _assert_port_free(5197)
            if not failed:
                print(
                    "Disposable PostgreSQL role/database, MinIO objects, and local "
                    "processes are clean."
                )


if __name__ == "__main__":
    main()
