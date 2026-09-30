"""Small object capabilities consumed independently of upload orchestration.

Keys are already-authorized physical locators, never inferred from ambient
upload state. Readers raise on absent objects; head returns None. Ranges use
half-open byte offsets. Streaming adapters close provider responses when the
iterator is exhausted or explicitly closed; callers close abandoned iterators.
The full ObjectStoragePort is retained for existing upload applications only.
"""

from collections.abc import Iterable, Sequence
from typing import Any, BinaryIO, Protocol, runtime_checkable

from .models import CompletedPartReference, MultipartPart, ObjectMetadata


@runtime_checkable
class ObjectHead(Protocol):
    def head(self, key: str) -> ObjectMetadata | None: ...


@runtime_checkable
class ObjectChunks(Protocol):
    def read_chunks(self, key: str, chunk_size: int = 8 * 1024 * 1024) -> Iterable[bytes]: ...


@runtime_checkable
class ObjectReader(ObjectHead, ObjectChunks, Protocol):
    """Bounded manifest and source readers need no upload or signing methods."""


@runtime_checkable
class ObjectRangeReader(Protocol):
    def read_range(self, key: str, start: int, end: int) -> bytes: ...


@runtime_checkable
class ObjectReadAuthorizer(Protocol):
    def presign_read(
        self, key: str, expires_seconds: int, *, download_name: str | None = None
    ) -> str: ...


class BrowsableObjectReader(ObjectReader, ObjectRangeReader, ObjectReadAuthorizer, Protocol):
    """Source browsers can read windows and authorize downloads, never write."""


class JsonObjectWriter(Protocol):
    def put_json(
        self, key: str, value: dict[str, Any], *, if_none_match: bool
    ) -> ObjectMetadata: ...


class JsonObjectStore(ObjectReader, JsonObjectWriter, Protocol):
    """Immutable metadata revisions need read/write JSON, not multipart uploads."""


class ObjectReferenceAuthorizer(Protocol):
    def authorize_existing_object(self, uri: str, expected_key: str) -> ObjectMetadata: ...


class MultipartObjectWriter(Protocol):
    def create_multipart(self, key: str) -> str: ...

    def presign_part(
        self, key: str, upload_id: str, part_number: int, expires_seconds: int
    ) -> str: ...

    def upload_part_stream(
        self, key: str, upload_id: str, part_number: int, body: BinaryIO, size: int
    ) -> MultipartPart: ...

    def list_parts(self, key: str, upload_id: str) -> list[MultipartPart]: ...

    def complete_multipart(
        self, key: str, upload_id: str, parts: Sequence[CompletedPartReference]
    ) -> ObjectMetadata: ...

    def abort_multipart(self, key: str, upload_id: str) -> None: ...


class ObjectDeleter(Protocol):
    def delete_object(self, key: str) -> None: ...


class ObjectStoragePort(
    BrowsableObjectReader,
    JsonObjectWriter,
    ObjectReferenceAuthorizer,
    MultipartObjectWriter,
    Protocol,
):
    """Compatibility aggregate for upload use cases using all capabilities.

    Read-only consumers must select a narrower capability above. Keeping this
    name stable does not require readers to implement multipart operations.
    """
