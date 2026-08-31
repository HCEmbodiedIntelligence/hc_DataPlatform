"""Independent S3 repository reader for rebuilding the PostgreSQL backup catalog."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from hc_data_platform.backup.contracts import (
    MAX_MANIFEST_BYTES,
    MAX_SIGNATURE_ENVELOPE_BYTES,
    ArtifactPath,
    KmsKeyReference,
    Sha256,
    VerifiedManifestV1,
    canonical_json_bytes,
    parse_signature_envelope,
    verify_signed_manifest,
)

_IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
_PREFIX = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,255}$")
_MEDIA_TYPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]*/[A-Za-z0-9][A-Za-z0-9.+_-]{0,126}$")
_MAX_CHECKPOINT_BYTES = 16 * 1024 * 1024


class BackupRepositoryError(ValueError):
    """Stable repository error that never exposes bucket, key, or vendor detail."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class RepositorySignedManifestArtifacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repository_id: str = Field(pattern=_IDENTIFIER.pattern)
    backup_id: str = Field(pattern=_IDENTIFIER.pattern)
    manifest_bytes: bytes = Field(max_length=MAX_MANIFEST_BYTES)
    signature_bytes: bytes = Field(max_length=MAX_SIGNATURE_ENVELOPE_BYTES)
    manifest_version_id: str = Field(min_length=1, max_length=1024)
    signature_version_id: str = Field(min_length=1, max_length=1024)


class BackupManifestRepository(Protocol):
    @property
    def repository_id(self) -> str: ...

    def list_signed_manifests(self) -> tuple[RepositorySignedManifestArtifacts, ...]: ...


class S3Client(Protocol):
    def get_bucket_versioning(self, **kwargs: object) -> Mapping[str, Any]: ...

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, Any]: ...

    def get_object(self, **kwargs: object) -> Mapping[str, Any]: ...


class S3BackupManifestRepository:
    """Reads versioned, compliance-locked signed manifests from independent S3."""

    def __init__(
        self,
        client: S3Client,
        *,
        bucket: str,
        repository_id: str,
        prefix: str = "whole-platform-backups",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not _IDENTIFIER.fullmatch(repository_id):
            raise ValueError("repository_id is invalid")
        normalized_prefix = prefix.strip("/")
        if not _PREFIX.fullmatch(normalized_prefix) or any(
            part in {"", ".", ".."} for part in normalized_prefix.split("/")
        ):
            raise ValueError("backup repository prefix is invalid")
        if not bucket or len(bucket) > 255:
            raise ValueError("backup repository bucket is invalid")
        self._client = client
        self._bucket = bucket
        self._repository_id = repository_id
        self._prefix = normalized_prefix
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def repository_id(self) -> str:
        return self._repository_id

    def list_signed_manifests(self) -> tuple[RepositorySignedManifestArtifacts, ...]:
        self._assert_versioning_enabled()
        pairs = self._discover_artifact_pairs()
        return tuple(self._read_pair(backup_id) for backup_id in sorted(pairs))

    def _assert_versioning_enabled(self) -> None:
        try:
            response = self._client.get_bucket_versioning(Bucket=self._bucket)
        except Exception as exc:
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_READ_FAILED",
                "the backup repository versioning state could not be read",
            ) from exc
        if response.get("Status") != "Enabled":
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_VERSIONING_REQUIRED",
                "the backup repository must have versioning enabled",
            )

    def _discover_artifact_pairs(self) -> set[str]:
        continuation_token: str | None = None
        manifests: set[str] = set()
        signatures: set[str] = set()
        expected_prefix = f"{self._prefix}/"
        while True:
            request: dict[str, object] = {
                "Bucket": self._bucket,
                "Prefix": expected_prefix,
                "MaxKeys": 1000,
            }
            if continuation_token is not None:
                request["ContinuationToken"] = continuation_token
            try:
                response = self._client.list_objects_v2(**request)
            except Exception as exc:
                raise BackupRepositoryError(
                    "BACKUP_REPOSITORY_READ_FAILED",
                    "the signed manifest inventory could not be listed",
                ) from exc
            for item in response.get("Contents", []):
                if not isinstance(item, Mapping):
                    raise BackupRepositoryError(
                        "BACKUP_REPOSITORY_RESPONSE_INVALID",
                        "the signed manifest inventory response is malformed",
                    )
                key = item.get("Key")
                if not isinstance(key, str):
                    raise BackupRepositoryError(
                        "BACKUP_REPOSITORY_RESPONSE_INVALID",
                        "the signed manifest inventory response is malformed",
                    )
                relative = key.removeprefix(expected_prefix)
                parts = relative.split("/")
                if len(parts) != 2 or not _IDENTIFIER.fullmatch(parts[0]):
                    continue
                if parts[1] == "manifest.json":
                    manifests.add(parts[0])
                elif parts[1] == "signature.sig":
                    signatures.add(parts[0])
            if not bool(response.get("IsTruncated")):
                break
            next_token = response.get("NextContinuationToken")
            if not isinstance(next_token, str) or not next_token:
                raise BackupRepositoryError(
                    "BACKUP_REPOSITORY_RESPONSE_INVALID",
                    "the signed manifest inventory cursor is malformed",
                )
            continuation_token = next_token

        if manifests != signatures:
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_ARTIFACT_PAIR_INCOMPLETE",
                "every catalog manifest must have one detached signature envelope",
            )
        return manifests

    def _read_pair(self, backup_id: str) -> RepositorySignedManifestArtifacts:
        manifest_bytes, manifest_version_id = self._read_locked_object(
            f"{self._prefix}/{backup_id}/manifest.json",
            maximum_bytes=MAX_MANIFEST_BYTES,
        )
        signature_bytes, signature_version_id = self._read_locked_object(
            f"{self._prefix}/{backup_id}/signature.sig",
            maximum_bytes=MAX_SIGNATURE_ENVELOPE_BYTES,
        )
        return RepositorySignedManifestArtifacts(
            repository_id=self._repository_id,
            backup_id=backup_id,
            manifest_bytes=manifest_bytes,
            signature_bytes=signature_bytes,
            manifest_version_id=manifest_version_id,
            signature_version_id=signature_version_id,
        )

    def _read_locked_object(self, key: str, *, maximum_bytes: int) -> tuple[bytes, str]:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            size = response.get("ContentLength")
            version_id = response.get("VersionId")
            lock_mode = response.get("ObjectLockMode")
            retain_until = response.get("ObjectLockRetainUntilDate")
            body = response.get("Body")
            if (
                not isinstance(size, int)
                or size < 0
                or size > maximum_bytes
                or not isinstance(version_id, str)
                or not version_id
                or version_id == "null"
                or lock_mode != "COMPLIANCE"
                or not isinstance(retain_until, datetime)
                or retain_until <= self._clock()
                or body is None
                or not callable(getattr(body, "read", None))
            ):
                close = getattr(body, "close", None)
                if callable(close):
                    close()
                raise BackupRepositoryError(
                    "BACKUP_REPOSITORY_OBJECT_POLICY_INVALID",
                    "a signed manifest artifact violates version, lock, or size policy",
                )
            try:
                payload = body.read(maximum_bytes + 1)
            finally:
                close = getattr(body, "close", None)
                if callable(close):
                    close()
        except BackupRepositoryError:
            raise
        except Exception as exc:
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_READ_FAILED",
                "a signed manifest artifact could not be read",
            ) from exc
        if not isinstance(payload, bytes) or len(payload) != size:
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_RESPONSE_INVALID",
                "a signed manifest artifact body did not match its bounded metadata",
            )
        return payload, version_id


class LockedRepositoryConfig(BaseModel):
    """Logical manifest policy plus adapter-private S3/KMS mapping."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repository_id: str = Field(pattern=_IDENTIFIER.pattern)
    provider: str = Field(pattern=r"^(s3_compatible|aws_s3|aliyun_oss)$")
    bucket: str = Field(min_length=1, max_length=255)
    bucket_reference: str = Field(min_length=1, max_length=255)
    prefix: str = Field(default="whole-platform-backups", pattern=_PREFIX.pattern)
    kms_key_reference: KmsKeyReference
    provider_kms_key_id: SecretStr
    provider_kms_observed_key_id: SecretStr
    provider_replica_destination: SecretStr
    retention_until: datetime
    cross_site_replica_reference: str = Field(min_length=1, max_length=255)
    multipart_threshold_bytes: int = Field(default=64 * 1024 * 1024, ge=5 * 1024 * 1024)
    multipart_part_bytes: int = Field(
        default=64 * 1024 * 1024,
        ge=5 * 1024 * 1024,
        le=5 * 1024 * 1024 * 1024,
    )
    send_small_object_checksum: bool = True

    @model_validator(mode="after")
    def require_safe_policy(self) -> LockedRepositoryConfig:
        normalized_prefix = self.prefix.strip("/")
        if normalized_prefix != self.prefix or any(
            part in {"", ".", ".."} for part in normalized_prefix.split("/")
        ):
            raise ValueError("backup repository prefix is not normalized")
        if self.retention_until.utcoffset() != timezone.utc.utcoffset(self.retention_until):
            raise ValueError("backup repository retention must be UTC")
        if self.retention_until.microsecond != 0:
            raise ValueError("backup repository retention must use whole-second precision")
        provider_key = self.provider_kms_key_id.get_secret_value()
        observed_provider_key = self.provider_kms_observed_key_id.get_secret_value()
        replica_destination = self.provider_replica_destination.get_secret_value()
        if (
            not provider_key
            or any(char in provider_key for char in "\x00\r\n")
            or not observed_provider_key
            or any(char in observed_provider_key for char in "\x00\r\n")
            or not replica_destination
            or any(char in replica_destination for char in "\x00\r\n")
        ):
            raise ValueError("provider KMS key identifier is invalid")
        return self


class RepositoryUploadSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    logical_path: ArtifactPath
    path: Path
    media_type: str = Field(min_length=1, max_length=127)
    size_bytes: int = Field(ge=0)
    sha256: Sha256

    @model_validator(mode="after")
    def require_media_type(self) -> RepositoryUploadSource:
        if _MEDIA_TYPE.fullmatch(self.media_type) is None:
            raise ValueError("repository upload media type is invalid")
        return self


class RepositoryStoredObject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    logical_path: ArtifactPath
    version_id: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=0)
    sha256: Sha256
    resumed: bool


class RepositoryMultipartState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    logical_path: ArtifactPath
    upload_id: str = Field(min_length=1, max_length=2048)
    size_bytes: int = Field(ge=0)
    sha256: Sha256


class RepositoryUploadCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: str = Field(pattern=r"^hc-backup-repository-upload-checkpoint/v1$")
    repository_id: str = Field(pattern=_IDENTIFIER.pattern)
    backup_id: str = Field(pattern=_IDENTIFIER.pattern)
    uploads: tuple[RepositoryMultipartState, ...] = ()

    @model_validator(mode="after")
    def require_unique_uploads(self) -> RepositoryUploadCheckpoint:
        paths = [item.logical_path for item in self.uploads]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("repository multipart checkpoint paths must be sorted and unique")
        return self


@dataclass(frozen=True)
class RepositoryVerifiedManifest:
    verified_manifest: VerifiedManifestV1
    manifest_version_id: str
    signature_version_id: str


@dataclass(frozen=True)
class RepositoryVerifiedBackup:
    verified_manifest: VerifiedManifestV1
    artifact_paths: Mapping[str, Path]
    manifest_version_id: str
    signature_version_id: str


class S3WholeBackupClient(Protocol):
    def get_bucket_versioning(self, **kwargs: object) -> Mapping[str, Any]: ...

    def get_object_lock_configuration(self, **kwargs: object) -> Mapping[str, Any]: ...

    def get_bucket_replication(self, **kwargs: object) -> Mapping[str, Any]: ...

    def head_object(self, **kwargs: object) -> Mapping[str, Any]: ...

    def put_object(self, **kwargs: object) -> Mapping[str, Any]: ...

    def create_multipart_upload(self, **kwargs: object) -> Mapping[str, Any]: ...

    def list_parts(self, **kwargs: object) -> Mapping[str, Any]: ...

    def upload_part(self, **kwargs: object) -> Mapping[str, Any]: ...

    def complete_multipart_upload(self, **kwargs: object) -> Mapping[str, Any]: ...

    def abort_multipart_upload(self, **kwargs: object) -> Mapping[str, Any]: ...

    def get_object(self, **kwargs: object) -> Mapping[str, Any]: ...


class S3LockedBackupRepository:
    """Idempotent whole-backup payload writer and independent strict reader."""

    def __init__(
        self,
        client: S3WholeBackupClient,
        config: LockedRepositoryConfig,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self.config = config
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def preflight(self) -> None:
        if self.config.retention_until <= self._clock():
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_RETENTION_INVALID",
                "the backup repository retention is not in the future",
            )
        try:
            versioning = self._client.get_bucket_versioning(Bucket=self.config.bucket)
            lock = self._client.get_object_lock_configuration(Bucket=self.config.bucket)
            replication = self._client.get_bucket_replication(Bucket=self.config.bucket)
        except Exception as exc:
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_PREFLIGHT_FAILED",
                "the backup repository policy could not be read",
            ) from exc
        lock_config = lock.get("ObjectLockConfiguration")
        if versioning.get("Status") != "Enabled":
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_VERSIONING_REQUIRED",
                "the backup repository must have versioning enabled",
            )
        if (
            not isinstance(lock_config, Mapping)
            or lock_config.get("ObjectLockEnabled") != "Enabled"
        ):
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_OBJECT_LOCK_REQUIRED",
                "the backup repository must have object lock enabled",
            )
        replication_config = replication.get("ReplicationConfiguration")
        rules = (
            replication_config.get("Rules", []) if isinstance(replication_config, Mapping) else []
        )
        expected_destination = self.config.provider_replica_destination.get_secret_value()
        if not any(
            isinstance(rule, Mapping)
            and rule.get("Status") == "Enabled"
            and isinstance(rule.get("Destination"), Mapping)
            and rule["Destination"].get("Bucket") == expected_destination
            for rule in rules
        ):
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_REPLICATION_REQUIRED",
                "the backup repository must have its exact cross-site replication enabled",
            )

    def upload_file(
        self,
        backup_id: str,
        source: RepositoryUploadSource,
        *,
        checkpoint_path: Path | None = None,
    ) -> RepositoryStoredObject:
        self._validate_identity(backup_id)
        self.preflight()
        resolved = _verify_upload_source(source)
        key = self._key(backup_id, source.logical_path)
        existing = self._head_optional(key)
        if existing is not None:
            version_id = self._validate_head(existing, source)
            return RepositoryStoredObject(
                logical_path=source.logical_path,
                version_id=version_id,
                size_bytes=source.size_bytes,
                sha256=source.sha256,
                resumed=True,
            )
        if source.size_bytes < self.config.multipart_threshold_bytes:
            version_id = self._put_small(key, source, resolved)
        else:
            if checkpoint_path is None:
                raise BackupRepositoryError(
                    "BACKUP_REPOSITORY_CHECKPOINT_REQUIRED",
                    "multipart repository upload requires an owner-only checkpoint",
                )
            version_id = self._put_multipart(
                backup_id,
                key,
                source,
                resolved,
                checkpoint_path=checkpoint_path,
            )
        return RepositoryStoredObject(
            logical_path=source.logical_path,
            version_id=version_id,
            size_bytes=source.size_bytes,
            sha256=source.sha256,
            resumed=False,
        )

    def verify_backup(
        self,
        backup_id: str,
        *,
        public_keys_by_sha256: Mapping[str, Ed25519PublicKey],
        staging_directory: Path,
    ) -> RepositoryVerifiedBackup:
        return self._verify_backup_payload(
            backup_id,
            public_keys_by_sha256=public_keys_by_sha256,
            staging_directory=staging_directory,
            resume_verified_files=False,
        )

    def restore_backup_payload(
        self,
        backup_id: str,
        *,
        public_keys_by_sha256: Mapping[str, Ed25519PublicKey],
        staging_directory: Path,
    ) -> RepositoryVerifiedBackup:
        """Download or re-verify an exact signed payload for resumable restore."""

        return self._verify_backup_payload(
            backup_id,
            public_keys_by_sha256=public_keys_by_sha256,
            staging_directory=staging_directory,
            resume_verified_files=True,
        )

    def _verify_backup_payload(
        self,
        backup_id: str,
        *,
        public_keys_by_sha256: Mapping[str, Ed25519PublicKey],
        staging_directory: Path,
        resume_verified_files: bool,
    ) -> RepositoryVerifiedBackup:
        authenticated = self.verify_manifest(
            backup_id,
            public_keys_by_sha256=public_keys_by_sha256,
        )
        staging = _secure_directory(staging_directory)
        verified = authenticated.verified_manifest
        manifest = verified.manifest
        artifact_paths: dict[str, Path] = {}
        for artifact in manifest.artifacts:
            destination = _artifact_destination(staging, artifact.path)
            if resume_verified_files and (destination.exists() or destination.is_symlink()):
                _verify_restored_payload_file(
                    destination,
                    expected_size=artifact.size_bytes,
                    expected_sha256=artifact.sha256,
                )
            else:
                self.download_file(
                    backup_id,
                    artifact.path,
                    destination=destination,
                    expected_size=artifact.size_bytes,
                    expected_sha256=artifact.sha256,
                )
            artifact_paths[artifact.path] = destination
        return RepositoryVerifiedBackup(
            verified_manifest=verified,
            artifact_paths=artifact_paths,
            manifest_version_id=authenticated.manifest_version_id,
            signature_version_id=authenticated.signature_version_id,
        )

    def verify_manifest(
        self,
        backup_id: str,
        *,
        public_keys_by_sha256: Mapping[str, Ed25519PublicKey],
    ) -> RepositoryVerifiedManifest:
        """Authenticate repository metadata without downloading backup payloads."""

        self._validate_identity(backup_id)
        self.preflight()
        manifest_bytes, manifest_version = self._read_bounded_bytes(
            backup_id,
            "manifest.json",
            maximum_bytes=MAX_MANIFEST_BYTES,
        )
        signature_bytes, signature_version = self._read_bounded_bytes(
            backup_id,
            "signature.sig",
            maximum_bytes=MAX_SIGNATURE_ENVELOPE_BYTES,
        )
        envelope = parse_signature_envelope(signature_bytes)
        public_key = public_keys_by_sha256.get(envelope.public_key_sha256)
        if public_key is None:
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_SIGNER_UNTRUSTED",
                "the repository manifest signer is not pinned",
            )
        verified = verify_signed_manifest(
            manifest_bytes,
            signature_bytes,
            public_key=public_key,
        )
        manifest = verified.manifest
        if (
            manifest.backup_id != backup_id
            or manifest.backup_repository.repository_id != self.config.repository_id
            or manifest.backup_repository.provider != self.config.provider
            or manifest.backup_repository.bucket_reference != self.config.bucket_reference
            or manifest.backup_repository.kms_key_reference != self.config.kms_key_reference
            or manifest.backup_repository.retention_until != self.config.retention_until
            or manifest.backup_repository.cross_site_replica_reference
            != self.config.cross_site_replica_reference
        ):
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_POLICY_MISMATCH",
                "the signed manifest does not bind the configured repository policy",
            )
        return RepositoryVerifiedManifest(
            verified_manifest=verified,
            manifest_version_id=manifest_version,
            signature_version_id=signature_version,
        )

    def download_file(
        self,
        backup_id: str,
        logical_path: str,
        *,
        destination: Path,
        expected_size: int,
        expected_sha256: str,
    ) -> str:
        self._validate_identity(backup_id)
        key = self._key(backup_id, logical_path)
        if destination.exists() or destination.is_symlink():
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_DOWNLOAD_EXISTS",
                "a repository download destination already exists",
            )
        _private_parent_directories(destination)
        try:
            response = self._client.get_object(Bucket=self.config.bucket, Key=key)
            version_id = self._validate_read_metadata(
                response,
                expected_size=expected_size,
                expected_sha256=expected_sha256,
            )
            body = response.get("Body")
            if body is None or not callable(getattr(body, "read", None)):
                raise BackupRepositoryError(
                    "BACKUP_REPOSITORY_RESPONSE_INVALID",
                    "a repository object body is unavailable",
                )
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            digest = hashlib.sha256()
            written = 0
            try:
                with os.fdopen(descriptor, "wb", buffering=0) as stream:
                    while True:
                        chunk = body.read(1024 * 1024)
                        if not isinstance(chunk, bytes):
                            raise BackupRepositoryError(
                                "BACKUP_REPOSITORY_RESPONSE_INVALID",
                                "a repository object returned non-byte content",
                            )
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > expected_size:
                            raise BackupRepositoryError(
                                "BACKUP_REPOSITORY_ARTIFACT_MISMATCH",
                                "a repository artifact exceeded its signed size",
                            )
                        digest.update(chunk)
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                close = getattr(body, "close", None)
                if callable(close):
                    close()
        except BackupRepositoryError:
            with suppress(FileNotFoundError):
                destination.unlink()
            raise
        except Exception as exc:
            with suppress(FileNotFoundError):
                destination.unlink()
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_READ_FAILED",
                "a repository artifact could not be read",
            ) from exc
        if written != expected_size or digest.hexdigest() != expected_sha256:
            with suppress(FileNotFoundError):
                destination.unlink()
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_ARTIFACT_MISMATCH",
                "a repository artifact differs from its signed size or hash",
            )
        return version_id

    def _read_bounded_bytes(
        self,
        backup_id: str,
        logical_path: str,
        *,
        maximum_bytes: int,
    ) -> tuple[bytes, str]:
        key = self._key(backup_id, logical_path)
        try:
            response = self._client.get_object(Bucket=self.config.bucket, Key=key)
            size = response.get("ContentLength")
            metadata = _normalized_metadata(response.get("Metadata"))
            expected_sha256 = metadata.get("hc-sha256")
            if (
                not isinstance(size, int)
                or size < 1
                or size > maximum_bytes
                or expected_sha256 is None
            ):
                raise BackupRepositoryError(
                    "BACKUP_REPOSITORY_OBJECT_POLICY_INVALID",
                    "a signed repository artifact violates its bounded policy",
                )
            version_id = self._validate_read_metadata(
                response,
                expected_size=size,
                expected_sha256=expected_sha256,
            )
            body = response.get("Body")
            if body is None or not callable(getattr(body, "read", None)):
                raise BackupRepositoryError(
                    "BACKUP_REPOSITORY_RESPONSE_INVALID",
                    "a signed repository artifact body is unavailable",
                )
            try:
                payload = body.read(maximum_bytes + 1)
            finally:
                close = getattr(body, "close", None)
                if callable(close):
                    close()
        except BackupRepositoryError:
            raise
        except Exception as exc:
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_READ_FAILED",
                "a signed repository artifact could not be read",
            ) from exc
        if (
            not isinstance(payload, bytes)
            or len(payload) != size
            or hashlib.sha256(payload).hexdigest() != expected_sha256
        ):
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_ARTIFACT_MISMATCH",
                "a signed repository artifact differs from its protected metadata",
            )
        return payload, version_id

    def _put_small(self, key: str, source: RepositoryUploadSource, resolved: Path) -> str:
        checksum = base64.b64encode(bytes.fromhex(source.sha256)).decode("ascii")
        request: dict[str, object] = {
            "Bucket": self.config.bucket,
            "Key": key,
            "ContentLength": source.size_bytes,
            "ContentType": source.media_type,
            "ServerSideEncryption": "aws:kms",
            "SSEKMSKeyId": self.config.provider_kms_key_id.get_secret_value(),
            "ObjectLockMode": "COMPLIANCE",
            "ObjectLockRetainUntilDate": self.config.retention_until,
            "Metadata": {"hc-sha256": source.sha256},
            "IfNoneMatch": "*",
        }
        if self.config.send_small_object_checksum:
            request["ChecksumSHA256"] = checksum
        try:
            with resolved.open("rb") as stream:
                self._client.put_object(Body=stream, **request)
        except Exception as exc:
            existing = self._head_optional(key)
            if existing is None:
                raise BackupRepositoryError(
                    "BACKUP_REPOSITORY_WRITE_FAILED",
                    "a repository artifact could not be created",
                ) from exc
            return self._validate_head(existing, source)
        head = self._head_required(key)
        return self._validate_head(head, source)

    def _put_multipart(
        self,
        backup_id: str,
        key: str,
        source: RepositoryUploadSource,
        resolved: Path,
        *,
        checkpoint_path: Path,
    ) -> str:
        checkpoint = _load_upload_checkpoint(
            checkpoint_path,
            repository_id=self.config.repository_id,
            backup_id=backup_id,
        )
        state = next(
            (item for item in checkpoint.uploads if item.logical_path == source.logical_path),
            None,
        )
        if state is not None and (state.size_bytes, state.sha256) != (
            source.size_bytes,
            source.sha256,
        ):
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_CHECKPOINT_MISMATCH",
                "a multipart checkpoint is bound to different artifact bytes",
            )
        if state is None:
            try:
                response = self._client.create_multipart_upload(
                    Bucket=self.config.bucket,
                    Key=key,
                    ContentType=source.media_type,
                    ChecksumAlgorithm="SHA256",
                    ServerSideEncryption="aws:kms",
                    SSEKMSKeyId=self.config.provider_kms_key_id.get_secret_value(),
                    ObjectLockMode="COMPLIANCE",
                    ObjectLockRetainUntilDate=self.config.retention_until,
                    Metadata={"hc-sha256": source.sha256},
                )
                upload_id = response.get("UploadId")
            except Exception as exc:
                raise BackupRepositoryError(
                    "BACKUP_REPOSITORY_WRITE_FAILED",
                    "a multipart repository upload could not be created",
                ) from exc
            if not isinstance(upload_id, str) or not upload_id:
                raise BackupRepositoryError(
                    "BACKUP_REPOSITORY_RESPONSE_INVALID",
                    "the repository returned an invalid multipart identity",
                )
            state = RepositoryMultipartState(
                logical_path=source.logical_path,
                upload_id=upload_id,
                size_bytes=source.size_bytes,
                sha256=source.sha256,
            )
            checkpoint = checkpoint.model_copy(
                update={
                    "uploads": tuple(
                        sorted((*checkpoint.uploads, state), key=lambda item: item.logical_path)
                    )
                }
            )
            _write_upload_checkpoint(checkpoint_path, checkpoint)
        uploaded = self._list_parts(key, state.upload_id)
        completed: list[dict[str, object]] = []
        with resolved.open("rb") as stream:
            part_number = 1
            while chunk := stream.read(self.config.multipart_part_bytes):
                checksum = base64.b64encode(hashlib.sha256(chunk).digest()).decode("ascii")
                existing = uploaded.get(part_number)
                if existing is not None:
                    if existing[1] != len(chunk) or existing[2] != checksum:
                        raise BackupRepositoryError(
                            "BACKUP_REPOSITORY_MULTIPART_MISMATCH",
                            "a resumed multipart part differs from the local artifact",
                        )
                    etag = existing[0]
                else:
                    try:
                        response = self._client.upload_part(
                            Bucket=self.config.bucket,
                            Key=key,
                            UploadId=state.upload_id,
                            PartNumber=part_number,
                            Body=chunk,
                            ContentLength=len(chunk),
                            ChecksumSHA256=checksum,
                        )
                    except Exception as exc:
                        raise BackupRepositoryError(
                            "BACKUP_REPOSITORY_WRITE_FAILED",
                            "a multipart repository part could not be uploaded",
                        ) from exc
                    etag_value = response.get("ETag")
                    returned_checksum = response.get("ChecksumSHA256")
                    if (
                        not isinstance(etag_value, str)
                        or not etag_value
                        or returned_checksum != checksum
                    ):
                        raise BackupRepositoryError(
                            "BACKUP_REPOSITORY_RESPONSE_INVALID",
                            "the repository returned invalid multipart part evidence",
                        )
                    etag = etag_value
                completed.append(
                    {"ETag": etag, "PartNumber": part_number, "ChecksumSHA256": checksum}
                )
                part_number += 1
        try:
            self._client.complete_multipart_upload(
                Bucket=self.config.bucket,
                Key=key,
                UploadId=state.upload_id,
                MultipartUpload={"Parts": completed},
            )
        except Exception as exc:
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_WRITE_FAILED",
                "the multipart repository upload could not be completed",
            ) from exc
        checkpoint = checkpoint.model_copy(
            update={
                "uploads": tuple(
                    item for item in checkpoint.uploads if item.logical_path != source.logical_path
                )
            }
        )
        _write_upload_checkpoint(checkpoint_path, checkpoint)
        return self._validate_head(self._head_required(key), source)

    def _list_parts(self, key: str, upload_id: str) -> dict[int, tuple[str, int, str]]:
        marker: int | None = None
        result: dict[int, tuple[str, int, str]] = {}
        while True:
            request: dict[str, object] = {
                "Bucket": self.config.bucket,
                "Key": key,
                "UploadId": upload_id,
                "MaxParts": 1000,
            }
            if marker is not None:
                request["PartNumberMarker"] = marker
            try:
                response = self._client.list_parts(**request)
            except Exception as exc:
                raise BackupRepositoryError(
                    "BACKUP_REPOSITORY_CHECKPOINT_UNAVAILABLE",
                    "a resumed multipart upload could not be inspected",
                ) from exc
            for item in response.get("Parts", []):
                if not isinstance(item, Mapping):
                    raise BackupRepositoryError(
                        "BACKUP_REPOSITORY_RESPONSE_INVALID",
                        "the multipart part inventory is malformed",
                    )
                number = item.get("PartNumber")
                etag = item.get("ETag")
                size = item.get("Size")
                checksum = item.get("ChecksumSHA256")
                if (
                    not isinstance(number, int)
                    or number <= 0
                    or number in result
                    or not isinstance(etag, str)
                    or not etag
                    or not isinstance(size, int)
                    or size <= 0
                    or not isinstance(checksum, str)
                    or not checksum
                ):
                    raise BackupRepositoryError(
                        "BACKUP_REPOSITORY_RESPONSE_INVALID",
                        "the multipart part inventory is malformed",
                    )
                result[number] = (etag, size, checksum)
            if not bool(response.get("IsTruncated")):
                break
            next_marker = response.get("NextPartNumberMarker")
            if not isinstance(next_marker, int) or next_marker <= 0:
                raise BackupRepositoryError(
                    "BACKUP_REPOSITORY_RESPONSE_INVALID",
                    "the multipart part inventory cursor is malformed",
                )
            marker = next_marker
        if sorted(result) != list(range(1, len(result) + 1)):
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_MULTIPART_MISMATCH",
                "the resumed multipart upload has a non-contiguous part set",
            )
        return result

    def _head_optional(self, key: str) -> Mapping[str, Any] | None:
        try:
            return self._client.head_object(Bucket=self.config.bucket, Key=key)
        except Exception as exc:
            if _is_not_found(exc):
                return None
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_READ_FAILED",
                "a repository artifact identity could not be inspected",
            ) from exc

    def _head_required(self, key: str) -> Mapping[str, Any]:
        response = self._head_optional(key)
        if response is None:
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_ARTIFACT_MISSING",
                "a repository artifact is missing after upload",
            )
        return response

    def _validate_head(
        self,
        response: Mapping[str, Any],
        source: RepositoryUploadSource,
    ) -> str:
        return self._validate_read_metadata(
            response,
            expected_size=source.size_bytes,
            expected_sha256=source.sha256,
        )

    def _validate_read_metadata(
        self,
        response: Mapping[str, Any],
        *,
        expected_size: int,
        expected_sha256: str,
    ) -> str:
        metadata = _normalized_metadata(response.get("Metadata"))
        version_id = response.get("VersionId")
        retain_until = response.get("ObjectLockRetainUntilDate")
        provider_key_id = response.get("SSEKMSKeyId")
        if (
            response.get("ContentLength") != expected_size
            or metadata.get("hc-sha256") != expected_sha256
            or not isinstance(version_id, str)
            or not version_id
            or version_id == "null"
            or response.get("ServerSideEncryption") != "aws:kms"
            or provider_key_id != self.config.provider_kms_observed_key_id.get_secret_value()
            or response.get("ObjectLockMode") != "COMPLIANCE"
            or not isinstance(retain_until, datetime)
            or retain_until < self.config.retention_until
        ):
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_OBJECT_POLICY_INVALID",
                "a repository artifact violates its signed encryption, lock, or hash policy",
            )
        return version_id

    def _key(self, backup_id: str, logical_path: str) -> str:
        if any(part in {"", ".", ".."} for part in logical_path.split("/")):
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_PATH_INVALID", "a repository artifact path is invalid"
            )
        return f"{self.config.prefix}/{backup_id}/{logical_path}"

    @staticmethod
    def _validate_identity(backup_id: str) -> None:
        if _IDENTIFIER.fullmatch(backup_id) is None:
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_IDENTITY_INVALID", "the backup identity is invalid"
            )


class FunctionalFileRepositoryConfig(BaseModel):
    """Owner-only repository used solely by a disposable Compose exercise."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repository_id: str = Field(pattern=_IDENTIFIER.pattern)
    provider: str = Field(pattern=_IDENTIFIER.pattern)
    bucket_reference: str = Field(min_length=1, max_length=255)
    kms_key_reference: KmsKeyReference
    retention_until: datetime
    cross_site_replica_reference: str = Field(min_length=1, max_length=255)
    root_directory: Path
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{5,63}$")

    @model_validator(mode="after")
    def require_disposable_root(self) -> FunctionalFileRepositoryConfig:
        if f"hc-migration-{self.run_id}" not in self.root_directory.name:
            raise ValueError("functional repository directory is not bound to the run ID")
        return self


class FunctionalFileBackupRepository:
    """Fail-closed local repository for the non-production Compose drill.

    Production continues to use :class:`S3LockedBackupRepository`. This adapter
    deliberately requires an owner-only, run-ID-named directory and never
    claims object lock, KMS, replication, or production durability.
    """

    def __init__(self, config: FunctionalFileRepositoryConfig) -> None:
        self.config = config

    def preflight(self) -> None:
        root = _secure_directory(self.config.root_directory)
        if root.name != self.config.root_directory.name:
            raise BackupRepositoryError(
                "BACKUP_FUNCTIONAL_REPOSITORY_UNSAFE",
                "the functional repository resolves outside its exact run directory",
            )
        if self.config.retention_until <= datetime.now(timezone.utc):
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_RETENTION_INVALID",
                "the backup repository retention is not in the future",
            )

    def upload_file(
        self,
        backup_id: str,
        source: RepositoryUploadSource,
        *,
        checkpoint_path: Path | None = None,
    ) -> RepositoryStoredObject:
        del checkpoint_path
        self._validate_identity(backup_id)
        self.preflight()
        resolved = _verify_upload_source(source)
        destination = self._path(backup_id, source.logical_path, create_parent=True)
        if destination.exists() or destination.is_symlink():
            _verify_restored_payload_file(
                destination,
                expected_size=source.size_bytes,
                expected_sha256=source.sha256,
            )
            return self._stored(source, resumed=True)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with (
                resolved.open("rb") as input_stream,
                os.fdopen(descriptor, "wb", buffering=0) as output_stream,
            ):
                while chunk := input_stream.read(1024 * 1024):
                    output_stream.write(chunk)
                output_stream.flush()
                os.fsync(output_stream.fileno())
            _verify_restored_payload_file(
                destination,
                expected_size=source.size_bytes,
                expected_sha256=source.sha256,
            )
        except BaseException:
            with suppress(FileNotFoundError):
                destination.unlink()
            raise
        return self._stored(source, resumed=False)

    def verify_manifest(
        self,
        backup_id: str,
        *,
        public_keys_by_sha256: Mapping[str, Ed25519PublicKey],
    ) -> RepositoryVerifiedManifest:
        self._validate_identity(backup_id)
        self.preflight()
        manifest_bytes = self._read_bounded(backup_id, "manifest.json", MAX_MANIFEST_BYTES)
        signature_bytes = self._read_bounded(
            backup_id, "signature.sig", MAX_SIGNATURE_ENVELOPE_BYTES
        )
        envelope = parse_signature_envelope(signature_bytes)
        public_key = public_keys_by_sha256.get(envelope.public_key_sha256)
        if public_key is None:
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_SIGNER_UNTRUSTED",
                "the repository manifest signer is not pinned",
            )
        verified = verify_signed_manifest(manifest_bytes, signature_bytes, public_key=public_key)
        repository = verified.manifest.backup_repository
        if (
            verified.manifest.backup_id != backup_id
            or repository.repository_id != self.config.repository_id
            or repository.provider != self.config.provider
            or repository.bucket_reference != self.config.bucket_reference
            or repository.kms_key_reference != self.config.kms_key_reference
            or repository.retention_until != self.config.retention_until
            or repository.cross_site_replica_reference != self.config.cross_site_replica_reference
        ):
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_POLICY_MISMATCH",
                "the signed manifest does not bind the configured repository policy",
            )
        return RepositoryVerifiedManifest(
            verified_manifest=verified,
            manifest_version_id=self._version(manifest_bytes),
            signature_version_id=self._version(signature_bytes),
        )

    def verify_backup(
        self,
        backup_id: str,
        *,
        public_keys_by_sha256: Mapping[str, Ed25519PublicKey],
        staging_directory: Path,
    ) -> RepositoryVerifiedBackup:
        return self._verify_backup_payload(
            backup_id,
            public_keys_by_sha256=public_keys_by_sha256,
            staging_directory=staging_directory,
            resume=False,
        )

    def restore_backup_payload(
        self,
        backup_id: str,
        *,
        public_keys_by_sha256: Mapping[str, Ed25519PublicKey],
        staging_directory: Path,
    ) -> RepositoryVerifiedBackup:
        return self._verify_backup_payload(
            backup_id,
            public_keys_by_sha256=public_keys_by_sha256,
            staging_directory=staging_directory,
            resume=True,
        )

    def download_file(
        self,
        backup_id: str,
        logical_path: str,
        *,
        destination: Path,
        expected_size: int,
        expected_sha256: str,
    ) -> str:
        source = self._path(backup_id, logical_path, create_parent=False)
        _verify_restored_payload_file(
            source,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
        )
        if destination.exists() or destination.is_symlink():
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_DOWNLOAD_EXISTS",
                "a repository download destination already exists",
            )
        _private_parent_directories(destination)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with (
                source.open("rb") as input_stream,
                os.fdopen(descriptor, "wb", buffering=0) as output_stream,
            ):
                while chunk := input_stream.read(1024 * 1024):
                    output_stream.write(chunk)
                output_stream.flush()
                os.fsync(output_stream.fileno())
            _verify_restored_payload_file(
                destination,
                expected_size=expected_size,
                expected_sha256=expected_sha256,
            )
        except BaseException:
            with suppress(FileNotFoundError):
                destination.unlink()
            raise
        return self._version(source.read_bytes())

    def _verify_backup_payload(
        self,
        backup_id: str,
        *,
        public_keys_by_sha256: Mapping[str, Ed25519PublicKey],
        staging_directory: Path,
        resume: bool,
    ) -> RepositoryVerifiedBackup:
        authenticated = self.verify_manifest(backup_id, public_keys_by_sha256=public_keys_by_sha256)
        staging = _secure_directory(staging_directory)
        artifact_paths: dict[str, Path] = {}
        for artifact in authenticated.verified_manifest.manifest.artifacts:
            destination = _artifact_destination(staging, artifact.path)
            if resume and (destination.exists() or destination.is_symlink()):
                _verify_restored_payload_file(
                    destination,
                    expected_size=artifact.size_bytes,
                    expected_sha256=artifact.sha256,
                )
            else:
                self.download_file(
                    backup_id,
                    artifact.path,
                    destination=destination,
                    expected_size=artifact.size_bytes,
                    expected_sha256=artifact.sha256,
                )
            artifact_paths[artifact.path] = destination
        return RepositoryVerifiedBackup(
            verified_manifest=authenticated.verified_manifest,
            artifact_paths=artifact_paths,
            manifest_version_id=authenticated.manifest_version_id,
            signature_version_id=authenticated.signature_version_id,
        )

    def _read_bounded(self, backup_id: str, logical_path: str, maximum: int) -> bytes:
        path = self._path(backup_id, logical_path, create_parent=False)
        try:
            info = path.stat()
            if (
                path.is_symlink()
                or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077
                or not 0 < info.st_size <= maximum
            ):
                raise ValueError("file policy differs")
            payload = path.read_bytes()
        except Exception as exc:
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_READ_FAILED",
                "a signed repository artifact could not be read",
            ) from exc
        return payload

    def _path(self, backup_id: str, logical_path: str, *, create_parent: bool) -> Path:
        self._validate_identity(backup_id)
        if any(part in {"", ".", ".."} for part in logical_path.split("/")):
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_PATH_INVALID", "a repository artifact path is invalid"
            )
        root = _secure_directory(self.config.root_directory)
        backup = root / backup_id
        if create_parent:
            backup.mkdir(mode=0o700, exist_ok=True)
            _private_parent_directories(backup / "placeholder")
        elif not backup.is_dir():
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_READ_FAILED", "the requested backup is unavailable"
            )
        destination = backup.joinpath(*logical_path.split("/"))
        if create_parent:
            _private_parent_directories(destination)
        if destination.resolve(strict=False).parent != destination.parent.resolve(strict=False):
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_PATH_INVALID", "a repository artifact path escapes its backup"
            )
        return destination

    @staticmethod
    def _version(payload: bytes) -> str:
        return "functional-sha256-" + hashlib.sha256(payload).hexdigest()

    @classmethod
    def _stored(cls, source: RepositoryUploadSource, *, resumed: bool) -> RepositoryStoredObject:
        return RepositoryStoredObject(
            logical_path=source.logical_path,
            version_id="functional-sha256-" + source.sha256,
            size_bytes=source.size_bytes,
            sha256=source.sha256,
            resumed=resumed,
        )

    @staticmethod
    def _validate_identity(backup_id: str) -> None:
        if _IDENTIFIER.fullmatch(backup_id) is None:
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_IDENTITY_INVALID", "the backup identity is invalid"
            )


def _verify_upload_source(source: RepositoryUploadSource) -> Path:
    try:
        resolved = source.path.resolve(strict=True)
        info = resolved.stat()
    except (FileNotFoundError, OSError) as exc:
        raise BackupRepositoryError(
            "BACKUP_REPOSITORY_SOURCE_INVALID", "a repository upload source is unavailable"
        ) from exc
    if (
        source.path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) & 0o077
        or info.st_size != source.size_bytes
    ):
        raise BackupRepositoryError(
            "BACKUP_REPOSITORY_SOURCE_INVALID",
            "a repository upload source violates owner-only file policy",
        )
    digest = hashlib.sha256()
    with resolved.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != source.sha256:
        raise BackupRepositoryError(
            "BACKUP_REPOSITORY_SOURCE_MISMATCH",
            "a repository upload source differs from its receipt hash",
        )
    return resolved


def _normalized_metadata(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key).lower(): str(item) for key, item in value.items()}


def _is_not_found(error: BaseException) -> bool:
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return isinstance(error, (FileNotFoundError, KeyError))
    details = response.get("Error")
    if not isinstance(details, Mapping):
        return False
    return str(details.get("Code")) in {"404", "NoSuchKey", "NotFound"}


def _secure_directory(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except (FileNotFoundError, OSError) as exc:
        raise BackupRepositoryError(
            "BACKUP_REPOSITORY_STAGING_UNSAFE",
            "the repository verification staging directory is unavailable",
        ) from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise BackupRepositoryError(
            "BACKUP_REPOSITORY_STAGING_UNSAFE",
            "the repository verification staging directory is not owner-only",
        )
    return resolved


def _verify_restored_payload_file(
    path: Path,
    *,
    expected_size: int,
    expected_sha256: str,
) -> None:
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except (FileNotFoundError, OSError) as exc:
        raise BackupRepositoryError(
            "BACKUP_REPOSITORY_ARTIFACT_MISMATCH",
            "a resumed restore artifact is unavailable",
        ) from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
        or info.st_size != expected_size
    ):
        raise BackupRepositoryError(
            "BACKUP_REPOSITORY_ARTIFACT_MISMATCH",
            "a resumed restore artifact has unsafe or changed metadata",
        )
    digest = hashlib.sha256()
    with resolved.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise BackupRepositoryError(
            "BACKUP_REPOSITORY_ARTIFACT_MISMATCH",
            "a resumed restore artifact differs from its signed hash",
        )


def _private_parent_directories(path: Path) -> None:
    missing: list[Path] = []
    current = path.parent
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    info = path.parent.stat()
    if (
        path.parent.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise BackupRepositoryError(
            "BACKUP_REPOSITORY_STAGING_UNSAFE",
            "a repository artifact parent directory is not owner-only",
        )


def _artifact_destination(staging: Path, logical_path: str) -> Path:
    if any(part in {"", ".", ".."} for part in logical_path.split("/")):
        raise BackupRepositoryError(
            "BACKUP_REPOSITORY_PATH_INVALID", "a signed artifact path is invalid"
        )
    destination = staging.joinpath(*logical_path.split("/"))
    if destination.resolve(strict=False).parent != destination.parent.resolve(strict=False):
        raise BackupRepositoryError(
            "BACKUP_REPOSITORY_PATH_INVALID", "a signed artifact path escapes staging"
        )
    return destination


def _load_upload_checkpoint(
    path: Path,
    *,
    repository_id: str,
    backup_id: str,
) -> RepositoryUploadCheckpoint:
    if not path.exists():
        checkpoint = RepositoryUploadCheckpoint(
            format_version="hc-backup-repository-upload-checkpoint/v1",
            repository_id=repository_id,
            backup_id=backup_id,
        )
        _write_upload_checkpoint(path, checkpoint)
        return checkpoint
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_size > _MAX_CHECKPOINT_BYTES
        ):
            raise ValueError("checkpoint file policy differs")
        payload = resolved.read_bytes()
        value = json.loads(payload, object_pairs_hook=_unique_json_object)
        checkpoint = RepositoryUploadCheckpoint.model_validate(value)
    except BackupRepositoryError:
        raise
    except Exception as exc:
        raise BackupRepositoryError(
            "BACKUP_REPOSITORY_CHECKPOINT_INVALID",
            "the multipart upload checkpoint is invalid",
        ) from exc
    if checkpoint.repository_id != repository_id or checkpoint.backup_id != backup_id:
        raise BackupRepositoryError(
            "BACKUP_REPOSITORY_CHECKPOINT_MISMATCH",
            "the multipart upload checkpoint belongs to another backup",
        )
    return checkpoint


def _write_upload_checkpoint(path: Path, checkpoint: RepositoryUploadCheckpoint) -> None:
    _private_parent_directories(path)
    temporary = path.parent / f".{path.name}.{os.getpid()}.partial"
    with suppress(FileNotFoundError):
        temporary.unlink()
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb", buffering=0) as stream:
            stream.write(canonical_json_bytes(checkpoint.model_dump(mode="json")))
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except BaseException:
        with suppress(FileNotFoundError):
            temporary.unlink()
        raise


def _unique_json_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BackupRepositoryError(
                "BACKUP_REPOSITORY_CHECKPOINT_INVALID",
                "the multipart upload checkpoint has duplicate members",
            )
        result[key] = value
    return result
