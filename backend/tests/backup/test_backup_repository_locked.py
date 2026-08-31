from __future__ import annotations

import base64
import hashlib
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, cast

import pytest

from hc_data_platform.backup.repository import (
    BackupRepositoryError,
    LockedRepositoryConfig,
    RepositoryUploadSource,
    S3LockedBackupRepository,
)
from hc_data_platform.backup.whole import assemble_whole_backup, prepare_postgresql_artifact
from tests.backup.test_backup_whole import _evidence, _fixtures

NOW = datetime(2026, 8, 29, tzinfo=timezone.utc)
RETAIN_UNTIL = NOW + timedelta(days=365)


class _LockedS3Fixture:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, dict[str, Any]]] = {}
        self.uploads: dict[str, dict[str, Any]] = {}
        self.parts: dict[str, dict[int, tuple[bytes, str, str]]] = {}
        self.next_upload = 1
        self.next_version = 1
        self.fail_part_once: int | None = None
        self.put_calls = 0
        self.upload_part_calls = 0
        self.get_object_keys: list[str] = []

    def get_bucket_versioning(self, **_: object) -> dict[str, object]:
        return {"Status": "Enabled"}

    def get_object_lock_configuration(self, **_: object) -> dict[str, object]:
        return {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}}

    def get_bucket_replication(self, **_: object) -> dict[str, object]:
        return {
            "ReplicationConfiguration": {
                "Rules": [
                    {
                        "Status": "Enabled",
                        "Destination": {"Bucket": "provider-replica-destination"},
                    }
                ]
            }
        }

    def head_object(self, **kwargs: object) -> dict[str, Any]:
        key = str(kwargs["Key"])
        if key not in self.objects:
            raise KeyError(key)
        _payload, metadata = self.objects[key]
        return dict(metadata)

    def put_object(self, **kwargs: object) -> dict[str, object]:
        self.put_calls += 1
        key = str(kwargs["Key"])
        if key in self.objects:
            raise RuntimeError("conditional write failed")
        body = cast(BinaryIO, kwargs["Body"])
        payload = body.read()
        assert isinstance(payload, bytes)
        self._store(key, payload, kwargs)
        return {"VersionId": self.objects[key][1]["VersionId"]}

    def create_multipart_upload(self, **kwargs: object) -> dict[str, object]:
        upload_id = f"upload-{self.next_upload}"
        self.next_upload += 1
        self.uploads[upload_id] = dict(kwargs)
        self.parts[upload_id] = {}
        return {"UploadId": upload_id}

    def list_parts(self, **kwargs: object) -> dict[str, object]:
        upload_id = str(kwargs["UploadId"])
        parts = [
            {
                "PartNumber": number,
                "ETag": etag,
                "Size": len(payload),
                "ChecksumSHA256": checksum,
            }
            for number, (payload, etag, checksum) in sorted(self.parts[upload_id].items())
        ]
        return {"Parts": parts, "IsTruncated": False}

    def upload_part(self, **kwargs: object) -> dict[str, object]:
        self.upload_part_calls += 1
        number = int(str(kwargs["PartNumber"]))
        if self.fail_part_once == number:
            self.fail_part_once = None
            raise RuntimeError("injected interruption")
        upload_id = str(kwargs["UploadId"])
        payload = kwargs["Body"]
        assert isinstance(payload, bytes)
        checksum = str(kwargs["ChecksumSHA256"])
        etag = f'"etag-{number}"'
        self.parts[upload_id][number] = (payload, etag, checksum)
        return {"ETag": etag, "ChecksumSHA256": checksum}

    def complete_multipart_upload(self, **kwargs: object) -> dict[str, object]:
        upload_id = str(kwargs["UploadId"])
        created = self.uploads[upload_id]
        key = str(created["Key"])
        payload = b"".join(item[0] for _, item in sorted(self.parts[upload_id].items()))
        self._store(key, payload, created)
        return {"VersionId": self.objects[key][1]["VersionId"]}

    def abort_multipart_upload(self, **_: object) -> dict[str, object]:
        return {}

    def get_object(self, **kwargs: object) -> dict[str, Any]:
        key = str(kwargs["Key"])
        self.get_object_keys.append(key)
        if key not in self.objects:
            raise KeyError(key)
        payload, metadata = self.objects[key]
        return {**metadata, "Body": io.BytesIO(payload)}

    def _store(self, key: str, payload: bytes, request: dict[str, Any]) -> None:
        version = f"version-{self.next_version}"
        self.next_version += 1
        self.objects[key] = (
            payload,
            {
                "ContentLength": len(payload),
                "Metadata": request["Metadata"],
                "VersionId": version,
                "ServerSideEncryption": request["ServerSideEncryption"],
                "SSEKMSKeyId": request["SSEKMSKeyId"],
                "ObjectLockMode": request["ObjectLockMode"],
                "ObjectLockRetainUntilDate": request["ObjectLockRetainUntilDate"],
            },
        )


def _config(*, multipart_threshold_bytes: int = 64 * 1024 * 1024) -> LockedRepositoryConfig:
    return LockedRepositoryConfig(
        repository_id="backup-repository-cn",
        provider="s3_compatible",
        bucket="physical-bucket-private",
        bucket_reference="backup-bucket-logical-cn",
        kms_key_reference="kms://security/repository/versions/v7",
        provider_kms_key_id="provider-kms-key-id-must-never-be-signed",
        provider_kms_observed_key_id="provider-kms-key-id-must-never-be-signed",
        provider_replica_destination="provider-replica-destination",
        retention_until=RETAIN_UNTIL,
        cross_site_replica_reference="backup-replica-cn-west",
        multipart_threshold_bytes=multipart_threshold_bytes,
        multipart_part_bytes=5 * 1024 * 1024,
    )


def _source(path: Path, *, logical_path: str = "checksums.sha256") -> RepositoryUploadSource:
    payload = path.read_bytes()
    return RepositoryUploadSource(
        logical_path=logical_path,
        path=path,
        media_type="application/json",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _private_file(path: Path, payload: bytes) -> Path:
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


def test_locked_repository_small_upload_is_kms_locked_and_idempotent(tmp_path: Path) -> None:
    client = _LockedS3Fixture()
    repository = S3LockedBackupRepository(client, _config(), clock=lambda: NOW)
    source = _source(_private_file(tmp_path / "checksums.json", b'{"verified":true}\n'))

    created = repository.upload_file("backup-20260829-001", source)
    resumed = repository.upload_file("backup-20260829-001", source)

    assert created.resumed is False
    assert resumed.resumed is True
    assert created.version_id == resumed.version_id
    assert client.put_calls == 1
    key = "whole-platform-backups/backup-20260829-001/checksums.sha256"
    payload, metadata = client.objects[key]
    assert payload == b'{"verified":true}\n'
    assert metadata["ServerSideEncryption"] == "aws:kms"
    assert metadata["ObjectLockMode"] == "COMPLIANCE"
    assert metadata["Metadata"]["hc-sha256"] == source.sha256
    assert "provider-kms-key-id" not in resumed.model_dump_json()


def test_locked_repository_multipart_resumes_only_matching_parts(tmp_path: Path) -> None:
    part_bytes = 5 * 1024 * 1024
    payload = b"a" * part_bytes + b"tail"
    source = _source(
        _private_file(tmp_path / "large.bin", payload),
        logical_path="objects/parts/part-000001.tar.zst.age",
    )
    client = _LockedS3Fixture()
    client.fail_part_once = 2
    repository = S3LockedBackupRepository(
        client,
        _config(multipart_threshold_bytes=part_bytes),
        clock=lambda: NOW,
    )
    checkpoint = tmp_path / "repository-upload.checkpoint.json"

    with pytest.raises(BackupRepositoryError) as interrupted:
        repository.upload_file("backup-20260829-002", source, checkpoint_path=checkpoint)
    assert interrupted.value.code == "BACKUP_REPOSITORY_WRITE_FAILED"
    assert checkpoint.stat().st_mode & 0o077 == 0
    assert client.upload_part_calls == 2

    completed = repository.upload_file("backup-20260829-002", source, checkpoint_path=checkpoint)

    assert completed.resumed is False
    assert client.upload_part_calls == 3
    key = "whole-platform-backups/backup-20260829-002/objects/parts/part-000001.tar.zst.age"
    assert client.objects[key][0] == payload
    assert b'"uploads":[]' in checkpoint.read_bytes()


def test_locked_repository_rejects_tampered_kms_or_body_without_leaving_file(
    tmp_path: Path,
) -> None:
    client = _LockedS3Fixture()
    repository = S3LockedBackupRepository(client, _config(), clock=lambda: NOW)
    source = _source(_private_file(tmp_path / "source.json", b'{"verified":true}\n'))
    repository.upload_file("backup-20260829-003", source)
    key = "whole-platform-backups/backup-20260829-003/checksums.sha256"

    payload, metadata = client.objects[key]
    client.objects[key] = (payload, {**metadata, "SSEKMSKeyId": "wrong-key"})
    destination = tmp_path / "download" / "checksums.sha256"
    with pytest.raises(BackupRepositoryError) as kms_error:
        repository.download_file(
            "backup-20260829-003",
            source.logical_path,
            destination=destination,
            expected_size=source.size_bytes,
            expected_sha256=source.sha256,
        )
    assert kms_error.value.code == "BACKUP_REPOSITORY_OBJECT_POLICY_INVALID"
    assert not destination.exists()

    client.objects[key] = (b"x" + payload[1:], metadata)
    with pytest.raises(BackupRepositoryError) as hash_error:
        repository.download_file(
            "backup-20260829-003",
            source.logical_path,
            destination=destination,
            expected_size=source.size_bytes,
            expected_sha256=source.sha256,
        )
    assert hash_error.value.code == "BACKUP_REPOSITORY_ARTIFACT_MISMATCH"
    assert not destination.exists()


def test_locked_repository_validates_provider_checksum_input(tmp_path: Path) -> None:
    client = _LockedS3Fixture()
    repository = S3LockedBackupRepository(client, _config(), clock=lambda: NOW)
    source = _source(_private_file(tmp_path / "source.json", b"proof"))

    repository.upload_file("backup-20260829-004", source)

    key = "whole-platform-backups/backup-20260829-004/checksums.sha256"
    assert base64.b64encode(bytes.fromhex(source.sha256)).decode("ascii")
    assert client.objects[key][1]["Metadata"] == {"hc-sha256": source.sha256}


def test_restore_payload_resumes_only_owner_only_files_matching_signed_hashes(
    tmp_path: Path,
) -> None:
    source_staging = tmp_path / "source"
    source_staging.mkdir(mode=0o700)
    plan, postgres, objects, configuration, temporal, signer = _fixtures(source_staging)
    prepared = prepare_postgresql_artifact(plan, postgres, staging_directory=source_staging)
    assembly = assemble_whole_backup(
        plan,
        postgresql_receipt=postgres,
        postgresql_upload=prepared,
        object_receipt=objects,
        configuration_receipt=configuration,
        temporal_receipt=temporal,
        verification=_evidence(plan),  # type: ignore[arg-type]
        signer=signer,
        staging_directory=source_staging,
    )
    client = _LockedS3Fixture()
    repository = S3LockedBackupRepository(
        client,
        LockedRepositoryConfig(
            repository_id=plan.backup_repository.repository_id,
            provider=plan.backup_repository.provider,
            bucket="physical-bucket-private",
            bucket_reference=plan.backup_repository.bucket_reference,
            kms_key_reference=plan.backup_repository.kms_key_reference,
            provider_kms_key_id="provider-kms-key-id-must-never-be-signed",
            provider_kms_observed_key_id="provider-kms-key-id-must-never-be-signed",
            provider_replica_destination="provider-replica-destination",
            retention_until=plan.backup_repository.retention_until,
            cross_site_replica_reference=(plan.backup_repository.cross_site_replica_reference),
        ),
        clock=lambda: NOW,
    )
    for source in assembly.upload_sources:
        repository.upload_file(plan.backup_id, source)

    restore_staging = tmp_path / "restore"
    restore_staging.mkdir(mode=0o700)
    public_keys = {assembly.manifest.signature.public_key_sha256: signer.public_key}
    first = repository.restore_backup_payload(
        plan.backup_id,
        public_keys_by_sha256=public_keys,
        staging_directory=restore_staging,
    )
    calls_after_first = len(client.get_object_keys)

    second = repository.restore_backup_payload(
        plan.backup_id,
        public_keys_by_sha256=public_keys,
        staging_directory=restore_staging,
    )

    assert second.artifact_paths == first.artifact_paths
    assert len(client.get_object_keys) == calls_after_first + 2
    resumed_path = second.artifact_paths[assembly.manifest.artifacts[0].path]
    original = resumed_path.read_bytes()
    resumed_path.write_bytes(b"x" + original[1:])
    calls_before_tamper = len(client.get_object_keys)

    with pytest.raises(BackupRepositoryError) as captured:
        repository.restore_backup_payload(
            plan.backup_id,
            public_keys_by_sha256=public_keys,
            staging_directory=restore_staging,
        )

    assert captured.value.code == "BACKUP_REPOSITORY_ARTIFACT_MISMATCH"
    assert len(client.get_object_keys) == calls_before_tamper + 2
    assert resumed_path.read_bytes() != original
