from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from hc_data_platform.backup.postgresql import (
    DatabaseVerificationEvidence,
    LogicalDumpArtifact,
    MaintenanceBackupLease,
    PhysicalBackupFile,
    PhysicalBaselineArtifact,
    PostgresBackupError,
    PostgresEndpoint,
    PostgresLogicalBackupAdapter,
    PostgresPhysicalBackupAdapter,
    PostgresWalAnchor,
    WalArchiveReceipt,
    compose_functional_postgres_toolchain,
)


def _endpoint(*, database: str = "source_db") -> PostgresEndpoint:
    return PostgresEndpoint(
        host="postgres.internal",
        port=5432,
        database=database,
        username="backup_job",
        password="not-logged:secret\\value",
        sslmode="verify-full",
    )


def _empty_evidence() -> DatabaseVerificationEvidence:
    empty_hash = "0" * 64
    return DatabaseVerificationEvidence(
        database_size_bytes=16_777_216,
        migration_count=1,
        migration_sha256=empty_hash,
        schema_object_count=0,
        schema_sha256=empty_hash,
        constraint_count=0,
        constraint_sha256=empty_hash,
        sequence_count=0,
        sequence_sha256=empty_hash,
        tables=(),
        aggregate_sha256=empty_hash,
    )


def _artifact(path: Path) -> LogicalDumpArtifact:
    content = path.read_bytes()
    return LogicalDumpArtifact(
        path=path,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        server_major=16,
        wal_anchor=PostgresWalAnchor(
            system_identifier=123,
            timeline_id=1,
            wal_lsn="0/16B6C50",
            server_version="16.10",
            server_major=16,
            database_name="source_db",
            in_recovery=False,
        ),
        source_evidence=_empty_evidence(),
    )


def test_endpoint_never_places_password_or_dsn_in_command_environment(tmp_path: Path) -> None:
    endpoint = _endpoint()
    passfile = tmp_path / "credential"
    environment = endpoint.command_environment(passfile=passfile)

    assert "PGPASSWORD" not in environment
    assert "DATABASE_URL" not in environment
    assert endpoint.password.get_secret_value() not in repr(endpoint)
    assert endpoint.password.get_secret_value() not in "\n".join(environment.values())
    assert environment["PGPASSFILE"] == str(passfile)


def test_compose_functional_toolchain_is_pinned_labeled_and_mounts_private_staging(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)

    toolchain = compose_functional_postgres_toolchain(staging, run_id="unit001")

    assert toolchain.pg_dump[:5] == ("docker", "run", "--rm", "--network", "host")
    assert ("--user", f"{os.getuid()}:{os.getgid()}") == (
        toolchain.pg_dump[5],
        toolchain.pg_dump[6],
    )
    assert "hc.migration.run_id=unit001" in toolchain.pg_dump
    assert f"{staging}:{staging}" in toolchain.pg_dump
    assert "postgres:16.10-bookworm@sha256:" in toolchain.pg_dump[-2]
    assert toolchain.pg_dump[-1] == "pg_dump"
    assert toolchain.pg_restore[-1] == "pg_restore"


@pytest.mark.parametrize("password", ["", "line\nbreak", "carriage\rreturn", "nul\x00byte"])
def test_endpoint_rejects_passwords_that_cannot_be_safely_encoded(password: str) -> None:
    with pytest.raises(ValidationError):
        PostgresEndpoint(
            host="postgres.internal",
            database="source_db",
            username="backup_job",
            password=password,
        )


def test_create_dump_rejects_non_private_staging_before_opening_database(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o755)
    opened = False

    def forbidden_connection(_endpoint: PostgresEndpoint) -> object:
        nonlocal opened
        opened = True
        raise AssertionError("database must not be opened")

    adapter = PostgresLogicalBackupAdapter(connection_factory=forbidden_connection)
    with pytest.raises(PostgresBackupError) as rejected:
        adapter.create_dump(
            _endpoint(),
            lease=MaintenanceBackupLease(
                environment_id="production",
                operation_id="backup-001",
                owner_instance_id=UUID("11111111-1111-4111-8111-111111111111"),
                fencing_token=9,
            ),
            staging_directory=tmp_path,
        )
    assert rejected.value.code == "BACKUP_POSTGRES_STAGING_UNSAFE"
    assert not opened


def test_restore_rejects_changed_archive_before_opening_target(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    archive = tmp_path / "postgresql.dump"
    archive.write_bytes(b"valid-before-tamper")
    os.chmod(archive, 0o600)
    artifact = _artifact(archive)
    archive.write_bytes(b"tampered")
    opened = False

    def forbidden_connection(_endpoint: PostgresEndpoint) -> object:
        nonlocal opened
        opened = True
        raise AssertionError("target must not be opened")

    adapter = PostgresLogicalBackupAdapter(connection_factory=forbidden_connection)
    with pytest.raises(PostgresBackupError) as rejected:
        adapter.restore_and_verify(
            artifact,
            _endpoint(database="restore_db"),
            staging_directory=tmp_path,
        )
    assert rejected.value.code == "BACKUP_POSTGRES_ARCHIVE_INVALID"
    assert not opened


def test_restore_same_database_name_defers_to_server_identity(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    archive = tmp_path / "postgresql.dump"
    archive.write_bytes(b"archive")
    archive.chmod(0o600)
    artifact = _artifact(archive)
    opened = False

    class TargetInspected(RuntimeError):
        pass

    def observed_connection(_endpoint: PostgresEndpoint) -> object:
        nonlocal opened
        opened = True
        raise TargetInspected

    adapter = PostgresLogicalBackupAdapter(connection_factory=observed_connection)
    with pytest.raises(TargetInspected):
        adapter.restore_and_verify(
            artifact,
            _endpoint(database="source_db"),
            staging_directory=tmp_path,
        )
    assert opened


def test_wal_anchor_is_explicitly_not_a_physical_backup() -> None:
    anchor = PostgresWalAnchor(
        system_identifier=123,
        timeline_id=7,
        wal_lsn="A/10",
        server_version="16.10",
        server_major=16,
        database_name="source_db",
        in_recovery=False,
    )
    assert anchor.physical_recovery_ready is False
    assert anchor.manifest_coordinate() == (
        "postgresql-wal-anchor/v1:system=123;timeline=7;lsn=A/10"
    )


class _ReceiptVerifier:
    def __init__(self, receipt: WalArchiveReceipt) -> None:
        self.receipt = receipt

    def verify_for_baseline(self, baseline: PhysicalBaselineArtifact) -> WalArchiveReceipt:
        del baseline
        return self.receipt


def _physical_baseline(tmp_path: Path) -> PhysicalBaselineArtifact:
    return PhysicalBaselineArtifact(
        path=tmp_path / "physical",
        server_major=16,
        system_identifier=123,
        timeline_id=7,
        start_lsn="A/100",
        end_lsn="A/200",
        manifest_sha256="1" * 64,
        total_bytes=3,
        files=(
            PhysicalBackupFile(
                relative_path="backup_manifest",
                size_bytes=3,
                sha256="2" * 64,
            ),
        ),
        aggregate_sha256="3" * 64,
    )


def _wal_receipt(**updates: object) -> WalArchiveReceipt:
    now = datetime.now(timezone.utc)
    values: dict[str, object] = {
        "archive_reference": "wal-archive://production-cn",
        "system_identifier": 123,
        "timeline_id": 7,
        "continuous_from_lsn": "A/0",
        "continuous_through_lsn": "A/300",
        "verified_at": now - timedelta(seconds=1),
        "retained_until": now + timedelta(days=30),
        "verification_sha256": "4" * 64,
    }
    values.update(updates)
    return WalArchiveReceipt.model_validate(values)


def test_verified_continuous_wal_receipt_makes_physical_recovery_explicit(
    tmp_path: Path,
) -> None:
    baseline = _physical_baseline(tmp_path)
    receipt = _wal_receipt()
    evidence = PostgresPhysicalBackupAdapter.bind_verified_wal_archive(
        baseline,
        _ReceiptVerifier(receipt),
    )
    assert evidence.physical_recovery_ready is True
    assert evidence.recovery_target_lsn == "A/300"
    assert evidence.wal_archive.immutable is True


@pytest.mark.parametrize(
    ("updates", "code"),
    [
        ({"system_identifier": 124}, "BACKUP_POSTGRES_WAL_SYSTEM_MISMATCH"),
        ({"timeline_id": 8}, "BACKUP_POSTGRES_WAL_TIMELINE_MISMATCH"),
        ({"continuous_from_lsn": "A/101"}, "BACKUP_POSTGRES_WAL_GAP"),
        ({"continuous_through_lsn": "A/1FF"}, "BACKUP_POSTGRES_WAL_GAP"),
        (
            {"retained_until": datetime.now(timezone.utc) - timedelta(seconds=1)},
            "BACKUP_POSTGRES_WAL_RETENTION_INVALID",
        ),
    ],
)
def test_physical_recovery_rejects_wrong_or_gapped_wal_receipt(
    tmp_path: Path,
    updates: dict[str, object],
    code: str,
) -> None:
    with pytest.raises(PostgresBackupError) as rejected:
        PostgresPhysicalBackupAdapter.bind_verified_wal_archive(
            _physical_baseline(tmp_path),
            _ReceiptVerifier(_wal_receipt(**updates)),
        )
    assert rejected.value.code == code
