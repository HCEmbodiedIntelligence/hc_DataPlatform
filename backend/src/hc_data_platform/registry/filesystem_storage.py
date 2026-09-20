"""Server-local multipart staging for PostgreSQL-backed robot-model assets.

Robot definitions are platform master data, not dataset objects. Browser uploads are
assembled here temporarily, verified, copied into PostgreSQL, and then removed. The
directory is not an authoritative store and is not required in a server backup.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from threading import RLock
from typing import Any, BinaryIO
from urllib.parse import quote, unquote, urlparse
from uuid import uuid4

from hc_data_platform.core.context import retain_current_writer_permit
from hc_data_platform.core.errors import problem
from hc_data_platform.core.pagination import CursorCodec
from hc_data_platform.ingest.models import CompletedPart
from hc_data_platform.ingest.ports import (
    MultipartPart,
    ObjectMetadata,
    crc64_ecma,
    validate_completion_parts,
)

_UPLOAD_TOKEN_KIND = "robot-model-asset-upload/v1"
_DOWNLOAD_TOKEN_KIND = "robot-model-asset-download/v1"


class FilesystemRobotModelStorage:
    """Temporary multipart staging rooted in one server-owned directory."""

    def __init__(
        self,
        root: str | Path,
        *,
        signing_secret: str,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._root = Path(root).resolve()
        self._multipart_root = self._root / ".multipart"
        self._codec = CursorCodec(signing_secret)
        self._clock = clock
        self._lock = RLock()
        self._root.mkdir(parents=True, exist_ok=True)
        self._multipart_root.mkdir(parents=True, exist_ok=True)

    def create_multipart(self, key: str) -> str:
        if self.head(key) is not None:
            raise _immutable_object_exists()
        for _attempt in range(8):
            upload_id = str(uuid4())
            try:
                self._multipart_dir(upload_id).mkdir(parents=False, exist_ok=False)
            except FileExistsError:
                continue
            return upload_id
        raise RuntimeError("could not allocate a robot-model multipart upload")

    def presign_part(
        self,
        key: str,
        upload_id: str,
        part_number: int,
        expires_seconds: int,
    ) -> str:
        if not 1 <= part_number <= 10_000:
            raise ValueError("part_number must be between 1 and 10000")
        if expires_seconds < 1:
            raise ValueError("expires_seconds must be positive")
        if self.head(key) is not None:
            raise _immutable_object_exists()
        # Interrupted uploads created before the registry moved away from OSS have
        # durable upload ids in PostgreSQL.  Lazily materialize their local staging
        # directory so the same browser session can be resumed safely.
        self._multipart_dir(upload_id).mkdir(parents=False, exist_ok=True)
        retain_current_writer_permit(expires_seconds)
        organization_id = self._organization_for_key(key)
        token = self._codec.encode(
            {
                "kind": _UPLOAD_TOKEN_KIND,
                "organization_id": organization_id,
                "key": key,
                "upload_id": upload_id,
                "part_number": part_number,
                "expires_at": self._expires_at(expires_seconds),
            }
        )
        return (
            f"/api/v1/organizations/{quote(organization_id, safe='')}"
            f"/robot-model-assets/upload-part?token={quote(token, safe='')}"
        )

    def put_authorized_part(
        self,
        *,
        organization_id: str,
        token: str,
        data: bytes,
    ) -> MultipartPart:
        payload = self._authorized_payload(
            token, kind=_UPLOAD_TOKEN_KIND, organization_id=organization_id
        )
        key = self._required_text(payload, "key")
        upload_id = self._required_text(payload, "upload_id")
        part_number = payload.get("part_number")
        if not isinstance(part_number, int) or not 1 <= part_number <= 10_000:
            raise _invalid_transfer_token()
        if self.head(key) is not None:
            raise _immutable_object_exists()
        directory = self._multipart_dir(upload_id)
        directory.mkdir(parents=False, exist_ok=True)
        destination = directory / f"part-{part_number:05d}"
        self._write_atomic(destination, data)
        return MultipartPart(
            part_number=part_number,
            etag=hashlib.md5(data, usedforsecurity=False).hexdigest(),
            size=len(data),
            crc64=crc64_ecma(data),
        )

    def upload_part_stream(
        self,
        key: str,
        upload_id: str,
        part_number: int,
        body: BinaryIO,
        size: int,
    ) -> MultipartPart:
        """Persist a proxied multipart body without contacting object storage."""

        if not 1 <= part_number <= 10_000:
            raise ValueError("part_number must be between 1 and 10000")
        if size < 0:
            raise ValueError("multipart part size must not be negative")
        if self.head(key) is not None:
            raise _immutable_object_exists()
        data = body.read(size + 1)
        if len(data) != size:
            raise ValueError("multipart part stream size does not match its declaration")
        directory = self._multipart_dir(upload_id)
        directory.mkdir(parents=False, exist_ok=True)
        destination = directory / f"part-{part_number:05d}"
        self._write_atomic(destination, data)
        return MultipartPart(
            part_number=part_number,
            etag=hashlib.md5(data, usedforsecurity=False).hexdigest(),
            size=size,
            crc64=crc64_ecma(data),
        )

    def list_parts(self, key: str, upload_id: str) -> list[MultipartPart]:
        del key
        directory = self._multipart_dir(upload_id)
        if not directory.is_dir():
            return []
        found: list[MultipartPart] = []
        for path in sorted(directory.glob("part-[0-9][0-9][0-9][0-9][0-9]")):
            try:
                part_number = int(path.name.removeprefix("part-"))
            except ValueError:
                continue
            body = path.read_bytes()
            found.append(
                MultipartPart(
                    part_number=part_number,
                    etag=hashlib.md5(body, usedforsecurity=False).hexdigest(),
                    size=len(body),
                    crc64=crc64_ecma(body),
                )
            )
        return found

    def complete_multipart(
        self,
        key: str,
        upload_id: str,
        parts: Sequence[CompletedPart],
    ) -> ObjectMetadata:
        with self._lock:
            actual = self.list_parts(key, upload_id)
            validate_completion_parts(parts, actual)
            destination = self._object_path(key)
            if destination.exists():
                raise _immutable_object_exists()
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=destination.parent,
                    prefix=".assembling-",
                    delete=False,
                ) as output:
                    temporary = Path(output.name)
                    directory = self._multipart_dir(upload_id)
                    for part in parts:
                        with (directory / f"part-{part.part_number:05d}").open("rb") as source:
                            shutil.copyfileobj(source, output, length=1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                try:
                    os.link(temporary, destination)
                except FileExistsError as exc:
                    raise _immutable_object_exists() from exc
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            shutil.rmtree(self._multipart_dir(upload_id), ignore_errors=True)
            metadata = self.head(key)
            if metadata is None:
                raise RuntimeError("completed robot-model asset is not visible")
            return metadata

    def abort_multipart(self, key: str, upload_id: str) -> None:
        del key
        shutil.rmtree(self._multipart_dir(upload_id), ignore_errors=True)

    def delete_object(self, key: str) -> None:
        """Remove staged bytes after persistence or rollback and prune empty directories."""

        with self._lock:
            path = self._object_path(key)
            path.unlink(missing_ok=True)
            parent = path.parent
            while parent != self._root:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent

    def authorize_existing_object(self, uri: str, expected_key: str) -> ObjectMetadata:
        parsed = urlparse(uri)
        expected = self._object_path(expected_key)
        candidate = Path(unquote(parsed.path)).resolve()
        if parsed.scheme != "file" or candidate != expected:
            raise problem(
                status=422,
                code="ROBOT_MODEL_ASSET_REFERENCE_INVALID",
                title="Robot model asset reference is invalid",
                detail="The reference does not identify the expected server asset.",
            )
        metadata = self.head(expected_key)
        if metadata is None:
            raise problem(
                status=404,
                code="ROBOT_MODEL_ASSET_NOT_FOUND",
                title="Robot model asset not found",
                detail="The referenced server asset does not exist.",
            )
        return metadata

    def head(self, key: str) -> ObjectMetadata | None:
        path = self._object_path(key)
        if not path.is_file():
            return None
        digest = hashlib.md5(usedforsecurity=False)
        size = 0
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        return ObjectMetadata(key=key, size=size, crc64=None, etag=digest.hexdigest())

    def read_chunks(self, key: str, chunk_size: int = 8 * 1024 * 1024) -> Iterable[bytes]:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        with self._object_path(key).open("rb") as source:
            while chunk := source.read(chunk_size):
                yield chunk

    def read_range(self, key: str, start: int, end: int) -> bytes:
        if start < 0 or end < start:
            raise ValueError("invalid object byte range")
        with self._object_path(key).open("rb") as source:
            source.seek(start)
            return source.read(end - start)

    def presign_read(
        self, key: str, expires_seconds: int, *, download_name: str | None = None
    ) -> str:
        # The registry content route owns attachment names from asset metadata.
        del download_name
        if expires_seconds < 1:
            raise ValueError("expires_seconds must be positive")
        if self.head(key) is None:
            raise KeyError(key)
        organization_id = self._organization_for_key(key)
        token = self._codec.encode(
            {
                "kind": _DOWNLOAD_TOKEN_KIND,
                "organization_id": organization_id,
                "key": key,
                "expires_at": self._expires_at(expires_seconds),
            }
        )
        return (
            f"/api/v1/organizations/{quote(organization_id, safe='')}"
            f"/robot-model-assets/content?token={quote(token, safe='')}"
        )

    def resolve_authorized_read(self, *, organization_id: str, token: str) -> str:
        payload = self._authorized_payload(
            token, kind=_DOWNLOAD_TOKEN_KIND, organization_id=organization_id
        )
        key = self._required_text(payload, "key")
        if self.head(key) is None:
            raise problem(
                status=404,
                code="ROBOT_MODEL_ASSET_NOT_FOUND",
                title="Robot model asset not found",
                detail="The authorized server asset no longer exists.",
            )
        return key

    def put_json(self, key: str, value: dict[str, Any], *, if_none_match: bool) -> ObjectMetadata:
        destination = self._object_path(key)
        if if_none_match and destination.exists():
            raise _immutable_object_exists()
        body = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
        self._write_atomic(destination, body)
        metadata = self.head(key)
        if metadata is None:
            raise RuntimeError("written robot-model JSON is not visible")
        return metadata

    def _authorized_payload(
        self,
        token: str,
        *,
        kind: str,
        organization_id: str,
    ) -> dict[str, Any]:
        payload = self._codec.decode(token)
        expires_at = payload.get("expires_at")
        if (
            payload.get("kind") != kind
            or payload.get("organization_id") != organization_id
            or not isinstance(expires_at, int)
        ):
            raise _invalid_transfer_token()
        if expires_at < int(self._clock().timestamp()):
            raise problem(
                status=403,
                code="ROBOT_MODEL_ASSET_TRANSFER_EXPIRED",
                title="Robot model asset transfer expired",
                detail="Request a fresh robot-model asset transfer URL and retry.",
            )
        return payload

    @staticmethod
    def _required_text(payload: dict[str, Any], name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or not value:
            raise _invalid_transfer_token()
        return value

    def _expires_at(self, expires_seconds: int) -> int:
        return int(self._clock().timestamp()) + expires_seconds

    @staticmethod
    def _organization_for_key(key: str) -> str:
        parts = PurePosixPath(key).parts
        if len(parts) < 3 or parts[0] != "registry-assets":
            raise ValueError("robot-model asset key has no organization scope")
        organization_id = unquote(parts[1])
        if not organization_id:
            raise ValueError("robot-model asset key has an empty organization scope")
        return organization_id

    def _object_path(self, key: str) -> Path:
        pure = PurePosixPath(key)
        if (
            pure.is_absolute()
            or not pure.parts
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise ValueError("robot-model asset key is unsafe")
        candidate = (self._root / Path(*pure.parts)).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise ValueError("robot-model asset key escapes its storage root") from exc
        return candidate

    def _multipart_dir(self, upload_id: str) -> Path:
        if not upload_id:
            raise ValueError("multipart upload id is required")
        digest = hashlib.sha256(upload_id.encode()).hexdigest()
        return self._multipart_root / digest

    @staticmethod
    def _write_atomic(destination: Path, body: bytes) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=destination.parent, prefix=".writing-", delete=False
            ) as output:
                temporary = Path(output.name)
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _invalid_transfer_token() -> Exception:
    return problem(
        status=403,
        code="ROBOT_MODEL_ASSET_TRANSFER_INVALID",
        title="Robot model asset transfer is invalid",
        detail="Request a fresh robot-model asset transfer URL and retry.",
    )


def _immutable_object_exists() -> Exception:
    return problem(
        status=409,
        code="ROBOT_MODEL_ASSET_ALREADY_EXISTS",
        title="Robot model asset already exists",
        detail="Robot model assets are immutable and cannot be overwritten.",
    )
