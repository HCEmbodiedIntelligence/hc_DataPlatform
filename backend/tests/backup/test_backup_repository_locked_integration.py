from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest

from hc_data_platform.backup.repository import (
    BackupRepositoryError,
    LockedRepositoryConfig,
    RepositoryUploadSource,
    S3LockedBackupRepository,
)


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is required")
    return value


def _source(path: Path, *, logical_path: str) -> RepositoryUploadSource:
    payload = path.read_bytes()
    return RepositoryUploadSource(
        logical_path=logical_path,
        path=path,
        media_type="application/octet-stream",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )


@pytest.mark.integration
def test_real_sse_kms_locked_repository_multipart_and_cross_site_replication(
    tmp_path: Path,
) -> None:
    assert _required("HC_BACKUP_LOCKED_REPOSITORY_ALLOW_MUTATION") == "disposable-only"
    endpoint = _required("HC_BACKUP_LOCKED_REPOSITORY_ENDPOINT")
    replica_endpoint = _required("HC_BACKUP_LOCKED_REPOSITORY_REPLICA_ENDPOINT")
    bucket = _required("HC_BACKUP_LOCKED_REPOSITORY_BUCKET")
    access_key = _required("HC_BACKUP_LOCKED_REPOSITORY_ACCESS_KEY")
    secret_key = _required("HC_BACKUP_LOCKED_REPOSITORY_SECRET_KEY")
    kms_key_id = _required("HC_BACKUP_LOCKED_REPOSITORY_KMS_KEY_ID")
    observed_kms_key_id = _required("HC_BACKUP_LOCKED_REPOSITORY_KMS_OBSERVED_KEY_ID")
    replica_destination = _required("HC_BACKUP_LOCKED_REPOSITORY_REPLICA_DESTINATION")
    prefix = _required("HC_BACKUP_LOCKED_REPOSITORY_PREFIX")
    boto3 = pytest.importorskip("boto3")
    source_client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
    )
    replica_client = boto3.client(
        "s3",
        endpoint_url=replica_endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
    )
    retain_until = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0)
    repository = S3LockedBackupRepository(
        source_client,
        LockedRepositoryConfig(
            repository_id="bak206-real-repository",
            provider="s3_compatible",
            bucket=bucket,
            bucket_reference="bak206-logical-bucket",
            prefix=prefix,
            kms_key_reference="kms://bak206/repository/versions/v1",
            provider_kms_key_id=kms_key_id,
            provider_kms_observed_key_id=observed_kms_key_id,
            provider_replica_destination=replica_destination,
            retention_until=retain_until,
            cross_site_replica_reference="bak206-cross-site-replica",
            multipart_threshold_bytes=5 * 1024 * 1024,
            multipart_part_bytes=5 * 1024 * 1024,
            send_small_object_checksum=False,
        ),
    )
    repository.preflight()
    backup_id = f"bak206-real-locked-{int(time.time())}"
    small_path = tmp_path / "small.bin"
    small_path.write_bytes(b"real-sse-kms-object-lock-proof")
    small_path.chmod(0o600)
    large_path = tmp_path / "large.bin"
    large_path.write_bytes(b"m" * (5 * 1024 * 1024) + b"multipart-tail")
    large_path.chmod(0o600)
    small = _source(small_path, logical_path="checksums.sha256")
    large = _source(
        large_path,
        logical_path="objects/parts/part-000001.tar.zst.age",
    )

    small_created = repository.upload_file(backup_id, small)
    large_created = repository.upload_file(
        backup_id,
        large,
        checkpoint_path=tmp_path / "multipart-checkpoint.json",
    )
    small_resumed = repository.upload_file(backup_id, small)
    large_resumed = repository.upload_file(
        backup_id,
        large,
        checkpoint_path=tmp_path / "multipart-checkpoint.json",
    )

    assert small_created.resumed is False and large_created.resumed is False
    assert small_resumed.resumed is True and large_resumed.resumed is True
    assert small_created.version_id == small_resumed.version_id
    assert large_created.version_id == large_resumed.version_id
    destination = tmp_path / "download" / "large.bin"
    repository.download_file(
        backup_id,
        large.logical_path,
        destination=destination,
        expected_size=large.size_bytes,
        expected_sha256=large.sha256,
    )
    assert destination.read_bytes() == large_path.read_bytes()
    assert destination.stat().st_mode & 0o077 == 0

    replicated_key = f"{prefix}/{backup_id}/{large.logical_path}"
    replica_head: dict[str, object] | None = None
    for _ in range(60):
        try:
            replica_head = cast(
                dict[str, object],
                replica_client.head_object(Bucket=bucket, Key=replicated_key),
            )
            break
        except Exception:
            time.sleep(0.25)
    assert replica_head is not None
    assert replica_head["ServerSideEncryption"] == "aws:kms"
    assert replica_head["SSEKMSKeyId"] == observed_kms_key_id
    replicated = replica_client.get_object(Bucket=bucket, Key=replicated_key)
    assert replicated["Body"].read() == large_path.read_bytes()
    replicated["Body"].close()

    small_key = f"{prefix}/{backup_id}/{small.logical_path}"
    source_client.put_object(
        Bucket=bucket,
        Key=small_key,
        Body=b"tampered-latest-version",
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=kms_key_id,
        ObjectLockMode="COMPLIANCE",
        ObjectLockRetainUntilDate=retain_until,
        Metadata={"hc-sha256": small.sha256},
    )
    tampered_destination = tmp_path / "download" / "tampered.bin"
    with pytest.raises(BackupRepositoryError) as tampered:
        repository.download_file(
            backup_id,
            small.logical_path,
            destination=tampered_destination,
            expected_size=small.size_bytes,
            expected_sha256=small.sha256,
        )
    assert tampered.value.code in {
        "BACKUP_REPOSITORY_OBJECT_POLICY_INVALID",
        "BACKUP_REPOSITORY_ARTIFACT_MISMATCH",
    }
    assert not tampered_destination.exists()
