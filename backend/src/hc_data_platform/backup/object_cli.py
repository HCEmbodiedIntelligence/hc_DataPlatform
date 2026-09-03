"""External Job entry point for versioned S3 snapshot and portable artifacts."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from hc_data_platform.backup.objects import (
    AgeCliDecryptor,
    AgeCliEncryptor,
    AgeToolchain,
    ObjectBackupArtifact,
    ObjectBackupError,
    ObjectBackupVerifier,
    S3ObjectBackupAdapter,
)
from hc_data_platform.backup.postgresql import (
    MaintenanceBackupLease,
    PostgresBackupError,
    PostgresEndpoint,
    assert_current_backup_lease,
)

_SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_SAFE_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
_MAX_RECEIPT_BYTES = 16 * 1024 * 1024


class _S3SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    endpoint_url: str = Field(min_length=1, max_length=2048)
    bucket: str = Field(min_length=3, max_length=63)
    access_key: SecretStr
    secret_key: SecretStr
    region: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$")
    provider: Literal["s3_compatible", "aws_s3", "aliyun_oss"]
    bucket_reference: str = Field(min_length=1, max_length=255)
    prefix: str = Field(max_length=512)
    addressing_style: Literal["path", "virtual"] = "path"

    @field_validator("endpoint_url")
    @classmethod
    def require_safe_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("S3 endpoint must be an origin URL without credentials")
        return value.rstrip("/")

    @field_validator("bucket")
    @classmethod
    def require_bucket(cls, value: str) -> str:
        if _SAFE_BUCKET.fullmatch(value) is None or ".." in value:
            raise ValueError("S3 bucket name is invalid")
        return value

    @field_validator("access_key", "secret_key")
    @classmethod
    def require_credentials(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw or any(char in raw for char in ("\x00", "\r", "\n")):
            raise ValueError("S3 credential material is invalid")
        return value


def _environment(name: str, *, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or not value:
        raise ObjectBackupError(
            "BACKUP_OBJECT_CONFIGURATION_INVALID",
            "a required object-backup Job setting is absent",
        )
    return value


def _integer(name: str, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(_environment(name, default=str(default)))
    except ValueError as exc:
        raise ObjectBackupError(
            "BACKUP_OBJECT_CONFIGURATION_INVALID",
            "an object-backup Job integer setting is invalid",
        ) from exc
    if value < minimum or value > maximum:
        raise ObjectBackupError(
            "BACKUP_OBJECT_CONFIGURATION_INVALID",
            "an object-backup Job integer setting is out of range",
        )
    return value


def _source() -> _S3SourceConfig:
    try:
        return _S3SourceConfig(
            endpoint_url=_environment("HC_BACKUP_OBJECT_ENDPOINT"),
            bucket=_environment("HC_BACKUP_OBJECT_BUCKET"),
            access_key=_environment("HC_BACKUP_OBJECT_ACCESS_KEY"),
            secret_key=_environment("HC_BACKUP_OBJECT_SECRET_KEY"),
            region=_environment("HC_BACKUP_OBJECT_REGION", default="us-east-1"),
            provider=_environment("HC_BACKUP_OBJECT_PROVIDER", default="s3_compatible"),
            bucket_reference=_environment("HC_BACKUP_OBJECT_BUCKET_REFERENCE"),
            prefix=os.environ.get("HC_BACKUP_OBJECT_PREFIX", ""),
            addressing_style=_environment("HC_BACKUP_OBJECT_ADDRESSING_STYLE", default="path"),
        )
    except (ValueError, ValidationError) as exc:
        raise ObjectBackupError(
            "BACKUP_OBJECT_CONFIGURATION_INVALID",
            "the object-backup Job source endpoint is invalid",
        ) from exc


def _postgres_endpoint() -> PostgresEndpoint:
    try:
        return PostgresEndpoint(
            host=_environment("HC_BACKUP_POSTGRES_HOST"),
            port=_integer("HC_BACKUP_POSTGRES_PORT", default=5432, minimum=1, maximum=65535),
            database=_environment("HC_BACKUP_POSTGRES_DATABASE"),
            username=_environment("HC_BACKUP_POSTGRES_USERNAME"),
            password=_environment("HC_BACKUP_POSTGRES_PASSWORD"),
            sslmode=_environment("HC_BACKUP_POSTGRES_SSLMODE", default="verify-full"),
        )
    except (ValueError, ValidationError) as exc:
        raise ObjectBackupError(
            "BACKUP_OBJECT_CONFIGURATION_INVALID",
            "the object-backup Job maintenance endpoint is invalid",
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
        raise ObjectBackupError(
            "BACKUP_OBJECT_CONFIGURATION_INVALID",
            "the object-backup Job maintenance lease is invalid",
        ) from exc


def _client(config: _S3SourceConfig) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=config.endpoint_url,
        aws_access_key_id=config.access_key.get_secret_value(),
        aws_secret_access_key=config.secret_key.get_secret_value(),
        region_name=config.region,
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 1, "mode": "standard"},
            s3={"addressing_style": config.addressing_style},
        ),
    )


def _write_receipt(
    artifact: ObjectBackupArtifact,
    *,
    staging_directory: Path,
    receipt_name: str,
) -> Path:
    if _SAFE_FILENAME.fullmatch(receipt_name) is None:
        raise ObjectBackupError(
            "BACKUP_OBJECT_RECEIPT_NAME_INVALID", "the object receipt filename is invalid"
        )
    staging = staging_directory.resolve(strict=True)
    info = staging.stat()
    if (
        staging_directory.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ObjectBackupError(
            "BACKUP_OBJECT_STAGING_UNSAFE", "the receipt staging directory is unsafe"
        )
    receipt = staging / receipt_name
    if receipt.exists() or receipt.is_symlink():
        raise ObjectBackupError(
            "BACKUP_OBJECT_RECEIPT_EXISTS", "the object-backup receipt already exists"
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
        directory_descriptor = os.open(staging, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        with suppress(FileNotFoundError):
            temporary.unlink()
        raise
    return receipt


def _artifact_from_receipt(receipt: Path) -> ObjectBackupArtifact:
    try:
        resolved = receipt.resolve(strict=True)
        info = resolved.stat()
        if (
            receipt.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_size > _MAX_RECEIPT_BYTES
        ):
            raise ValueError("receipt file policy differs")
        payload = resolved.read_bytes()
        value = json.loads(payload, object_pairs_hook=_unique_json_object)
        return ObjectBackupArtifact.model_validate(value)
    except ObjectBackupError:
        raise
    except Exception as exc:
        raise ObjectBackupError(
            "BACKUP_OBJECT_RECEIPT_INVALID",
            "the object-backup receipt violates its strict contract",
        ) from exc


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hc-object-backup")
    subparsers = parser.add_subparsers(dest="command", required=True)
    snapshot = subparsers.add_parser("snapshot-create")
    snapshot.add_argument("--staging-directory", type=Path, required=True)
    snapshot.add_argument("--inventory-name", default="inventory.jsonl.zst")
    snapshot.add_argument("--receipt-name", default="object-snapshot-receipt.json")
    portable = subparsers.add_parser("portable-create")
    portable.add_argument("--staging-directory", type=Path, required=True)
    portable.add_argument("--inventory-name", default="inventory.jsonl.zst.age")
    portable.add_argument("--receipt-name", default="object-portable-receipt.json")
    portable.add_argument("--checkpoint-name", default=".hc-object-portable-checkpoint.json")
    portable.add_argument("--max-part-bytes", type=int, default=512 * 1024 * 1024)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--receipt", type=Path, required=True)
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "verify":
        artifact = _artifact_from_receipt(arguments.receipt)
        decryptor = None
        if artifact.inventory.mode == "portable":
            decryptor = AgeCliDecryptor(
                Path(_environment("HC_BACKUP_AGE_IDENTITY_FILE")),
                AgeToolchain(
                    age=(_environment("HC_BACKUP_AGE_BINARY", default="age"),),
                    required_version=_environment(
                        "HC_BACKUP_AGE_REQUIRED_VERSION", default="1.3.1"
                    ),
                ),
                command_timeout_seconds=_integer(
                    "HC_BACKUP_OBJECT_COMMAND_TIMEOUT_SECONDS",
                    default=7_200,
                    minimum=1,
                    maximum=86_400,
                ),
            )
        report = ObjectBackupVerifier().verify(artifact, decryptor=decryptor)
        print(
            json.dumps(
                {"status": "verified", **report.model_dump(mode="json")},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    source = _source()
    endpoint = _postgres_endpoint()
    lease = _lease()
    timeout = _integer(
        "HC_BACKUP_OBJECT_COMMAND_TIMEOUT_SECONDS",
        default=7_200,
        minimum=1,
        maximum=86_400,
    )
    adapter = S3ObjectBackupAdapter(
        _client(source),
        bucket=source.bucket,
        bucket_reference=source.bucket_reference,
        provider=source.provider,
        max_workers=_integer("HC_BACKUP_OBJECT_MAX_WORKERS", default=4, minimum=1, maximum=32),
        retry_attempts=_integer(
            "HC_BACKUP_OBJECT_RETRY_ATTEMPTS", default=3, minimum=1, maximum=10
        ),
    )

    def verify_lease(current: MaintenanceBackupLease) -> None:
        assert_current_backup_lease(endpoint, current)

    if arguments.command == "snapshot-create":
        artifact = adapter.create_snapshot(
            lease=lease,
            lease_verifier=verify_lease,
            staging_directory=arguments.staging_directory,
            prefix=source.prefix,
            inventory_name=arguments.inventory_name,
        )
    else:
        recipient = _environment("HC_BACKUP_OBJECT_AGE_RECIPIENT")
        age_binary = _environment("HC_BACKUP_AGE_BINARY", default="age")
        if any(char in age_binary for char in ("\x00", "\r", "\n")):
            raise ObjectBackupError(
                "BACKUP_OBJECT_CONFIGURATION_INVALID", "the age binary path is invalid"
            )
        encryptor = AgeCliEncryptor(
            recipient,
            AgeToolchain(
                age=(age_binary,),
                required_version=_environment("HC_BACKUP_AGE_REQUIRED_VERSION", default="1.3.1"),
            ),
            command_timeout_seconds=timeout,
        )
        artifact = adapter.create_portable(
            lease=lease,
            lease_verifier=verify_lease,
            staging_directory=arguments.staging_directory,
            encryptor=encryptor,
            age_recipient=recipient,
            prefix=source.prefix,
            max_part_bytes=arguments.max_part_bytes,
            inventory_name=arguments.inventory_name,
            checkpoint_name=arguments.checkpoint_name,
        )
    receipt = _write_receipt(
        artifact,
        staging_directory=arguments.staging_directory,
        receipt_name=arguments.receipt_name,
    )
    print(
        json.dumps(
            {
                "status": "created",
                "mode": artifact.inventory.mode,
                "inventory_sha256": artifact.inventory.sha256,
                "object_count": artifact.inventory.object_count,
                "part_count": len(artifact.parts),
                "resumed_part_count": artifact.resumed_part_count,
                "receipt": receipt.name,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def main() -> None:
    try:
        raise SystemExit(run())
    except (ObjectBackupError, PostgresBackupError) as exc:
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
        print('{"code":"BACKUP_OBJECT_INTERNAL","status":"failed"}', file=sys.stderr)
        raise SystemExit(3) from None


if __name__ == "__main__":
    main()
