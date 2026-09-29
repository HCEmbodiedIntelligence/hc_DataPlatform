"""Logical catalog views for mixed Episode collections; no fabricated Lance IDs."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from hc_data_platform.lance_catalog.models import StepWindow

from .models import CollectionSnapshot, DataVersionRef, EpisodeDataRef, FrozenModel


class CollectionVersion(CollectionSnapshot):
    storage_format: Literal["episode_collection"] = "episode_collection"
    created_at: datetime
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def committed_rollouts(self):
        return tuple(item.identity.rollout_id for item in self.episodes)

    @property
    def dataset_uri(self):
        return self.manifest_key

    @property
    def content_hash(self):
        return self.manifest_sha256

    @property
    def storage_commit_id(self):
        return self.manifest_sha256


class CollectionStepWindow(StepWindow):
    data: EpisodeDataRef
    collection_version_id: str


class EpisodeLineage(FrozenModel):
    data: EpisodeDataRef
    project_id: str
    dataset_id: str
    dataset_version: int
    collection_version_id: str
    rollout_id: str
    source_sha256: str
    converter_version: str
    schema_snapshot_id: str
    schema_fingerprint: str
    fragment_uri: str
    fragment_content_hash: str
    dataset_uri: str


class EpisodeDerivedReady(FrozenModel):
    """Package + collection committed; human review remains a separate state."""

    schema_version: Literal["2"] = "2"
    data: DataVersionRef
    collection_version_id: str
    project_id: str
    dataset_id: str
    rollout_id: str
    source_sha256: str
    converter_version: str
    dataset_version: int
    step_count: int
    content_hash: str
