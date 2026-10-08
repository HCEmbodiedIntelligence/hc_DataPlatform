"""Local and S3 implementations of the immutable artifact capabilities."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager, suppress
from pathlib import Path
from typing import Any


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
        with closing(response["Body"]) as body:
            size = int(response.get("ContentLength", -1))
            if size < 1 or size > max_bytes:
                raise ValueError("frame selection artifact size is invalid")
            content = bytes(body.read(max_bytes + 1))
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
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            with closing(response["Body"]) as body, path.open("wb") as target:
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
            descriptor, name = tempfile.mkstemp(prefix=".artifact-", dir=target.parent)
            os.close(descriptor)
            temporary = Path(name)
            try:
                shutil.copyfile(source, temporary)
                if _file_sha256(temporary) != sha256:
                    raise ValueError("projection artifact checksum mismatch")
                # A racing writer may win, but it must never be overwritten.
                with suppress(FileExistsError):
                    os.link(temporary, target)
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
                with suppress(FileExistsError):
                    os.link(name, target)
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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
