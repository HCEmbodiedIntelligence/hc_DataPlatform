from __future__ import annotations

import hashlib
from dataclasses import dataclass
from io import BytesIO
from types import SimpleNamespace
from typing import Any

from hc_data_platform.ingest.adapters import OssObjectStorage, S3ObjectStorage
from hc_data_platform.ingest.models import CompletedPart
from hc_data_platform.ingest.ports import crc64_ecma


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

    def init_multipart_upload(self, key: str) -> Any:
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
    assert bucket.completion_headers == {"x-oss-forbid-overwrite": "true"}
    assert metadata.crc64 == crc64_ecma(b"firstsecond")
    assert b"".join(adapter.read_chunks(key, chunk_size=2)) == b"firstsecond"
