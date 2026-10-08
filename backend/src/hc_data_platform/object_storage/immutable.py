"""Bounded immutable streaming publication shared by server-side consumers."""

from collections.abc import Callable
from contextlib import suppress
from hashlib import sha256 as new_digest
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, Protocol, cast

from .models import CompletedPart, ObjectIntegrityError, VerifiedObject
from .ports import MultipartObjectWriter, ObjectReader


class MultipartStore(ObjectReader, MultipartObjectWriter, Protocol):
    """Only the infrastructure adapter needs multipart details."""


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


class MultipartImmutableWriter:
    """Provider-neutral adapter; commit uses each provider's conditional creation.

    Reads at most 1 MiB at a time. Each part spills to a temporary file after
    8 MiB; even maximum-size objects do not require an entire part in RAM.
    Input integrity is checked before making the object visible. A lost complete
    response or concurrent identical winner is resolved by exact readback.
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
        if self.storage.head(key) is not None:
            result = verify_object(self.storage, key, size, sha256, checkpoint=check)
            report(size)
            return result
        try:
            upload = self.storage.create_multipart(key)
        except Exception:
            if self.storage.head(key) is None:
                raise
            result = verify_object(self.storage, key, size, sha256, checkpoint=check)
            report(size)
            return result
        try:
            checksum, transferred = new_digest(), 0
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
                        checksum.update(block)
                        part.write(block)
                        remaining -= len(block)
                    part.seek(0)
                    check()
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
                self.storage.complete_multipart(key, upload, parts)
            except Exception:
                # Preserve an identical concurrent winner or a lost successful
                # response. Do not swallow an absent object or cancellation.
                if self.storage.head(key) is None:
                    raise
                verify_object(self.storage, key, size, sha256, checkpoint=check)
                with suppress(Exception):
                    self.storage.abort_multipart(key, upload)
        except BaseException:
            with suppress(Exception):
                self.storage.abort_multipart(key, upload)
            raise
        return verify_object(self.storage, key, size, sha256, checkpoint=check)
