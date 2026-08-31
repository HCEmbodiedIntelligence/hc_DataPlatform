from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from psycopg.conninfo import conninfo_to_dict

from hc_data_platform.backup.objects import (
    AgeCliDecryptor,
    AgeCliEncryptor,
    AgeToolchain,
    ObjectBackupError,
    ObjectBackupVerifier,
    S3ObjectBackupAdapter,
)
from hc_data_platform.backup.postgresql import (
    MaintenanceBackupLease,
    PostgresEndpoint,
    assert_current_backup_lease,
)
from hc_data_platform.core.dbapi import normalize_postgres_dsn


class _RecordingClient:
    def __init__(self, client: Any) -> None:
        self._client = client
        self.get_requests: list[dict[str, object]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def get_object(self, **kwargs: object) -> Any:
        self.get_requests.append(dict(kwargs))
        return self._client.get_object(**kwargs)


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is required")
    return value


def _endpoint(dsn: str) -> PostgresEndpoint:
    parsed = conninfo_to_dict(normalize_postgres_dsn(dsn))
    return PostgresEndpoint(
        host=parsed["host"],
        port=int(parsed["port"]),
        database=parsed["dbname"],
        username=parsed["user"],
        password=parsed["password"],
        sslmode="disable",
    )


@pytest.mark.integration
def test_real_minio_exact_versions_age_parts_and_postgres_fence() -> None:
    endpoint = _required("HC_BACKUP_OBJECT_TEST_ENDPOINT")
    bucket = _required("HC_BACKUP_OBJECT_TEST_BUCKET")
    access_key = _required("HC_BACKUP_OBJECT_TEST_ACCESS_KEY")
    secret_key = _required("HC_BACKUP_OBJECT_TEST_SECRET_KEY")
    staging_root = Path(_required("HC_BACKUP_OBJECT_TEST_STAGING_DIR"))
    age_binary = _required("HC_BACKUP_AGE_BIN")
    age_keygen = _required("HC_BACKUP_AGE_KEYGEN_BIN")
    postgres = _endpoint(_required("HC_BACKUP_OBJECT_TEST_POSTGRES_DSN"))
    lease = MaintenanceBackupLease(
        environment_id=_required("HC_BACKUP_POSTGRES_ENVIRONMENT_ID"),
        operation_id=_required("HC_BACKUP_POSTGRES_OPERATION_ID"),
        owner_instance_id=UUID(_required("HC_BACKUP_POSTGRES_OWNER_INSTANCE_ID")),
        fencing_token=int(_required("HC_BACKUP_POSTGRES_FENCING_TOKEN")),
    )
    boto3 = pytest.importorskip("boto3")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
    )
    assert client.get_bucket_versioning(Bucket=bucket).get("Status") == "Enabled"

    suffix = uuid4().hex
    prefix = f"bak203-real/{suffix}/"
    large_key = f"{prefix}large.bin"
    empty_key = f"{prefix}empty.bin"
    deleted_key = f"{prefix}deleted.bin"
    body = os.urandom(420_000)
    client.put_object(Bucket=bucket, Key=large_key, Body=b"superseded")
    current = client.put_object(Bucket=bucket, Key=large_key, Body=body)
    client.put_object(Bucket=bucket, Key=empty_key, Body=b"")
    deleted_version = client.put_object(Bucket=bucket, Key=deleted_key, Body=b"deleted")
    client.delete_object(Bucket=bucket, Key=deleted_key)
    staging = staging_root / suffix
    staging.mkdir(mode=0o700)
    snapshot_staging = staging / "snapshot"
    snapshot_staging.mkdir(mode=0o700)
    portable_staging = staging / "portable"
    portable_staging.mkdir(mode=0o700)
    identity = staging / "identity.txt"
    toolchain = AgeToolchain(age=(age_binary,), required_version="1.3.1")
    try:
        subprocess.run(
            [age_keygen, "-o", str(identity)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=30,
        )
        identity.chmod(0o600)
        recipient = (
            subprocess.run(
                [age_keygen, "-y", str(identity)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=30,
            )
            .stdout.decode("ascii")
            .strip()
        )
        recording_client = _RecordingClient(client)
        adapter = S3ObjectBackupAdapter(
            recording_client,
            bucket=bucket,
            bucket_reference="object-store:bak203-real",
            max_workers=3,
            retry_attempts=3,
        )

        stale = lease.model_copy(update={"fencing_token": lease.fencing_token + 1})
        with pytest.raises(ObjectBackupError) as fenced:
            adapter.create_snapshot(
                lease=stale,
                lease_verifier=lambda current_lease: assert_current_backup_lease(
                    postgres, current_lease
                ),
                staging_directory=snapshot_staging,
                prefix=prefix,
            )
        assert fenced.value.code == "BACKUP_OBJECT_FENCE_REJECTED"
        assert not (snapshot_staging / "objects" / "inventory.jsonl.zst").exists()

        snapshot = adapter.create_snapshot(
            lease=lease,
            lease_verifier=lambda current_lease: assert_current_backup_lease(
                postgres, current_lease
            ),
            staging_directory=snapshot_staging,
            prefix=prefix,
        )
        snapshot_report = ObjectBackupVerifier().verify(snapshot)
        assert snapshot_report.object_count == 2
        assert snapshot_report.total_bytes == len(body)
        assert str(current["VersionId"]) in {
            str(call["VersionId"]) for call in recording_client.get_requests
        }

        portable = adapter.create_portable(
            lease=lease,
            lease_verifier=lambda current_lease: assert_current_backup_lease(
                postgres, current_lease
            ),
            staging_directory=portable_staging,
            encryptor=AgeCliEncryptor(recipient, toolchain),
            age_recipient=recipient,
            prefix=prefix,
            max_part_bytes=256 * 1024,
        )
        report = ObjectBackupVerifier().verify(
            portable,
            decryptor=AgeCliDecryptor(identity, toolchain),
        )
        assert report.object_count == 2
        assert report.total_bytes == len(body)
        assert report.verified_part_count == len(portable.parts)
        assert len(portable.parts) == 5
        assert all(part.size_bytes <= 256 * 1024 for part in portable.parts)
        assert all(part.path.stat().st_mode & 0o077 == 0 for part in portable.parts)
        assert portable.inventory.path.read_bytes().startswith(b"age-encryption.org/v1")
        assert deleted_version["VersionId"] is not None
    finally:
        key_marker: str | None = None
        version_marker: str | None = None
        while True:
            request: dict[str, object] = {"Bucket": bucket, "Prefix": prefix}
            if key_marker is not None:
                request["KeyMarker"] = key_marker
            if version_marker is not None:
                request["VersionIdMarker"] = version_marker
            versions = client.list_object_versions(**request)
            for group in (versions.get("Versions", ()), versions.get("DeleteMarkers", ())):
                for item in group:
                    client.delete_object(
                        Bucket=bucket,
                        Key=item["Key"],
                        VersionId=item["VersionId"],
                    )
            if not versions.get("IsTruncated"):
                break
            key_marker = str(versions["NextKeyMarker"])
            version_marker = str(versions["NextVersionIdMarker"])
        shutil.rmtree(staging)
