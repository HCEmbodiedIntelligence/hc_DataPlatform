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
    PostgresPhysicalBackupAdapter,
    PostgresToolchain,
)
from hc_data_platform.core.dbapi import normalize_postgres_dsn


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is required")
    return value


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
def test_real_physical_baseline_is_manifest_verified_and_fenced() -> None:
    source_dsn = normalize_postgres_dsn(_required("HC_BACKUP_POSTGRES_PHYSICAL_DSN"))
    parsed = conninfo_to_dict(source_dsn)
    tool_host = _required("HC_BACKUP_POSTGRES_PHYSICAL_TOOL_HOST")
    client_container = _required("HC_BACKUP_POSTGRES_CLIENT_CONTAINER")
    staging = Path(_required("HC_BACKUP_POSTGRES_STAGING_DIR"))
    endpoint = PostgresEndpoint(
        host=tool_host,
        port=5432,
        database=parsed["dbname"],
        username=parsed["user"],
        password=parsed["password"],
        sslmode="disable",
    )

    def connect(_endpoint: PostgresEndpoint) -> psycopg.Connection[tuple[object, ...]]:
        return psycopg.connect(source_dsn)

    adapter = PostgresPhysicalBackupAdapter(
        toolchain=PostgresToolchain(
            pg_dump=_container_tool(client_container, "pg_dump"),
            pg_restore=_container_tool(client_container, "pg_restore"),
            pg_basebackup=_container_tool(client_container, "pg_basebackup"),
            pg_controldata=_container_tool(client_container, "pg_controldata"),
            pg_verifybackup=_container_tool(client_container, "pg_verifybackup"),
        ),
        connection_factory=connect,
        command_timeout_seconds=300,
    )
    lease = MaintenanceBackupLease(
        environment_id=_required("HC_BACKUP_POSTGRES_PHYSICAL_ENVIRONMENT_ID"),
        operation_id=_required("HC_BACKUP_POSTGRES_PHYSICAL_OPERATION_ID"),
        owner_instance_id=UUID(_required("HC_BACKUP_POSTGRES_PHYSICAL_OWNER_INSTANCE_ID")),
        fencing_token=int(_required("HC_BACKUP_POSTGRES_PHYSICAL_FENCING_TOKEN")),
    )
    with pytest.raises(PostgresBackupError) as stale:
        adapter.create_physical_baseline(
            endpoint,
            lease=lease.model_copy(update={"fencing_token": lease.fencing_token + 1}),
            staging_directory=staging,
            artifact_directory_name="stale-physical",
        )
    assert stale.value.code == "BACKUP_POSTGRES_FENCE_REJECTED"
    assert not (staging / "stale-physical").exists()

    artifact = adapter.create_physical_baseline(
        endpoint,
        lease=lease,
        staging_directory=staging,
    )
    assert artifact.server_major == 16
    assert artifact.baseline_recovery_ready is True
    assert artifact.path == staging / "postgresql-physical"
    assert (artifact.path / "backup_manifest").is_file()
    assert artifact.files
    assert artifact.total_bytes > 0
