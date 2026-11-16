"""Bounded immutable streaming publication shared by server-side consumers."""

from collections.abc import Callable
from contextlib import suppress
from hashlib import sha256 as new_digest
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, Protocol, cast

from hc_data_platform.core.ingest_timing import span, timed

from .checksums import crc64_ecma
from .models import CompletedPart, ObjectIntegrityError, VerifiedObject
from .ports import MultipartObjectWriter, ObjectReader


class MultipartStore(ObjectReader, MultipartObjectWriter, Protocol):
    """Only the infrastructure adapter needs multipart details."""

    def put_bytes(self, key: str, body: bytes, *, if_none_match: bool) -> None:
        """Single-request conditional write; adapters that lack it raise AttributeError."""


@timed("READBACK_VERIFY")
def verify_object(
    reader: ObjectReader,
    key: str,
    size: int,
    sha256: str,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> VerifiedObject:
    check = checkpoint or (lambda: None)
    check()
    metadata = reader.head(key)
    if metadata is None or metadata.size != size:
        raise ObjectIntegrityError("object missing or wrong size")
    checksum, count = new_digest(), 0
    chunks = iter(reader.read_chunks(key))
    try:
        for block in chunks:
            check()
            count += len(block)
            if count > size:
                raise ObjectIntegrityError("object exceeds its frozen size")
            checksum.update(block)
    finally:
        close = getattr(chunks, "close", None)
        if close is not None:
            close()
    if count != size or checksum.hexdigest() != sha256:
        raise ObjectIntegrityError("object hash mismatch; existing objects are never overwritten")
    return VerifiedObject(key, size, sha256)


def verify_written(
    reader: ObjectReader,
    key: str,
    size: int,
    sha256: str,
    crc64: int,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> VerifiedObject:
    """Accept the provider's stored CRC-64 for bytes whose SHA-256 was checked locally.

    The source digest was verified while streaming; the server-side CRC-64 of
    the stored object then proves the stored bytes equal those streamed bytes.
    Providers that do not report a CRC fall back to the complete readback.
    """
    (checkpoint or (lambda: None))()
    metadata = reader.head(key)
    if metadata is None or metadata.size != size:
        raise ObjectIntegrityError("object missing or wrong size")
    if metadata.crc64 is None:
        return verify_object(reader, key, size, sha256, checkpoint=checkpoint)
    if metadata.crc64 != crc64:
        raise ObjectIntegrityError("object hash mismatch; existing objects are never overwritten")
    return VerifiedObject(key, size, sha256)


def _source_digests(source: BinaryIO, size: int, check: Callable[[], None]) -> tuple[str, int]:
    checksum, crc, remaining = new_digest(), 0, size
    while remaining:
        check()
        block = source.read(min(8 * 1024**2, remaining))
        if not block or len(block) > remaining:
            raise ObjectIntegrityError("source differs from its frozen size")
        checksum.update(block)
        crc = crc64_ecma(block, crc)
        remaining -= len(block)
    if source.read(1):
        raise ObjectIntegrityError("source differs from its frozen size")
    return checksum.hexdigest(), crc


class MultipartImmutableWriter:
    """Provider-neutral adapter; commit uses each provider's conditional creation.

    Reads at most 1 MiB at a time. Each part spills to a temporary file after
    8 MiB; even maximum-size objects do not require an entire part in RAM.
    Input integrity is checked before making the object visible. The stored
    object is then accepted by its provider CRC-64 (full readback without one).
    """

    def __init__(self, storage: MultipartStore) -> None:
        self.storage = storage

    def write_immutable(
        self,
        key: str,
        source: BinaryIO,
        *,
        size: int,
        sha256: str,
        checkpoint: Callable[[], None] | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> VerifiedObject:
        if size < 0 or len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise ValueError("immutable write requires a nonnegative size and SHA-256")
        check = checkpoint or (lambda: None)
        report = progress or (lambda count: None)
        check()
        # A small object costs one round trip instead of the multipart handshake
        # (create, part, list, head, complete). The readback below is unchanged.
        put = getattr(self.storage, "put_bytes", None)
        if put is not None and size <= 8 * 1024**2 and self.storage.head(key) is None:
            blocks, remaining = [], size
            while remaining:
                check()
                block = source.read(min(1024**2, remaining))
                if not block or len(block) > remaining:
                    raise ObjectIntegrityError("source differs from its frozen size")
                blocks.append(block)
                remaining -= len(block)
            body = b"".join(blocks)
            if source.read(1) or new_digest(body).hexdigest() != sha256:
                raise ObjectIntegrityError("source differs from its frozen length or SHA-256")
            check()
            try:
                put(key, body, if_none_match=True)
            except Exception:
                if self.storage.head(key) is None:
                    raise
            report(size)
            return verify_written(self.storage, key, size, sha256, crc64_ecma(body), checkpoint=check)
        if self.storage.head(key) is not None:
            # A retry finds its earlier object: hash the local source, then
            # compare the stored CRC instead of downloading the object again.
            digest, crc = _source_digests(source, size, check)
            if digest != sha256:
                raise ObjectIntegrityError("source differs from its frozen length or SHA-256")
            result = verify_written(self.storage, key, size, sha256, crc, checkpoint=check)
            report(size)
            return result
        try:
            with span("UPLOAD_BEGIN"):
                upload = self.storage.create_multipart(key)
        except Exception:
            if self.storage.head(key) is None:
                raise
            result = verify_object(self.storage, key, size, sha256, checkpoint=check)
            report(size)
            return result
        try:
            checksum, crc, transferred = new_digest(), 0, 0
            parts: list[CompletedPart] = []
            part_size = max(8 * 1024**2, (size + 9998) // 9999)
            while transferred < size or not parts:
                length = min(part_size, size - transferred)
                with SpooledTemporaryFile(max_size=8 * 1024**2) as part:
                    remaining = length
                    while remaining:
                        check()
                        block = source.read(min(1024**2, remaining))
                        if not block or len(block) > remaining:
                            raise ObjectIntegrityError("source differs from its frozen size")
                        with span("UPLOAD_SOURCE_HASH"):
                            checksum.update(block)
                            crc = crc64_ecma(block, crc)
                        part.write(block)
                        remaining -= len(block)
                    part.seek(0)
                    check()
                    with span("UPLOAD_PART"):
                        uploaded = self.storage.upload_part_stream(
                            key, upload, len(parts) + 1, cast(BinaryIO, part), length
                        )
                parts.append(CompletedPart(len(parts) + 1, uploaded.etag))
                transferred += length
                report(transferred)
            if source.read(1) or checksum.hexdigest() != sha256:
                raise ObjectIntegrityError("source differs from its frozen length or SHA-256")
            check()
            try:
                with span("UPLOAD_COMPLETE"):
                    self.storage.complete_multipart(key, upload, parts)
            except Exception:
                # Preserve an identical concurrent winner or a lost successful
                # response. Do not swallow an absent object or cancellation.
                if self.storage.head(key) is None:
                    raise
                verify_written(self.storage, key, size, sha256, crc, checkpoint=check)
                with suppress(Exception):
                    self.storage.abort_multipart(key, upload)
        except BaseException:
            with suppress(Exception):
                self.storage.abort_multipart(key, upload)
            raise
        return verify_written(self.storage, key, size, sha256, crc, checkpoint=check)
