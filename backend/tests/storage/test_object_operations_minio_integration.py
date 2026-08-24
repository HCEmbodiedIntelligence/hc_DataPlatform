from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen
from uuid import uuid4

import pytest

from hc_data_platform.security.auth import AuthContext, Role
from hc_data_platform.security.versioning import ResourceVersion
from hc_data_platform.storage.models import (
    BusinessCapacityCategory,
    LifecyclePolicyAction,
    ManagedMultipartUploadRecord,
    ManagedStorageObjectRecord,
    ObjectRole,
    RestoreStorageObjectRequest,
    StorageObjectStatus,
    StorageTier,
    TransitionStorageObjectRequest,
    TrashStorageObjectRequest,
)
from hc_data_platform.storage.object_store import S3StorageObjectOperator
from hc_data_platform.storage.repository import InMemoryStorageRepository
from hc_data_platform.storage.service import StorageGovernanceService


@pytest.mark.integration
def test_minio_download_recoverable_moves_and_multipart_abort() -> None:
    endpoint = os.environ.get("HC_MINIO_ENDPOINT")
    bucket = os.environ.get("HC_MINIO_BUCKET")
    access_key = os.environ.get("HC_MINIO_ACCESS_KEY")
    secret_key = os.environ.get("HC_MINIO_SECRET_KEY")
    if not all((endpoint, bucket, access_key, secret_key)):
        pytest.skip("HC_MINIO_ENDPOINT/BUCKET/ACCESS_KEY/SECRET_KEY are required")
    assert endpoint is not None
    assert bucket is not None
    assert access_key is not None
    assert secret_key is not None

    boto3 = pytest.importorskip("boto3")
    botocore_config = pytest.importorskip("botocore.config")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
        config=botocore_config.Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        ),
    )
    try:
        client.head_bucket(Bucket=bucket)
    except Exception:
        client.create_bucket(Bucket=bucket)

    suffix = uuid4().hex
    project_id = f"storage-minio-{suffix}"
    object_id = f"object-{suffix}"
    original_key = f"storage-tests/{suffix}/payload.bin"
    multipart_key = f"storage-tests/{suffix}/unfinished.mcap"
    body = (b"storage-governance-minio-proof-" + suffix.encode()) * 1024
    now = datetime(2026, 8, 21, 8, tzinfo=timezone.utc)
    actor = AuthContext.service(
        subject_id="storage-minio-manager",
        roles={Role.ADMIN},
        project_ids={project_id},
    )
    repository = InMemoryStorageRepository()
    operator = S3StorageObjectOperator(client, bucket)
    ids = iter(f"minio-operation-{index:03d}-{suffix}" for index in range(100))
    service = StorageGovernanceService(
        repository,
        object_operator=operator,
        clock=lambda: now,
        id_factory=lambda: next(ids),
        cursor_secret=f"storage-minio-{suffix}",
    )
    managed = ManagedStorageObjectRecord(
        object_id=object_id,
        project_id=project_id,
        display_key="datasets/minio-proof.bin",
        object_key=original_key,
        original_object_key=original_key,
        physical_bytes=str(len(body)),
        checksum_sha256=hashlib.sha256(body).hexdigest(),
        business_category=BusinessCapacityCategory.ANNOTATION_COMPLETE,
        object_role=ObjectRole.OTHER,
        storage_tier=StorageTier.HOT,
        status=StorageObjectStatus.ACTIVE,
        active_reference_count=0,
        version=1,
        etag=ResourceVersion(1).etag,
        created_at=now - timedelta(days=90),
        updated_at=now - timedelta(days=60),
    )
    tracked_keys = {original_key, multipart_key}
    provider_upload_id: str | None = None
    try:
        client.put_object(Bucket=bucket, Key=original_key, Body=body)
        repository.save_managed_object(
            managed,
            expected_version=None,
            audit_action="storage.object.registered",
            actor_id="fixture",
            request_id=f"fixture-{suffix}",
            before=None,
        )

        grant = service.authorize_object_download(
            project_id=project_id,
            object_id=object_id,
            actor=actor,
            idempotency_key=f"download-{suffix}",
            request_id=f"download-{suffix}",
        )
        with urlopen(grant.url) as response:  # noqa: S310 - service-issued short-lived URL
            downloaded = response.read()
            assert response.headers["Cache-Control"] == "no-store"
        assert hashlib.sha256(downloaded).hexdigest() == grant.checksum_sha256

        trashed = service.trash_object(
            project_id=project_id,
            object_id=object_id,
            command=TrashStorageObjectRequest(reason="real MinIO recoverability proof"),
            actor=actor,
            if_match=managed.etag,
            idempotency_key=f"trash-{suffix}",
            request_id=f"trash-{suffix}",
        )
        moved = repository.get_managed_object(project_id=project_id, object_id=object_id)
        assert moved is not None
        tracked_keys.add(moved.object_key)
        assert trashed.status is StorageObjectStatus.TRASHED
        assert operator.head(original_key) is None
        assert operator.head(moved.object_key) is not None

        restored = service.restore_object(
            project_id=project_id,
            object_id=object_id,
            command=RestoreStorageObjectRequest(reason="prove exact MinIO restore"),
            actor=actor,
            if_match=trashed.etag,
            idempotency_key=f"restore-trash-{suffix}",
            request_id=f"restore-trash-{suffix}",
        )
        assert restored.status is StorageObjectStatus.ACTIVE
        assert client.get_object(Bucket=bucket, Key=original_key)["Body"].read() == body

        cold = service.transition_object(
            project_id=project_id,
            object_id=object_id,
            command=TransitionStorageObjectRequest(
                action=LifecyclePolicyAction.TRANSITION_TO_COLD,
                reason="prove real MinIO cold-tier migration",
            ),
            actor=actor,
            if_match=restored.etag,
            idempotency_key=f"cold-{suffix}",
            request_id=f"cold-{suffix}",
        )
        cold_record = repository.get_managed_object(project_id=project_id, object_id=object_id)
        assert cold_record is not None
        tracked_keys.add(cold_record.object_key)
        assert cold.storage_tier is StorageTier.COLD

        archived = service.transition_object(
            project_id=project_id,
            object_id=object_id,
            command=TransitionStorageObjectRequest(
                action=LifecyclePolicyAction.ARCHIVE,
                reason="prove real MinIO archive migration",
            ),
            actor=actor,
            if_match=cold.etag,
            idempotency_key=f"archive-{suffix}",
            request_id=f"archive-{suffix}",
        )
        archive_record = repository.get_managed_object(
            project_id=project_id,
            object_id=object_id,
        )
        assert archive_record is not None
        tracked_keys.add(archive_record.object_key)
        assert archived.status is StorageObjectStatus.ARCHIVED

        online = service.restore_object(
            project_id=project_id,
            object_id=object_id,
            command=RestoreStorageObjectRequest(reason="restore archived MinIO object"),
            actor=actor,
            if_match=archived.etag,
            idempotency_key=f"restore-archive-{suffix}",
            request_id=f"restore-archive-{suffix}",
        )
        assert online.status is StorageObjectStatus.ACTIVE
        assert (
            hashlib.sha256(
                client.get_object(Bucket=bucket, Key=original_key)["Body"].read()
            ).hexdigest()
            == hashlib.sha256(body).hexdigest()
        )

        provider_upload_id = client.create_multipart_upload(
            Bucket=bucket,
            Key=multipart_key,
        )["UploadId"]
        part = b"unfinished-provider-part"
        client.upload_part(
            Bucket=bucket,
            Key=multipart_key,
            UploadId=provider_upload_id,
            PartNumber=1,
            Body=part,
        )
        multipart = ManagedMultipartUploadRecord(
            multipart_id=f"multipart-{suffix}",
            project_id=project_id,
            display_key="uploads/unfinished.mcap",
            object_key=multipart_key,
            upload_id=provider_upload_id,
            received_bytes=str(len(part)),
            part_count=1,
            status="ACTIVE",
            started_at=now - timedelta(hours=1),
            updated_at=now - timedelta(minutes=1),
            version=1,
            etag=ResourceVersion(1).etag,
        )
        repository.seed_managed_multipart(multipart)
        aborted = service.abort_multipart(
            project_id=project_id,
            multipart_id=multipart.multipart_id,
            reason="abort real abandoned MinIO upload",
            actor=actor,
            if_match=multipart.etag,
            idempotency_key=f"abort-{suffix}",
            request_id=f"abort-{suffix}",
        )
        assert aborted.status == "ABORTED"
        with pytest.raises(Exception) as missing_upload:
            client.list_parts(
                Bucket=bucket,
                Key=multipart_key,
                UploadId=provider_upload_id,
            )
        error = getattr(missing_upload.value, "response", {}).get("Error", {})
        assert error.get("Code") == "NoSuchUpload"
        provider_upload_id = None
    finally:
        if provider_upload_id is not None:
            client.abort_multipart_upload(
                Bucket=bucket,
                Key=multipart_key,
                UploadId=provider_upload_id,
            )
        for key in tracked_keys:
            client.delete_object(Bucket=bucket, Key=key)
