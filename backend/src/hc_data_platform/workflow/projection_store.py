"""Immutable short-lived Arrow projection artifacts shared by ingest activities."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol


class ProjectionArtifactStorePort(Protocol):
    def publish_file(self, key: str, source: Path, *, sha256: str) -> int: ...

    def publish_json(self, key: str, value: Mapping[str, object]) -> tuple[str, int]: ...

    def read_json(
        self, key: str, *, expected_sha256: str, max_bytes: int = 8 * 1024 * 1024
    ) -> dict[str, object]: ...

    def local_file(self, key: str, *, expected_sha256: str, expected_size: int) -> Any: ...

    def delete(self, key: str) -> None: ...


class S3ProjectionArtifactStore:
    """S3/MinIO store that streams Arrow files and verifies immutable metadata."""

    def __init__(self, client: Any, bucket: str, staging_root: Path) -> None:
        self._client = client
        self._bucket = bucket
        self._staging_root = staging_root.resolve()

    def publish_file(self, key: str, source: Path, *, sha256: str) -> int:
        size = source.stat().st_size
        try:
            with source.open("rb") as body:
                self._client.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=body,
                    ContentType="application/vnd.apache.arrow.file",
                    Metadata={"sha256": sha256},
                    IfNoneMatch="*",
                )
        except Exception as exc:
            if not self._matches(key, size=size, sha256=sha256):
                raise RuntimeError("immutable projection artifact collision") from exc
        if not self._matches(key, size=size, sha256=sha256):
            raise RuntimeError("projection artifact verification failed")
        return size

    def publish_json(self, key: str, value: Mapping[str, object]) -> tuple[str, int]:
        body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        sha256 = hashlib.sha256(body).hexdigest()
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType="application/json",
                Metadata={"sha256": sha256},
                IfNoneMatch="*",
            )
        except Exception as exc:
            if not self._matches(key, size=len(body), sha256=sha256):
                raise RuntimeError("immutable frame selection collision") from exc
        if not self._matches(key, size=len(body), sha256=sha256):
            raise RuntimeError("frame selection verification failed")
        return sha256, len(body)

    def read_json(
        self, key: str, *, expected_sha256: str, max_bytes: int = 8 * 1024 * 1024
    ) -> dict[str, object]:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        size = int(response.get("ContentLength", -1))
        if size < 1 or size > max_bytes:
            raise ValueError("frame selection artifact size is invalid")
        body = response["Body"]
        try:
            content = bytes(body.read(max_bytes + 1))
        finally:
            body.close()
        if len(content) != size or hashlib.sha256(content).hexdigest() != expected_sha256:
            raise ValueError("frame selection artifact checksum mismatch")
        value = json.loads(content)
        if not isinstance(value, dict):
            raise ValueError("frame selection artifact must be an object")
        return value

    @contextmanager
    def local_file(self, key: str, *, expected_sha256: str, expected_size: int) -> Iterator[Path]:
        self._staging_root.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            prefix="projection-read-", suffix=".arrow", dir=self._staging_root
        )
        os.close(descriptor)
        path = Path(name)
        digest = hashlib.sha256()
        written = 0
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        body = response["Body"]
        try:
            with path.open("wb") as target:
                while True:
                    chunk = body.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes):
                        raise TypeError("projection object stream returned non-bytes")
                    written += len(chunk)
                    if written > expected_size:
                        raise ValueError("projection artifact exceeded its manifest size")
                    digest.update(chunk)
                    target.write(chunk)
        finally:
            body.close()
        try:
            if written != expected_size or digest.hexdigest() != expected_sha256:
                raise ValueError("projection artifact checksum mismatch")
            yield path
        finally:
            with suppress(OSError):
                path.unlink()

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def _matches(self, key: str, *, size: int, sha256: str) -> bool:
        try:
            head = self._client.head_object(Bucket=self._bucket, Key=key)
        except Exception:
            return False
        metadata = head.get("Metadata")
        return (
            int(head.get("ContentLength", -1)) == size
            and isinstance(metadata, Mapping)
            and metadata.get("sha256") == sha256
        )


class LocalProjectionArtifactStore:
    """Explicit local/test adapter with the same immutable and exact-delete rules."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def publish_file(self, key: str, source: Path, *, sha256: str) -> int:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary = target.with_name(f".{target.name}.{os.getpid()}.part")
            try:
                shutil.copyfile(source, temporary)
                os.replace(temporary, target)
            finally:
                with suppress(OSError):
                    temporary.unlink()
        content_sha = _file_sha256(target)
        if content_sha != sha256:
            raise RuntimeError("immutable projection artifact collision")
        return target.stat().st_size

    def publish_json(self, key: str, value: Mapping[str, object]) -> tuple[str, int]:
        body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        sha256 = hashlib.sha256(body).hexdigest()
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            descriptor, name = tempfile.mkstemp(prefix=".selection-", dir=target.parent)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(body)
                os.replace(name, target)
            finally:
                with suppress(OSError):
                    Path(name).unlink()
        if target.read_bytes() != body:
            raise RuntimeError("immutable frame selection collision")
        return sha256, len(body)

    def read_json(
        self, key: str, *, expected_sha256: str, max_bytes: int = 8 * 1024 * 1024
    ) -> dict[str, object]:
        path = self._path(key)
        if path.stat().st_size > max_bytes:
            raise ValueError("frame selection artifact is too large")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != expected_sha256:
            raise ValueError("frame selection artifact checksum mismatch")
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError("frame selection artifact must be an object")
        return value

    @contextmanager
    def local_file(self, key: str, *, expected_sha256: str, expected_size: int) -> Iterator[Path]:
        path = self._path(key)
        if path.stat().st_size != expected_size or _file_sha256(path) != expected_sha256:
            raise ValueError("projection artifact checksum mismatch")
        yield path

    def delete(self, key: str) -> None:
        path = self._path(key)
        with suppress(FileNotFoundError):
            path.unlink()
        with suppress(OSError):
            path.parent.rmdir()

    def _path(self, key: str) -> Path:
        candidate = (self._root / key).resolve()
        if self._root not in candidate.parents:
            raise ValueError("projection artifact key escapes its store")
        return candidate


class S3ProjectionStagingSweeper:
    """Delete expired projection/alignment Arrow keys by exact object key."""

    _PREFIXES = ("staging/projections/", "staging/alignment/")

    def __init__(
        self,
        client: Any,
        bucket: str,
        *,
        ttl: timedelta,
        clock: Any = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not bucket or ttl <= timedelta(0):
            raise ValueError("projection staging bucket and positive TTL are required")
        self._client = client
        self._bucket = bucket
        self._ttl = ttl
        self._clock = clock

    def run_once(self) -> int:
        cutoff = self._clock() - self._ttl
        deleted = 0
        for prefix in self._PREFIXES:
            deleted += self._sweep_prefix(prefix, cutoff)
        return deleted

    def _sweep_prefix(self, prefix: str, cutoff: datetime) -> int:
        deleted = 0
        continuation: str | None = None
        while True:
            parameters: dict[str, object] = {
                "Bucket": self._bucket,
                "Prefix": prefix,
                "MaxKeys": 1_000,
            }
            if continuation is not None:
                parameters["ContinuationToken"] = continuation
            response = self._client.list_objects_v2(**parameters)
            contents = response.get("Contents", ())
            if not isinstance(contents, (list, tuple)):
                raise TypeError("projection staging listing returned invalid Contents")
            for item in contents:
                if not isinstance(item, Mapping):
                    continue
                key = item.get("Key")
                modified = item.get("LastModified")
                if (
                    not isinstance(key, str)
                    or not key.startswith(prefix)
                    or not key.endswith(".arrow")
                    or not isinstance(modified, datetime)
                ):
                    continue
                if modified.tzinfo is None:
                    modified = modified.replace(tzinfo=timezone.utc)
                if modified > cutoff:
                    continue
                self._client.delete_object(Bucket=self._bucket, Key=key)
                deleted += 1
            if not response.get("IsTruncated"):
                return deleted
            token = response.get("NextContinuationToken")
            if not isinstance(token, str) or not token:
                raise RuntimeError("projection staging listing omitted its continuation token")
            continuation = token


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
