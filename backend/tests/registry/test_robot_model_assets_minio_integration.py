from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest

from hc_data_platform.ingest.adapters import S3ObjectStorage
from hc_data_platform.registry.models import (
    CompleteRobotModelAssetFileRequest,
    CreateRobotModelAssetUploadRequest,
    RobotAssetCompletedPart,
    RobotAssetRole,
    RobotModelAssetUploadFileRequest,
    RobotModelSummary,
    RobotModelVersion,
)
from hc_data_platform.registry.repository import InMemoryRegistryRepository
from hc_data_platform.registry.service import RegistryService
from hc_data_platform.security.auth import AuthContext


@pytest.mark.integration
def test_minio_robot_model_asset_direct_multipart_manifest_and_fresh_download() -> None:
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
        config=botocore_config.Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    try:
        client.head_bucket(Bucket=bucket)
    except Exception:
        client.create_bucket(Bucket=bucket)

    suffix = uuid4().hex
    organization_id = f"p14-assets-{suffix}"
    project_id = f"project-{suffix}"
    version_id = f"version-{suffix}"
    repository = InMemoryRegistryRepository(
        organization_projects=((organization_id, project_id),),
        models=(
            (
                organization_id,
                RobotModelSummary(
                    id=f"model-{suffix}",
                    manufacturer="HC Robotics",
                    model_code="P14-MINIO",
                    display_name="P14 MinIO robot",
                    current_published_version_id=None,
                ),
            ),
        ),
        versions=(
            (
                organization_id,
                RobotModelVersion(
                    id=version_id,
                    robot_model_id=f"model-{suffix}",
                    version_label="1.0.0-rc.1",
                    lifecycle="DRAFT",
                    asset_availability="MISSING",
                    publish_readiness="CONFIGURATION_REQUIRED",
                    asset_manifest_hash=None,
                    validation_input_hash=None,
                    etag='"p14-assets:1"',
                    allowed_actions=("MANAGE",),
                    blocked_reasons=(),
                ),
            ),
        ),
    )
    storage = S3ObjectStorage(client, bucket)
    service = RegistryService(
        repository,
        storage=storage,
        clock=lambda: datetime(2026, 8, 19, 12, tzinfo=timezone.utc),
    )
    auth = AuthContext(
        subject_id="p14-minio-manager",
        project_ids=frozenset({project_id}),
        region_codes=frozenset(),
        scope_pairs=frozenset({(project_id, None)}),
        scoped_capabilities=frozenset(
            {(project_id, "robot_model.read"), (project_id, "robot_model.manage")}
        ),
    )
    # Cross the production 16 MiB boundary so this is a genuine S3 multipart
    # upload: the first non-final part is exactly the service's part size.
    part_size = 16 * 1024**2
    body = b'<robot name="p14-minio"><link name="base"/>' + (b" " * part_size) + b"</robot>"
    command = CreateRobotModelAssetUploadRequest(
        files=(
            RobotModelAssetUploadFileRequest(
                relative_path="models/p14-minio.urdf",
                role=RobotAssetRole.URDF,
                media_type="application/xml",
                size_bytes=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
            ),
        )
    )
    object_key: str | None = None
    multipart_upload_id: str | None = None
    upload_completed = False
    try:
        created = service.create_asset_upload(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            version_id=version_id,
            request_id=f"p14-minio-create-{suffix}",
            idempotency_key=f"p14-minio-create-{suffix}",
            command=command,
        )
        record = repository.get_asset_upload(
            organization_id=organization_id,
            project_id=project_id,
            upload_id=created.data.upload_id,
        )
        assert record is not None
        upload_file = record.files[0]
        object_key = upload_file.object_key
        multipart_upload_id = upload_file.multipart_upload_id
        authorizations = created.data.files[0].part_authorizations
        assert created.data.files[0].part_size_bytes == part_size
        assert created.data.files[0].total_parts == 2
        assert [item.part_number for item in authorizations] == [1, 2]
        for authorization, payload in zip(
            authorizations,
            (body[:part_size], body[part_size:]),
            strict=True,
        ):
            with urlopen(Request(authorization.url, data=payload, method="PUT")) as response:  # noqa: S310 - service-generated short-lived grant
                assert response.status in {200, 201}
        uploaded_parts = storage.list_parts(object_key, multipart_upload_id)
        assert [item.part_number for item in uploaded_parts] == [1, 2]
        completed = service.complete_asset_upload_file(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            upload_id=record.upload_id,
            request_id=f"p14-minio-complete-{suffix}",
            command=CompleteRobotModelAssetFileRequest(
                relative_path="models/p14-minio.urdf",
                parts=tuple(
                    RobotAssetCompletedPart(part_number=item.part_number, etag=item.etag)
                    for item in uploaded_parts
                ),
            ),
        )
        assert completed.data.status.value == "COMPLETED"
        upload_completed = True
        assets = service.list_robot_model_assets(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            version_id=version_id,
            request_id=f"p14-minio-list-{suffix}",
        )
        assert [asset.relative_path for asset in assets.items] == ["models/p14-minio.urdf"]
        authorization = service.authorize_asset_download(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            version_id=version_id,
            asset_id=assets.items[0].asset_id,
            request_id=f"p14-minio-download-{suffix}",
        )
        token = authorization.download_url.partition("token=")[2]
        _filename, chunks = service.open_authorized_asset_download(
            organization_id=organization_id,
            token=token,
        )
        assert hashlib.sha256(b"".join(chunks)).hexdigest() == hashlib.sha256(body).hexdigest()
    finally:
        if object_key is not None:
            client.delete_object(Bucket=bucket, Key=object_key)
        if multipart_upload_id is not None and not upload_completed and object_key is not None:
            client.abort_multipart_upload(
                Bucket=bucket, Key=object_key, UploadId=multipart_upload_id
            )
