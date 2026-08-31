from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from hc_data_platform.backup.postgresql import (
    MaintenanceBackupLease,
    PostgresBackupError,
    PostgresEndpoint,
    PostgresLogicalBackupAdapter,
    PostgresToolchain,
)
from hc_data_platform.core.dbapi import normalize_postgres_dsn


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is required")
    return value


def _endpoint(dsn: str, *, tool_host: str, tool_port: int) -> PostgresEndpoint:
    parsed = conninfo_to_dict(normalize_postgres_dsn(dsn))
    return PostgresEndpoint(
        host=tool_host,
        port=tool_port,
        database=parsed["dbname"],
        username=parsed["user"],
        password=parsed["password"],
        sslmode="disable",
    )


def _container_tool(container: str, program: str) -> tuple[str, ...]:
    exported = (
        "LANG",
        "LC_ALL",
        "PGCONNECT_TIMEOUT",
        "PGDATABASE",
        "PGHOST",
        "PGPASSFILE",
        "PGPORT",
        "PGSSLMODE",
        "PGUSER",
    )
    arguments = ["docker", "exec"]
    for name in exported:
        arguments.extend(("-e", name))
    arguments.extend((container, program))
    return tuple(arguments)


@pytest.mark.integration
def test_real_custom_dump_restores_to_empty_same_major_database() -> None:
    source_dsn = normalize_postgres_dsn(_required("HC_BACKUP_POSTGRES_SOURCE_DSN"))
    target_dsn = normalize_postgres_dsn(_required("HC_BACKUP_POSTGRES_TARGET_DSN"))
    tool_host = _required("HC_BACKUP_POSTGRES_TOOL_HOST")
    tool_port = int(_required("HC_BACKUP_POSTGRES_TOOL_PORT"))
    client_container = _required("HC_BACKUP_POSTGRES_CLIENT_CONTAINER")
    staging = Path(_required("HC_BACKUP_POSTGRES_STAGING_DIR"))
    owner = UUID(_required("HC_BACKUP_POSTGRES_OWNER_INSTANCE_ID"))
    token = int(_required("HC_BACKUP_POSTGRES_FENCING_TOKEN"))
    operation_id = _required("HC_BACKUP_POSTGRES_OPERATION_ID")
    environment_id = _required("HC_BACKUP_POSTGRES_ENVIRONMENT_ID")
    source = _endpoint(source_dsn, tool_host=tool_host, tool_port=tool_port)
    target = _endpoint(target_dsn, tool_host=tool_host, tool_port=tool_port)
    assert source.database != target.database

    dsns = {source.database: source_dsn, target.database: target_dsn}

    def connect(endpoint: PostgresEndpoint) -> psycopg.Connection[tuple[object, ...]]:
        return psycopg.connect(dsns[endpoint.database])

    adapter = PostgresLogicalBackupAdapter(
        toolchain=PostgresToolchain(
            pg_dump=_container_tool(client_container, "pg_dump"),
            pg_restore=_container_tool(client_container, "pg_restore"),
        ),
        connection_factory=connect,
        command_timeout_seconds=300,
    )
    lease = MaintenanceBackupLease(
        environment_id=environment_id,
        operation_id=operation_id,
        owner_instance_id=owner,
        fencing_token=token,
    )
    stale_lease = lease.model_copy(update={"fencing_token": token + 1})

    with pytest.raises(PostgresBackupError) as stale:
        adapter.create_dump(
            source,
            lease=stale_lease,
            staging_directory=staging,
            artifact_name="stale-owner.dump",
        )
    assert stale.value.code == "BACKUP_POSTGRES_FENCE_REJECTED"
    assert not (staging / "stale-owner.dump").exists()

    artifact = adapter.create_dump(
        source,
        lease=lease,
        staging_directory=staging,
    )
    with pytest.raises(PostgresBackupError) as collision:
        adapter.restore_and_verify(
            artifact,
            source,
            staging_directory=staging,
        )
    assert collision.value.code == "BACKUP_POSTGRES_TARGET_SOURCE_COLLISION"

    report = adapter.restore_and_verify(
        artifact,
        target,
        staging_directory=staging,
    )

    assert artifact.server_major == 16
    assert artifact.wal_anchor.physical_recovery_ready is False
    assert artifact.source_evidence.migration_count > 0
    assert artifact.source_evidence.constraint_count > 0
    assert artifact.source_evidence.tables
    assert report.target_database == target.database
    assert report.aggregate_sha256 == artifact.source_evidence.aggregate_sha256

    with pytest.raises(PostgresBackupError) as nonempty:
        adapter.restore_and_verify(
            artifact,
            target,
            staging_directory=staging,
        )
    assert nonempty.value.code == "BACKUP_POSTGRES_TARGET_NOT_EMPTY"
