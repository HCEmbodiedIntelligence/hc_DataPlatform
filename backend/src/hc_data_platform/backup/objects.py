"""Version-aware S3 snapshot and encrypted portable object backup artifacts.

The adapter reads immutable S3 object versions, hashes the exact bytes behind
each ``VersionId``, and verifies that the current version set is unchanged
across the maintenance window.  Portable artifacts are deterministic tar/zstd
segments wrapped by an external age X25519 implementation.  Only ciphertext
and owner-only checkpoints are ever persisted.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import tarfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from contextlib import AbstractContextManager, contextmanager, suppress
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from threading import Lock
from typing import Annotated, Any, BinaryIO, Literal, Protocol, cast

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)

from hc_data_platform.backup.contracts import ArtifactPath, Sha256, canonical_json_bytes
from hc_data_platform.backup.postgresql import MaintenanceBackupLease

SafeIdentifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9_.:-]{0,126}[A-Za-z0-9])?$",
    ),
]
AgeRecipient = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=62,
        max_length=62,
        pattern=r"^age1[023456789acdefghjklmnpqrstuvwxyz]{58}$",
    ),
]

_ETAG = re.compile(r'^[^\x00\r\n"]{1,255}$')
_VERSION_ID = re.compile(r"^[^\x00\r\n]{1,1024}$")
_AGE_VERSION = re.compile(r"^v?(?P<version>[0-9]+\.[0-9]+\.[0-9]+)(?:\s|$)")
_CHECKPOINT_NAME = ".hc-object-portable-checkpoint.json"
_PART_OUTPUT_OVERHEAD_BYTES = 128 * 1024
_STREAM_CHUNK_BYTES = 1024 * 1024
_MAX_INVENTORY_LINE_BYTES = 4 * 1024 * 1024
_MAX_OBJECT_COUNT = 10_000_000
_MAX_LIST_PAGES = 10_000


def _normalize_etag(value: str) -> str:
    if value.startswith('"') or value.endswith('"'):
        if len(value) < 3 or not (value.startswith('"') and value.endswith('"')):
            raise ValueError("object ETag quoting is invalid")
        value = value[1:-1]
    if _ETAG.fullmatch(value) is None:
        raise ValueError("object ETag is invalid")
    return value


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ObjectBackupError(RuntimeError):
    """Stable, redacted object-backup failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class PortableObjectSegment(_StrictModel):
    part_path: ArtifactPath
    part_size_bytes: int = Field(gt=0)
    part_sha256: Sha256
    member_name: str = Field(pattern=r"^segments/object-[0-9]{8}-segment-[0-9]{8}\.bin$")
    source_offset: int = Field(ge=0)
    size_bytes: int = Field(ge=0)
    sha256: Sha256


class ObjectInventoryRecord(_StrictModel):
    kind: Literal["object"] = "object"
    ordinal: int = Field(ge=0)
    object_key: str = Field(min_length=1, max_length=1024)
    version_id: str = Field(min_length=1, max_length=1024)
    etag: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0)
    sha256: Sha256
    last_modified: AwareDatetime
    segments: tuple[PortableObjectSegment, ...] = ()

    @field_validator("object_key")
    @classmethod
    def require_s3_key(cls, value: str) -> str:
        if "\x00" in value or "\r" in value or "\n" in value:
            raise ValueError("S3 object key contains forbidden characters")
        if len(value.encode("utf-8")) > 1024:
            raise ValueError("S3 object key exceeds the provider byte limit")
        return value

    @field_validator("version_id")
    @classmethod
    def require_version_id(cls, value: str) -> str:
        if value == "null" or _VERSION_ID.fullmatch(value) is None:
            raise ValueError("an immutable non-null object VersionId is required")
        return value

    @field_validator("etag")
    @classmethod
    def require_etag(cls, value: str) -> str:
        return _normalize_etag(value)

    @field_validator("last_modified")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("object last-modified timestamp must be UTC")
        return value

    @model_validator(mode="after")
    def require_contiguous_segments(self) -> ObjectInventoryRecord:
        if not self.segments:
            return self
        offset = 0
        paths: set[str] = set()
        for segment in self.segments:
            if segment.source_offset != offset:
                raise ValueError("portable object segments must be contiguous")
            if segment.part_path in paths:
                raise ValueError("portable part paths must be unique")
            paths.add(segment.part_path)
            offset += segment.size_bytes
        if offset != self.size_bytes:
            raise ValueError("portable object segments do not cover the source object")
        return self


class ObjectInventoryHeader(_StrictModel):
    format_version: Literal["hc-object-inventory/v1"] = "hc-object-inventory/v1"
    kind: Literal["header"] = "header"
    mode: Literal["snapshot", "portable"]
    provider: Literal["s3_compatible", "aws_s3", "aliyun_oss"]
    bucket_reference: str = Field(min_length=1, max_length=255)
    prefix: str = Field(max_length=512)
    versioning_status: Literal["enabled"] = "enabled"
    consistency_coordinate: str = Field(pattern=r"^s3-version-set/v1:sha256:[0-9a-f]{64}$")
    object_count: int = Field(ge=0, le=_MAX_OBJECT_COUNT)
    total_bytes: int = Field(ge=0)
    max_part_bytes: int | None = Field(default=None, ge=256 * 1024)
    age_recipient_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def require_mode_fields(self) -> ObjectInventoryHeader:
        if self.mode == "portable":
            if self.max_part_bytes is None or self.age_recipient_sha256 is None:
                raise ValueError("portable inventory requires part and recipient metadata")
        elif self.max_part_bytes is not None or self.age_recipient_sha256 is not None:
            raise ValueError("snapshot inventory cannot contain portable metadata")
        return self


class ObjectInventoryArtifact(_StrictModel):
    path: Path
    logical_path: ArtifactPath
    mode: Literal["snapshot", "portable"]
    media_type: Literal[
        "application/vnd.hc.object-inventory.v1+jsonl+zstd",
        "application/vnd.hc.object-inventory.v1+jsonl+zstd+age",
    ]
    client_side_encryption: Literal["repository_kms_only", "age_x25519_v1"]
    size_bytes: int = Field(gt=0)
    sha256: Sha256
    consistency_coordinate: str = Field(pattern=r"^s3-version-set/v1:sha256:[0-9a-f]{64}$")
    object_count: int = Field(ge=0, le=_MAX_OBJECT_COUNT)
    total_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def require_encryption_for_mode(self) -> ObjectInventoryArtifact:
        if self.mode == "portable":
            expected_media = "application/vnd.hc.object-inventory.v1+jsonl+zstd+age"
            expected_encryption = "age_x25519_v1"
        else:
            expected_media = "application/vnd.hc.object-inventory.v1+jsonl+zstd"
            expected_encryption = "repository_kms_only"
        if self.media_type != expected_media or self.client_side_encryption != expected_encryption:
            raise ValueError("inventory media type and encryption do not match its mode")
        return self


class PortableObjectPartArtifact(_StrictModel):
    path: Path
    logical_path: ArtifactPath
    part_number: int = Field(gt=0)
    object_ordinal: int = Field(ge=0)
    segment_number: int = Field(ge=0)
    member_name: str = Field(pattern=r"^segments/object-[0-9]{8}-segment-[0-9]{8}\.bin$")
    source_offset: int = Field(ge=0)
    source_size_bytes: int = Field(ge=0)
    source_sha256: Sha256
    size_bytes: int = Field(gt=0)
    sha256: Sha256
    media_type: Literal["application/vnd.hc.object-part.v1+tar+zstd+age"] = (
        "application/vnd.hc.object-part.v1+tar+zstd+age"
    )
    client_side_encryption: Literal["age_x25519_v1"] = "age_x25519_v1"


class ObjectBackupArtifact(_StrictModel):
    format_version: Literal["hc-object-backup-artifact/v1"] = "hc-object-backup-artifact/v1"
    inventory: ObjectInventoryArtifact
    parts: tuple[PortableObjectPartArtifact, ...] = ()
    resumed_part_count: int = Field(ge=0)
    max_part_bytes: int | None = Field(default=None, ge=256 * 1024)
    age_recipient_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def require_consistent_parts(self) -> ObjectBackupArtifact:
        numbers = [part.part_number for part in self.parts]
        paths = [part.logical_path for part in self.parts]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError("portable part numbers must be contiguous")
        if len(paths) != len(set(paths)):
            raise ValueError("portable part paths must be unique")
        if self.inventory.mode == "portable":
            if self.max_part_bytes is None or self.age_recipient_sha256 is None:
                raise ValueError("portable backup summary is incomplete")
            if any(part.size_bytes > self.max_part_bytes for part in self.parts):
                raise ValueError("portable part exceeds the configured upper bound")
        elif self.parts or self.max_part_bytes is not None or self.age_recipient_sha256 is not None:
            raise ValueError("snapshot backup cannot contain portable parts")
        if self.resumed_part_count > len(self.parts):
            raise ValueError("resumed part count exceeds the artifact part count")
        return self


class ObjectBackupVerificationReport(_StrictModel):
    mode: Literal["snapshot", "portable"]
    consistency_coordinate: str = Field(pattern=r"^s3-version-set/v1:sha256:[0-9a-f]{64}$")
    object_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    verified_part_count: int = Field(ge=0)
    content_sha256: Sha256


class ObjectRestoreReport(_StrictModel):
    target_bucket_reference: str = Field(min_length=1, max_length=255)
    target_prefix: str = Field(min_length=1, max_length=512)
    object_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    resumed_object_count: int = Field(ge=0)
    source_content_sha256: Sha256
    target_version_set_sha256: Sha256


class S3VersionClient(Protocol):
    def get_bucket_versioning(self, **kwargs: object) -> Mapping[str, Any]: ...

    def list_object_versions(self, **kwargs: object) -> Mapping[str, Any]: ...

    def list_multipart_uploads(self, **kwargs: object) -> Mapping[str, Any]: ...

    def get_object(self, **kwargs: object) -> Mapping[str, Any]: ...


class S3ObjectRestoreClient(Protocol):
    def get_bucket_versioning(self, **kwargs: object) -> Mapping[str, Any]: ...

    def head_object(self, **kwargs: object) -> Mapping[str, Any]: ...

    def get_object(self, **kwargs: object) -> Mapping[str, Any]: ...

    def put_object(self, **kwargs: object) -> Mapping[str, Any]: ...


class AgeEncryptedWriter(Protocol):
    def open(self, destination: Path, *, cwd: Path) -> AbstractContextManager[BinaryIO]: ...


class AgeDecryptedReader(Protocol):
    def open(self, source: Path, *, cwd: Path) -> AbstractContextManager[BinaryIO]: ...


class AgeToolchain(_StrictModel):
    age: tuple[str, ...] = ("age",)
    required_version: str = Field(default="1.3.1", pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")

    @field_validator("age")
    @classmethod
    def require_argv_prefix(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(value) > 32:
            raise ValueError("age argv prefix must contain 1..32 arguments")
        if any(not item or any(char in item for char in ("\x00", "\r", "\n")) for item in value):
            raise ValueError("age argv prefix contains invalid text")
        return value


class _AgeCliBase:
    def __init__(
        self,
        toolchain: AgeToolchain | None = None,
        *,
        command_timeout_seconds: int = 7_200,
    ) -> None:
        if command_timeout_seconds < 1:
            raise ValueError("age command timeout must be positive")
        self._toolchain = toolchain or AgeToolchain()
        self._timeout = command_timeout_seconds
        self._version_lock = Lock()
        self._version_verified = False

    @staticmethod
    def _environment() -> dict[str, str]:
        return {
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        }

    def _assert_version(self, *, cwd: Path) -> None:
        with self._version_lock:
            if self._version_verified:
                return
            try:
                completed = subprocess.run(
                    [*self._toolchain.age, "--version"],
                    cwd=cwd,
                    env=self._environment(),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=min(self._timeout, 30),
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ObjectBackupError(
                    "BACKUP_OBJECT_AGE_UNAVAILABLE",
                    "the pinned age tool could not be executed",
                ) from exc
            match = _AGE_VERSION.match(completed.stdout.decode("ascii", errors="ignore").strip())
            if (
                completed.returncode != 0
                or match is None
                or match.group("version") != self._toolchain.required_version
            ):
                raise ObjectBackupError(
                    "BACKUP_OBJECT_AGE_VERSION_MISMATCH",
                    "the age tool version does not match the backup image contract",
                )
            self._version_verified = True


class AgeCliEncryptor(_AgeCliBase):
    """Streaming age X25519 encryption; recipient material is public."""

    def __init__(
        self,
        recipient: AgeRecipient,
        toolchain: AgeToolchain | None = None,
        *,
        command_timeout_seconds: int = 7_200,
    ) -> None:
        super().__init__(toolchain, command_timeout_seconds=command_timeout_seconds)
        try:
            self.recipient = TypeAdapter(AgeRecipient).validate_python(recipient)
        except Exception as exc:
            raise ObjectBackupError(
                "BACKUP_OBJECT_AGE_RECIPIENT_INVALID",
                "the age X25519 recipient is invalid",
            ) from exc

    @contextmanager
    def open(self, destination: Path, *, cwd: Path) -> Iterator[BinaryIO]:
        self._assert_version(cwd=cwd)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        output = os.fdopen(descriptor, "wb", buffering=0)
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(
                [*self._toolchain.age, "--encrypt", "--recipient", self.recipient],
                cwd=cwd,
                env=self._environment(),
                stdin=subprocess.PIPE,
                stdout=output,
                stderr=subprocess.DEVNULL,
            )
            if process.stdin is None:
                raise ObjectBackupError(
                    "BACKUP_OBJECT_AGE_FAILED", "the age encryptor did not open its input"
                )
            yield cast(BinaryIO, process.stdin)
            process.stdin.close()
            returncode = process.wait(timeout=self._timeout)
            if returncode != 0:
                raise ObjectBackupError(
                    "BACKUP_OBJECT_AGE_FAILED", "age encryption did not complete"
                )
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            if process is not None and process.poll() is None:
                process.kill()
                with suppress(Exception):
                    process.wait(timeout=5)
            raise
        finally:
            output.close()


class AgeCliDecryptor(_AgeCliBase):
    """Streaming age decryption using an owner-only identity file."""

    def __init__(
        self,
        identity_path: Path,
        toolchain: AgeToolchain | None = None,
        *,
        command_timeout_seconds: int = 7_200,
    ) -> None:
        super().__init__(toolchain, command_timeout_seconds=command_timeout_seconds)
        resolved = identity_path.resolve(strict=True)
        info = resolved.stat()
        if (
            identity_path.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise ObjectBackupError(
                "BACKUP_OBJECT_AGE_IDENTITY_UNSAFE",
                "the age identity file must be an owner-only regular file",
            )
        self._identity_path = resolved

    @contextmanager
    def open(self, source: Path, *, cwd: Path) -> Iterator[BinaryIO]:
        self._assert_version(cwd=cwd)
        process: subprocess.Popen[bytes] | None = None
        encrypted = source.open("rb")
        try:
            process = subprocess.Popen(
                [
                    *self._toolchain.age,
                    "--decrypt",
                    "--identity",
                    str(self._identity_path),
                ],
                cwd=cwd,
                env=self._environment(),
                stdin=encrypted,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            if process.stdout is None:
                raise ObjectBackupError(
                    "BACKUP_OBJECT_AGE_FAILED", "the age decryptor did not open its output"
                )
            yield cast(BinaryIO, process.stdout)
            process.stdout.close()
            returncode = process.wait(timeout=self._timeout)
            if returncode != 0:
                raise ObjectBackupError(
                    "BACKUP_OBJECT_AGE_FAILED", "age decryption did not complete"
                )
        except BaseException:
            if process is not None and process.poll() is None:
                process.kill()
                with suppress(Exception):
                    process.wait(timeout=5)
            raise
        finally:
            encrypted.close()


class _ListedObject(_StrictModel):
    object_key: str
    version_id: str
    etag: str
    size_bytes: int = Field(ge=0)
    last_modified: AwareDatetime

    @field_validator("object_key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        return ObjectInventoryRecord.require_s3_key(value)

    @field_validator("version_id")
    @classmethod
    def validate_version(cls, value: str) -> str:
        return ObjectInventoryRecord.require_version_id(value)

    @field_validator("etag")
    @classmethod
    def validate_etag(cls, value: str) -> str:
        return ObjectInventoryRecord.require_etag(value)

    @field_validator("last_modified")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return ObjectInventoryRecord.require_utc(value)


class _PartPlan(_StrictModel):
    part_number: int = Field(gt=0)
    object_ordinal: int = Field(ge=0)
    segment_number: int = Field(ge=0)
    logical_path: ArtifactPath
    member_name: str
    source_offset: int = Field(ge=0)
    source_size_bytes: int = Field(ge=0)


class _CheckpointRecord(_StrictModel):
    object_key: str
    version_id: str
    etag: str
    size_bytes: int = Field(ge=0)
    sha256: Sha256
    last_modified: AwareDatetime


class _PortableCheckpoint(_StrictModel):
    format_version: Literal["hc-object-portable-checkpoint/v1"] = "hc-object-portable-checkpoint/v1"
    consistency_coordinate: str
    age_recipient_sha256: Sha256
    max_part_bytes: int
    inventory_logical_path: ArtifactPath
    records: tuple[_CheckpointRecord, ...]
    plans: tuple[_PartPlan, ...]
    completed_parts: tuple[PortableObjectPartArtifact, ...] = ()

    @model_validator(mode="after")
    def require_sorted_checkpoint(self) -> _PortableCheckpoint:
        if [plan.part_number for plan in self.plans] != list(range(1, len(self.plans) + 1)):
            raise ValueError("checkpoint part plan is not contiguous")
        completed = [part.part_number for part in self.completed_parts]
        if completed != sorted(completed) or len(completed) != len(set(completed)):
            raise ValueError("checkpoint completed parts are not sorted and unique")
        return self


def _sha256_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(_STREAM_CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _secure_staging(directory: Path) -> Path:
    resolved = directory.resolve(strict=True)
    info = resolved.stat()
    if (
        directory.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ObjectBackupError(
            "BACKUP_OBJECT_STAGING_UNSAFE",
            "the object-backup staging directory must be owner-only",
        )
    return resolved


def _private_directory(parent: Path, name: str) -> Path:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ObjectBackupError(
            "BACKUP_OBJECT_PATH_INVALID", "an object artifact directory name is invalid"
        )
    path = parent / name
    with suppress(FileExistsError):
        path.mkdir(mode=0o700)
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ObjectBackupError(
            "BACKUP_OBJECT_STAGING_UNSAFE", "an object artifact directory is unsafe"
        )
    return resolved


def _load_json_strict(payload: bytes) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ObjectBackupError(
                    "BACKUP_OBJECT_INVENTORY_INVALID",
                    "object backup JSON contains duplicate member names",
                )
            result[key] = value
        return result

    try:
        return json.loads(payload, object_pairs_hook=pairs)
    except ObjectBackupError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ObjectBackupError(
            "BACKUP_OBJECT_INVENTORY_INVALID", "object backup JSON is malformed"
        ) from exc


def _atomic_json(path: Path, model: BaseModel) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.partial")
    if temporary.exists() or temporary.is_symlink():
        raise ObjectBackupError(
            "BACKUP_OBJECT_STAGING_CONFLICT", "an object backup partial file already exists"
        )
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json_bytes(model.model_dump(mode="json")))
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        _fsync_directory(path.parent)
    except BaseException:
        with suppress(FileNotFoundError):
            temporary.unlink()
        raise


def _coordinate(records: Sequence[ObjectInventoryRecord]) -> str:
    evidence = [
        {
            "etag": record.etag,
            "last_modified": record.last_modified.isoformat(),
            "object_key": record.object_key,
            "sha256": record.sha256,
            "size_bytes": record.size_bytes,
            "version_id": record.version_id,
        }
        for record in records
    ]
    return f"s3-version-set/v1:sha256:{hashlib.sha256(canonical_json_bytes(evidence)).hexdigest()}"


def _records_content_sha256(records: Sequence[ObjectInventoryRecord]) -> str:
    evidence = [
        {
            "object_key": record.object_key,
            "sha256": record.sha256,
            "size_bytes": record.size_bytes,
            "version_id": record.version_id,
        }
        for record in records
    ]
    return hashlib.sha256(canonical_json_bytes(evidence)).hexdigest()


class S3ObjectBackupAdapter:
    """Create exact-version snapshot inventories and resumable portable parts."""

    def __init__(
        self,
        client: S3VersionClient,
        *,
        bucket: str,
        bucket_reference: str,
        provider: Literal["s3_compatible", "aws_s3", "aliyun_oss"] = "s3_compatible",
        max_workers: int = 4,
        retry_attempts: int = 3,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not bucket or len(bucket) > 255 or any(char in bucket for char in ("\x00", "\r", "\n")):
            raise ValueError("object backup bucket is invalid")
        if (
            not bucket_reference
            or len(bucket_reference) > 255
            or any(char in bucket_reference for char in ("\x00", "\r", "\n"))
        ):
            raise ValueError("object backup bucket reference is invalid")
        if max_workers < 1 or max_workers > 32:
            raise ValueError("object backup worker count must be between 1 and 32")
        if retry_attempts < 1 or retry_attempts > 10:
            raise ValueError("object backup retry attempts must be between 1 and 10")
        self._client = client
        self._bucket = bucket
        self._bucket_reference = bucket_reference
        self._provider = provider
        self._max_workers = max_workers
        self._retry_attempts = retry_attempts
        self._sleeper = sleeper

    def create_snapshot(
        self,
        *,
        lease: MaintenanceBackupLease,
        lease_verifier: Callable[[MaintenanceBackupLease], None],
        staging_directory: Path,
        prefix: str = "",
        inventory_name: str = "inventory.jsonl.zst",
    ) -> ObjectBackupArtifact:
        staging = _secure_staging(staging_directory)
        logical_path = self._inventory_logical_path(inventory_name, portable=False)
        destination = self._artifact_destination(staging, logical_path)
        self._reject_existing(destination)
        self._verify_lease(lease, lease_verifier)
        records, coordinate = self._capture_records(prefix)
        self._verify_lease(lease, lease_verifier)
        header = ObjectInventoryHeader(
            mode="snapshot",
            provider=self._provider,
            bucket_reference=self._bucket_reference,
            prefix=prefix,
            consistency_coordinate=coordinate,
            object_count=len(records),
            total_bytes=sum(record.size_bytes for record in records),
        )
        size, digest = self._write_inventory(
            destination,
            header=header,
            records=records,
            encryptor=None,
        )
        inventory = ObjectInventoryArtifact(
            path=destination,
            logical_path=logical_path,
            mode="snapshot",
            media_type="application/vnd.hc.object-inventory.v1+jsonl+zstd",
            client_side_encryption="repository_kms_only",
            size_bytes=size,
            sha256=digest,
            consistency_coordinate=coordinate,
            object_count=len(records),
            total_bytes=sum(record.size_bytes for record in records),
        )
        return ObjectBackupArtifact(inventory=inventory, resumed_part_count=0)

    def create_portable(
        self,
        *,
        lease: MaintenanceBackupLease,
        lease_verifier: Callable[[MaintenanceBackupLease], None],
        staging_directory: Path,
        encryptor: AgeEncryptedWriter,
        age_recipient: AgeRecipient,
        prefix: str = "",
        max_part_bytes: int = 512 * 1024 * 1024,
        inventory_name: str = "inventory.jsonl.zst.age",
        checkpoint_name: str = _CHECKPOINT_NAME,
    ) -> ObjectBackupArtifact:
        if max_part_bytes < 256 * 1024:
            raise ObjectBackupError(
                "BACKUP_OBJECT_PART_LIMIT_INVALID",
                "the portable part upper bound must be at least 256 KiB",
            )
        if max_part_bytes <= _PART_OUTPUT_OVERHEAD_BYTES:
            raise ObjectBackupError(
                "BACKUP_OBJECT_PART_LIMIT_INVALID",
                "the portable part upper bound leaves no payload capacity",
            )
        if (
            not checkpoint_name
            or checkpoint_name in {".", ".."}
            or "/" in checkpoint_name
            or "\\" in checkpoint_name
        ):
            raise ObjectBackupError(
                "BACKUP_OBJECT_PATH_INVALID", "the portable checkpoint name is invalid"
            )
        staging = _secure_staging(staging_directory)
        logical_inventory_path = self._inventory_logical_path(inventory_name, portable=True)
        inventory_destination = self._artifact_destination(staging, logical_inventory_path)
        self._reject_existing(inventory_destination)
        checkpoint_path = staging / checkpoint_name
        if checkpoint_path.is_symlink():
            raise ObjectBackupError(
                "BACKUP_OBJECT_STAGING_CONFLICT", "the portable checkpoint path is unsafe"
            )

        self._verify_lease(lease, lease_verifier)
        records, coordinate = self._capture_records(prefix)
        recipient_sha256 = hashlib.sha256(age_recipient.encode("ascii")).hexdigest()
        plans = self._part_plans(records, max_part_bytes=max_part_bytes)
        checkpoint_records = tuple(
            _CheckpointRecord(
                object_key=record.object_key,
                version_id=record.version_id,
                etag=record.etag,
                size_bytes=record.size_bytes,
                sha256=record.sha256,
                last_modified=record.last_modified,
            )
            for record in records
        )
        expected_checkpoint = _PortableCheckpoint(
            consistency_coordinate=coordinate,
            age_recipient_sha256=recipient_sha256,
            max_part_bytes=max_part_bytes,
            inventory_logical_path=logical_inventory_path,
            records=checkpoint_records,
            plans=plans,
        )
        checkpoint = self._load_or_create_checkpoint(checkpoint_path, expected_checkpoint)
        completed: dict[int, PortableObjectPartArtifact] = {
            part.part_number: part for part in checkpoint.completed_parts
        }
        resumed = len(completed)
        for part in completed.values():
            self._verify_part_file(part, max_part_bytes=max_part_bytes)

        checkpoint_lock = Lock()
        failure: BaseException | None = None
        futures: dict[Future[PortableObjectPartArtifact], _PartPlan] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            for plan in plans:
                if plan.part_number in completed:
                    continue
                destination = self._artifact_destination(staging, plan.logical_path)
                if destination.exists() or destination.is_symlink():
                    raise ObjectBackupError(
                        "BACKUP_OBJECT_RESUME_CONFLICT",
                        "an uncheckpointed portable part already exists",
                    )
                record = records[plan.object_ordinal]
                future = executor.submit(
                    self._create_part,
                    plan,
                    record,
                    destination=destination,
                    encryptor=encryptor,
                    max_part_bytes=max_part_bytes,
                )
                futures[future] = plan
            for future in as_completed(futures):
                try:
                    part = future.result()
                    with checkpoint_lock:
                        completed[part.part_number] = part
                        current = expected_checkpoint.model_copy(
                            update={
                                "completed_parts": tuple(
                                    completed[number] for number in sorted(completed)
                                )
                            }
                        )
                        _atomic_json(checkpoint_path, current)
                except BaseException as exc:
                    failure = failure or exc
        if failure is not None:
            if isinstance(failure, ObjectBackupError):
                raise failure
            raise ObjectBackupError(
                "BACKUP_OBJECT_PORTABLE_CREATE_FAILED",
                "a portable object part could not be created",
            ) from failure

        parts = tuple(completed[number] for number in sorted(completed))
        if len(parts) != len(plans):
            raise ObjectBackupError(
                "BACKUP_OBJECT_CHECKPOINT_INVALID",
                "the portable checkpoint does not contain every planned part",
            )
        records_with_segments = self._attach_segments(records, parts)
        self._verify_current_capture(prefix, records)
        self._verify_lease(lease, lease_verifier)
        header = ObjectInventoryHeader(
            mode="portable",
            provider=self._provider,
            bucket_reference=self._bucket_reference,
            prefix=prefix,
            consistency_coordinate=coordinate,
            object_count=len(records_with_segments),
            total_bytes=sum(record.size_bytes for record in records_with_segments),
            max_part_bytes=max_part_bytes,
            age_recipient_sha256=recipient_sha256,
        )
        inventory_size, inventory_digest = self._write_inventory(
            inventory_destination,
            header=header,
            records=records_with_segments,
            encryptor=encryptor,
        )
        with suppress(FileNotFoundError):
            checkpoint_path.unlink()
        _fsync_directory(staging)
        inventory = ObjectInventoryArtifact(
            path=inventory_destination,
            logical_path=logical_inventory_path,
            mode="portable",
            media_type="application/vnd.hc.object-inventory.v1+jsonl+zstd+age",
            client_side_encryption="age_x25519_v1",
            size_bytes=inventory_size,
            sha256=inventory_digest,
            consistency_coordinate=coordinate,
            object_count=len(records_with_segments),
            total_bytes=sum(record.size_bytes for record in records_with_segments),
        )
        return ObjectBackupArtifact(
            inventory=inventory,
            parts=parts,
            resumed_part_count=resumed,
            max_part_bytes=max_part_bytes,
            age_recipient_sha256=recipient_sha256,
        )

    @staticmethod
    def _verify_lease(
        lease: MaintenanceBackupLease,
        verifier: Callable[[MaintenanceBackupLease], None],
    ) -> None:
        try:
            verifier(lease)
        except ObjectBackupError:
            raise
        except Exception as exc:
            raise ObjectBackupError(
                "BACKUP_OBJECT_FENCE_REJECTED",
                "the object backup Job does not own the maintenance fence",
            ) from exc

    def _retry(self, code: str, operation: Callable[[], Any]) -> Any:
        last_error: BaseException | None = None
        for attempt in range(self._retry_attempts):
            try:
                return operation()
            except ObjectBackupError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self._retry_attempts:
                    self._sleeper(min(0.1 * (2**attempt), 1.0))
        raise ObjectBackupError(
            code, "an S3 object backup operation exhausted retries"
        ) from last_error

    def _assert_versioning(self) -> None:
        response = cast(
            Mapping[str, Any],
            self._retry(
                "BACKUP_OBJECT_SOURCE_READ_FAILED",
                lambda: self._client.get_bucket_versioning(Bucket=self._bucket),
            ),
        )
        if response.get("Status") != "Enabled":
            raise ObjectBackupError(
                "BACKUP_OBJECT_VERSIONING_REQUIRED",
                "the source object bucket must have versioning enabled",
            )

    def _assert_no_multipart(self, prefix: str) -> None:
        key_marker: str | None = None
        upload_marker: str | None = None
        page_count = 0
        while True:
            page_count += 1
            if page_count > _MAX_LIST_PAGES:
                raise ObjectBackupError(
                    "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                    "the multipart inventory exceeds its bounded page count",
                )
            request: dict[str, object] = {
                "Bucket": self._bucket,
                "Prefix": prefix,
                "MaxUploads": 1000,
            }
            if key_marker is not None:
                request["KeyMarker"] = key_marker
            if upload_marker is not None:
                request["UploadIdMarker"] = upload_marker
            response = cast(
                Mapping[str, Any],
                self._retry(
                    "BACKUP_OBJECT_SOURCE_READ_FAILED",
                    partial(self._client.list_multipart_uploads, **request),
                ),
            )
            uploads = response.get("Uploads", ())
            if not isinstance(uploads, Sequence) or isinstance(uploads, (str, bytes)):
                raise ObjectBackupError(
                    "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                    "the multipart inventory response is malformed",
                )
            if uploads:
                raise ObjectBackupError(
                    "BACKUP_OBJECT_MULTIPART_INCOMPLETE",
                    "the source prefix contains an incomplete multipart upload",
                )
            truncated = response.get("IsTruncated", False)
            if not isinstance(truncated, bool):
                raise ObjectBackupError(
                    "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                    "the multipart inventory truncation flag is malformed",
                )
            if not truncated:
                return
            next_key = response.get("NextKeyMarker")
            next_upload = response.get("NextUploadIdMarker")
            if not isinstance(next_key, str) or not isinstance(next_upload, str):
                raise ObjectBackupError(
                    "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                    "the multipart inventory cursor is malformed",
                )
            if (next_key, next_upload) == (key_marker, upload_marker):
                raise ObjectBackupError(
                    "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                    "the multipart inventory cursor did not advance",
                )
            key_marker = next_key
            upload_marker = next_upload

    def _list_current_versions(self, prefix: str) -> tuple[_ListedObject, ...]:
        self._assert_versioning()
        self._assert_no_multipart(prefix)
        key_marker: str | None = None
        version_marker: str | None = None
        current: dict[str, _ListedObject] = {}
        deleted: set[str] = set()
        scanned_entries = 0
        page_count = 0
        while True:
            page_count += 1
            if page_count > _MAX_LIST_PAGES:
                raise ObjectBackupError(
                    "BACKUP_OBJECT_INVENTORY_TOO_LARGE",
                    "the object version inventory exceeds its bounded page count",
                )
            request: dict[str, object] = {
                "Bucket": self._bucket,
                "Prefix": prefix,
                "MaxKeys": 1000,
            }
            if key_marker is not None:
                request["KeyMarker"] = key_marker
            if version_marker is not None:
                request["VersionIdMarker"] = version_marker
            response = cast(
                Mapping[str, Any],
                self._retry(
                    "BACKUP_OBJECT_SOURCE_READ_FAILED",
                    partial(self._client.list_object_versions, **request),
                ),
            )
            raw_versions = response.get("Versions", ())
            raw_markers = response.get("DeleteMarkers", ())
            if (
                not isinstance(raw_versions, Sequence)
                or isinstance(raw_versions, (str, bytes))
                or not isinstance(raw_markers, Sequence)
                or isinstance(raw_markers, (str, bytes))
            ):
                raise ObjectBackupError(
                    "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                    "the object version inventory response is malformed",
                )
            scanned_entries += len(raw_versions) + len(raw_markers)
            if scanned_entries > _MAX_OBJECT_COUNT:
                raise ObjectBackupError(
                    "BACKUP_OBJECT_INVENTORY_TOO_LARGE",
                    "the source version entry count exceeds the bounded inventory contract",
                )
            for raw in raw_markers:
                if not isinstance(raw, Mapping):
                    raise ObjectBackupError(
                        "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                        "an object delete marker is malformed",
                    )
                latest = raw.get("IsLatest")
                if not isinstance(latest, bool):
                    raise ObjectBackupError(
                        "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                        "an object delete marker has a malformed latest flag",
                    )
                if latest:
                    key = raw.get("Key")
                    if not isinstance(key, str) or not key.startswith(prefix):
                        raise ObjectBackupError(
                            "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                            "an object delete marker is outside the requested prefix",
                        )
                    deleted.add(key)
            for raw in raw_versions:
                if not isinstance(raw, Mapping):
                    raise ObjectBackupError(
                        "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                        "an object version entry is malformed",
                    )
                latest = raw.get("IsLatest")
                if not isinstance(latest, bool):
                    raise ObjectBackupError(
                        "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                        "an object version entry has a malformed latest flag",
                    )
                if not latest:
                    continue
                key = raw.get("Key")
                version_id = raw.get("VersionId")
                etag = raw.get("ETag")
                size = raw.get("Size")
                modified = raw.get("LastModified")
                if (
                    not isinstance(key, str)
                    or not key.startswith(prefix)
                    or not isinstance(version_id, str)
                    or not isinstance(etag, str)
                    or not isinstance(size, int)
                    or isinstance(size, bool)
                    or not isinstance(modified, datetime)
                    or modified.tzinfo is None
                    or modified.utcoffset() is None
                ):
                    raise ObjectBackupError(
                        "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                        "an object version entry is incomplete",
                    )
                if key in current:
                    raise ObjectBackupError(
                        "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                        "the source returned duplicate current object versions",
                    )
                try:
                    current[key] = _ListedObject(
                        object_key=key,
                        version_id=version_id,
                        etag=etag,
                        size_bytes=size,
                        last_modified=modified.astimezone(timezone.utc),
                    )
                except Exception as exc:
                    raise ObjectBackupError(
                        "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                        "an object version entry violates the strict source contract",
                    ) from exc
                if len(current) > _MAX_OBJECT_COUNT:
                    raise ObjectBackupError(
                        "BACKUP_OBJECT_INVENTORY_TOO_LARGE",
                        "the source object count exceeds the bounded inventory contract",
                    )
            truncated = response.get("IsTruncated", False)
            if not isinstance(truncated, bool):
                raise ObjectBackupError(
                    "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                    "the object version inventory truncation flag is malformed",
                )
            if not truncated:
                break
            next_key = response.get("NextKeyMarker")
            next_version = response.get("NextVersionIdMarker")
            if not isinstance(next_key, str) or not isinstance(next_version, str):
                raise ObjectBackupError(
                    "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                    "the object version inventory cursor is malformed",
                )
            if (next_key, next_version) == (key_marker, version_marker):
                raise ObjectBackupError(
                    "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                    "the object version inventory cursor did not advance",
                )
            key_marker = next_key
            version_marker = next_version
        if deleted & set(current):
            raise ObjectBackupError(
                "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                "an object cannot have both a current version and current delete marker",
            )
        for key in deleted:
            current.pop(key, None)
        return tuple(current[key] for key in sorted(current))

    def _capture_records(self, prefix: str) -> tuple[tuple[ObjectInventoryRecord, ...], str]:
        if (
            len(prefix) > 512
            or len(prefix.encode("utf-8")) > 1024
            or any(char in prefix for char in ("\x00", "\r", "\n"))
        ):
            raise ObjectBackupError(
                "BACKUP_OBJECT_PREFIX_INVALID", "the object backup prefix is invalid"
            )
        before = self._list_current_versions(prefix)
        records_by_ordinal: dict[int, ObjectInventoryRecord] = {}
        futures: dict[Future[ObjectInventoryRecord], int] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            for ordinal, item in enumerate(before):
                futures[executor.submit(self._hash_object, ordinal, item)] = ordinal
            for future in as_completed(futures):
                record = future.result()
                records_by_ordinal[record.ordinal] = record
        records = tuple(records_by_ordinal[index] for index in range(len(before)))
        self._verify_current_capture(prefix, records)
        return records, _coordinate(records)

    def _verify_current_capture(
        self, prefix: str, records: Sequence[ObjectInventoryRecord]
    ) -> None:
        after = self._list_current_versions(prefix)
        expected = tuple(
            (
                record.object_key,
                record.version_id,
                record.etag,
                record.size_bytes,
                record.last_modified,
            )
            for record in records
        )
        observed = tuple(
            (
                item.object_key,
                item.version_id,
                item.etag,
                item.size_bytes,
                item.last_modified,
            )
            for item in after
        )
        if observed != expected:
            raise ObjectBackupError(
                "BACKUP_OBJECT_VERSION_SET_CHANGED",
                "the current object version set changed during the backup window",
            )

    def _hash_object(self, ordinal: int, item: _ListedObject) -> ObjectInventoryRecord:
        size, digest = self._read_version(item, offset=0, length=item.size_bytes, sink=None)
        if size != item.size_bytes:
            raise ObjectBackupError(
                "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                "an exact object version returned the wrong byte length",
            )
        return ObjectInventoryRecord(
            ordinal=ordinal,
            object_key=item.object_key,
            version_id=item.version_id,
            etag=item.etag,
            size_bytes=item.size_bytes,
            sha256=digest,
            last_modified=item.last_modified,
        )

    def _read_version(
        self,
        item: _ListedObject | ObjectInventoryRecord,
        *,
        offset: int,
        length: int,
        sink: BinaryIO | None,
    ) -> tuple[int, str]:
        def read_once() -> tuple[int, str]:
            request: dict[str, object] = {
                "Bucket": self._bucket,
                "Key": item.object_key,
                "VersionId": item.version_id,
            }
            if length > 0 and (offset != 0 or length != item.size_bytes):
                request["Range"] = f"bytes={offset}-{offset + length - 1}"
            response = self._client.get_object(**request)
            version_id = response.get("VersionId")
            etag = response.get("ETag")
            content_length = response.get("ContentLength")
            body = response.get("Body")
            try:
                normalized_etag = _normalize_etag(etag) if isinstance(etag, str) else None
            except ValueError:
                normalized_etag = None
            if (
                version_id != item.version_id
                or normalized_etag != item.etag
                or content_length != length
                or isinstance(content_length, bool)
                or body is None
                or not callable(getattr(body, "read", None))
            ):
                close = getattr(body, "close", None)
                if callable(close):
                    close()
                raise ObjectBackupError(
                    "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                    "an exact object-version response is malformed",
                )
            digest = hashlib.sha256()
            count = 0
            try:
                while chunk := body.read(_STREAM_CHUNK_BYTES):
                    if not isinstance(chunk, bytes):
                        raise ObjectBackupError(
                            "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                            "an object-version body returned non-byte content",
                        )
                    digest.update(chunk)
                    count += len(chunk)
                    if sink is not None:
                        sink.write(chunk)
            finally:
                close = getattr(body, "close", None)
                if callable(close):
                    close()
            if count != length:
                raise ObjectBackupError(
                    "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                    "an exact object-version body was truncated",
                )
            return count, digest.hexdigest()

        if sink is not None:
            return read_once()
        return cast(
            tuple[int, str],
            self._retry("BACKUP_OBJECT_SOURCE_READ_FAILED", read_once),
        )

    @staticmethod
    def _inventory_logical_path(name: str, *, portable: bool) -> str:
        expected_suffix = ".jsonl.zst.age" if portable else ".jsonl.zst"
        if (
            not name.endswith(expected_suffix)
            or len(name) > 128
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
        ):
            raise ObjectBackupError(
                "BACKUP_OBJECT_PATH_INVALID", "the object inventory filename is invalid"
            )
        return f"objects/{name}"

    @staticmethod
    def _artifact_destination(staging: Path, logical_path: str) -> Path:
        parts = logical_path.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ObjectBackupError(
                "BACKUP_OBJECT_PATH_INVALID", "an object artifact path is invalid"
            )
        parent = staging
        for name in parts[:-1]:
            parent = _private_directory(parent, name)
        destination = parent / parts[-1]
        try:
            resolved_parent = destination.parent.resolve(strict=True)
        except OSError as exc:
            raise ObjectBackupError(
                "BACKUP_OBJECT_STAGING_UNSAFE", "an object artifact parent is unavailable"
            ) from exc
        if resolved_parent != parent.resolve(strict=True):
            raise ObjectBackupError(
                "BACKUP_OBJECT_STAGING_UNSAFE", "an object artifact parent is unsafe"
            )
        return destination

    @staticmethod
    def _reject_existing(path: Path) -> None:
        if path.exists() or path.is_symlink():
            raise ObjectBackupError(
                "BACKUP_OBJECT_ARTIFACT_EXISTS", "an object backup artifact already exists"
            )

    @staticmethod
    def _part_plans(
        records: Sequence[ObjectInventoryRecord], *, max_part_bytes: int
    ) -> tuple[_PartPlan, ...]:
        payload_bytes = max_part_bytes - _PART_OUTPUT_OVERHEAD_BYTES
        plans: list[_PartPlan] = []
        next_part = 1
        for record in records:
            offsets = range(0, record.size_bytes, payload_bytes) if record.size_bytes else (0,)
            for segment_number, offset in enumerate(offsets):
                size = min(payload_bytes, record.size_bytes - offset)
                plans.append(
                    _PartPlan(
                        part_number=next_part,
                        object_ordinal=record.ordinal,
                        segment_number=segment_number,
                        logical_path=f"objects/parts/part-{next_part:08d}.tar.zst.age",
                        member_name=(
                            f"segments/object-{record.ordinal:08d}-segment-{segment_number:08d}.bin"
                        ),
                        source_offset=offset,
                        source_size_bytes=size,
                    )
                )
                next_part += 1
        return tuple(plans)

    def _load_or_create_checkpoint(
        self, path: Path, expected: _PortableCheckpoint
    ) -> _PortableCheckpoint:
        if not path.exists():
            _atomic_json(path, expected)
            return expected
        info = path.stat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_size > 64 * 1024 * 1024
        ):
            raise ObjectBackupError(
                "BACKUP_OBJECT_CHECKPOINT_INVALID",
                "the portable checkpoint file is unsafe",
            )
        document = _load_json_strict(path.read_bytes())
        try:
            loaded = _PortableCheckpoint.model_validate(document)
        except Exception as exc:
            raise ObjectBackupError(
                "BACKUP_OBJECT_CHECKPOINT_INVALID",
                "the portable checkpoint does not match its strict contract",
            ) from exc
        if loaded.model_copy(update={"completed_parts": ()}) != expected:
            raise ObjectBackupError(
                "BACKUP_OBJECT_RESUME_SOURCE_MISMATCH",
                "the portable checkpoint belongs to a different version set or recipient",
            )
        plans = {plan.part_number: plan for plan in loaded.plans}
        for part in loaded.completed_parts:
            plan = plans.get(part.part_number)
            if plan is None:
                raise ObjectBackupError(
                    "BACKUP_OBJECT_CHECKPOINT_INVALID",
                    "a checkpoint part has no matching plan",
                )
            expected_path = self._artifact_destination(path.parent, plan.logical_path)
            if (
                part.path.resolve(strict=False) != expected_path.resolve(strict=False)
                or part.logical_path != plan.logical_path
                or part.object_ordinal != plan.object_ordinal
                or part.segment_number != plan.segment_number
                or part.member_name != plan.member_name
                or part.source_offset != plan.source_offset
                or part.source_size_bytes != plan.source_size_bytes
            ):
                raise ObjectBackupError(
                    "BACKUP_OBJECT_CHECKPOINT_INVALID",
                    "a checkpoint part does not match its deterministic plan",
                )
        return loaded

    @staticmethod
    def _verify_part_file(part: PortableObjectPartArtifact, *, max_part_bytes: int) -> None:
        try:
            info = part.path.stat()
        except FileNotFoundError as exc:
            raise ObjectBackupError(
                "BACKUP_OBJECT_PART_MISSING", "a portable object part is missing"
            ) from exc
        if part.path.is_symlink() or not stat.S_ISREG(info.st_mode):
            raise ObjectBackupError(
                "BACKUP_OBJECT_PART_INVALID", "a portable object part path is unsafe"
            )
        size, digest = _sha256_file(part.path)
        if (
            size != part.size_bytes
            or digest != part.sha256
            or size > max_part_bytes
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise ObjectBackupError(
                "BACKUP_OBJECT_PART_HASH_MISMATCH",
                "a portable object part does not match its checkpoint hash",
            )

    def _create_part(
        self,
        plan: _PartPlan,
        record: ObjectInventoryRecord,
        *,
        destination: Path,
        encryptor: AgeEncryptedWriter,
        max_part_bytes: int,
    ) -> PortableObjectPartArtifact:
        last_error: BaseException | None = None
        for attempt in range(self._retry_attempts):
            temporary = destination.with_name(
                f".{destination.name}.{os.getpid()}.{plan.part_number}.partial"
            )
            with suppress(FileNotFoundError):
                temporary.unlink()
            try:
                segment_digest = self._write_part_attempt(
                    plan,
                    record,
                    temporary=temporary,
                    encryptor=encryptor,
                )
                size, digest = _sha256_file(temporary)
                if size > max_part_bytes:
                    raise ObjectBackupError(
                        "BACKUP_OBJECT_PART_LIMIT_EXCEEDED",
                        "an encrypted portable part exceeds its configured upper bound",
                    )
                temporary.replace(destination)
                _fsync_directory(destination.parent)
                return PortableObjectPartArtifact(
                    path=destination,
                    logical_path=plan.logical_path,
                    part_number=plan.part_number,
                    object_ordinal=plan.object_ordinal,
                    segment_number=plan.segment_number,
                    member_name=plan.member_name,
                    source_offset=plan.source_offset,
                    source_size_bytes=plan.source_size_bytes,
                    source_sha256=segment_digest,
                    size_bytes=size,
                    sha256=digest,
                )
            except ObjectBackupError as exc:
                with suppress(FileNotFoundError):
                    temporary.unlink()
                if exc.code not in {
                    "BACKUP_OBJECT_SOURCE_READ_FAILED",
                    "BACKUP_OBJECT_PORTABLE_CREATE_FAILED",
                }:
                    raise
                last_error = exc
            except Exception as exc:
                with suppress(FileNotFoundError):
                    temporary.unlink()
                last_error = exc
            if attempt + 1 < self._retry_attempts:
                self._sleeper(min(0.1 * (2**attempt), 1.0))
        raise ObjectBackupError(
            "BACKUP_OBJECT_PORTABLE_CREATE_FAILED",
            "a portable object part exhausted bounded retries",
        ) from last_error

    def _write_part_attempt(
        self,
        plan: _PartPlan,
        record: ObjectInventoryRecord,
        *,
        temporary: Path,
        encryptor: AgeEncryptedWriter,
    ) -> str:
        try:
            import zstandard
        except ImportError as exc:  # pragma: no cover - packaging contract
            raise ObjectBackupError(
                "BACKUP_OBJECT_ZSTD_UNAVAILABLE", "the zstd runtime dependency is absent"
            ) from exc
        with self._open_version_body(
            record,
            offset=plan.source_offset,
            length=plan.source_size_bytes,
        ) as source:
            compressor = zstandard.ZstdCompressor(level=3, write_checksum=True)
            with (
                encryptor.open(temporary, cwd=temporary.parent) as encrypted,
                compressor.stream_writer(encrypted, closefd=False) as compressed,
                tarfile.open(
                    fileobj=compressed,
                    mode="w|",
                    format=tarfile.PAX_FORMAT,
                ) as archive,
            ):
                member = tarfile.TarInfo(plan.member_name)
                member.size = plan.source_size_bytes
                member.mtime = 0
                member.mode = 0o600
                member.uid = 0
                member.gid = 0
                member.uname = ""
                member.gname = ""
                archive.addfile(member, cast(BinaryIO, source))
            if source.bytes_read != plan.source_size_bytes:
                raise ObjectBackupError(
                    "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                    "an exact object-version range was truncated",
                )
            return source.sha256

    @contextmanager
    def _open_version_body(
        self,
        item: _ListedObject | ObjectInventoryRecord,
        *,
        offset: int,
        length: int,
    ) -> Iterator[_HashingVersionBody]:
        request: dict[str, object] = {
            "Bucket": self._bucket,
            "Key": item.object_key,
            "VersionId": item.version_id,
        }
        if length > 0 and (offset != 0 or length != item.size_bytes):
            request["Range"] = f"bytes={offset}-{offset + length - 1}"
        try:
            response = self._client.get_object(**request)
        except Exception as exc:
            raise ObjectBackupError(
                "BACKUP_OBJECT_SOURCE_READ_FAILED",
                "an exact object version could not be opened",
            ) from exc
        version_id = response.get("VersionId")
        etag = response.get("ETag")
        content_length = response.get("ContentLength")
        body = response.get("Body")
        try:
            normalized_etag = _normalize_etag(etag) if isinstance(etag, str) else None
        except ValueError:
            normalized_etag = None
        if (
            version_id != item.version_id
            or normalized_etag != item.etag
            or content_length != length
            or isinstance(content_length, bool)
            or body is None
            or not callable(getattr(body, "read", None))
        ):
            close = getattr(body, "close", None)
            if callable(close):
                close()
            raise ObjectBackupError(
                "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                "an exact object-version range response is malformed",
            )
        reader = _HashingVersionBody(body, expected_bytes=length)
        try:
            yield reader
            if reader.bytes_read != length:
                raise ObjectBackupError(
                    "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                    "an exact object-version range was truncated",
                )
        finally:
            reader.close()

    @staticmethod
    def _attach_segments(
        records: Sequence[ObjectInventoryRecord],
        parts: Sequence[PortableObjectPartArtifact],
    ) -> tuple[ObjectInventoryRecord, ...]:
        grouped: dict[int, list[PortableObjectPartArtifact]] = {
            record.ordinal: [] for record in records
        }
        for part in parts:
            if part.object_ordinal not in grouped:
                raise ObjectBackupError(
                    "BACKUP_OBJECT_CHECKPOINT_INVALID",
                    "a portable part references an absent inventory object",
                )
            grouped[part.object_ordinal].append(part)
        result: list[ObjectInventoryRecord] = []
        for record in records:
            object_parts = sorted(grouped[record.ordinal], key=lambda item: item.segment_number)
            segments = tuple(
                PortableObjectSegment(
                    part_path=part.logical_path,
                    part_size_bytes=part.size_bytes,
                    part_sha256=part.sha256,
                    member_name=part.member_name,
                    source_offset=part.source_offset,
                    size_bytes=part.source_size_bytes,
                    sha256=part.source_sha256,
                )
                for part in object_parts
            )
            result.append(record.model_copy(update={"segments": segments}))
        return tuple(result)

    @staticmethod
    def _write_inventory(
        destination: Path,
        *,
        header: ObjectInventoryHeader,
        records: Sequence[ObjectInventoryRecord],
        encryptor: AgeEncryptedWriter | None,
    ) -> tuple[int, str]:
        try:
            import zstandard
        except ImportError as exc:  # pragma: no cover - packaging contract
            raise ObjectBackupError(
                "BACKUP_OBJECT_ZSTD_UNAVAILABLE", "the zstd runtime dependency is absent"
            ) from exc
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.partial")
        if temporary.exists() or temporary.is_symlink():
            raise ObjectBackupError(
                "BACKUP_OBJECT_STAGING_CONFLICT", "an inventory partial file already exists"
            )
        try:
            writer_context = (
                encryptor.open(temporary, cwd=temporary.parent)
                if encryptor is not None
                else _private_file_writer(temporary)
            )
            with writer_context as output:
                compressor = zstandard.ZstdCompressor(level=3, write_checksum=True)
                with compressor.stream_writer(output, closefd=False) as compressed:
                    for model in (header, *records):
                        line = canonical_json_bytes(model.model_dump(mode="json"))
                        if len(line) > _MAX_INVENTORY_LINE_BYTES:
                            raise ObjectBackupError(
                                "BACKUP_OBJECT_INVENTORY_TOO_LARGE",
                                "an object inventory row exceeds its bounded size",
                            )
                        compressed.write(line)
                        compressed.write(b"\n")
            temporary.replace(destination)
            _fsync_directory(destination.parent)
        except BaseException:
            with suppress(FileNotFoundError):
                temporary.unlink()
            raise
        return _sha256_file(destination)


class _HashingVersionBody:
    def __init__(self, body: Any, *, expected_bytes: int) -> None:
        self._body = body
        self._expected = expected_bytes
        self._digest = hashlib.sha256()
        self.bytes_read = 0

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest()

    def read(self, size: int = -1) -> bytes:
        chunk = self._body.read(size)
        if not isinstance(chunk, bytes):
            raise ObjectBackupError(
                "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                "an object-version body returned non-byte content",
            )
        self.bytes_read += len(chunk)
        if self.bytes_read > self._expected:
            raise ObjectBackupError(
                "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID",
                "an object-version body exceeded its declared size",
            )
        self._digest.update(chunk)
        return chunk

    def close(self) -> None:
        close = getattr(self._body, "close", None)
        if callable(close):
            close()


@contextmanager
def _private_file_writer(path: Path) -> Iterator[BinaryIO]:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb", buffering=0) as stream:
        yield stream
        stream.flush()
        os.fsync(stream.fileno())


class ObjectBackupVerifier:
    """Verify inventory/part hashes and reconstruct portable content hashes."""

    def inventory_records(
        self,
        artifact: ObjectBackupArtifact,
        *,
        decryptor: AgeDecryptedReader | None = None,
        expected_bucket_reference: str | None = None,
        expected_prefix: str | None = None,
    ) -> tuple[ObjectInventoryRecord, ...]:
        """Return strict records after verifying the complete backup artifact."""

        self.verify(artifact, decryptor=decryptor)
        return self._read_inventory(
            artifact.inventory,
            decryptor=decryptor,
            expected_bucket_reference=expected_bucket_reference,
            expected_prefix=expected_prefix,
        )

    def inventory_document(
        self,
        inventory: ObjectInventoryArtifact,
        *,
        decryptor: AgeDecryptedReader | None = None,
        expected_bucket_reference: str | None = None,
        expected_prefix: str | None = None,
    ) -> tuple[ObjectInventoryHeader, tuple[ObjectInventoryRecord, ...]]:
        """Read a strict inventory before portable part receipts are reconstructed."""

        self._verify_inventory_file(inventory)
        return self._read_inventory_document(
            inventory,
            decryptor=decryptor,
            expected_bucket_reference=expected_bucket_reference,
            expected_prefix=expected_prefix,
        )

    def verify(
        self,
        artifact: ObjectBackupArtifact,
        *,
        decryptor: AgeDecryptedReader | None = None,
    ) -> ObjectBackupVerificationReport:
        self._verify_inventory_file(artifact.inventory)
        if artifact.inventory.mode == "portable" and decryptor is None:
            raise ObjectBackupError(
                "BACKUP_OBJECT_AGE_IDENTITY_REQUIRED",
                "portable verification requires an external age identity",
            )
        records = self._read_inventory(
            artifact.inventory,
            decryptor=decryptor,
        )
        if artifact.inventory.mode == "snapshot":
            if any(record.segments for record in records):
                raise ObjectBackupError(
                    "BACKUP_OBJECT_INVENTORY_INVALID",
                    "a snapshot inventory cannot reference portable parts",
                )
            return ObjectBackupVerificationReport(
                mode="snapshot",
                consistency_coordinate=artifact.inventory.consistency_coordinate,
                object_count=len(records),
                total_bytes=sum(record.size_bytes for record in records),
                verified_part_count=0,
                content_sha256=_records_content_sha256(records),
            )

        assert decryptor is not None
        assert artifact.max_part_bytes is not None
        parts_by_path = {part.logical_path: part for part in artifact.parts}
        expected_paths = {segment.part_path for record in records for segment in record.segments}
        if expected_paths != set(parts_by_path) or len(expected_paths) != len(artifact.parts):
            raise ObjectBackupError(
                "BACKUP_OBJECT_INVENTORY_PART_MISMATCH",
                "the portable inventory and part receipt do not close over the same paths",
            )
        for part in artifact.parts:
            S3ObjectBackupAdapter._verify_part_file(
                part,
                max_part_bytes=artifact.max_part_bytes,
            )

        part_order = [
            part
            for record in records
            for segment in record.segments
            for part in (parts_by_path[segment.part_path],)
        ]
        if [part.part_number for part in part_order] != list(range(1, len(part_order) + 1)):
            raise ObjectBackupError(
                "BACKUP_OBJECT_INVENTORY_PART_MISMATCH",
                "portable parts are not ordered by inventory object and segment",
            )
        for record in records:
            object_digest = hashlib.sha256()
            object_bytes = 0
            for segment in record.segments:
                part = parts_by_path[segment.part_path]
                if (
                    part.member_name != segment.member_name
                    or part.source_offset != segment.source_offset
                    or part.source_size_bytes != segment.size_bytes
                    or part.source_sha256 != segment.sha256
                    or part.size_bytes != segment.part_size_bytes
                    or part.sha256 != segment.part_sha256
                ):
                    raise ObjectBackupError(
                        "BACKUP_OBJECT_INVENTORY_PART_MISMATCH",
                        "a portable part receipt differs from its inventory segment",
                    )
                segment_size, segment_sha = self._verify_part_payload(
                    part,
                    decryptor,
                    object_digest=object_digest,
                )
                if segment_size != segment.size_bytes or segment_sha != segment.sha256:
                    raise ObjectBackupError(
                        "BACKUP_OBJECT_PART_CONTENT_MISMATCH",
                        "a decrypted portable part differs from its source segment",
                    )
                object_bytes += segment_size
            if object_bytes != record.size_bytes or object_digest.hexdigest() != record.sha256:
                raise ObjectBackupError(
                    "BACKUP_OBJECT_CONTENT_HASH_MISMATCH",
                    "portable parts do not reconstruct the inventory object hash",
                )
        return ObjectBackupVerificationReport(
            mode="portable",
            consistency_coordinate=artifact.inventory.consistency_coordinate,
            object_count=len(records),
            total_bytes=sum(record.size_bytes for record in records),
            verified_part_count=len(artifact.parts),
            content_sha256=_records_content_sha256(records),
        )

    @staticmethod
    def _verify_inventory_file(inventory: ObjectInventoryArtifact) -> None:
        try:
            info = inventory.path.stat()
        except FileNotFoundError as exc:
            raise ObjectBackupError(
                "BACKUP_OBJECT_INVENTORY_MISSING", "the object inventory artifact is missing"
            ) from exc
        if (
            inventory.path.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise ObjectBackupError(
                "BACKUP_OBJECT_INVENTORY_INVALID", "the object inventory path is unsafe"
            )
        size, digest = _sha256_file(inventory.path)
        if size != inventory.size_bytes or digest != inventory.sha256:
            raise ObjectBackupError(
                "BACKUP_OBJECT_INVENTORY_HASH_MISMATCH",
                "the object inventory does not match its receipt hash",
            )

    def _read_inventory(
        self,
        inventory: ObjectInventoryArtifact,
        *,
        decryptor: AgeDecryptedReader | None,
        expected_bucket_reference: str | None = None,
        expected_prefix: str | None = None,
    ) -> tuple[ObjectInventoryRecord, ...]:
        return self._read_inventory_document(
            inventory,
            decryptor=decryptor,
            expected_bucket_reference=expected_bucket_reference,
            expected_prefix=expected_prefix,
        )[1]

    def _read_inventory_document(
        self,
        inventory: ObjectInventoryArtifact,
        *,
        decryptor: AgeDecryptedReader | None,
        expected_bucket_reference: str | None = None,
        expected_prefix: str | None = None,
    ) -> tuple[ObjectInventoryHeader, tuple[ObjectInventoryRecord, ...]]:
        with self._decompressed_reader(inventory, decryptor=decryptor) as stream:
            header_line = stream.readline(_MAX_INVENTORY_LINE_BYTES + 1)
            if not header_line or len(header_line) > _MAX_INVENTORY_LINE_BYTES:
                raise ObjectBackupError(
                    "BACKUP_OBJECT_INVENTORY_INVALID",
                    "the object inventory header is absent or oversized",
                )
            try:
                header = ObjectInventoryHeader.model_validate(_load_json_strict(header_line))
            except ObjectBackupError:
                raise
            except Exception as exc:
                raise ObjectBackupError(
                    "BACKUP_OBJECT_INVENTORY_INVALID",
                    "the object inventory header violates its strict contract",
                ) from exc
            records: list[ObjectInventoryRecord] = []
            while line := stream.readline(_MAX_INVENTORY_LINE_BYTES + 1):
                if len(line) > _MAX_INVENTORY_LINE_BYTES:
                    raise ObjectBackupError(
                        "BACKUP_OBJECT_INVENTORY_INVALID",
                        "an object inventory record is oversized",
                    )
                try:
                    record = ObjectInventoryRecord.model_validate(_load_json_strict(line))
                except ObjectBackupError:
                    raise
                except Exception as exc:
                    raise ObjectBackupError(
                        "BACKUP_OBJECT_INVENTORY_INVALID",
                        "an object inventory record violates its strict contract",
                    ) from exc
                records.append(record)
                if len(records) > _MAX_OBJECT_COUNT:
                    raise ObjectBackupError(
                        "BACKUP_OBJECT_INVENTORY_TOO_LARGE",
                        "the object inventory exceeds its bounded record count",
                    )
        if [record.ordinal for record in records] != list(range(len(records))):
            raise ObjectBackupError(
                "BACKUP_OBJECT_INVENTORY_INVALID",
                "object inventory ordinals must be contiguous",
            )
        keys = [record.object_key for record in records]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ObjectBackupError(
                "BACKUP_OBJECT_INVENTORY_INVALID",
                "object inventory keys must be sorted and unique",
            )
        if (
            header.mode != inventory.mode
            or header.consistency_coordinate != inventory.consistency_coordinate
            or header.object_count != inventory.object_count
            or header.total_bytes != inventory.total_bytes
            or header.object_count != len(records)
            or header.total_bytes != sum(record.size_bytes for record in records)
            or _coordinate(records) != header.consistency_coordinate
            or (
                expected_bucket_reference is not None
                and header.bucket_reference != expected_bucket_reference
            )
            or (expected_prefix is not None and header.prefix != expected_prefix)
        ):
            raise ObjectBackupError(
                "BACKUP_OBJECT_INVENTORY_SUMMARY_MISMATCH",
                "the object inventory header differs from its exact records or receipt",
            )
        return header, tuple(records)

    @contextmanager
    def _decompressed_reader(
        self,
        inventory: ObjectInventoryArtifact,
        *,
        decryptor: AgeDecryptedReader | None,
    ) -> Iterator[BinaryIO]:
        try:
            import zstandard
        except ImportError as exc:  # pragma: no cover - packaging contract
            raise ObjectBackupError(
                "BACKUP_OBJECT_ZSTD_UNAVAILABLE", "the zstd runtime dependency is absent"
            ) from exc
        if inventory.mode == "portable":
            if decryptor is None:
                raise ObjectBackupError(
                    "BACKUP_OBJECT_AGE_IDENTITY_REQUIRED",
                    "portable verification requires an age identity",
                )
            source_context = decryptor.open(inventory.path, cwd=inventory.path.parent)
        else:
            source_context = _binary_file_reader(inventory.path)
        try:
            with (
                source_context as source,
                zstandard.ZstdDecompressor().stream_reader(source, closefd=False) as decompressed,
            ):
                buffered = io.BufferedReader(decompressed)
                try:
                    yield cast(BinaryIO, buffered)
                    while buffered.read(_STREAM_CHUNK_BYTES):
                        pass
                finally:
                    buffered.close()
        except ObjectBackupError:
            raise
        except Exception as exc:
            raise ObjectBackupError(
                "BACKUP_OBJECT_INVENTORY_INVALID",
                "the object inventory could not be decrypted or decompressed",
            ) from exc

    def _verify_part_payload(
        self,
        part: PortableObjectPartArtifact,
        decryptor: AgeDecryptedReader,
        *,
        object_digest: Any,
    ) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        with self._part_payload_reader(part, decryptor) as payload:
            while chunk := payload.read(_STREAM_CHUNK_BYTES):
                digest.update(chunk)
                object_digest.update(chunk)
                size += len(chunk)
        return size, digest.hexdigest()

    @contextmanager
    def _part_payload_reader(
        self,
        part: PortableObjectPartArtifact,
        decryptor: AgeDecryptedReader,
    ) -> Iterator[BinaryIO]:
        try:
            import zstandard
        except ImportError as exc:  # pragma: no cover - packaging contract
            raise ObjectBackupError(
                "BACKUP_OBJECT_ZSTD_UNAVAILABLE", "the zstd runtime dependency is absent"
            ) from exc
        try:
            with (
                decryptor.open(part.path, cwd=part.path.parent) as encrypted,
                zstandard.ZstdDecompressor().stream_reader(
                    encrypted, closefd=False
                ) as decompressed,
                tarfile.open(fileobj=decompressed, mode="r|") as archive,
            ):
                member = archive.next()
                if (
                    member is None
                    or not member.isfile()
                    or member.name != part.member_name
                    or member.size != part.source_size_bytes
                    or member.uid != 0
                    or member.gid != 0
                    or stat.S_IMODE(member.mode) != 0o600
                    or member.mtime != 0
                ):
                    raise ObjectBackupError(
                        "BACKUP_OBJECT_PART_FORMAT_INVALID",
                        "a portable part tar member violates its strict contract",
                    )
                payload = archive.extractfile(member)
                if payload is None:
                    raise ObjectBackupError(
                        "BACKUP_OBJECT_PART_FORMAT_INVALID",
                        "a portable part tar member has no payload",
                    )
                yield cast(BinaryIO, payload)
                while payload.read(_STREAM_CHUNK_BYTES):
                    pass
                if archive.next() is not None:
                    raise ObjectBackupError(
                        "BACKUP_OBJECT_PART_FORMAT_INVALID",
                        "a portable part must contain exactly one tar member",
                    )
                while decompressed.read(_STREAM_CHUNK_BYTES):
                    pass
        except ObjectBackupError:
            raise
        except Exception as exc:
            raise ObjectBackupError(
                "BACKUP_OBJECT_PART_FORMAT_INVALID",
                "a portable part could not be decrypted or decompressed",
            ) from exc


class S3ObjectRestoreAdapter:
    """Restore exact inventory records into an isolated versioned target prefix."""

    def __init__(
        self,
        source_client: S3VersionClient,
        target_client: S3ObjectRestoreClient,
        *,
        source_bucket: str,
        source_bucket_reference: str,
        target_bucket: str,
        target_bucket_reference: str,
        target_prefix: str,
        restore_plan_id: str,
    ) -> None:
        values = (
            source_bucket,
            source_bucket_reference,
            target_bucket,
            target_bucket_reference,
            restore_plan_id,
        )
        if any(
            not value or any(char in value for char in ("\x00", "\r", "\n")) or len(value) > 255
            for value in values
        ):
            raise ValueError("object restore identity is invalid")
        normalized_prefix = target_prefix.strip("/")
        if (
            not normalized_prefix
            or len(normalized_prefix) > 512
            or len(normalized_prefix.encode("utf-8")) > 1024
            or any(char in normalized_prefix for char in ("\x00", "\r", "\n"))
        ):
            raise ValueError("object restore target prefix is invalid")
        self._source = source_client
        self._target = target_client
        self._source_bucket = source_bucket
        self._source_bucket_reference = source_bucket_reference
        self._target_bucket = target_bucket
        self._target_bucket_reference = target_bucket_reference
        self._target_prefix = normalized_prefix
        self._restore_plan_id = restore_plan_id

    def restore(
        self,
        artifact: ObjectBackupArtifact,
        *,
        source_prefix: str,
        staging_directory: Path,
        decryptor: AgeDecryptedReader | None = None,
    ) -> ObjectRestoreReport:
        staging = _secure_staging(staging_directory)
        if (
            len(source_prefix) > 512
            or len(source_prefix.encode("utf-8")) > 1024
            or any(char in source_prefix for char in ("\x00", "\r", "\n"))
        ):
            raise ObjectBackupError(
                "RESTORE_OBJECT_PREFIX_INVALID", "the signed source prefix is invalid"
            )
        try:
            versioning = self._target.get_bucket_versioning(Bucket=self._target_bucket)
        except Exception as exc:
            raise ObjectBackupError(
                "RESTORE_OBJECT_TARGET_UNAVAILABLE",
                "the restore target versioning state is unavailable",
            ) from exc
        if versioning.get("Status") != "Enabled":
            raise ObjectBackupError(
                "RESTORE_OBJECT_VERSIONING_REQUIRED",
                "the restore target must keep versioning enabled",
            )

        verifier = ObjectBackupVerifier()
        records = verifier.inventory_records(
            artifact,
            decryptor=decryptor,
            expected_bucket_reference=self._source_bucket_reference,
            expected_prefix=source_prefix,
        )
        parts = {part.logical_path: part for part in artifact.parts}
        restore_directory = _private_directory(staging, "restore-objects")
        target_versions: list[dict[str, object]] = []
        resumed = 0
        for record in records:
            target_key = self._target_key(record.object_key, source_prefix=source_prefix)
            existing_version = self._verify_target_optional(target_key, record)
            if existing_version is not None:
                resumed += 1
                target_versions.append(
                    {
                        "key": target_key,
                        "sha256": record.sha256,
                        "size_bytes": record.size_bytes,
                        "version_id": existing_version,
                    }
                )
                continue
            restored_file = self._materialize_record(
                record,
                artifact=artifact,
                parts=parts,
                verifier=verifier,
                restore_directory=restore_directory,
                decryptor=decryptor,
            )
            try:
                version_id = self._put_target(target_key, record, restored_file)
                verified_version = self._verify_target_required(
                    target_key,
                    record,
                    version_id=version_id,
                )
            finally:
                with suppress(FileNotFoundError):
                    restored_file.unlink()
            target_versions.append(
                {
                    "key": target_key,
                    "sha256": record.sha256,
                    "size_bytes": record.size_bytes,
                    "version_id": verified_version,
                }
            )
        _fsync_directory(restore_directory)
        return ObjectRestoreReport(
            target_bucket_reference=self._target_bucket_reference,
            target_prefix=self._target_prefix,
            object_count=len(records),
            total_bytes=sum(item.size_bytes for item in records),
            resumed_object_count=resumed,
            source_content_sha256=_records_content_sha256(records),
            target_version_set_sha256=hashlib.sha256(
                canonical_json_bytes(target_versions)
            ).hexdigest(),
        )

    def verify_restored(
        self,
        artifact: ObjectBackupArtifact,
        *,
        source_prefix: str,
        decryptor: AgeDecryptedReader | None = None,
    ) -> ObjectRestoreReport:
        """Read every mapped target version and compare it with signed inventory.

        This method has no create, copy, or overwrite path.  It is the object
        half of RST3-03 reconciliation after the independently checkpointed
        restore has reached ``READ_ONLY_READY``.
        """

        if (
            len(source_prefix) > 512
            or len(source_prefix.encode("utf-8")) > 1024
            or any(char in source_prefix for char in ("\x00", "\r", "\n"))
        ):
            raise ObjectBackupError(
                "RESTORE_OBJECT_PREFIX_INVALID", "the signed source prefix is invalid"
            )
        try:
            versioning = self._target.get_bucket_versioning(Bucket=self._target_bucket)
        except Exception as exc:
            raise ObjectBackupError(
                "RESTORE_OBJECT_TARGET_UNAVAILABLE",
                "the restore target versioning state is unavailable",
            ) from exc
        if versioning.get("Status") != "Enabled":
            raise ObjectBackupError(
                "RESTORE_OBJECT_VERSIONING_REQUIRED",
                "the restore target must keep versioning enabled",
            )
        records = ObjectBackupVerifier().inventory_records(
            artifact,
            decryptor=decryptor,
            expected_bucket_reference=self._source_bucket_reference,
            expected_prefix=source_prefix,
        )
        target_versions = []
        for record in records:
            target_key = self._target_key(record.object_key, source_prefix=source_prefix)
            version_id = self._verify_target_required(target_key, record, version_id=None)
            target_versions.append(
                {
                    "key": target_key,
                    "sha256": record.sha256,
                    "size_bytes": record.size_bytes,
                    "version_id": version_id,
                }
            )
        return ObjectRestoreReport(
            target_bucket_reference=self._target_bucket_reference,
            target_prefix=self._target_prefix,
            object_count=len(records),
            total_bytes=sum(item.size_bytes for item in records),
            resumed_object_count=len(records),
            source_content_sha256=_records_content_sha256(records),
            target_version_set_sha256=hashlib.sha256(
                canonical_json_bytes(target_versions)
            ).hexdigest(),
        )

    def verify_external(
        self,
        artifact: ObjectBackupArtifact,
        *,
        source_prefix: str,
        decryptor: AgeDecryptedReader | None = None,
    ) -> ObjectRestoreReport:
        """Stream-verify exact source versions when restore reuses external objects."""

        normalized_source = source_prefix.strip("/")
        if (
            not normalized_source
            or len(normalized_source) > 512
            or len(normalized_source.encode("utf-8")) > 1024
            or any(char in normalized_source for char in ("\x00", "\r", "\n"))
        ):
            raise ObjectBackupError(
                "RESTORE_OBJECT_PREFIX_INVALID", "the signed source prefix is invalid"
            )
        if (
            self._target_bucket_reference != self._source_bucket_reference
            or self._target_prefix != normalized_source
        ):
            raise ObjectBackupError(
                "RESTORE_OBJECT_TARGET_CONFLICT",
                "external reuse must retain the exact signed bucket and prefix",
            )
        try:
            versioning = self._source.get_bucket_versioning(Bucket=self._source_bucket)
        except Exception as exc:
            raise ObjectBackupError(
                "RESTORE_OBJECT_SOURCE_READ_FAILED",
                "the external source versioning state is unavailable",
            ) from exc
        if versioning.get("Status") != "Enabled":
            raise ObjectBackupError(
                "RESTORE_OBJECT_VERSIONING_REQUIRED",
                "the reused external bucket must keep versioning enabled",
            )
        records = ObjectBackupVerifier().inventory_records(
            artifact,
            decryptor=decryptor,
            expected_bucket_reference=self._source_bucket_reference,
            expected_prefix=source_prefix,
        )
        versions: list[dict[str, object]] = []
        for record in records:
            self._verify_external_record(record)
            versions.append(
                {
                    "key": record.object_key,
                    "sha256": record.sha256,
                    "size_bytes": record.size_bytes,
                    "version_id": record.version_id,
                }
            )
        return ObjectRestoreReport(
            target_bucket_reference=self._source_bucket_reference,
            target_prefix=normalized_source,
            object_count=len(records),
            total_bytes=sum(item.size_bytes for item in records),
            resumed_object_count=len(records),
            source_content_sha256=_records_content_sha256(records),
            target_version_set_sha256=hashlib.sha256(canonical_json_bytes(versions)).hexdigest(),
        )

    def _verify_external_record(self, record: ObjectInventoryRecord) -> None:
        try:
            response = self._source.get_object(
                Bucket=self._source_bucket,
                Key=record.object_key,
                VersionId=record.version_id,
            )
        except Exception as exc:
            raise ObjectBackupError(
                "RESTORE_OBJECT_SOURCE_READ_FAILED",
                "an exact external object version could not be read",
            ) from exc
        body = response.get("Body")
        try:
            etag = response.get("ETag")
            normalized_etag = _normalize_etag(etag) if isinstance(etag, str) else None
        except ValueError:
            normalized_etag = None
        if (
            response.get("VersionId") != record.version_id
            or response.get("ContentLength") != record.size_bytes
            or normalized_etag != record.etag
            or body is None
            or not callable(getattr(body, "read", None))
        ):
            close = getattr(body, "close", None)
            if callable(close):
                close()
            raise ObjectBackupError(
                "RESTORE_OBJECT_SOURCE_RESPONSE_INVALID",
                "an exact external object-version response is malformed",
            )
        digest = hashlib.sha256()
        count = 0
        try:
            while chunk := body.read(_STREAM_CHUNK_BYTES):
                if not isinstance(chunk, bytes):
                    raise ObjectBackupError(
                        "RESTORE_OBJECT_SOURCE_RESPONSE_INVALID",
                        "an exact external object returned non-byte content",
                    )
                count += len(chunk)
                if count > record.size_bytes:
                    raise ObjectBackupError(
                        "RESTORE_OBJECT_SOURCE_RESPONSE_INVALID",
                        "an exact external object exceeded its signed size",
                    )
                digest.update(chunk)
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
        if count != record.size_bytes or digest.hexdigest() != record.sha256:
            raise ObjectBackupError(
                "RESTORE_OBJECT_CONTENT_MISMATCH",
                "the reused external object differs from signed inventory bytes",
            )

    def _target_key(self, source_key: str, *, source_prefix: str) -> str:
        normalized_source = source_prefix.rstrip("/")
        if normalized_source:
            required = normalized_source + "/"
            if not source_key.startswith(required):
                raise ObjectBackupError(
                    "RESTORE_OBJECT_SOURCE_PREFIX_MISMATCH",
                    "an inventory object is outside the signed source prefix",
                )
            relative = source_key[len(required) :]
        else:
            relative = source_key
        if not relative:
            raise ObjectBackupError(
                "RESTORE_OBJECT_SOURCE_PREFIX_MISMATCH",
                "an inventory object has no target-relative key",
            )
        target_key = f"{self._target_prefix}/{relative}"
        if len(target_key.encode("utf-8")) > 1024:
            raise ObjectBackupError(
                "RESTORE_OBJECT_TARGET_KEY_INVALID", "a mapped target key is oversized"
            )
        return target_key

    def _materialize_record(
        self,
        record: ObjectInventoryRecord,
        *,
        artifact: ObjectBackupArtifact,
        parts: Mapping[str, PortableObjectPartArtifact],
        verifier: ObjectBackupVerifier,
        restore_directory: Path,
        decryptor: AgeDecryptedReader | None,
    ) -> Path:
        destination = restore_directory / f"object-{record.ordinal:08d}.bin"
        if destination.exists() or destination.is_symlink():
            size, digest = _sha256_file(destination)
            info = destination.stat()
            if (
                destination.is_symlink()
                or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077
                or size != record.size_bytes
                or digest != record.sha256
            ):
                raise ObjectBackupError(
                    "RESTORE_OBJECT_STAGING_MISMATCH",
                    "a resumed object staging file differs from inventory",
                )
            return destination
        filesystem = os.statvfs(restore_directory)
        available = filesystem.f_bavail * filesystem.f_frsize
        required = (record.size_bytes * 130 + 99) // 100
        if available < required:
            raise ObjectBackupError(
                "RESTORE_OBJECT_STAGING_CAPACITY_INSUFFICIENT",
                "object reconstruction lacks thirty-percent staging headroom",
            )
        temporary = restore_directory / f".{destination.name}.{os.getpid()}.partial"
        if temporary.exists() or temporary.is_symlink():
            raise ObjectBackupError(
                "RESTORE_OBJECT_STAGING_CONFLICT",
                "an object reconstruction is already active",
            )
        try:
            with _private_file_writer(temporary) as output:
                if artifact.inventory.mode == "snapshot":
                    self._read_snapshot_record(record, output)
                else:
                    if decryptor is None:
                        raise ObjectBackupError(
                            "BACKUP_OBJECT_AGE_IDENTITY_REQUIRED",
                            "portable object restore requires an age identity",
                        )
                    for segment in record.segments:
                        part = parts.get(segment.part_path)
                        if part is None:
                            raise ObjectBackupError(
                                "BACKUP_OBJECT_INVENTORY_PART_MISMATCH",
                                "a portable restore part is missing",
                            )
                        with verifier._part_payload_reader(part, decryptor) as payload:
                            while chunk := payload.read(_STREAM_CHUNK_BYTES):
                                output.write(chunk)
            size, digest = _sha256_file(temporary)
            if size != record.size_bytes or digest != record.sha256:
                raise ObjectBackupError(
                    "RESTORE_OBJECT_CONTENT_MISMATCH",
                    "reconstructed object bytes differ from signed inventory",
                )
            temporary.replace(destination)
            _fsync_directory(restore_directory)
            return destination
        except BaseException:
            with suppress(FileNotFoundError):
                temporary.unlink()
            raise

    def _read_snapshot_record(self, record: ObjectInventoryRecord, output: BinaryIO) -> None:
        try:
            response = self._source.get_object(
                Bucket=self._source_bucket,
                Key=record.object_key,
                VersionId=record.version_id,
            )
        except Exception as exc:
            raise ObjectBackupError(
                "RESTORE_OBJECT_SOURCE_READ_FAILED",
                "an exact source object version could not be read",
            ) from exc
        body = response.get("Body")
        try:
            etag = response.get("ETag")
            normalized_etag = _normalize_etag(etag) if isinstance(etag, str) else None
        except ValueError:
            normalized_etag = None
        if (
            response.get("VersionId") != record.version_id
            or response.get("ContentLength") != record.size_bytes
            or normalized_etag != record.etag
            or body is None
            or not callable(getattr(body, "read", None))
        ):
            close = getattr(body, "close", None)
            if callable(close):
                close()
            raise ObjectBackupError(
                "RESTORE_OBJECT_SOURCE_RESPONSE_INVALID",
                "an exact source object-version response is malformed",
            )
        count = 0
        try:
            while chunk := body.read(_STREAM_CHUNK_BYTES):
                if not isinstance(chunk, bytes):
                    raise ObjectBackupError(
                        "RESTORE_OBJECT_SOURCE_RESPONSE_INVALID",
                        "an exact source object returned non-byte content",
                    )
                count += len(chunk)
                if count > record.size_bytes:
                    raise ObjectBackupError(
                        "RESTORE_OBJECT_SOURCE_RESPONSE_INVALID",
                        "an exact source object exceeded its signed size",
                    )
                output.write(chunk)
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
        if count != record.size_bytes:
            raise ObjectBackupError(
                "RESTORE_OBJECT_SOURCE_RESPONSE_INVALID",
                "an exact source object was truncated",
            )

    def _put_target(
        self,
        target_key: str,
        record: ObjectInventoryRecord,
        source: Path,
    ) -> str:
        checksum = base64.b64encode(bytes.fromhex(record.sha256)).decode("ascii")
        try:
            with source.open("rb") as stream:
                response = self._target.put_object(
                    Bucket=self._target_bucket,
                    Key=target_key,
                    Body=stream,
                    ContentLength=record.size_bytes,
                    ChecksumSHA256=checksum,
                    IfNoneMatch="*",
                    Metadata={
                        "hc-restore-plan": self._restore_plan_id,
                        "hc-source-sha256": record.sha256,
                        "hc-source-version-sha256": _sha_text(record.version_id),
                    },
                )
        except Exception as exc:
            existing = self._verify_target_optional(target_key, record)
            if existing is not None:
                return existing
            raise ObjectBackupError(
                "RESTORE_OBJECT_TARGET_WRITE_FAILED",
                "a target object could not be created without overwrite",
            ) from exc
        version_id = response.get("VersionId")
        if not isinstance(version_id, str) or version_id == "null" or not version_id:
            raise ObjectBackupError(
                "RESTORE_OBJECT_TARGET_RESPONSE_INVALID",
                "the target did not return an immutable object version",
            )
        return version_id

    def _verify_target_optional(
        self,
        target_key: str,
        record: ObjectInventoryRecord,
    ) -> str | None:
        try:
            return self._verify_target_required(target_key, record, version_id=None)
        except ObjectBackupError as exc:
            if exc.code == "RESTORE_OBJECT_TARGET_MISSING":
                return None
            raise

    def _verify_target_required(
        self,
        target_key: str,
        record: ObjectInventoryRecord,
        *,
        version_id: str | None,
    ) -> str:
        request: dict[str, object] = {"Bucket": self._target_bucket, "Key": target_key}
        if version_id is not None:
            request["VersionId"] = version_id
        try:
            head = self._target.head_object(**request)
        except Exception as exc:
            if _restore_not_found(exc):
                raise ObjectBackupError(
                    "RESTORE_OBJECT_TARGET_MISSING", "the target object is absent"
                ) from exc
            raise ObjectBackupError(
                "RESTORE_OBJECT_TARGET_READ_FAILED",
                "target object evidence could not be read",
            ) from exc
        observed_version = head.get("VersionId")
        raw_metadata = head.get("Metadata")
        metadata = (
            {str(key).lower(): str(value) for key, value in raw_metadata.items()}
            if isinstance(raw_metadata, Mapping)
            else {}
        )
        if (
            not isinstance(observed_version, str)
            or not observed_version
            or observed_version == "null"
            or (version_id is not None and observed_version != version_id)
            or head.get("ContentLength") != record.size_bytes
            or metadata.get("hc-restore-plan") != self._restore_plan_id
            or metadata.get("hc-source-sha256") != record.sha256
            or metadata.get("hc-source-version-sha256") != _sha_text(record.version_id)
        ):
            raise ObjectBackupError(
                "RESTORE_OBJECT_TARGET_CONFLICT",
                "an existing target object differs from this exact restore",
            )
        try:
            response = self._target.get_object(
                Bucket=self._target_bucket,
                Key=target_key,
                VersionId=observed_version,
            )
            body = response.get("Body")
            if (
                response.get("VersionId") != observed_version
                or response.get("ContentLength") != record.size_bytes
                or body is None
                or not callable(getattr(body, "read", None))
            ):
                raise ObjectBackupError(
                    "RESTORE_OBJECT_TARGET_RESPONSE_INVALID",
                    "the target object-version response is malformed",
                )
            digest = hashlib.sha256()
            count = 0
            try:
                while chunk := body.read(_STREAM_CHUNK_BYTES):
                    if not isinstance(chunk, bytes):
                        raise ObjectBackupError(
                            "RESTORE_OBJECT_TARGET_RESPONSE_INVALID",
                            "the target object returned non-byte content",
                        )
                    count += len(chunk)
                    digest.update(chunk)
            finally:
                close = getattr(body, "close", None)
                if callable(close):
                    close()
        except ObjectBackupError:
            raise
        except Exception as exc:
            raise ObjectBackupError(
                "RESTORE_OBJECT_TARGET_READ_FAILED",
                "the target object version could not be verified",
            ) from exc
        if count != record.size_bytes or digest.hexdigest() != record.sha256:
            raise ObjectBackupError(
                "RESTORE_OBJECT_TARGET_CONFLICT",
                "an existing target object differs from signed inventory bytes",
            )
        return observed_version


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _restore_not_found(error: BaseException) -> bool:
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return isinstance(error, (FileNotFoundError, KeyError))
    details = response.get("Error")
    return isinstance(details, Mapping) and str(details.get("Code")) in {
        "404",
        "NoSuchKey",
        "NotFound",
    }


@contextmanager
def _binary_file_reader(path: Path) -> Iterator[BinaryIO]:
    with path.open("rb") as stream:
        yield stream
