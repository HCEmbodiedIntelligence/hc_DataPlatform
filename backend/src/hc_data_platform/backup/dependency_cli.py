"""External Job entry point for public configuration and Secret dependencies."""

from __future__ import annotations

import argparse
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
from hc_data_platform.backup.dependencies import (
    ConfigurationBackupAdapter,
    ConfigurationBackupArtifact,
    ConfigurationBackupError,
    ConfigurationBackupVerifier,
    VaultKvTransitProvider,
    VaultProviderConfig,
)
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

_SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_MAX_HELM_BYTES = 32 * 1024 * 1024
_MAX_RECEIPT_BYTES = 4 * 1024 * 1024


def _environment(name: str, *, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or not value:
        raise ConfigurationBackupError(
            "BACKUP_CONFIGURATION_JOB_CONFIG_INVALID",
            "a required configuration-backup Job setting is absent",
        )
    return value


def _integer(name: str, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(_environment(name, default=str(default)))
    except ValueError as exc:
        raise ConfigurationBackupError(
            "BACKUP_CONFIGURATION_JOB_CONFIG_INVALID",
            "a configuration-backup Job integer setting is invalid",
        ) from exc
    if value < minimum or value > maximum:
        raise ConfigurationBackupError(
            "BACKUP_CONFIGURATION_JOB_CONFIG_INVALID",
            "a configuration-backup Job integer setting is out of range",
        )
    return value


def _vault_provider() -> VaultKvTransitProvider:
    try:
        config = VaultProviderConfig.model_validate(
            {
                "endpoint_url": _environment("HC_BACKUP_VAULT_ENDPOINT"),
                "token": _environment("HC_BACKUP_VAULT_TOKEN"),
                "provider_reference": _environment("HC_BACKUP_VAULT_PROVIDER_REFERENCE"),
                "kv_mount": _environment("HC_BACKUP_VAULT_KV_MOUNT", default="secret"),
                "kv_prefix": _environment("HC_BACKUP_VAULT_KV_PREFIX", default="hc-data-platform"),
                "transit_mount": _environment("HC_BACKUP_VAULT_TRANSIT_MOUNT", default="transit"),
                "hmac_key_name": _environment("HC_BACKUP_VAULT_HMAC_KEY_NAME"),
                "hmac_key_version": _integer(
                    "HC_BACKUP_VAULT_HMAC_KEY_VERSION",
                    default=1,
                    minimum=1,
                    maximum=2**31 - 1,
                ),
                "hmac_key_reference": _environment("HC_BACKUP_VAULT_HMAC_KEY_REFERENCE"),
                "namespace": os.environ.get("HC_BACKUP_VAULT_NAMESPACE") or None,
                "timeout_seconds": _integer(
                    "HC_BACKUP_VAULT_TIMEOUT_SECONDS",
                    default=10,
                    minimum=1,
                    maximum=120,
                ),
            }
        )
    except (ValueError, ValidationError) as exc:
        raise ConfigurationBackupError(
            "BACKUP_CONFIGURATION_JOB_CONFIG_INVALID",
            "the Vault Secret/KMS endpoint configuration is invalid",
        ) from exc
    return VaultKvTransitProvider(config)


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
        raise ConfigurationBackupError(
            "BACKUP_CONFIGURATION_JOB_CONFIG_INVALID",
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
        raise ConfigurationBackupError(
            "BACKUP_CONFIGURATION_JOB_CONFIG_INVALID",
            "the maintenance-fence lease coordinate is invalid",
        ) from exc


def _age_toolchain() -> tuple[AgeToolchain, int]:
    binary = _environment("HC_BACKUP_AGE_BINARY", default="age")
    if any(char in binary for char in ("\x00", "\r", "\n")):
        raise ConfigurationBackupError(
            "BACKUP_CONFIGURATION_JOB_CONFIG_INVALID",
            "the age binary path is invalid",
        )
    timeout = _integer(
        "HC_BACKUP_CONFIGURATION_COMMAND_TIMEOUT_SECONDS",
        default=7_200,
        minimum=1,
        maximum=86_400,
    )
    try:
        toolchain = AgeToolchain(
            age=(binary,),
            required_version=_environment("HC_BACKUP_AGE_REQUIRED_VERSION", default="1.3.1"),
        )
    except (ValueError, ValidationError) as exc:
        raise ConfigurationBackupError(
            "BACKUP_CONFIGURATION_JOB_CONFIG_INVALID",
            "the age toolchain contract is invalid",
        ) from exc
    return toolchain, timeout


def _read_regular(path: Path, *, maximum_bytes: int, private: bool) -> bytes:
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except (FileNotFoundError, OSError) as exc:
        raise ConfigurationBackupError(
            "BACKUP_CONFIGURATION_INPUT_INVALID",
            "a configuration-backup input file is unavailable",
        ) from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or (private and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077))
        or info.st_size < 1
        or info.st_size > maximum_bytes
    ):
        raise ConfigurationBackupError(
            "BACKUP_CONFIGURATION_INPUT_INVALID",
            "a configuration-backup input file violates its bounded file contract",
        )
    with resolved.open("rb") as stream:
        payload = stream.read(maximum_bytes + 1)
    if len(payload) > maximum_bytes:
        raise ConfigurationBackupError(
            "BACKUP_CONFIGURATION_INPUT_INVALID",
            "a configuration-backup input file exceeds its bounded size",
        )
    return payload


def _strict_json(payload: bytes) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ConfigurationBackupError(
                    "BACKUP_CONFIGURATION_RECEIPT_INVALID",
                    "the configuration-backup receipt has duplicate members",
                )
            result[key] = value
        return result

    try:
        return json.loads(payload, object_pairs_hook=pairs)
    except ConfigurationBackupError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationBackupError(
            "BACKUP_CONFIGURATION_RECEIPT_INVALID",
            "the configuration-backup receipt is malformed JSON",
        ) from exc


def _read_rendered_helm(path: Path) -> bytes:
    if str(path) != "-":
        return _read_regular(path, maximum_bytes=_MAX_HELM_BYTES, private=False)
    payload = sys.stdin.buffer.read(_MAX_HELM_BYTES + 1)
    if not payload or len(payload) > _MAX_HELM_BYTES:
        raise ConfigurationBackupError(
            "BACKUP_CONFIGURATION_INPUT_INVALID",
            "the rendered Helm stdin stream is absent or exceeds its bounded size",
        )
    return payload


def _artifact_from_receipt(receipt: Path) -> ConfigurationBackupArtifact:
    value = _strict_json(_read_regular(receipt, maximum_bytes=_MAX_RECEIPT_BYTES, private=True))
    try:
        return ConfigurationBackupArtifact.model_validate(value)
    except (ValueError, ValidationError) as exc:
        raise ConfigurationBackupError(
            "BACKUP_CONFIGURATION_RECEIPT_INVALID",
            "the configuration-backup receipt violates its strict contract",
        ) from exc


def _write_receipt(
    artifact: ConfigurationBackupArtifact,
    *,
    staging_directory: Path,
    receipt_name: str,
) -> Path:
    if _SAFE_FILENAME.fullmatch(receipt_name) is None:
        raise ConfigurationBackupError(
            "BACKUP_CONFIGURATION_RECEIPT_INVALID",
            "the configuration-backup receipt filename is invalid",
        )
    staging = staging_directory.resolve(strict=True)
    info = staging.stat()
    if (
        staging_directory.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ConfigurationBackupError(
            "BACKUP_CONFIGURATION_STAGING_UNSAFE",
            "the configuration-backup receipt directory is unsafe",
        )
    receipt = staging / receipt_name
    temporary = staging / f".{receipt_name}.{os.getpid()}.partial"
    if receipt.exists() or receipt.is_symlink() or temporary.exists() or temporary.is_symlink():
        raise ConfigurationBackupError(
            "BACKUP_CONFIGURATION_RECEIPT_EXISTS",
            "the configuration-backup receipt or partial file already exists",
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
            raise ConfigurationBackupError(
                "BACKUP_CONFIGURATION_RECEIPT_EXISTS",
                "the configuration-backup receipt already exists",
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
    parser = argparse.ArgumentParser(prog="hc-configuration-backup")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("snapshot-create", "portable-create"):
        create = subparsers.add_parser(command)
        create.add_argument("--helm-rendered-manifest", type=Path, required=True)
        create.add_argument("--staging-directory", type=Path, required=True)
        create.add_argument(
            "--receipt-name",
            default=f"configuration-{command.removesuffix('-create')}-receipt.json",
        )
    verify = subparsers.add_parser("verify")
    verify.add_argument("--receipt", type=Path, required=True)
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    provider = _vault_provider()
    if arguments.command == "verify":
        artifact = _artifact_from_receipt(arguments.receipt)
        decryptor = None
        if artifact.secrets_envelope.mode == "portable":
            toolchain, timeout = _age_toolchain()
            decryptor = AgeCliDecryptor(
                Path(_environment("HC_BACKUP_AGE_IDENTITY_FILE")),
                toolchain,
                command_timeout_seconds=timeout,
            )
        report = ConfigurationBackupVerifier().verify(
            artifact,
            provider=provider,
            decryptor=decryptor,
        )
        summary: Mapping[str, Any] = {
            "status": "verified",
            "dependency_count": report.dependency_count,
            "preflight_coordinate": report.preflight_coordinate,
            "public_config_sha256": report.public_config_sha256,
            "secrets_envelope_sha256": report.secrets_envelope_sha256,
        }
    else:
        rendered = _read_rendered_helm(arguments.helm_rendered_manifest)
        endpoint = _postgres_endpoint()
        lease = _lease()
        encryptor = None
        mode: Literal["snapshot", "portable"] = (
            "portable" if arguments.command == "portable-create" else "snapshot"
        )
        if mode == "portable":
            toolchain, timeout = _age_toolchain()
            encryptor = AgeCliEncryptor(
                _environment("HC_BACKUP_CONFIGURATION_AGE_RECIPIENT"),
                toolchain,
                command_timeout_seconds=timeout,
            )

        def verify_lease(current: MaintenanceBackupLease) -> None:
            assert_current_backup_lease(endpoint, current)

        artifact = ConfigurationBackupAdapter(provider).create(
            rendered,
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
        summary = {
            "status": "created",
            "mode": mode,
            "dependency_count": artifact.secrets_envelope.dependency_count,
            "preflight_coordinate": artifact.preflight_coordinate,
            "public_config_sha256": artifact.public_config.sha256,
            "receipt": receipt.name,
            "secrets_envelope_sha256": artifact.secrets_envelope.sha256,
        }
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


def main() -> None:
    try:
        raise SystemExit(run())
    except (ConfigurationBackupError, ObjectBackupError, PostgresBackupError) as exc:
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
            '{"code":"BACKUP_CONFIGURATION_INTERNAL","status":"failed"}',
            file=sys.stderr,
        )
        raise SystemExit(3) from None


if __name__ == "__main__":
    main()
