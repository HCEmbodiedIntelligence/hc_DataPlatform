"""Provider operations used by P12/P13 recoverable object workflows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Any, NoReturn, Protocol


@dataclass(frozen=True, slots=True)
class StoredObjectMetadata:
    size: int
    etag: str


class StorageObjectOperator(Protocol):
    def head(self, key: str) -> StoredObjectMetadata | None: ...

    def copy(self, source_key: str, destination_key: str) -> StoredObjectMetadata: ...

    def delete(self, key: str) -> None: ...

    def presign_read(self, key: str, expires_seconds: int) -> str: ...

    def abort_multipart(self, key: str, upload_id: str) -> None: ...


class StorageObjectOperatorNotConfigured(RuntimeError):
    pass


class DisabledStorageObjectOperator:
    def _disabled(self) -> NoReturn:
        raise StorageObjectOperatorNotConfigured("storage object operations are not configured")

    def head(self, key: str) -> StoredObjectMetadata | None:
        del key
        self._disabled()

    def copy(self, source_key: str, destination_key: str) -> StoredObjectMetadata:
        del source_key, destination_key
        self._disabled()

    def delete(self, key: str) -> None:
        del key
        self._disabled()

    def presign_read(self, key: str, expires_seconds: int) -> str:
        del key, expires_seconds
        self._disabled()

    def abort_multipart(self, key: str, upload_id: str) -> None:
        del key, upload_id
        self._disabled()


class S3StorageObjectOperator:
    """Small boto3 adapter; bucket/key values remain behind the service boundary."""

    def __init__(
        self,
        client: Any,
        bucket: str,
        *,
        presign_client: Any | None = None,
    ) -> None:
        self._client = client
        self._presign_client = presign_client or client
        self._bucket = bucket

    def head(self, key: str) -> StoredObjectMetadata | None:
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            response = getattr(exc, "response", None)
            status = (
                response.get("ResponseMetadata", {}).get("HTTPStatusCode") if response else None
            )
            code = response.get("Error", {}).get("Code") if response else None
            if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return StoredObjectMetadata(
            size=int(response["ContentLength"]),
            etag=str(response.get("ETag", "")).strip('"'),
        )

    def copy(self, source_key: str, destination_key: str) -> StoredObjectMetadata:
        source = self.head(source_key)
        if source is None:
            raise FileNotFoundError("source object is not present")
        destination = self.head(destination_key)
        if destination is None:
            self._client.copy_object(
                Bucket=self._bucket,
                Key=destination_key,
                CopySource={"Bucket": self._bucket, "Key": source_key},
                CopySourceIfMatch=source.etag,
                MetadataDirective="COPY",
            )
            destination = self.head(destination_key)
        if destination is None or destination.size != source.size:
            raise RuntimeError("copied object did not preserve its physical byte length")
        return destination

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def presign_read(self, key: str, expires_seconds: int) -> str:
        if expires_seconds < 1:
            raise ValueError("expires_seconds must be positive")
        if self.head(key) is None:
            raise FileNotFoundError("object is not present")
        return str(
            self._presign_client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": key,
                    "ResponseCacheControl": "no-store",
                    "ResponseContentDisposition": "attachment",
                },
                ExpiresIn=expires_seconds,
                HttpMethod="GET",
            )
        )

    def abort_multipart(self, key: str, upload_id: str) -> None:
        self._client.abort_multipart_upload(
            Bucket=self._bucket,
            Key=key,
            UploadId=upload_id,
        )


class InMemoryStorageObjectOperator:
    """Deterministic object port for service tests, including replay assertions."""

    def __init__(self, *, clock_url: Callable[[str, int], str] | None = None) -> None:
        self.objects: dict[str, bytes] = {}
        self.aborted: set[tuple[str, str]] = set()
        self._clock_url = clock_url or (
            lambda key, expires: f"https://objects.invalid/{key}?expires={expires}"
        )
        self._lock = RLock()

    def put(self, key: str, body: bytes) -> None:
        with self._lock:
            self.objects[key] = body

    def head(self, key: str) -> StoredObjectMetadata | None:
        with self._lock:
            body = self.objects.get(key)
        if body is None:
            return None
        return StoredObjectMetadata(size=len(body), etag=f"memory-{len(body)}")

    def copy(self, source_key: str, destination_key: str) -> StoredObjectMetadata:
        with self._lock:
            body = self.objects.get(source_key)
            if body is None:
                raise FileNotFoundError("source object is not present")
            existing = self.objects.get(destination_key)
            if existing is not None and existing != body:
                raise FileExistsError("destination object already contains different bytes")
            self.objects[destination_key] = body
        metadata = self.head(destination_key)
        assert metadata is not None
        return metadata

    def delete(self, key: str) -> None:
        with self._lock:
            self.objects.pop(key, None)

    def presign_read(self, key: str, expires_seconds: int) -> str:
        if self.head(key) is None:
            raise FileNotFoundError("object is not present")
        return self._clock_url(key, expires_seconds)

    def abort_multipart(self, key: str, upload_id: str) -> None:
        with self._lock:
            self.aborted.add((key, upload_id))
