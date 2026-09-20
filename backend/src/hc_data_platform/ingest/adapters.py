from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, BinaryIO
from urllib.parse import quote, unquote, urlparse

from hc_data_platform.core.context import retain_current_writer_permit

from .models import CompletedPart
from .ports import (
    MultipartPart,
    ObjectMetadata,
    normalize_etag,
    validate_completion_parts,
)


class S3ObjectStorage:
    """Production adapter for MinIO and other S3-compatible object stores.

    The injected client is a boto3 S3 client. Keeping construction outside this module lets
    deployments use their normal credential provider chain without importing boto3 in tests.
    """

    def __init__(self, client: Any, bucket: str, *, presign_client: Any | None = None) -> None:
        self._client = client
        self._presign_client = presign_client or client
        self._bucket = bucket

    @classmethod
    def from_boto3(cls, bucket: str, **client_options: Any) -> S3ObjectStorage:
        boto3 = importlib.import_module("boto3")
        return cls(boto3.client("s3", **client_options), bucket)

    def create_multipart(self, key: str) -> str:
        if self.head(key) is not None:
            raise _immutable_object_exists()
        response = self._client.create_multipart_upload(Bucket=self._bucket, Key=key)
        return str(response["UploadId"])

    def presign_part(
        self,
        key: str,
        upload_id: str,
        part_number: int,
        expires_seconds: int,
    ) -> str:
        retain_current_writer_permit(expires_seconds)
        return str(
            self._presign_client.generate_presigned_url(
                "upload_part",
                Params={
                    "Bucket": self._bucket,
                    "Key": key,
                    "UploadId": upload_id,
                    "PartNumber": part_number,
                },
                ExpiresIn=expires_seconds,
                HttpMethod="PUT",
            )
        )

    def upload_part_stream(
        self,
        key: str,
        upload_id: str,
        part_number: int,
        body: BinaryIO,
        size: int,
    ) -> MultipartPart:
        response = self._client.upload_part(
            Bucket=self._bucket,
            Key=key,
            UploadId=upload_id,
            PartNumber=part_number,
            Body=body,
            ContentLength=size,
        )
        return MultipartPart(
            part_number=part_number,
            etag=normalize_etag(str(response["ETag"])),
            size=size,
        )

    def list_parts(self, key: str, upload_id: str) -> list[MultipartPart]:
        marker = 0
        found: list[MultipartPart] = []
        while True:
            response = self._client.list_parts(
                Bucket=self._bucket,
                Key=key,
                UploadId=upload_id,
                PartNumberMarker=marker,
            )
            for value in response.get("Parts", []):
                found.append(
                    MultipartPart(
                        part_number=int(value["PartNumber"]),
                        etag=normalize_etag(str(value["ETag"])),
                        size=int(value["Size"]),
                    )
                )
            if not response.get("IsTruncated", False):
                return sorted(found, key=lambda part: part.part_number)
            marker = int(response["NextPartNumberMarker"])

    def complete_multipart(
        self,
        key: str,
        upload_id: str,
        parts: Sequence[CompletedPart],
    ) -> ObjectMetadata:
        validate_completion_parts(parts, self.list_parts(key, upload_id))
        if self.head(key) is not None:
            raise _immutable_object_exists()
        try:
            self._client.complete_multipart_upload(
                Bucket=self._bucket,
                Key=key,
                UploadId=upload_id,
                IfNoneMatch="*",
                MultipartUpload={
                    "Parts": [{"PartNumber": part.part_number, "ETag": part.etag} for part in parts]
                },
            )
        except Exception as exc:
            if _precondition_failed(exc):
                raise _immutable_object_exists() from exc
            raise
        metadata = self.head(key)
        if metadata is None:
            raise RuntimeError("completed S3 multipart object is not visible")
        return metadata

    def abort_multipart(self, key: str, upload_id: str) -> None:
        self._client.abort_multipart_upload(
            Bucket=self._bucket,
            Key=key,
            UploadId=upload_id,
        )

    def authorize_existing_object(self, uri: str, expected_key: str) -> ObjectMetadata:
        key = _authorized_key(uri, scheme="s3", bucket=self._bucket, expected_key=expected_key)
        metadata = self.head(key)
        if metadata is None:
            raise _object_reference_not_found()
        return metadata

    def head(self, key: str) -> ObjectMetadata | None:
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            if _not_found(exc):
                return None
            raise
        raw_crc = response.get("Metadata", {}).get("crc64")
        crc64 = None if raw_crc is None else int(raw_crc)
        return ObjectMetadata(
            key=key,
            size=int(response["ContentLength"]),
            crc64=crc64,
            etag=normalize_etag(str(response.get("ETag", "unknown"))),
        )

    def read_chunks(self, key: str, chunk_size: int = 8 * 1024 * 1024) -> Iterable[bytes]:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        body = response["Body"]
        try:
            yield from body.iter_chunks(chunk_size=chunk_size)
        finally:
            body.close()

    def read_range(self, key: str, start: int, end: int) -> bytes:
        if start < 0 or end < start:
            raise ValueError("invalid object byte range")
        if start == end:
            return b""
        response = self._client.get_object(
            Bucket=self._bucket,
            Key=key,
            Range=f"bytes={start}-{end - 1}",
        )
        body = response["Body"]
        try:
            return bytes(body.read())
        finally:
            body.close()

    def presign_read(
        self, key: str, expires_seconds: int, *, download_name: str | None = None
    ) -> str:
        if expires_seconds < 1:
            raise ValueError("expires_seconds must be positive")
        return str(
            self._presign_client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": key,
                    # The source URL is credential-like evidence. Keep a browser
                    # or intermediary from retaining its successful response.
                    "ResponseCacheControl": "no-store",
                    **(
                        {
                            "ResponseContentDisposition": "attachment; filename*=UTF-8''"
                            + quote(download_name, safe="")
                        }
                        if download_name
                        else {}
                    ),
                },
                ExpiresIn=expires_seconds,
                HttpMethod="GET",
            )
        )

    def put_json(self, key: str, value: dict[str, Any], *, if_none_match: bool) -> ObjectMetadata:
        body = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
        arguments: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": key,
            "Body": body,
            "ContentType": "application/json",
        }
        if if_none_match:
            arguments["IfNoneMatch"] = "*"
        try:
            self._client.put_object(**arguments)
        except Exception as exc:
            if _precondition_failed(exc):
                raise _immutable_object_exists() from exc
            raise
        metadata = self.head(key)
        if metadata is None:
            raise RuntimeError("written S3 object is not visible")
        return metadata


class OssObjectStorage:
    """Production adapter for Alibaba Cloud OSS Python SDK V1 buckets."""

    def __init__(
        self,
        bucket: Any,
        *,
        presign_bucket: Any | None = None,
        part_info_factory: Callable[[int, str, int], Any] | None = None,
    ) -> None:
        self._bucket = bucket
        self._presign_bucket = presign_bucket or bucket
        self._part_info_factory = part_info_factory or _oss_part_info

    def create_multipart(self, key: str) -> str:
        if self.head(key) is not None:
            raise _immutable_object_exists()
        return str(
            self._bucket.init_multipart_upload(
                key,
                headers={"x-oss-forbid-overwrite": "true"},
            ).upload_id
        )

    def presign_part(
        self,
        key: str,
        upload_id: str,
        part_number: int,
        expires_seconds: int,
    ) -> str:
        retain_current_writer_permit(expires_seconds)
        return str(
            self._presign_bucket.sign_url(
                "PUT",
                key,
                expires_seconds,
                params={"uploadId": upload_id, "partNumber": str(part_number)},
                slash_safe=True,
            )
        )

    def upload_part_stream(
        self,
        key: str,
        upload_id: str,
        part_number: int,
        body: BinaryIO,
        size: int,
    ) -> MultipartPart:
        result = self._bucket.upload_part(
            key,
            upload_id,
            part_number,
            body,
            headers={"Content-Length": str(size)},
        )
        headers = getattr(result, "headers", {})
        raw_crc64 = _case_insensitive_header(headers, "x-oss-hash-crc64ecma")
        return MultipartPart(
            part_number=part_number,
            etag=normalize_etag(str(result.etag)),
            size=size,
            crc64=None if raw_crc64 is None else int(raw_crc64),
        )

    def list_parts(self, key: str, upload_id: str) -> list[MultipartPart]:
        marker = "0"
        found: list[MultipartPart] = []
        while True:
            result = self._bucket.list_parts(key, upload_id, marker=marker, max_parts=1000)
            for value in result.parts:
                headers = getattr(value, "headers", {})
                raw_crc64 = _case_insensitive_header(headers, "x-oss-hash-crc64ecma")
                found.append(
                    MultipartPart(
                        part_number=int(value.part_number),
                        etag=normalize_etag(str(value.etag)),
                        size=int(value.size),
                        crc64=None if raw_crc64 is None else int(raw_crc64),
                    )
                )
            if not result.is_truncated:
                return sorted(found, key=lambda part: part.part_number)
            marker = str(result.next_part_number_marker)

    def complete_multipart(
        self,
        key: str,
        upload_id: str,
        parts: Sequence[CompletedPart],
    ) -> ObjectMetadata:
        uploaded = self.list_parts(key, upload_id)
        validate_completion_parts(parts, uploaded)
        if self.head(key) is not None:
            raise _immutable_object_exists()
        sizes = {part.part_number: part.size for part in uploaded}
        part_infos = [
            self._part_info_factory(part.part_number, part.etag, sizes[part.part_number])
            for part in parts
        ]
        try:
            self._bucket.complete_multipart_upload(
                key,
                upload_id,
                part_infos,
                headers={"x-oss-forbid-overwrite": "true"},
            )
        except Exception as exc:
            if _precondition_failed(exc):
                raise _immutable_object_exists() from exc
            raise
        metadata = self.head(key)
        if metadata is None:
            raise RuntimeError("completed OSS multipart object is not visible")
        return metadata

    def abort_multipart(self, key: str, upload_id: str) -> None:
        self._bucket.abort_multipart_upload(key, upload_id)

    def authorize_existing_object(self, uri: str, expected_key: str) -> ObjectMetadata:
        bucket_name = str(
            getattr(self._bucket, "bucket_name", None) or getattr(self._bucket, "name", None) or ""
        )
        if not bucket_name:
            raise RuntimeError("OSS bucket adapter does not expose its bucket name")
        key = _authorized_key(
            uri,
            scheme="oss",
            bucket=bucket_name,
            expected_key=expected_key,
        )
        metadata = self.head(key)
        if metadata is None:
            raise _object_reference_not_found()
        return metadata

    def head(self, key: str) -> ObjectMetadata | None:
        try:
            result = self._bucket.get_object_meta(key)
        except Exception as exc:
            if _not_found(exc):
                return None
            raise
        headers = result.headers
        raw_crc64 = _case_insensitive_header(headers, "x-oss-hash-crc64ecma")
        size = _case_insensitive_header(headers, "content-length")
        etag = getattr(result, "etag", None) or _case_insensitive_header(headers, "etag")
        if size is None:
            raise RuntimeError("OSS object metadata has no content-length")
        return ObjectMetadata(
            key=key,
            size=int(size),
            crc64=None if raw_crc64 is None else int(raw_crc64),
            etag=normalize_etag(str(etag)),
        )

    def read_chunks(self, key: str, chunk_size: int = 8 * 1024 * 1024) -> Iterable[bytes]:
        result = self._bucket.get_object(key)
        try:
            while chunk := result.read(chunk_size):
                yield bytes(chunk)
        finally:
            close = getattr(result, "close", None)
            if close is not None:
                close()

    def read_range(self, key: str, start: int, end: int) -> bytes:
        if start < 0 or end < start:
            raise ValueError("invalid object byte range")
        if start == end:
            return b""
        result = self._bucket.get_object(key, byte_range=(start, end - 1))
        try:
            return bytes(result.read())
        finally:
            close = getattr(result, "close", None)
            if close is not None:
                close()

    def presign_read(
        self, key: str, expires_seconds: int, *, download_name: str | None = None
    ) -> str:
        if expires_seconds < 1:
            raise ValueError("expires_seconds must be positive")
        return str(
            self._presign_bucket.sign_url(
                "GET",
                key,
                expires_seconds,
                params={
                    "response-cache-control": "no-store",
                    **(
                        {
                            "response-content-disposition": "attachment; filename*=UTF-8''"
                            + quote(download_name, safe="")
                        }
                        if download_name
                        else {}
                    ),
                },
                slash_safe=True,
            )
        )

    def put_json(self, key: str, value: dict[str, Any], *, if_none_match: bool) -> ObjectMetadata:
        body = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
        headers = {"x-oss-forbid-overwrite": "true"} if if_none_match else None
        try:
            self._bucket.put_object(key, body, headers=headers)
        except Exception as exc:
            if _precondition_failed(exc):
                raise _immutable_object_exists() from exc
            raise
        metadata = self.head(key)
        if metadata is None:
            raise RuntimeError("written OSS object is not visible")
        return metadata


def _oss_part_info(part_number: int, etag: str, size: int) -> Any:
    models = importlib.import_module("oss2.models")
    return models.PartInfo(part_number, etag, size=size)


def _case_insensitive_header(headers: Mapping[str, Any], name: str) -> Any | None:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def _not_found(exc: Exception) -> bool:
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    if status == 404:
        return True
    response = getattr(exc, "response", {})
    if isinstance(response, Mapping):
        metadata = response.get("ResponseMetadata", {})
        error = response.get("Error", {})
        return metadata.get("HTTPStatusCode") == 404 or error.get("Code") in {
            "404",
            "NoSuchKey",
            "NotFound",
        }
    return False


def _precondition_failed(exc: Exception) -> bool:
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    if status in {409, 412}:
        return True
    response = getattr(exc, "response", {})
    if isinstance(response, Mapping):
        metadata = response.get("ResponseMetadata", {})
        error = response.get("Error", {})
        return metadata.get("HTTPStatusCode") in {409, 412} or error.get("Code") in {
            "409",
            "412",
            "ConditionalRequestConflict",
            "ObjectAlreadyExists",
            "PreconditionFailed",
        }
    return False


def _authorized_key(uri: str, *, scheme: str, bucket: str, expected_key: str) -> str:
    parsed = urlparse(uri)
    key = unquote(parsed.path.lstrip("/"))
    if (
        parsed.scheme != scheme
        or parsed.netloc != bucket
        or parsed.params
        or parsed.query
        or parsed.fragment
        or key != expected_key
        or "\\" in key
        or any(part in {"", ".", ".."} for part in key.split("/"))
    ):
        from hc_data_platform.core.errors import problem

        raise problem(
            status=422,
            code="OBJECT_STORAGE_REFERENCE_INVALID",
            title="Object storage reference is invalid",
            detail=(
                "Only an address in the configured bucket that exactly matches the "
                "expected immutable Raw key can be registered."
            ),
        )
    return key


def _object_reference_not_found() -> Exception:
    from hc_data_platform.core.errors import problem

    return problem(
        status=404,
        code="OBJECT_STORAGE_OBJECT_NOT_FOUND",
        title="Object storage object not found",
        detail="The authorized object does not exist.",
    )


def _immutable_object_exists() -> Exception:
    from hc_data_platform.core.errors import problem

    return problem(
        status=409,
        code="OBJECT_ALREADY_EXISTS",
        title="Immutable object already exists",
        detail="Raw and manifest objects cannot be overwritten.",
    )
