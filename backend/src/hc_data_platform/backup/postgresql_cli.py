"""External Job entry point for fenced PostgreSQL backup artifacts."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from hc_data_platform.backup.postgresql import (
    LogicalDumpArtifact,
    MaintenanceBackupLease,
    PhysicalBaselineArtifact,
    PostgresBackupError,
    PostgresEndpoint,
    PostgresLogicalBackupAdapter,
    PostgresPhysicalBackupAdapter,
    _fsync_directory,
    compose_functional_postgres_toolchain,
)

_SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def _environment(name: str, *, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or not value:
        raise PostgresBackupError(
            "BACKUP_POSTGRES_CONFIGURATION_INVALID",
            "a required PostgreSQL backup Job setting is absent",
        )
    return value


def _endpoint() -> PostgresEndpoint:
    try:
        return PostgresEndpoint(
            host=_environment("HC_BACKUP_POSTGRES_HOST"),
            port=int(_environment("HC_BACKUP_POSTGRES_PORT", default="5432")),
            database=_environment("HC_BACKUP_POSTGRES_DATABASE"),
            username=_environment("HC_BACKUP_POSTGRES_USERNAME"),
            password=_environment("HC_BACKUP_POSTGRES_PASSWORD"),
            sslmode=_environment("HC_BACKUP_POSTGRES_SSLMODE", default="verify-full"),
        )
    except (ValueError, ValidationError) as exc:
        raise PostgresBackupError(
            "BACKUP_POSTGRES_CONFIGURATION_INVALID",
            "the PostgreSQL backup Job endpoint is invalid",
        ) from exc


def _lease() -> MaintenanceBackupLease:
    try:
        return MaintenanceBackupLease(
            environment_id=_environment("HC_BACKUP_POSTGRES_ENVIRONMENT_ID"),
            operation_id=_environment("HC_BACKUP_POSTGRES_OPERATION_ID"),
            owner_instance_id=UUID(_environment("HC_BACKUP_POSTGRES_OWNER_INSTANCE_ID")),
            fencing_token=int(_environment("HC_BACKUP_POSTGRES_FENCING_TOKEN")),
        )
    except (ValueError, ValidationError) as exc:
        raise PostgresBackupError(
            "BACKUP_POSTGRES_CONFIGURATION_INVALID",
            "the PostgreSQL backup Job lease is invalid",
        ) from exc


def _timeout() -> int:
    try:
        return int(_environment("HC_BACKUP_POSTGRES_COMMAND_TIMEOUT_SECONDS", default="7200"))
    except ValueError as exc:
        raise PostgresBackupError(
            "BACKUP_POSTGRES_CONFIGURATION_INVALID",
            "the PostgreSQL backup Job timeout is invalid",
        ) from exc


def _write_receipt(
    artifact: LogicalDumpArtifact | PhysicalBaselineArtifact,
    *,
    staging_directory: Path,
    receipt_name: str,
) -> Path:
    if _SAFE_FILENAME.fullmatch(receipt_name) is None:
        raise PostgresBackupError(
            "BACKUP_POSTGRES_RECEIPT_NAME_INVALID", "the receipt filename is invalid"
        )
    staging = staging_directory.resolve(strict=True)
    if staging_directory.is_symlink() or not staging.is_dir():
        raise PostgresBackupError(
            "BACKUP_POSTGRES_STAGING_UNSAFE", "the receipt staging directory is unsafe"
        )
    receipt = staging / receipt_name
    if receipt.exists() or receipt.is_symlink():
        raise PostgresBackupError(
            "BACKUP_POSTGRES_RECEIPT_EXISTS", "the PostgreSQL backup receipt already exists"
        )
    temporary = staging / f".{receipt_name}.{os.getpid()}.partial"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(artifact.model_dump_json())
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(receipt)
        _fsync_directory(staging)
    except BaseException:
        with suppress(FileNotFoundError):
            temporary.unlink()
        raise
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hc-postgres-backup")
    subparsers = parser.add_subparsers(dest="command", required=True)

    logical = subparsers.add_parser("logical-create")
    logical.add_argument("--staging-directory", type=Path, required=True)
    logical.add_argument("--artifact-name", default="postgresql.dump")
    logical.add_argument("--receipt-name", default="postgresql-logical-receipt.json")

    physical = subparsers.add_parser("physical-create")
    physical.add_argument("--staging-directory", type=Path, required=True)
    physical.add_argument("--artifact-directory-name", default="postgresql-physical")
    physical.add_argument("--receipt-name", default="postgresql-physical-receipt.json")
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    endpoint = _endpoint()
    lease = _lease()
    timeout = _timeout()
    if arguments.command == "logical-create":
        toolchain = None
        if os.environ.get("HC_BACKUP_RUNTIME_PROFILE") == "compose_functional":
            toolchain = compose_functional_postgres_toolchain(
                arguments.staging_directory,
                run_id=_environment("HC_BACKUP_FUNCTIONAL_RUN_ID"),
            )
        artifact = PostgresLogicalBackupAdapter(
            toolchain=toolchain,
            command_timeout_seconds=timeout,
        ).create_dump(
            endpoint,
            lease=lease,
            staging_directory=arguments.staging_directory,
            artifact_name=arguments.artifact_name,
        )
        receipt = _write_receipt(
            artifact,
            staging_directory=arguments.staging_directory,
            receipt_name=arguments.receipt_name,
        )
        summary = {
            "status": "created",
            "kind": "postgresql_custom",
            "artifact_sha256": artifact.sha256,
            "receipt": receipt.name,
        }
    else:
        physical_artifact = PostgresPhysicalBackupAdapter(
            command_timeout_seconds=timeout
        ).create_physical_baseline(
            endpoint,
            lease=lease,
            staging_directory=arguments.staging_directory,
            artifact_directory_name=arguments.artifact_directory_name,
        )
        receipt = _write_receipt(
            physical_artifact,
            staging_directory=arguments.staging_directory,
            receipt_name=arguments.receipt_name,
        )
        summary = {
            "status": "created",
            "kind": "postgresql_physical",
            "artifact_sha256": physical_artifact.aggregate_sha256,
            "receipt": receipt.name,
        }
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


def main() -> None:
    try:
        raise SystemExit(run())
    except PostgresBackupError as exc:
        print(
            json.dumps(
                {"status": "failed", "code": exc.code},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    except Exception:
        print(
            '{"code":"BACKUP_POSTGRES_INTERNAL","status":"failed"}',
            file=sys.stderr,
        )
        raise SystemExit(3) from None


if __name__ == "__main__":
    main()
