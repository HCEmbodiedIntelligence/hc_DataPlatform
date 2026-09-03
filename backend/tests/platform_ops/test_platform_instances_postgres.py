from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql

from hc_data_platform.core.dbapi import normalize_postgres_dsn
from hc_data_platform.core.migrations import apply_migrations
from hc_data_platform.platform_ops.instances import (
    InstanceReadinessSummary,
    InstanceRole,
    PlatformInstanceIdentity,
    PostgresPlatformInstanceRepository,
)

pytestmark = pytest.mark.integration
DIGEST = f"sha256:{'b' * 64}"


def _source_dsn() -> str:
    value = os.getenv("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    return normalize_postgres_dsn(value)


def _database_dsn(base_dsn: str, database_name: str) -> str:
    parsed = urlsplit(base_dsn)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("HC_TEST_POSTGRES_DSN must use a PostgreSQL URI")
    return parsed._replace(path=f"/{quote(database_name, safe='')}").geturl()


@pytest.fixture(scope="module")
def platform_instances_dsn() -> Iterator[str]:
    base_dsn = _source_dsn()
    database_name = f"hc_platform_instances_{uuid4().hex[:12]}"
    with psycopg.connect(base_dsn, autocommit=True) as admin:
        try:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        except psycopg.errors.InsufficientPrivilege:
            pytest.skip("HC_TEST_POSTGRES_DSN role cannot create an isolated database")
    isolated_dsn = _database_dsn(base_dsn, database_name)
    try:
        asyncio.run(apply_migrations(isolated_dsn))
        yield isolated_dsn
    finally:
        with psycopg.connect(base_dsn, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (database_name,),
            )
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))


def _identity(instance_id: UUID, *, role: InstanceRole, node_name: str) -> PlatformInstanceIdentity:
    return PlatformInstanceIdentity(
        instance_id=instance_id,
        node_name=node_name,
        role=role,
        release_id="platform-v0.1.0-postgres.1",
        release_manifest_digest=DIGEST,
        component_image_digest=DIGEST,
        runtime_version="CPython 3.12.11",
        pod_name=node_name,
        kubernetes_node_name="physical-node-a",
        kubernetes_zone="cn-east-1a",
    )


def test_postgres_heartbeat_stale_filter_cleanup_and_new_instance_identity(
    platform_instances_dsn: str,
) -> None:
    repository = PostgresPlatformInstanceRepository.from_dsn(platform_instances_dsn)
    original_id = UUID("00000000-0000-4000-8000-000000000011")
    replacement_id = UUID("00000000-0000-4000-8000-000000000012")
    original = _identity(original_id, role="worker", node_name="worker-pod-old")
    replacement = _identity(replacement_id, role="worker", node_name="worker-pod-new")

    first = repository.heartbeat(original, InstanceReadinessSummary(status="starting"))
    refreshed = repository.heartbeat(original, InstanceReadinessSummary(status="ready"))
    assert refreshed.started_at == first.started_at
    assert refreshed.last_heartbeat_at >= first.last_heartbeat_at
    assert refreshed.readiness.status == "ready"

    with psycopg.connect(platform_instances_dsn) as connection:
        connection.execute(
            """
            UPDATE platform.platform_instances
            SET started_at = statement_timestamp() - interval '2 minutes',
                last_heartbeat_at = statement_timestamp() - interval '91 seconds'
            WHERE instance_id = %s
            """,
            (original_id,),
        )

    stale_page = repository.list_instances(stale=True, role="worker", stale_after_seconds=90)
    assert stale_page.count == 1
    assert stale_page.instances[0].instance_id == original_id
    assert stale_page.instances[0].stale is True

    new_record = repository.heartbeat(
        replacement,
        InstanceReadinessSummary(status="ready"),
    )
    assert new_record.instance_id != original_id
    assert repository.list_instances(stale=False, role="worker", stale_after_seconds=90).count == 1

    with psycopg.connect(platform_instances_dsn) as connection:
        connection.execute(
            """
            UPDATE platform.platform_instances
            SET started_at = statement_timestamp() - interval '9 days',
                last_heartbeat_at = statement_timestamp() - interval '8 days'
            WHERE instance_id = %s
            """,
            (original_id,),
        )
    assert repository.cleanup_stale(retention_seconds=7 * 24 * 60 * 60) == 1
    remaining = repository.list_instances(stale=None, role="worker", stale_after_seconds=90)
    assert tuple(item.instance_id for item in remaining.instances) == (replacement_id,)
