from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.ingest.adapters import S3ObjectStorage
from hc_data_platform.ingest.models import CompletedPart, RolloutManifestV1, UploadStatus
from hc_data_platform.ingest.ports import crc64_ecma
from hc_data_platform.ingest.service import UploadSessionService


@pytest.mark.integration
def test_minio_interruption_renewal_part_validation_completion_and_cancel() -> None:
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
        config=botocore_config.Config(signature_version="s3v4"),
    )
    try:
        client.head_bucket(Bucket=bucket)
    except Exception:
        client.create_bucket(Bucket=bucket)

    body = b"a" * (5 * 1024 * 1024) + b"interrupted-tail"
    suffix = uuid4().hex
    start = datetime.now(timezone.utc)
    manifest = RolloutManifestV1(
        project_id=f"integration-{suffix}",
        task_id="minio",
        collection_job_id="job",
        rollout_id="rollout",
        sequence_no=1,
        robot_id="robot",
        start_time=start,
        end_time=start + timedelta(seconds=1),
        expected_topics=[],
        actual_topics=[],
        file_size=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        crc64=crc64_ecma(body),
        compression="none",
        recorder_version="integration",
    )
    storage = S3ObjectStorage(client, bucket)
    service = UploadSessionService(storage, authorization_ttl_seconds=60)
    session = service.create_session(
        manifest=manifest,
        region_code="local",
        idempotency_key="minio-create",
    )
    try:
        first_url = service.renew_part_authorizations(session.session_id, [1])[0].url
        first_etag = direct_put(first_url, body[: 5 * 1024 * 1024])
        service.pause_upload(session.session_id)
        resumed = service.resume_upload(session.session_id, [2])
        second_etag = direct_put(resumed.parts[0].url, body[5 * 1024 * 1024 :])
        listed = service.list_parts(session.session_id)
        assert [part.part_number for part in listed] == [1, 2]

        with pytest.raises(ProblemException) as missing:
            service.complete_upload(
                session.session_id,
                [CompletedPart(part_number=1, etag=first_etag)],
            )
        assert missing.value.problem.code == "MULTIPART_PARTS_MISMATCH"
        with pytest.raises(ProblemException) as unordered:
            service.complete_upload(
                session.session_id,
                [
                    CompletedPart(part_number=2, etag=second_etag),
                    CompletedPart(part_number=1, etag=first_etag),
                ],
            )
        assert unordered.value.problem.code == "PARTS_NOT_SORTED"

        completed = service.complete_upload(
            session.session_id,
            [
                CompletedPart(part_number=1, etag=first_etag),
                CompletedPart(part_number=2, etag=second_etag),
            ],
        )
        assert completed.status is UploadStatus.MULTIPART_COMPLETED
        event = service.commit_manifest(session_id=session.session_id, manifest=manifest)
        assert client.head_object(Bucket=bucket, Key=event.manifest_key)["ContentLength"] > 0

        cancelled_manifest = manifest.model_copy(
            update={"rollout_id": "cancelled", "sequence_no": 2, "sha256": "b" * 64}
        )
        cancelled = service.create_session(
            manifest=cancelled_manifest,
            region_code="local",
            idempotency_key="minio-cancel",
        )
        service.cancel_upload(cancelled.session_id)
        assert service.get_session(cancelled.session_id).status is UploadStatus.CANCELLED
    finally:
        prefix = f"raw/v1/project={manifest.project_id}/"
        response = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
        objects = [{"Key": value["Key"]} for value in response.get("Contents", [])]
        if objects:
            client.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})


def direct_put(url: str, body: bytes) -> str:
    request = Request(url, data=body, headers={"Content-Length": str(len(body))}, method="PUT")
    with urlopen(request) as response:  # noqa: S310 - URL is signed by the test's MinIO
        return response.headers["ETag"].strip('"')
