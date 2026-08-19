from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.ingest.adapters import S3ObjectStorage
from hc_data_platform.ingest.models import (
    CompletedPart,
    ManifestFileV1,
    RolloutManifestV1,
    UploadSourceType,
    UploadStatus,
    raw_object_key,
)
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
        collection_session_id="session",
        recording_request_id="request",
        data_package_id=f"package-{suffix}",
        sequence_no=1,
        robot_id="robot",
        start_time=start,
        end_time=start + timedelta(seconds=1),
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

        referenced_body = b"registered-from-existing-minio-object"
        referenced_sha = hashlib.sha256(referenced_body).hexdigest()
        referenced_crc = crc64_ecma(referenced_body)
        referenced_manifest = manifest.model_copy(
            update={
                "rollout_id": "referenced",
                "recording_request_id": "request-referenced",
                "data_package_id": f"package-referenced-{suffix}",
                "sequence_no": 2,
                "file_size": len(referenced_body),
                "sha256": referenced_sha,
                "crc64": referenced_crc,
                "files": [
                    ManifestFileV1(
                        path="recording.mcap",
                        size=len(referenced_body),
                        sha256=referenced_sha,
                        crc64=referenced_crc,
                    )
                ],
            }
        )
        referenced_key = raw_object_key(referenced_manifest)
        client.put_object(
            Bucket=bucket,
            Key=referenced_key,
            Body=referenced_body,
            Metadata={"crc64": str(referenced_crc)},
        )
        with pytest.raises(ProblemException) as unsafe_reference:
            service.create_upload(
                manifest=referenced_manifest,
                region_code="local",
                idempotency_key="minio-unsafe-reference",
                object_storage_uri=f"s3://{bucket}/{referenced_key}?access-key=forbidden",
            )
        assert unsafe_reference.value.problem.code == "OBJECT_STORAGE_REFERENCE_INVALID"
        referenced = service.create_upload(
            manifest=referenced_manifest,
            region_code="local",
            idempotency_key="minio-reference",
            object_storage_uri=f"s3://{bucket}/{referenced_key}",
        )
        assert referenced.session.source_type is UploadSourceType.OBJECT_STORAGE_REFERENCE
        assert referenced.session.multipart_upload_id is None
        service.commit_manifest(
            session_id=referenced.session.session_id,
            manifest=referenced_manifest,
        )

        cancelled_manifest = manifest.model_copy(
            update={
                "rollout_id": "cancelled",
                "recording_request_id": "request-cancelled",
                "data_package_id": f"package-cancelled-{suffix}",
                "sequence_no": 3,
                "sha256": "b" * 64,
                "files": [manifest.files[0].model_copy(update={"sha256": "b" * 64})],
            }
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
        for value in response.get("Contents", []):
            client.delete_object(Bucket=bucket, Key=value["Key"])


def direct_put(url: str, body: bytes) -> str:
    request = Request(url, data=body, headers={"Content-Length": str(len(body))}, method="PUT")
    with urlopen(request) as response:  # noqa: S310 - URL is signed by the test's MinIO
        return response.headers["ETag"].strip('"')


@pytest.mark.integration
def test_browser_public_presign_cors_put_internal_list_and_negative_security() -> None:
    internal_endpoint = os.environ.get("HC_MINIO_ENDPOINT")
    public_endpoint = os.environ.get("HC_MINIO_PUBLIC_ENDPOINT")
    bucket = os.environ.get("HC_MINIO_BUCKET")
    access_key = os.environ.get("HC_MINIO_ACCESS_KEY")
    secret_key = os.environ.get("HC_MINIO_SECRET_KEY")
    if not all((internal_endpoint, public_endpoint, bucket, access_key, secret_key)):
        pytest.skip("HC_MINIO_ENDPOINT/PUBLIC_ENDPOINT/BUCKET/ACCESS_KEY/SECRET_KEY are required")
    assert internal_endpoint is not None
    assert public_endpoint is not None
    assert bucket is not None
    assert access_key is not None
    assert secret_key is not None

    boto3 = pytest.importorskip("boto3")
    botocore_config = pytest.importorskip("botocore.config")
    client_options = {
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
        "region_name": "us-east-1",
        "config": botocore_config.Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        ),
    }
    internal = boto3.client("s3", endpoint_url=internal_endpoint, **client_options)
    public = boto3.client("s3", endpoint_url=public_endpoint, **client_options)
    storage = S3ObjectStorage(internal, bucket, presign_client=public)
    run_prefix = f"br04-browser-upload/{uuid4().hex}/"
    key = f"{run_prefix}part.bin"
    body = b"br04-browser-upload-proof"
    upload_id: str | None = None
    aborted = False

    try:
        upload_id = storage.create_multipart(key)
        signed = storage.presign_part(key, upload_id, 1, 120)
        parsed_public = urlsplit(public_endpoint)
        parsed_signed = urlsplit(signed)
        assert (parsed_signed.scheme, parsed_signed.hostname, parsed_signed.port) == (
            parsed_public.scheme,
            parsed_public.hostname,
            parsed_public.port,
        )

        preflight_status, preflight_headers = _request_status(
            signed,
            method="OPTIONS",
            headers={
                "Origin": "http://127.0.0.1:8088",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert preflight_status in {200, 204}
        assert preflight_headers.get("access-control-allow-origin") == ("http://127.0.0.1:8088")
        assert "PUT" in preflight_headers.get("access-control-allow-methods", "")
        assert "content-type" in preflight_headers.get("access-control-allow-headers", "").lower()
        assert "credentials" not in " ".join(preflight_headers).lower()

        put_status, put_headers = _request_status(
            signed,
            method="PUT",
            headers={
                "Origin": "http://127.0.0.1:8088",
                "Content-Type": "application/octet-stream",
            },
            body=body,
        )
        assert 200 <= put_status < 300
        assert put_headers.get("access-control-allow-origin") == "http://127.0.0.1:8088"
        assert "etag" in put_headers
        listed = storage.list_parts(key, upload_id)
        assert [(part.part_number, part.size) for part in listed] == [(1, len(body))]

        denied_options_status, denied_options_headers = _request_status(
            signed,
            method="OPTIONS",
            headers={
                "Origin": "https://attacker.invalid",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert denied_options_status in {200, 204, 400, 403}
        assert denied_options_headers.get("access-control-allow-origin") != (
            "https://attacker.invalid"
        )

        denied_put_status, denied_put_headers = _request_status(
            signed,
            method="PUT",
            headers={
                "Origin": "https://attacker.invalid",
                "Content-Type": "application/octet-stream",
            },
            body=body,
        )
        assert 200 <= denied_put_status < 300
        assert denied_put_headers.get("access-control-allow-origin") != ("https://attacker.invalid")

        tampered_status, _ = _request_status(
            _tamper_part_number(signed),
            method="PUT",
            headers={"Content-Type": "application/octet-stream"},
            body=body,
        )
        assert tampered_status in {400, 403}

        storage.abort_multipart(key, upload_id)
        aborted = True
    finally:
        if upload_id is not None and not aborted:
            internal.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        for value in internal.list_objects_v2(Bucket=bucket, Prefix=run_prefix).get("Contents", []):
            internal.delete_object(Bucket=bucket, Key=value["Key"])
        assert internal.list_objects_v2(Bucket=bucket, Prefix=run_prefix).get("KeyCount", 0) == 0
        remaining = internal.list_multipart_uploads(Bucket=bucket, Prefix=run_prefix).get(
            "Uploads", []
        )
        assert remaining == []


def _request_status(
    url: str,
    *,
    method: str,
    headers: dict[str, str],
    body: bytes | None = None,
) -> tuple[int, dict[str, str]]:
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request) as response:  # noqa: S310 - test-generated signed URL
            return response.status, {key.lower(): value for key, value in response.headers.items()}
    except HTTPError as exc:
        return exc.code, {key.lower(): value for key, value in exc.headers.items()}


def _tamper_part_number(url: str) -> str:
    parsed = urlsplit(url)
    query = [
        (name, "2" if name == "partNumber" else value)
        for name, value in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )
