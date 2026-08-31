"""Exercise a real MinIO network outage without mutating ingest business code."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest

from hc_data_platform.ingest.adapters import S3ObjectStorage
from hc_data_platform.ingest.models import (
    CompletedPart,
    ManifestFileV1,
    RolloutManifestV1,
    UploadStatus,
)
from hc_data_platform.ingest.ports import crc64_ecma
from hc_data_platform.ingest.service import UploadSessionService


def _put(url: str, body: bytes) -> str:
    request = Request(url, data=body, headers={"Content-Length": str(len(body))}, method="PUT")
    with urlopen(request, timeout=10) as response:  # noqa: S310 - test-owned MinIO URL
        return str(response.headers["ETag"]).strip('"')


@pytest.mark.integration
def test_minio_network_pause_recovers_one_raw_object_and_manifest() -> None:
    endpoint = os.environ.get("HC_MINIO_NETWORK_ENDPOINT") or os.environ.get("HC_MINIO_ENDPOINT")
    bucket = os.environ.get("HC_MINIO_NETWORK_BUCKET") or os.environ.get("HC_MINIO_BUCKET")
    access_key = os.environ.get("HC_MINIO_NETWORK_ACCESS_KEY") or os.environ.get(
        "HC_MINIO_ACCESS_KEY"
    )
    secret_key = os.environ.get("HC_MINIO_NETWORK_SECRET_KEY") or os.environ.get(
        "HC_MINIO_SECRET_KEY"
    )
    container = os.environ.get("HC_MINIO_NETWORK_TEST_CONTAINER") or os.environ.get(
        "HC_MINIO_TEST_CONTAINER"
    )
    if not all((endpoint, bucket, access_key, secret_key, container)):
        pytest.skip("disposable MinIO network-recovery variables are required")
    if shutil.which("docker") is None:
        pytest.skip("docker is required to pause the disposable MinIO container")
    assert endpoint and bucket and access_key and secret_key and container
    disposable = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            '{{ index .Config.Labels "hc.test.disposable" }}',
            container,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert disposable.stdout.strip() == "true", "refusing to pause a non-disposable container"

    boto3 = pytest.importorskip("boto3")
    botocore_config = pytest.importorskip("botocore.config")
    botocore_exceptions = pytest.importorskip("botocore.exceptions")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
        config=botocore_config.Config(
            signature_version="s3v4",
            connect_timeout=1,
            read_timeout=1,
            retries={"max_attempts": 0, "mode": "standard"},
        ),
    )
    try:
        client.head_bucket(Bucket=bucket)
    except Exception:
        client.create_bucket(Bucket=bucket)

    first_body = b"n" * (5 * 1024 * 1024)
    second_body = b"etwork-recovered"
    body = first_body + second_body
    suffix = uuid4().hex
    started = datetime.now(timezone.utc)
    manifest = RolloutManifestV1(
        project_id=f"be12-network-{suffix}",
        task_id="network-fault",
        collection_job_id="minio-pause",
        rollout_id="rollout-1",
        collection_session_id="network-session",
        recording_request_id="network-request",
        data_package_id=f"network-package-{suffix}",
        sequence_no=1,
        robot_id="be12",
        start_time=started,
        end_time=started + timedelta(seconds=1),
        expected_topics=[],
        actual_topics=[],
        cameras=[],
        topics=[],
        files=[
            ManifestFileV1(
                path="recording.mcap",
                size=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
                crc64=crc64_ecma(body),
            )
        ],
        file_size=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        crc64=crc64_ecma(body),
        compression="none",
        recorder_version="be12-network/1",
    )
    service = UploadSessionService(S3ObjectStorage(client, bucket), authorization_ttl_seconds=60)
    session = service.create_session(
        manifest=manifest,
        region_code="local",
        idempotency_key="be12-network-create",
    )
    paused = False
    try:
        first_url = service.renew_part_authorizations(session.session_id, [1])[0].url
        first_etag = _put(first_url, first_body)

        subprocess.run(["docker", "pause", container], check=True, capture_output=True, text=True)
        paused = True
        with pytest.raises(botocore_exceptions.BotoCoreError):
            service.list_parts(session.session_id)
        assert service.get_session(session.session_id).status is UploadStatus.UPLOADING
    finally:
        if paused:
            subprocess.run(
                ["docker", "unpause", container],
                check=True,
                capture_output=True,
                text=True,
            )

    deadline = time.monotonic() + 10
    while True:
        try:
            assert [part.part_number for part in service.list_parts(session.session_id)] == [1]
            break
        except botocore_exceptions.BotoCoreError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)

    second_url = service.renew_part_authorizations(session.session_id, [2])[0].url
    second_etag = _put(second_url, second_body)
    completed = service.complete_upload(
        session.session_id,
        [
            CompletedPart(part_number=1, etag=first_etag),
            CompletedPart(part_number=2, etag=second_etag),
        ],
    )
    assert completed.status is UploadStatus.MULTIPART_COMPLETED
    first_event = service.commit_manifest(session_id=session.session_id, manifest=manifest)
    second_event = service.commit_manifest(session_id=session.session_id, manifest=manifest)
    assert first_event == second_event

    prefix = f"raw/v1/project={manifest.project_id}/"
    objects = client.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])
    assert sorted(value["Key"] for value in objects) == sorted(
        [first_event.object_key, first_event.manifest_key]
    )
