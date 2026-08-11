from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from typing import Any

import boto3
from botocore.client import Config

from app.core.errors import ServerError, ValidationError


class S3ObjectStorage:
    """S3-compatible adapter used with OSS in production and MinIO locally."""

    def __init__(
        self,
        *,
        endpoint_url: str | None = None,
        region_name: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        max_presign_ttl: int | None = None,
    ) -> None:
        self.max_presign_ttl = max_presign_ttl or int(os.getenv("S3_PRESIGN_TTL_SECONDS", "300"))
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or os.getenv("S3_ENDPOINT_URL"),
            region_name=region_name or os.getenv("S3_REGION", "cn-shanghai"),
            aws_access_key_id=access_key_id or os.getenv("S3_ACCESS_KEY_ID"),
            aws_secret_access_key=secret_access_key or os.getenv("S3_SECRET_ACCESS_KEY"),
            config=Config(signature_version="s3v4"),
        )

    def _ttl(self, requested: int) -> int:
        if requested < 1:
            raise ValidationError(code="INVALID_PRESIGN_TTL")
        return min(requested, self.max_presign_ttl, 900)

    async def presign_put(
        self,
        bucket: str,
        object_key: str,
        expires_in: int = 300,
        content_type: str | None = None,
    ) -> str:
        params: dict[str, Any] = {"Bucket": bucket, "Key": object_key}
        if content_type:
            params["ContentType"] = content_type
        return await asyncio.to_thread(
            self._client.generate_presigned_url,
            "put_object",
            Params=params,
            ExpiresIn=self._ttl(expires_in),
            HttpMethod="PUT",
        )

    async def presign_get(
        self,
        bucket: str,
        object_key: str,
        expires_in: int = 300,
        download_name: str | None = None,
    ) -> str:
        params: dict[str, Any] = {"Bucket": bucket, "Key": object_key}
        if download_name:
            safe_name = download_name.replace('"', "").replace("\r", "").replace("\n", "")
            params["ResponseContentDisposition"] = f'attachment; filename="{safe_name}"'
        return await asyncio.to_thread(
            self._client.generate_presigned_url,
            "get_object",
            Params=params,
            ExpiresIn=self._ttl(expires_in),
            HttpMethod="GET",
        )

    async def head(self, bucket: str, object_key: str) -> Mapping[str, Any] | None:
        try:
            response = await asyncio.to_thread(
                self._client.head_object,
                Bucket=bucket,
                Key=object_key,
            )
        except self._client.exceptions.NoSuchKey:
            return None
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise ServerError(code="OBJECT_STORAGE_UNAVAILABLE", retryable=True) from exc
        return {
            "size_bytes": int(response["ContentLength"]),
            "etag": str(response.get("ETag", "")).strip('"'),
            "content_type": response.get("ContentType"),
            "version_id": response.get("VersionId"),
            "metadata": dict(response.get("Metadata", {})),
        }
