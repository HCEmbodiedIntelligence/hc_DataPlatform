"""Alibaba Cloud OSS adapters for the application's boto3-shaped object ports.

The domain adapters predate native OSS support and intentionally depend on a very
small, duck-typed subset of boto3.  This module translates that subset to ``oss2``
so production traffic uses OSS-native authentication and endpoints without an S3
compatibility gateway.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, NoReturn
from urllib.parse import urlparse


class OssClientError(RuntimeError):
    """Expose an OSS SDK failure in the shape expected by existing object ports."""

    def __init__(self, *, status: int, code: str, message: str) -> None:
        super().__init__(message or code)
        self.status = status
        self.response = {
            "ResponseMetadata": {"HTTPStatusCode": status},
            "Error": {"Code": code, "Message": message},
        }


def build_oss_bucket(
    *,
    endpoint: str,
    bucket: str,
    access_key: str,
    secret_key: str,
    connect_timeout: float | None = None,
    is_cname: bool | None = None,
) -> Any:
    """Build an ``oss2.Bucket`` without importing the optional SDK at module load."""

    oss2 = importlib.import_module("oss2")
    auth = oss2.Auth(access_key, secret_key)
    options: dict[str, object] = {}
    if connect_timeout is not None:
        options["connect_timeout"] = connect_timeout
    hostname = (urlparse(endpoint).hostname or "").rstrip(".").lower()
    options["is_cname"] = not hostname.endswith(".aliyuncs.com") if is_cname is None else is_cname
    return oss2.Bucket(auth, endpoint, bucket, **options)


class OssBotoCompatClient:
    """Translate the bounded boto3 client surface used by platform object adapters."""

    def __init__(self, bucket: Any) -> None:
        self._bucket = bucket
        self._bucket_name = str(getattr(bucket, "bucket_name", ""))
        if not self._bucket_name:
            raise ValueError("OSS bucket client does not expose a bucket name")

    def put_object(self, **kwargs: Any) -> dict[str, object]:
        key = self._arguments(kwargs)
        headers = _put_headers(kwargs)
        try:
            result = self._bucket.put_object(key, kwargs["Body"], headers=headers)
        except Exception as exc:
            _raise_compatible(exc)
        return {"ETag": _quoted_etag(getattr(result, "etag", None))}

    def head_object(self, **kwargs: Any) -> dict[str, object]:
        key = self._arguments(kwargs)
        try:
            result = self._bucket.get_object_meta(key)
        except Exception as exc:
            _raise_compatible(exc)
        headers = getattr(result, "headers", {})
        size = getattr(result, "content_length", None)
        if size is None:
            size = _header(headers, "content-length")
        if size is None:
            raise RuntimeError("OSS object metadata omitted content-length")
        modified = getattr(result, "last_modified", None)
        return {
            "ContentLength": int(str(size)),
            "ETag": _quoted_etag(getattr(result, "etag", None) or _header(headers, "etag")),
            "Metadata": _user_metadata(headers),
            "LastModified": _utc_datetime(modified),
        }

    def get_object(self, **kwargs: Any) -> dict[str, object]:
        key = self._arguments(kwargs)
        try:
            result = self._bucket.get_object(key)
        except Exception as exc:
            _raise_compatible(exc)
        size = getattr(result, "content_length", None)
        if size is None:
            size = _header(getattr(result, "headers", {}), "content-length")
        response: dict[str, object] = {"Body": result}
        if size is not None:
            response["ContentLength"] = int(str(size))
        return response

    def delete_object(self, **kwargs: Any) -> dict[str, object]:
        key = self._arguments(kwargs)
        try:
            result = self._bucket.delete_object(key)
        except Exception as exc:
            _raise_compatible(exc)
        return {"RequestId": str(getattr(result, "request_id", ""))}

    def copy_object(self, **kwargs: Any) -> dict[str, object]:
        key = self._arguments(kwargs)
        source = kwargs.get("CopySource")
        if not isinstance(source, Mapping):
            raise TypeError("CopySource must identify one bucket and key")
        source_bucket = str(source.get("Bucket", ""))
        source_key = str(source.get("Key", ""))
        if not source_bucket or not source_key:
            raise ValueError("CopySource bucket and key are required")
        headers: dict[str, str] = {}
        if kwargs.get("CopySourceIfMatch"):
            headers["x-oss-copy-source-if-match"] = str(kwargs["CopySourceIfMatch"])
        if kwargs.get("MetadataDirective"):
            headers["x-oss-metadata-directive"] = str(kwargs["MetadataDirective"])
        try:
            result = self._bucket.copy_object(
                source_bucket,
                source_key,
                key,
                headers=headers or None,
            )
        except Exception as exc:
            _raise_compatible(exc)
        return {"CopyObjectResult": {"ETag": _quoted_etag(getattr(result, "etag", None))}}

    def list_objects_v2(self, **kwargs: Any) -> dict[str, object]:
        self._arguments(kwargs, require_key=False)
        try:
            result = self._bucket.list_objects_v2(
                prefix=str(kwargs.get("Prefix", "")),
                delimiter=str(kwargs.get("Delimiter", "")),
                continuation_token=str(kwargs.get("ContinuationToken", "")),
                start_after=str(kwargs.get("StartAfter", "")),
                max_keys=int(kwargs.get("MaxKeys", 1_000)),
            )
        except Exception as exc:
            _raise_compatible(exc)
        contents = [
            {
                "Key": str(item.key),
                "Size": int(item.size),
                "ETag": _quoted_etag(getattr(item, "etag", None)),
                "LastModified": _utc_datetime(getattr(item, "last_modified", None)),
            }
            for item in getattr(result, "object_list", ())
        ]
        return {
            "Contents": contents,
            "IsTruncated": bool(getattr(result, "is_truncated", False)),
            "NextContinuationToken": str(getattr(result, "next_continuation_token", "") or ""),
        }

    def generate_presigned_url(
        self,
        client_method: str,
        *,
        Params: Mapping[str, object],
        ExpiresIn: int,
        HttpMethod: str | None = None,
    ) -> str:
        key = self._arguments(Params)
        methods = {"get_object": "GET", "upload_part": "PUT"}
        try:
            method = HttpMethod or methods[client_method]
        except KeyError as exc:
            raise ValueError(f"unsupported OSS presign operation: {client_method}") from exc
        params: dict[str, str] = {}
        mappings = {
            "UploadId": "uploadId",
            "PartNumber": "partNumber",
            "ResponseCacheControl": "response-cache-control",
            "ResponseContentDisposition": "response-content-disposition",
            "ResponseContentType": "response-content-type",
        }
        for source, target in mappings.items():
            value = Params.get(source)
            if value is not None:
                params[target] = str(value)
        return str(
            self._bucket.sign_url(
                method,
                key,
                ExpiresIn,
                params=params or None,
                slash_safe=True,
            )
        )

    def create_multipart_upload(self, **kwargs: Any) -> dict[str, str]:
        key = self._arguments(kwargs)
        headers = _put_headers(kwargs)
        try:
            result = self._bucket.init_multipart_upload(key, headers=headers)
        except Exception as exc:
            _raise_compatible(exc)
        return {"UploadId": str(result.upload_id)}

    def list_parts(self, **kwargs: Any) -> dict[str, object]:
        key = self._arguments(kwargs)
        try:
            result = self._bucket.list_parts(
                key,
                str(kwargs["UploadId"]),
                marker=str(kwargs.get("PartNumberMarker", "")),
                max_parts=int(kwargs.get("MaxParts", 1_000)),
            )
        except Exception as exc:
            _raise_compatible(exc)
        parts = [
            {
                "PartNumber": int(item.part_number),
                "ETag": _quoted_etag(item.etag),
                "Size": int(item.size),
            }
            for item in getattr(result, "parts", ())
        ]
        return {
            "Parts": parts,
            "IsTruncated": bool(getattr(result, "is_truncated", False)),
            "NextPartNumberMarker": int(getattr(result, "next_part_number_marker", 0) or 0),
        }

    def complete_multipart_upload(self, **kwargs: Any) -> dict[str, object]:
        key = self._arguments(kwargs)
        models = importlib.import_module("oss2.models")
        raw_parts = kwargs.get("MultipartUpload", {}).get("Parts", ())
        parts = [
            models.PartInfo(int(item["PartNumber"]), str(item["ETag"]).strip('"'))
            for item in raw_parts
        ]
        headers = _put_headers(kwargs)
        try:
            result = self._bucket.complete_multipart_upload(
                key,
                str(kwargs["UploadId"]),
                parts,
                headers=headers,
            )
        except Exception as exc:
            _raise_compatible(exc)
        return {"ETag": _quoted_etag(getattr(result, "etag", None))}

    def abort_multipart_upload(self, **kwargs: Any) -> dict[str, object]:
        key = self._arguments(kwargs)
        try:
            result = self._bucket.abort_multipart_upload(key, str(kwargs["UploadId"]))
        except Exception as exc:
            _raise_compatible(exc)
        return {"RequestId": str(getattr(result, "request_id", ""))}

    def head_bucket(self, **kwargs: Any) -> dict[str, object]:
        self._arguments(kwargs, require_key=False)
        try:
            result = self._bucket.get_bucket_info()
        except Exception as exc:
            _raise_compatible(exc)
        return {"RequestId": str(getattr(result, "request_id", ""))}

    def _arguments(
        self,
        values: Mapping[str, object],
        *,
        require_key: bool = True,
    ) -> str:
        bucket = str(values.get("Bucket", ""))
        if bucket != self._bucket_name:
            raise ValueError("object operation targeted a different OSS bucket")
        key = str(values.get("Key", ""))
        if require_key and not key:
            raise ValueError("object key is required")
        return key


def _put_headers(values: Mapping[str, object]) -> dict[str, str] | None:
    headers: dict[str, str] = {}
    if values.get("ContentType"):
        headers["Content-Type"] = str(values["ContentType"])
    metadata = values.get("Metadata")
    if isinstance(metadata, Mapping):
        for key, value in metadata.items():
            headers[f"x-oss-meta-{key}"] = str(value)
    if values.get("IfNoneMatch") == "*":
        headers["x-oss-forbid-overwrite"] = "true"
    return headers or None


def _user_metadata(headers: Mapping[str, object]) -> dict[str, str]:
    prefix = "x-oss-meta-"
    return {
        str(key)[len(prefix) :]: str(value)
        for key, value in headers.items()
        if str(key).lower().startswith(prefix)
    }


def _header(headers: Mapping[str, object], name: str) -> object | None:
    normalized = name.casefold()
    return next(
        (value for key, value in headers.items() if str(key).casefold() == normalized),
        None,
    )


def _quoted_etag(value: object) -> str:
    normalized = str(value or "").strip('"')
    return f'"{normalized}"' if normalized else ""


def _utc_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    return datetime.fromtimestamp(0, tz=timezone.utc)


def _raise_compatible(exc: Exception) -> NoReturn:
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    if not isinstance(status, int):
        raise exc
    raw_code = str(getattr(exc, "code", "") or status)
    if status == 404:
        code = "NoSuchKey"
    elif status in {409, 412}:
        code = "PreconditionFailed"
    else:
        code = raw_code
    raise OssClientError(status=status, code=code, message=str(exc)) from exc
