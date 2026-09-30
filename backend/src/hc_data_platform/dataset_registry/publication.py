"""Dataset-owned release projection and owner lookup contracts."""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class DatasetReleaseProjection:
    project_id: str
    dataset_id: str
    source_version_id: str
    publication_version: str
    publication_hash: str
    published_at: datetime
    rollout_count: int


def release_version_id(publication_version: str) -> str:
    """Preserve the existing public identity; never recompute content hashes."""
    digest = hashlib.sha256(publication_version.encode("utf-8")).hexdigest()[:32]
    return f"version_release_{digest}"


class DatasetReleaseProjectionPort(Protocol):
    def project_release(self, request: DatasetReleaseProjection) -> None:
        """Write metadata in the transaction supplied by application composition."""
        ...


class DatasetOwnerLookup(Protocol):
    def owner_id(
        self, *, organization_id: str, project_id: str, region_code: str, dataset_id: str
    ) -> str | None: ...
