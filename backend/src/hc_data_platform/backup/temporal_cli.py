"""External Job entry point for Temporal recovery-policy inventory."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import ValidationError

from hc_data_platform.backup.contracts import canonical_json_bytes
from hc_data_platform.backup.objects import (
    AgeCliDecryptor,
    AgeCliEncryptor,
    AgeToolchain,
    ObjectBackupError,
)
from hc_data_platform.backup.postgresql import (
    MaintenanceBackupLease,
    PostgresBackupError,
    PostgresEndpoint,
    assert_current_backup_lease,
)
from hc_data_platform.backup.temporal import (
    TemporalBackupAdapter,
    TemporalBackupError,
    TemporalBackupVerifier,
    TemporalConnectionConfig,
    TemporalPolicyArtifact,
    TemporalProviderRecoveryContract,
    TemporalSdkAdminAdapter,
)

_SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_MAX_RECEIPT_BYTES = 4 * 1024 * 1024
_MAX_PROVIDER_POLICY_BYTES = 4 * 1024 * 1024
_MAX_TLS_FILE_BYTES = 8 * 1024 * 1024


def _environment(name: str, *, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or not value:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_JOB_CONFIG_INVALID",
            "a required Temporal backup Job setting is absent",
        )
    return value


def _integer(name: str, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(_environment(name, default=str(default)))
    except ValueError as exc:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_JOB_CONFIG_INVALID",
            "a Temporal backup Job integer setting is invalid",
        ) from exc
    if value < minimum or value > maximum:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_JOB_CONFIG_INVALID",
            "a Temporal backup Job integer setting is out of range",
        )
    return value


def _boolean(name: str, *, default: bool) -> bool:
    value = _environment(name, default="true" if default else "false")
    if value not in {"true", "false"}:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_JOB_CONFIG_INVALID",
            "a Temporal backup Job boolean setting is invalid",
        )
    return value == "true"


def _read_regular(path: Path, *, maximum_bytes: int, private: bool) -> bytes:
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except (FileNotFoundError, OSError) as exc:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_INPUT_INVALID",
            "a Temporal backup input file is unavailable",
        ) from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or (private and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077))
        or info.st_size < 1
        or info.st_size > maximum_bytes
    ):
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_INPUT_INVALID",
            "a Temporal backup input file violates its bounded file contract",
        )
    with resolved.open("rb") as stream:
        payload = stream.read(maximum_bytes + 1)
    if len(payload) > maximum_bytes:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_INPUT_INVALID",
            "a Temporal backup input file exceeds its bounded size",
        )
    return payload


def _optional_file(name: str, *, private: bool) -> bytes | None:
    value = os.environ.get(name)
    if not value:
        return None
    return _read_regular(Path(value), maximum_bytes=_MAX_TLS_FILE_BYTES, private=private)


def _strict_json(payload: bytes) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise TemporalBackupError(
                    "BACKUP_TEMPORAL_RECEIPT_INVALID",
                    "a Temporal backup JSON input has duplicate members",
                )
            result[key] = value
        return result

    try:
        return json.loads(payload, object_pairs_hook=pairs)
    except TemporalBackupError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_RECEIPT_INVALID",
            "a Temporal backup JSON input is malformed",
        ) from exc


def _connection() -> TemporalConnectionConfig:
    mode = _environment("HC_BACKUP_TEMPORAL_DEPLOYMENT_MODE")
    try:
        return TemporalConnectionConfig.model_validate(
            {
                "deployment_mode": mode,
                "target": _environment("HC_BACKUP_TEMPORAL_TARGET"),
                "namespace": _environment("HC_BACKUP_TEMPORAL_NAMESPACE"),
                "cluster_reference": _environment("HC_BACKUP_TEMPORAL_CLUSTER_REFERENCE"),
                "tls_enabled": _boolean(
                    "HC_BACKUP_TEMPORAL_TLS_ENABLED",
                    default=mode == "external_managed",
                ),
                "api_key": os.environ.get("HC_BACKUP_TEMPORAL_API_KEY") or None,
                "server_root_ca_cert": _optional_file(
                    "HC_BACKUP_TEMPORAL_TLS_CA_FILE", private=False
                ),
                "client_certificate": _optional_file(
                    "HC_BACKUP_TEMPORAL_TLS_CERT_FILE", private=False
                ),
                "client_private_key": _optional_file(
                    "HC_BACKUP_TEMPORAL_TLS_KEY_FILE", private=True
                ),
                "verification_server_name": os.environ.get("HC_BACKUP_TEMPORAL_TLS_SERVER_NAME")
                or None,
                "rpc_timeout_seconds": _integer(
                    "HC_BACKUP_TEMPORAL_RPC_TIMEOUT_SECONDS",
                    default=15,
                    minimum=1,
                    maximum=120,
                ),
                "maximum_schedules": _integer(
                    "HC_BACKUP_TEMPORAL_MAXIMUM_SCHEDULES",
                    default=100_000,
                    minimum=1,
                    maximum=1_000_000,
                ),
                "maximum_open_workflows": _integer(
                    "HC_BACKUP_TEMPORAL_MAXIMUM_OPEN_WORKFLOWS",
                    default=1_000_000,
                    minimum=1,
                    maximum=5_000_000,
                ),
                "stability_attempts": _integer(
                    "HC_BACKUP_TEMPORAL_STABILITY_ATTEMPTS",
                    default=5,
                    minimum=2,
                    maximum=10,
                ),
                "stability_interval_milliseconds": _integer(
                    "HC_BACKUP_TEMPORAL_STABILITY_INTERVAL_MILLISECONDS",
                    default=100,
                    minimum=10,
                    maximum=1000,
                ),
            }
        )
    except (ValueError, ValidationError) as exc:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_JOB_CONFIG_INVALID",
            "the Temporal endpoint configuration is invalid",
        ) from exc


def _provider_contract() -> TemporalProviderRecoveryContract:
    payload = _read_regular(
        Path(_environment("HC_BACKUP_TEMPORAL_PROVIDER_POLICY_FILE")),
        maximum_bytes=_MAX_PROVIDER_POLICY_BYTES,
        private=True,
    )
    try:
        return TemporalProviderRecoveryContract.model_validate(_strict_json(payload))
    except (ValueError, ValidationError) as exc:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_PROVIDER_POLICY_INVALID",
            "the Temporal provider recovery contract is invalid",
        ) from exc


def _postgres_endpoint() -> PostgresEndpoint:
    try:
        return PostgresEndpoint.model_validate(
            {
                "host": _environment("HC_BACKUP_POSTGRES_HOST"),
                "port": _integer("HC_BACKUP_POSTGRES_PORT", default=5432, minimum=1, maximum=65535),
                "database": _environment("HC_BACKUP_POSTGRES_DATABASE"),
                "username": _environment("HC_BACKUP_POSTGRES_USERNAME"),
                "password": _environment("HC_BACKUP_POSTGRES_PASSWORD"),
                "sslmode": _environment("HC_BACKUP_POSTGRES_SSLMODE", default="verify-full"),
            }
        )
    except (ValueError, ValidationError) as exc:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_JOB_CONFIG_INVALID",
            "the maintenance-fence PostgreSQL endpoint is invalid",
        ) from exc


def _lease() -> MaintenanceBackupLease:
    try:
        return MaintenanceBackupLease(
            environment_id=_environment("HC_BACKUP_POSTGRES_ENVIRONMENT_ID"),
            operation_id=_environment("HC_BACKUP_POSTGRES_OPERATION_ID"),
            owner_instance_id=UUID(_environment("HC_BACKUP_POSTGRES_OWNER_INSTANCE_ID")),
            fencing_token=_integer(
                "HC_BACKUP_POSTGRES_FENCING_TOKEN",
                default=0,
                minimum=1,
                maximum=2**63 - 1,
            ),
        )
    except (ValueError, ValidationError) as exc:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_JOB_CONFIG_INVALID",
            "the maintenance-fence lease coordinate is invalid",
        ) from exc


def _age_toolchain() -> tuple[AgeToolchain, int]:
    binary = _environment("HC_BACKUP_AGE_BINARY", default="age")
    if any(char in binary for char in ("\x00", "\r", "\n")):
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_JOB_CONFIG_INVALID",
            "the age binary path is invalid",
        )
    timeout = _integer(
        "HC_BACKUP_TEMPORAL_COMMAND_TIMEOUT_SECONDS",
        default=7_200,
        minimum=1,
        maximum=86_400,
    )
    try:
        return (
            AgeToolchain(
                age=(binary,),
                required_version=_environment("HC_BACKUP_AGE_REQUIRED_VERSION", default="1.3.1"),
            ),
            timeout,
        )
    except (ValueError, ValidationError) as exc:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_JOB_CONFIG_INVALID",
            "the age toolchain contract is invalid",
        ) from exc


def _artifact_from_receipt(receipt: Path) -> TemporalPolicyArtifact:
    payload = _read_regular(receipt, maximum_bytes=_MAX_RECEIPT_BYTES, private=True)
    try:
        return TemporalPolicyArtifact.model_validate(_strict_json(payload))
    except (ValueError, ValidationError) as exc:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_RECEIPT_INVALID",
            "the Temporal backup receipt violates its strict contract",
        ) from exc


def _write_receipt(
    artifact: TemporalPolicyArtifact,
    *,
    staging_directory: Path,
    receipt_name: str,
) -> Path:
    if _SAFE_FILENAME.fullmatch(receipt_name) is None:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_RECEIPT_INVALID",
            "the Temporal backup receipt filename is invalid",
        )
    staging = staging_directory.resolve(strict=True)
    info = staging.stat()
    if (
        staging_directory.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_STAGING_UNSAFE",
            "the Temporal backup receipt directory is unsafe",
        )
    receipt = staging / receipt_name
    temporary = staging / f".{receipt_name}.{os.getpid()}.partial"
    if any(path.exists() or path.is_symlink() for path in (receipt, temporary)):
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_RECEIPT_EXISTS",
            "the Temporal backup receipt or partial file already exists",
        )
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    published = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json_bytes(artifact.model_dump(mode="json")))
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, receipt, follow_symlinks=False)
        except FileExistsError as exc:
            raise TemporalBackupError(
                "BACKUP_TEMPORAL_RECEIPT_EXISTS",
                "the Temporal backup receipt already exists",
            ) from exc
        published = True
        temporary.unlink()
        descriptor = os.open(staging, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except BaseException:
        with suppress(FileNotFoundError):
            temporary.unlink()
        if published:
            with suppress(FileNotFoundError):
                receipt.unlink()
        raise
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hc-temporal-backup")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("snapshot-create", "portable-create"):
        create = subparsers.add_parser(command)
        create.add_argument("--staging-directory", type=Path, required=True)
        create.add_argument(
            "--receipt-name",
            default=f"temporal-{command.removesuffix('-create')}-receipt.json",
        )
    verify = subparsers.add_parser("verify")
    verify.add_argument("--receipt", type=Path, required=True)
    subparsers.add_parser("probe")
    return parser


async def _run(arguments: argparse.Namespace) -> Mapping[str, Any]:
    provider = TemporalSdkAdminAdapter(_connection())
    if arguments.command == "probe":
        probe_report = await provider.development_probe()
        return {"status": "probed", **probe_report.model_dump(mode="json")}
    if arguments.command == "verify":
        artifact = _artifact_from_receipt(arguments.receipt)
        decryptor = None
        if artifact.mode == "portable":
            toolchain, timeout = _age_toolchain()
            decryptor = AgeCliDecryptor(
                Path(_environment("HC_BACKUP_AGE_IDENTITY_FILE")),
                toolchain,
                command_timeout_seconds=timeout,
            )
        verification_report = await TemporalBackupVerifier().verify(
            artifact,
            provider=provider,
            decryptor=decryptor,
        )
        return {"status": "verified", **verification_report.model_dump(mode="json")}

    endpoint = _postgres_endpoint()
    lease = _lease()
    mode: Literal["snapshot", "portable"] = (
        "portable" if arguments.command == "portable-create" else "snapshot"
    )
    encryptor = None
    if mode == "portable":
        toolchain, timeout = _age_toolchain()
        encryptor = AgeCliEncryptor(
            _environment("HC_BACKUP_TEMPORAL_AGE_RECIPIENT"),
            toolchain,
            command_timeout_seconds=timeout,
        )

    def verify_lease(current: MaintenanceBackupLease) -> None:
        assert_current_backup_lease(endpoint, current)

    artifact = await TemporalBackupAdapter(provider).create(
        provider_recovery=_provider_contract(),
        lease=lease,
        lease_verifier=verify_lease,
        staging_directory=arguments.staging_directory,
        mode=mode,
        encryptor=encryptor,
    )
    receipt = _write_receipt(
        artifact,
        staging_directory=arguments.staging_directory,
        receipt_name=arguments.receipt_name,
    )
    return {
        "status": "created",
        "mode": mode,
        "schedule_count": artifact.schedule_count,
        "open_workflow_count": artifact.open_workflow_count,
        "consistency_coordinate": artifact.consistency_coordinate,
        "policy_sha256": artifact.sha256,
        "receipt": receipt.name,
    }


def run(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    summary = asyncio.run(_run(arguments))
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


def main() -> None:
    try:
        raise SystemExit(run())
    except (TemporalBackupError, ObjectBackupError, PostgresBackupError) as exc:
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
        print('{"code":"BACKUP_TEMPORAL_INTERNAL","status":"failed"}', file=sys.stderr)
        raise SystemExit(3) from None


if __name__ == "__main__":
    main()
