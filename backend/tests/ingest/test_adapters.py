from __future__ import annotations

import hashlib
from dataclasses import dataclass
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from hc_data_platform.core.config import Settings
from hc_data_platform.ingest.adapters import OssObjectStorage, S3ObjectStorage
from hc_data_platform.ingest.models import CompletedPart
from hc_data_platform.ingest.ports import crc64_ecma
from hc_data_platform.runtime import (
    _lance_root,
    _lance_storage_options,
    _object_store_clients,
    _s3,
)
from hc_data_platform.storage.oss_client import OssBotoCompatClient


class MissingObject(Exception):
    def __init__(self) -> None:
        self.response = {
            "ResponseMetadata": {"HTTPStatusCode": 404},
            "Error": {"Code": "NoSuchKey"},
        }


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.uploads: dict[tuple[str, str], dict[int, bytes]] = {}
        self.last_presign: dict[str, Any] = {}
        self.completed_key: str | None = None
        self.complete_request: dict[str, Any] = {}

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        try:
            body = self.objects[Key]
        except KeyError as exc:
            raise MissingObject() from exc
        return {
            "ContentLength": len(body),
            "ETag": hashlib.md5(body, usedforsecurity=False).hexdigest(),
            "Metadata": {"crc64": str(crc64_ecma(body))},
        }

    def create_multipart_upload(self, *, Bucket: str, Key: str) -> dict[str, str]:
        upload_id = "s3-upload"
        self.uploads[(Key, upload_id)] = {}
        return {"UploadId": upload_id}

    def generate_presigned_url(self, operation: str, **kwargs: Any) -> str:
        self.last_presign = {"operation": operation, **kwargs}
        return "https://minio.invalid/direct-put"

    def list_parts(self, **kwargs: Any) -> dict[str, Any]:
        parts = self.uploads[(kwargs["Key"], kwargs["UploadId"])]
        return {
            "Parts": [
                {
                    "PartNumber": number,
                    "ETag": hashlib.md5(body, usedforsecurity=False).hexdigest(),
                    "Size": len(body),
                }
                for number, body in sorted(parts.items())
            ],
            "IsTruncated": False,
        }

    def complete_multipart_upload(self, **kwargs: Any) -> None:
        self.complete_request = kwargs
        key = kwargs["Key"]
        upload_id = kwargs["UploadId"]
        parts = self.uploads.pop((key, upload_id))
        self.objects[key] = b"".join(parts[number] for number in sorted(parts))
        self.completed_key = key

    def abort_multipart_upload(self, **kwargs: Any) -> None:
        self.uploads.pop((kwargs["Key"], kwargs["UploadId"]), None)

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        return {"Body": StreamingBody(self.objects[Key])}

    def put_object(self, **kwargs: Any) -> None:
        if kwargs.get("IfNoneMatch") == "*" and kwargs["Key"] in self.objects:
            raise RuntimeError("precondition failed")
        self.objects[kwargs["Key"]] = kwargs["Body"]


class StreamingBody(BytesIO):
    def iter_chunks(self, chunk_size: int) -> Any:
        while chunk := self.read(chunk_size):
            yield chunk


def test_s3_minio_adapter_binds_key_to_every_multipart_operation() -> None:
    client = FakeS3Client()
    adapter = S3ObjectStorage(client, "raw-bucket")
    key = "raw/v1/project=p/date=2026-08-14/recording.mcap"
    upload_id = adapter.create_multipart(key)
    client.uploads[(key, upload_id)] = {1: b"first", 2: b"second"}

    assert adapter.presign_part(key, upload_id, 2, 120).startswith("https://")
    assert client.last_presign["Params"]["Key"] == key
    parts = adapter.list_parts(key, upload_id)
    metadata = adapter.complete_multipart(
        key,
        upload_id,
        [CompletedPart(part_number=part.part_number, etag=part.etag) for part in parts],
    )

    assert client.completed_key == key
    assert client.complete_request["IfNoneMatch"] == "*"
    assert metadata.size == len(b"firstsecond")
    assert b"".join(adapter.read_chunks(key, chunk_size=3)) == b"firstsecond"
    assert adapter.presign_read(key, 120).startswith("https://")
    assert client.last_presign["operation"] == "get_object"
    assert client.last_presign["HttpMethod"] == "GET"
    assert client.last_presign["Params"] == {
        "Bucket": "raw-bucket",
        "Key": key,
        "ResponseCacheControl": "no-store",
    }


class PublicPresignClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def generate_presigned_url(self, operation: str, **kwargs: Any) -> str:
        self.calls.append((operation, kwargs))
        return "http://127.0.0.1:9000/raw-bucket/redacted"


def test_s3_adapter_uses_public_signer_for_part_and_raw_read_authorizations() -> None:
    internal = FakeS3Client()
    public = PublicPresignClient()
    adapter = S3ObjectStorage(internal, "raw-bucket", presign_client=public)
    key = "raw/v1/project=p/date=2026-08-19/recording.mcap"

    upload_id = adapter.create_multipart(key)
    signed = adapter.presign_part(key, upload_id, 1, 120)
    internal.uploads[(key, upload_id)] = {1: b"browser-part"}
    parts = adapter.list_parts(key, upload_id)
    completed = adapter.complete_multipart(
        key,
        upload_id,
        [CompletedPart(part_number=parts[0].part_number, etag=parts[0].etag)],
    )

    assert urlparse(signed).hostname == "127.0.0.1"
    raw_signed = adapter.presign_read(key, 120)
    assert urlparse(raw_signed).hostname == "127.0.0.1"
    assert len(public.calls) == 2
    operation, arguments = public.calls[0]
    assert operation == "upload_part"
    assert arguments["HttpMethod"] == "PUT"
    assert arguments["Params"] == {
        "Bucket": "raw-bucket",
        "Key": key,
        "UploadId": upload_id,
        "PartNumber": 1,
    }
    raw_operation, raw_arguments = public.calls[1]
    assert raw_operation == "get_object"
    assert raw_arguments["HttpMethod"] == "GET"
    assert raw_arguments["Params"] == {
        "Bucket": "raw-bucket",
        "Key": key,
        "ResponseCacheControl": "no-store",
    }
    assert internal.last_presign == {}
    assert completed.size == len(b"browser-part")
    assert adapter.head(key) == completed


def test_runtime_builds_distinct_path_style_sigv4_internal_and_public_clients() -> None:
    settings = Settings(
        environment="test",
        object_store_endpoint="http://minio:9000",
        object_store_public_endpoint="http://127.0.0.1:9000",
        object_store_bucket="hc-data-test",
        object_store_access_key="test-access",
        object_store_secret_key="test-secret-value",
        _env_file=None,
    )
    internal, public, storage = _s3(settings)
    signed = storage.presign_part(
        "raw/v1/project=br04/date=2026-08-19/recording.mcap",
        "redacted-upload-id",
        7,
        120,
    )
    parsed = urlparse(signed)
    query = parse_qs(parsed.query)

    assert internal.meta.endpoint_url == "http://minio:9000"
    assert public.meta.endpoint_url == "http://127.0.0.1:9000"
    assert (parsed.scheme, parsed.hostname, parsed.port) == ("http", "127.0.0.1", 9000)
    assert unquote(parsed.path) == (
        "/hc-data-test/raw/v1/project=br04/date=2026-08-19/recording.mcap"
    )
    assert query["partNumber"] == ["7"]
    assert query["uploadId"] == ["redacted-upload-id"]
    assert {
        "X-Amz-Algorithm",
        "X-Amz-Credential",
        "X-Amz-Date",
        "X-Amz-Expires",
        "X-Amz-Signature",
        "X-Amz-SignedHeaders",
    }.issubset(query)
    raw_read = storage.presign_read(
        "raw/v1/project=br04/date=2026-08-19/recording.mcap",
        120,
    )
    raw_read_parsed = urlparse(raw_read)
    assert (raw_read_parsed.scheme, raw_read_parsed.hostname, raw_read_parsed.port) == (
        "http",
        "127.0.0.1",
        9000,
    )
    assert unquote(raw_read_parsed.path).endswith("/recording.mcap")
    assert "partNumber" not in parse_qs(raw_read_parsed.query)


def test_runtime_builds_native_oss_upload_clients_and_lance_configuration() -> None:
    settings = Settings(
        environment="test",
        object_store_provider="oss",
        object_store_endpoint="https://oss-cn-hangzhou-internal.aliyuncs.com",
        object_store_public_endpoint="https://oss-cn-hangzhou.aliyuncs.com",
        object_store_bucket="hc-oss-test",
        object_store_access_key="test-access-key",
        object_store_secret_key="test-secret-key",
        object_store_region="cn-hangzhou",
        _env_file=None,
    )

    internal, public, storage = _object_store_clients(settings)
    signed = storage.presign_part("raw/recording.mcap", "upload-1", 2, 900)

    assert isinstance(internal, OssBotoCompatClient)
    assert isinstance(public, OssBotoCompatClient)
    assert urlparse(signed).hostname == "hc-oss-test.oss-cn-hangzhou.aliyuncs.com"
    assert _lance_root(settings) == "oss://hc-oss-test/lance"
    assert _lance_storage_options(settings) == {
        "oss_endpoint": "https://oss-cn-hangzhou-internal.aliyuncs.com",
        "oss_access_key_id": "test-access-key",
        "oss_secret_access_key": "test-secret-key",
        "oss_region": "cn-hangzhou",
    }


@dataclass
class OssPart:
    part_number: int
    body: bytes

    @property
    def etag(self) -> str:
        return hashlib.md5(self.body, usedforsecurity=False).hexdigest()

    @property
    def size(self) -> int:
        return len(self.body)

    @property
    def headers(self) -> dict[str, str]:
        return {"x-oss-hash-crc64ecma": str(crc64_ecma(self.body))}


class OssMissingObject(Exception):
    status = 404


class FakeOssBucket:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.uploads: dict[tuple[str, str], dict[int, bytes]] = {}
        self.signed: dict[str, Any] = {}
        self.completed_key: str | None = None
        self.initiation_headers: dict[str, str] = {}
        self.completion_headers: dict[str, str] = {}

    def get_object_meta(self, key: str) -> Any:
        if key not in self.objects:
            raise OssMissingObject()
        body = self.objects[key]
        return SimpleNamespace(
            etag=hashlib.md5(body, usedforsecurity=False).hexdigest(),
            headers={
                "Content-Length": str(len(body)),
                "X-Oss-Hash-Crc64ecma": str(crc64_ecma(body)),
            },
        )

    def init_multipart_upload(self, key: str, **kwargs: Any) -> Any:
        self.initiation_headers = kwargs["headers"]
        upload_id = "oss-upload"
        self.uploads[(key, upload_id)] = {}
        return SimpleNamespace(upload_id=upload_id)

    def sign_url(self, method: str, key: str, expires: int, **kwargs: Any) -> str:
        self.signed = {"method": method, "key": key, "expires": expires, **kwargs}
        return "https://oss.invalid/direct-put"

    def list_parts(self, key: str, upload_id: str, **kwargs: Any) -> Any:
        parts = self.uploads[(key, upload_id)]
        return SimpleNamespace(
            parts=[OssPart(number, body) for number, body in sorted(parts.items())],
            is_truncated=False,
        )

    def complete_multipart_upload(
        self,
        key: str,
        upload_id: str,
        parts: list[Any],
        **kwargs: Any,
    ) -> None:
        self.completion_headers = kwargs["headers"]
        bodies = self.uploads.pop((key, upload_id))
        self.objects[key] = b"".join(bodies[part[0]] for part in parts)
        self.completed_key = key

    def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        self.uploads.pop((key, upload_id), None)

    def get_object(self, key: str) -> BytesIO:
        return BytesIO(self.objects[key])

    def put_object(self, key: str, body: bytes, **kwargs: Any) -> None:
        self.objects[key] = body


def test_oss_adapter_exposes_server_crc64_and_uses_persisted_key() -> None:
    bucket = FakeOssBucket()
    adapter = OssObjectStorage(
        bucket,
        part_info_factory=lambda number, etag, size: (number, etag, size),
    )
    key = "raw/v1/project=p/date=2026-08-14/recording.mcap"
    upload_id = adapter.create_multipart(key)
    bucket.uploads[(key, upload_id)] = {1: b"first", 2: b"second"}

    adapter.presign_part(key, upload_id, 1, 120)
    assert bucket.signed["key"] == key
    assert bucket.signed["params"] == {"uploadId": upload_id, "partNumber": "1"}
    parts = adapter.list_parts(key, upload_id)
    metadata = adapter.complete_multipart(
        key,
        upload_id,
        [CompletedPart(part_number=part.part_number, etag=part.etag) for part in parts],
    )

    assert bucket.completed_key == key
    assert bucket.initiation_headers == {"x-oss-forbid-overwrite": "true"}
    assert bucket.completion_headers == {"x-oss-forbid-overwrite": "true"}
    assert metadata.crc64 == crc64_ecma(b"firstsecond")
    assert b"".join(adapter.read_chunks(key, chunk_size=2)) == b"firstsecond"
    assert adapter.presign_read(key, 120).startswith("https://")
    assert bucket.signed == {
        "method": "GET",
        "key": key,
        "expires": 120,
        "params": {"response-cache-control": "no-store"},
        "slash_safe": True,
    }
