"""Capabilities for immutable, checksummed files independent of job execution.

Keys are opaque to consumers. A successful read validates the expected digest
and size before exposing a file. The context owns any temporary local copy and
releases it on both success and failure. Publishing the same content is
idempotent; a different value at an existing key is a conflict.
"""

from collections.abc import Mapping
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol


class ArtifactFileReader(Protocol):
    def local_file(
        self, key: str, *, expected_sha256: str, expected_size: int
    ) -> AbstractContextManager[Path]: ...


class ArtifactReader(ArtifactFileReader, Protocol):
    def read_json(
        self, key: str, *, expected_sha256: str, max_bytes: int = 8 * 1024 * 1024
    ) -> dict[str, object]: ...


class ArtifactPublisher(Protocol):
    def publish_file(self, key: str, source: Path, *, sha256: str) -> int: ...

    def publish_json(self, key: str, value: Mapping[str, object]) -> tuple[str, int]: ...


class ArtifactDeleter(Protocol):
    def delete(self, key: str) -> None: ...


class ArtifactWorkspace(ArtifactReader, ArtifactPublisher, Protocol):
    """Read/write capability for processing that does not own garbage collection."""


class StagedArtifactStore(ArtifactWorkspace, ArtifactDeleter, Protocol):
    """Full capability for the orchestrator that owns artifact retention."""
