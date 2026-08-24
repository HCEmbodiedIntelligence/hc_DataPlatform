"""Immutable S3/MinIO staging, promotion, and download adapter."""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import quote

from hc_data_platform.core.errors import problem


class S3ArtifactSink:
    def __init__(
        self,
        client: Any,
        bucket: str,
        *,
        prefix: str = "artifacts",
        presign_client: Any | None = None,
    ) -> None:
        self._client = client
        self._presign_client = presign_client or client
        self._bucket = bucket
        self._prefix = prefix.strip("/")

    def put_immutable(self, artifact_uri: str, content: bytes) -> None:
        key = self._key(artifact_uri)
        existing = self._get(key)
        if existing is not None:
            if existing != content:
                raise self._immutable_error("PUBLICATION_ASSET_IMMUTABLE")
            return
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=content,
                IfNoneMatch="*",
            )
        except Exception:
            existing = self._get(key)
            if existing != content:
                raise

    def stage_attempt(self, attempt_id: str, content: bytes) -> str:
        uri = f"attempt-staging/{quote(attempt_id, safe='')}/artifact"
        key = self._key(uri)
        existing = self._get(key)
        if existing is not None and existing != content:
            raise self._immutable_error("EXPORT_ATTEMPT_IMMUTABLE")
        if existing is None:
            self._client.put_object(Bucket=self._bucket, Key=key, Body=content, IfNoneMatch="*")
        return uri

    def read_attempt(self, attempt_id: str) -> bytes:
        key = self._key(f"attempt-staging/{quote(attempt_id, safe='')}/artifact")
        content = self._get(key)
        if content is None:
            raise problem(
                status=409,
                code="EXPORT_ATTEMPT_NOT_FOUND",
                title="Export attempt not found",
                detail="No staged bytes exist for this export attempt.",
            )
        return content

    def publish_attempt(self, *, attempt_id: str, artifact_uri: str, expected_sha256: str) -> str:
        content = self.read_attempt(attempt_id)
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise problem(
                status=409,
                code="EXPORT_STAGING_HASH_MISMATCH",
                title="Export staging hash mismatch",
                detail="The staged artifact changed before promotion.",
            )
        self.put_immutable(artifact_uri, content)
        return str(
            self._presign_client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": self._key(artifact_uri),
                    "ResponseContentDisposition": "attachment",
                },
                ExpiresIn=900,
                HttpMethod="GET",
            )
        )

    def get_published(self, artifact_uri: str) -> bytes | None:
        return self._get(self._key(artifact_uri))

    def get_download_uri(self, artifact_uri: str) -> str | None:
        if self.get_published(artifact_uri) is None:
            return None
        return str(
            self._presign_client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": self._key(artifact_uri),
                    "ResponseContentDisposition": "attachment",
                },
                ExpiresIn=900,
                HttpMethod="GET",
            )
        )

    def _key(self, uri: str) -> str:
        return f"{self._prefix}/{uri.lstrip('/')}"

    def _get(self, key: str) -> bytes | None:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            metadata = getattr(exc, "response", {}).get("ResponseMetadata", {})
            if metadata.get("HTTPStatusCode") == 404:
                return None
            error = getattr(exc, "response", {}).get("Error", {})
            if error.get("Code") in {"NoSuchKey", "NotFound", "404"}:
                return None
            raise
        body = response["Body"]
        try:
            return bytes(body.read())
        finally:
            body.close()

    @staticmethod
    def _immutable_error(code: str) -> Exception:
        return problem(
            status=409,
            code=code,
            title="Artifact is immutable",
            detail="The target already contains different bytes.",
        )
