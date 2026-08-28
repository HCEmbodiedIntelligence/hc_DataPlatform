from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from datetime import timedelta
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import quote, unquote, urlparse

from .metrics import PREVIEW_OBJECT_GET
from .models import (
    EncodedPreviewArtifactV1,
    PreviewObjectV1,
    PublishedPreviewArtifactV1,
)

_SAFE_ARTIFACT_KEY = re.compile(r"^[0-9a-f]{64}$")
_SAFE_PROJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SAFE_MEMBER = re.compile(r"^(?:index\.m3u8|init\.mp4|segment_[0-9]{5,}\.m4s)$")


class S3PreviewClient(Protocol):
    def put_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def head_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def delete_object(self, **kwargs: object) -> Mapping[str, object]: ...


class S3PresignClient(Protocol):
    def generate_presigned_url(
        self, operation_name: str, *, Params: Mapping[str, str], ExpiresIn: int
    ) -> str: ...


def _source_directory(encoded: EncodedPreviewArtifactV1) -> Path:
    parsed = urlparse(encoded.artifact_uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise ValueError("the media encoder must return a local file URI")
    playlist = Path(unquote(parsed.path)).resolve()
    if playlist.name != "index.m3u8" or not playlist.is_file():
        raise ValueError("the encoded preview has no committed index.m3u8")
    return playlist.parent


def _artifact_members(directory: Path) -> list[Path]:
    members = [path for path in directory.iterdir() if path.is_file()]
    if not members or not any(path.name == "index.m3u8" for path in members):
        raise ValueError("the encoded preview has no playlist")
    if any(_SAFE_MEMBER.fullmatch(path.name) is None for path in members):
        raise ValueError("the encoded preview contains an undeclared file")
    # The playlist is the publication barrier and is uploaded last.
    return sorted(members, key=lambda path: (path.name == "index.m3u8", path.name))


class S3PreviewArtifactStore:
    """Immutable S3/MinIO artifact store with exact-object publication receipts."""

    def __init__(
        self,
        client: S3PreviewClient,
        bucket: str,
        *,
        presign_client: S3PresignClient | None = None,
        prefix: str = "derived/previews",
        max_playlist_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        self._client = client
        self._presign = presign_client or cast(S3PresignClient, client)
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._max_playlist_bytes = max_playlist_bytes

    def publish(
        self,
        *,
        project_id: str,
        artifact_key: str,
        encoded: EncodedPreviewArtifactV1,
    ) -> PublishedPreviewArtifactV1:
        if _SAFE_PROJECT.fullmatch(project_id) is None:
            raise ValueError("project_id is unsafe for a preview object key")
        if _SAFE_ARTIFACT_KEY.fullmatch(artifact_key) is None:
            raise ValueError("artifact_key must be a lowercase SHA-256 digest")
        directory = _source_directory(encoded)
        prefix = f"{self._prefix}/{project_id}/{artifact_key}"
        records: list[PreviewObjectV1] = []
        newly_created: list[str] = []
        content = hashlib.sha256()
        try:
            for member in _artifact_members(directory):
                body = member.read_bytes()
                digest = hashlib.sha256(body).hexdigest()
                key = f"{prefix}/{member.name}"
                created, etag = self._put_immutable(
                    key,
                    body,
                    sha256=digest,
                    media_type=_media_type(member.name),
                )
                if created:
                    newly_created.append(key)
                self._verify(key, size=len(body), sha256=digest)
                content.update(member.name.encode())
                content.update(b"\0")
                content.update(body)
                records.append(
                    PreviewObjectV1(
                        key=key,
                        size=len(body),
                        sha256=digest,
                        etag=etag,
                        media_type=_media_type(member.name),
                    )
                )
        except BaseException:
            # Never use a prefix delete. Only members created by this failed attempt
            # are removed; idempotently pre-existing members may be in active use.
            for key in reversed(newly_created):
                with suppress(Exception):
                    self._client.delete_object(Bucket=self._bucket, Key=key)
            raise
        return PublishedPreviewArtifactV1(
            object_prefix=prefix,
            playlist_key=f"{prefix}/index.m3u8",
            objects=tuple(records),
            total_bytes=sum(item.size for item in records),
            content_sha256=content.hexdigest(),
            etag=next((item.etag for item in records if item.key.endswith("/index.m3u8")), None),
        )

    def read_playlist(self, playlist_key: str) -> str:
        PREVIEW_OBJECT_GET.inc()
        response = self._client.get_object(Bucket=self._bucket, Key=playlist_key)
        size = _as_int(response.get("ContentLength", 0))
        if size < 1 or size > self._max_playlist_bytes:
            raise ValueError("preview playlist size is invalid")
        body = response.get("Body")
        if body is None or not hasattr(body, "read"):
            raise TypeError("S3 playlist response has no readable body")
        try:
            content = body.read(self._max_playlist_bytes + 1)
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
        if not isinstance(content, bytes) or len(content) != size:
            raise ValueError("preview playlist read was incomplete")
        return content.decode("utf-8")

    def authorize_object(self, object_key: str, *, expires_in: timedelta) -> str:
        return self._presign.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": object_key},
            ExpiresIn=max(1, int(expires_in.total_seconds())),
        )

    def delete_exact(self, objects: Sequence[PreviewObjectV1]) -> int:
        deleted = 0
        for member in objects:
            self._client.delete_object(Bucket=self._bucket, Key=member.key)
            deleted += member.size
        return deleted

    def resolve_local_object(self, object_key: str) -> Path | None:
        del object_key
        return None

    def _put_immutable(
        self, key: str, body: bytes, *, sha256: str, media_type: str
    ) -> tuple[bool, str | None]:
        try:
            result = self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType=media_type,
                Metadata={"sha256": sha256},
                IfNoneMatch="*",
            )
        except Exception as exc:
            if not _is_precondition_failure(exc):
                raise
            head = self._client.head_object(Bucket=self._bucket, Key=key)
            metadata = head.get("Metadata")
            if (
                _as_int(head.get("ContentLength", -1)) != len(body)
                or not isinstance(metadata, Mapping)
                or metadata.get("sha256") != sha256
            ):
                raise RuntimeError("immutable preview object collision") from exc
            return False, _etag(head)
        return True, _etag(result)

    def _verify(self, key: str, *, size: int, sha256: str) -> None:
        head = self._client.head_object(Bucket=self._bucket, Key=key)
        metadata = head.get("Metadata")
        if (
            _as_int(head.get("ContentLength", -1)) != size
            or not isinstance(metadata, Mapping)
            or metadata.get("sha256") != sha256
        ):
            raise RuntimeError("preview object verification failed")


class LocalPreviewArtifactStore:
    """Explicit local-development adapter; production composition uses S3."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def publish(
        self,
        *,
        project_id: str,
        artifact_key: str,
        encoded: EncodedPreviewArtifactV1,
    ) -> PublishedPreviewArtifactV1:
        if _SAFE_PROJECT.fullmatch(project_id) is None or _SAFE_ARTIFACT_KEY.fullmatch(
            artifact_key
        ) is None:
            raise ValueError("unsafe local preview identity")
        source = _source_directory(encoded)
        prefix = f"derived/previews/{project_id}/{artifact_key}"
        target = self._root / prefix
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary = Path(tempfile.mkdtemp(prefix=f".{artifact_key}.", dir=target.parent))
            try:
                for member in _artifact_members(source):
                    shutil.copyfile(member, temporary / member.name)
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        records: list[PreviewObjectV1] = []
        content = hashlib.sha256()
        for member in _artifact_members(target):
            body = member.read_bytes()
            digest = hashlib.sha256(body).hexdigest()
            key = f"{prefix}/{member.name}"
            content.update(member.name.encode())
            content.update(b"\0")
            content.update(body)
            records.append(
                PreviewObjectV1(
                    key=key,
                    size=len(body),
                    sha256=digest,
                    etag=digest,
                    media_type=_media_type(member.name),
                )
            )
        return PublishedPreviewArtifactV1(
            object_prefix=prefix,
            playlist_key=f"{prefix}/index.m3u8",
            objects=tuple(records),
            total_bytes=sum(item.size for item in records),
            content_sha256=content.hexdigest(),
        )

    def read_playlist(self, playlist_key: str) -> str:
        return self._path(playlist_key).read_text(encoding="utf-8")

    def authorize_object(self, object_key: str, *, expires_in: timedelta) -> str:
        del expires_in
        return f"/api/v1/previews/local-media/{quote(object_key, safe='')}"

    def delete_exact(self, objects: Sequence[PreviewObjectV1]) -> int:
        deleted = 0
        parents: set[Path] = set()
        for member in objects:
            path = self._path(member.key)
            parents.add(path.parent)
            if path.exists():
                path.unlink()
                deleted += member.size
        for parent in sorted(parents, key=lambda item: len(item.parts), reverse=True):
            with suppress(OSError):
                parent.rmdir()
        return deleted

    def resolve_local_object(self, object_key: str) -> Path | None:
        path = self._path(object_key)
        return path if path.is_file() else None

    def _path(self, key: str) -> Path:
        candidate = (self._root / key).resolve()
        if self._root not in candidate.parents:
            raise FileNotFoundError
        return candidate


def _media_type(name: str) -> str:
    if name == "index.m3u8":
        return "application/vnd.apple.mpegurl"
    if name == "init.mp4":
        return "video/mp4"
    if name.endswith(".m4s"):
        return "video/iso.segment"
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def _etag(response: Mapping[str, object]) -> str | None:
    value = response.get("ETag")
    return value.strip('"') if isinstance(value, str) else None


def _is_precondition_failure(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, Mapping):
        return False
    error = response.get("Error")
    if not isinstance(error, Mapping):
        return False
    return str(error.get("Code", "")) in {"PreconditionFailed", "412"}


def _as_int(value: object) -> int:
    if isinstance(value, (int, str)):
        return int(value)
    raise TypeError("object-store value is not an integer")
