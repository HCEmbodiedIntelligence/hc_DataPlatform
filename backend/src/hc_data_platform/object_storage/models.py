"""Object transfer values, independent of HTTP upload request models."""

from dataclasses import dataclass
from typing import Protocol


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


class CompletedPartReference(Protocol):
    """Read-only shape also satisfied by historical upload request values."""

    @property
    def part_number(self) -> int: ...

    @property
    def etag(self) -> str: ...


@dataclass(frozen=True, slots=True)
class CompletedPart:
    part_number: int
    etag: str
