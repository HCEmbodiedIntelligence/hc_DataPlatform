from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Protocol
from urllib.parse import unquote, urlparse
from uuid import uuid4

from hc_data_platform.core.context import retain_current_writer_permit
from hc_data_platform.core.errors import problem

from .models import CompletedPart


@dataclass(frozen=True, slots=True)
class ObjectMetadata:
    key: str
    size: int
    crc64: int | None
    etag: str


@dataclass(frozen=True, slots=True)
class MultipartPart:
    part_number: int
    etag: str
    size: int
    crc64: int | None = None


class ObjectStoragePort(Protocol):
    """Storage operations never infer an object key from an in-process upload map."""

    def create_multipart(self, key: str) -> str: ...

    def presign_part(
        self,
        key: str,
        upload_id: str,
        part_number: int,
        expires_seconds: int,
    ) -> str: ...

    def list_parts(self, key: str, upload_id: str) -> list[MultipartPart]: ...

    def complete_multipart(
        self,
        key: str,
        upload_id: str,
        parts: Sequence[CompletedPart],
    ) -> ObjectMetadata: ...

    def abort_multipart(self, key: str, upload_id: str) -> None: ...

    def authorize_existing_object(self, uri: str, expected_key: str) -> ObjectMetadata: ...

    def head(self, key: str) -> ObjectMetadata | None: ...

    def read_chunks(self, key: str, chunk_size: int = 8 * 1024 * 1024) -> Iterable[bytes]: ...

    def presign_read(self, key: str, expires_seconds: int) -> str: ...

    def put_json(
        self, key: str, value: dict[str, Any], *, if_none_match: bool
    ) -> ObjectMetadata: ...


_CRC64_MASK = 0xFFFFFFFFFFFFFFFF
_CRC64_XZ_POLYNOMIAL = 0xC96C5795D7870F42


def _build_crc64_table() -> tuple[int, ...]:
    values: list[int] = []
    for index in range(256):
        value = index
        for _ in range(8):
            value = (value >> 1) ^ _CRC64_XZ_POLYNOMIAL if value & 1 else value >> 1
        values.append(value & _CRC64_MASK)
    return tuple(values)


_CRC64_TABLE = _build_crc64_table()


def crc64_ecma(data: bytes, crc: int = 0) -> int:
    """Return Alibaba OSS-compatible CRC-64/XZ, supporting incremental calls.

    The public ``crc`` is the finalized value from a previous call. Un-finalizing it here
    makes ``crc64_ecma(chunk_b, crc64_ecma(chunk_a))`` equal the one-shot checksum.
    """

    accumulator = crc ^ _CRC64_MASK
    for byte in data:
        accumulator = _CRC64_TABLE[(accumulator ^ byte) & 0xFF] ^ (accumulator >> 8)
    return (accumulator ^ _CRC64_MASK) & _CRC64_MASK


@dataclass(slots=True)
class _Multipart:
    key: str
    parts: dict[int, bytes] = field(default_factory=dict)


class InMemoryObjectStorage:
    """Deterministic direct-upload adapter used by module and workflow tests."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self._uploads: dict[str, _Multipart] = {}
        self._lock = RLock()

    def create_multipart(self, key: str) -> str:
        with self._lock:
            if key in self.objects:
                raise _object_exists()
            upload_id = str(uuid4())
            self._uploads[upload_id] = _Multipart(key=key)
            return upload_id

    def presign_part(
        self,
        key: str,
        upload_id: str,
        part_number: int,
        expires_seconds: int,
    ) -> str:
        self._get_upload(key, upload_id)
        _validate_part_number(part_number)
        if expires_seconds < 1:
            raise ValueError("expires_seconds must be positive")
        retain_current_writer_permit(expires_seconds)
        return f"memory://multipart/{upload_id}/{part_number}?key={key}&expires={expires_seconds}"

    def upload_part(
        self,
        upload_id: str,
        part_number: int,
        data: bytes,
        *,
        key: str | None = None,
    ) -> MultipartPart:
        """Simulate a client PUT; it is deliberately not part of ObjectStoragePort."""

        _validate_part_number(part_number)
        with self._lock:
            upload = self._uploads.get(upload_id)
            if upload is None or (key is not None and upload.key != key):
                raise KeyError(upload_id)
            upload.parts[part_number] = data
        return _part_metadata(part_number, data)

    def list_parts(self, key: str, upload_id: str) -> list[MultipartPart]:
        upload = self._get_upload(key, upload_id)
        return [_part_metadata(number, body) for number, body in sorted(upload.parts.items())]

    def complete_multipart(
        self,
        key: str,
        upload_id: str,
        parts: Sequence[CompletedPart],
    ) -> ObjectMetadata:
        with self._lock:
            upload = self._get_upload(key, upload_id)
            actual = self.list_parts(key, upload_id)
            validate_completion_parts(parts, actual)
            if key in self.objects:
                raise _object_exists()
            body = b"".join(upload.parts[part.part_number] for part in parts)
            self.objects[key] = body
            del self._uploads[upload_id]
            return self._metadata(key, body)

    def abort_multipart(self, key: str, upload_id: str) -> None:
        with self._lock:
            upload = self._uploads.get(upload_id)
            if upload is not None and upload.key != key:
                raise KeyError(upload_id)
            self._uploads.pop(upload_id, None)

    def authorize_existing_object(self, uri: str, expected_key: str) -> ObjectMetadata:
        parsed = urlparse(uri)
        key = unquote(parsed.path.lstrip("/"))
        if (
            parsed.scheme != "memory"
            or parsed.netloc != "object"
            or parsed.params
            or parsed.query
            or parsed.fragment
            or key != expected_key
        ):
            raise problem(
                status=422,
                code="OBJECT_STORAGE_REFERENCE_INVALID",
                title="Object storage reference is invalid",
                detail="The reference must identify the expected immutable Raw object.",
            )
        metadata = self.head(key)
        if metadata is None:
            raise problem(
                status=404,
                code="OBJECT_STORAGE_OBJECT_NOT_FOUND",
                title="Object storage object not found",
                detail="The authorized object does not exist.",
            )
        return metadata

    def head(self, key: str) -> ObjectMetadata | None:
        body = self.objects.get(key)
        return None if body is None else self._metadata(key, body)

    def read_chunks(self, key: str, chunk_size: int = 8 * 1024 * 1024) -> Iterable[bytes]:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        body = self.objects[key]
        for offset in range(0, len(body), chunk_size):
            yield body[offset : offset + chunk_size]

    def presign_read(self, key: str, expires_seconds: int) -> str:
        if expires_seconds < 1:
            raise ValueError("expires_seconds must be positive")
        if self.head(key) is None:
            raise KeyError(key)
        return f"memory://object/{key}?expires={expires_seconds}"

    def put_json(self, key: str, value: dict[str, Any], *, if_none_match: bool) -> ObjectMetadata:
        with self._lock:
            if if_none_match and key in self.objects:
                raise _object_exists()
            body = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
            self.objects[key] = body
            return self._metadata(key, body)

    def _get_upload(self, key: str, upload_id: str) -> _Multipart:
        upload = self._uploads.get(upload_id)
        if upload is None or upload.key != key:
            raise KeyError(upload_id)
        return upload

    @staticmethod
    def _metadata(key: str, body: bytes) -> ObjectMetadata:
        return ObjectMetadata(
            key=key,
            size=len(body),
            crc64=crc64_ecma(body),
            etag=hashlib.md5(body, usedforsecurity=False).hexdigest(),
        )


def _part_metadata(part_number: int, body: bytes) -> MultipartPart:
    return MultipartPart(
        part_number=part_number,
        size=len(body),
        crc64=crc64_ecma(body),
        etag=hashlib.md5(body, usedforsecurity=False).hexdigest(),
    )


def normalize_etag(etag: str) -> str:
    return etag.strip().strip('"')


def _validate_part_number(part_number: int) -> None:
    if not 1 <= part_number <= 10_000:
        raise ValueError("part_number must be between 1 and 10000")


def validate_completion_parts(
    supplied: Sequence[CompletedPart],
    actual: Sequence[MultipartPart],
) -> None:
    numbers = [part.part_number for part in supplied]
    if not numbers:
        raise problem(
            status=409,
            code="MULTIPART_EMPTY",
            title="Multipart upload is empty",
            detail="At least one uploaded part is required.",
        )
    if numbers != sorted(numbers) or len(numbers) != len(set(numbers)):
        raise problem(
            status=422,
            code="PARTS_NOT_SORTED",
            title="Multipart completion parts are not sorted",
            detail="Parts must be unique and sorted by ascending part_number.",
        )
    supplied_pairs = [(part.part_number, normalize_etag(part.etag)) for part in supplied]
    actual_pairs = [(part.part_number, normalize_etag(part.etag)) for part in actual]
    if supplied_pairs != actual_pairs:
        raise problem(
            status=409,
            code="MULTIPART_PARTS_MISMATCH",
            title="Multipart part list does not match storage",
            detail="The supplied part numbers and ETags must exactly match uploaded parts.",
            details={
                "supplied_part_numbers": numbers,
                "uploaded_part_numbers": [part.part_number for part in actual],
            },
        )


def _object_exists() -> Exception:
    return problem(
        status=409,
        code="OBJECT_ALREADY_EXISTS",
        title="Immutable object already exists",
        detail="Raw and manifest objects cannot be overwritten.",
    )
