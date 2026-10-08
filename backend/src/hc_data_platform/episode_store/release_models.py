"""Business releases pin a real collection and independent annotation decisions."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from .models import Digest, FrozenModel


class PublishCollectionRelease(FrozenModel):
    dataset_version: str = Field(
        min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
    )
    collection_version_id: str = Field(pattern=r"^cv_[0-9a-f]{64}$")
    annotation_tasks: dict[str, str] = Field(default_factory=dict, max_length=10000)
    expected_content_hash: Digest | None = None


class CollectionReleaseExclusion(FrozenModel):
    episode_id: str
    reason: Literal["ANNOTATION_NOT_APPROVED", "ANNOTATION_TASK_SELECTION_REQUIRED"]


class CollectionReleasePreflight(FrozenModel):
    dataset_id: str
    dataset_version: str
    collection_version_id: str
    collection_manifest_sha256: Digest
    content_hash: Digest
    eligible_episode_ids: tuple[str, ...]
    excluded_episodes: tuple[CollectionReleaseExclusion, ...]


class CollectionRelease(FrozenModel):
    format: Literal["hc-collection-release/1"] = "hc-collection-release/1"
    dataset_id: str
    dataset_version: str
    version_id: str = Field(pattern=r"^version_release_[0-9a-f]{32}$")
    collection_version_id: str
    collection_manifest_sha256: Digest
    content_hash: Digest
    document_sha256: Digest
    document_size: int = Field(gt=0, le=64 * 1024**2)
    eligible_episode_ids: tuple[str, ...] = Field(min_length=1)
    excluded_episodes: tuple[CollectionReleaseExclusion, ...]
    created_at: datetime
