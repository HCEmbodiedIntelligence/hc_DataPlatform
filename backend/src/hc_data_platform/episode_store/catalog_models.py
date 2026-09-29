"""Logical catalog views for mixed Episode collections; no fabricated Lance IDs."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from hc_data_platform.lance_catalog.models import StepWindow

from .models import CollectionSnapshot, DataVersionRef, Digest, EpisodeDataRef, FrozenModel


class CollectionVersion(CollectionSnapshot):
    storage_format: Literal["episode_collection"] = "episode_collection"
    created_at: datetime
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def version_id(self):
        return "version_" + self.collection_version_id

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
    source_sha256: Digest
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
    source_sha256: Digest
    converter_version: str
    dataset_version: int = Field(ge=1)
    step_count: int = Field(ge=1)
    content_hash: Digest

    @model_validator(mode="after")
    def validate_data_identity(self):
        if (self.project_id, self.dataset_id, self.rollout_id) != (
            self.data.identity.project_id,
            self.data.identity.dataset_id,
            self.data.identity.rollout_id,
        ) or self.step_count != self.data.frame_count:
            raise ValueError("ready result differs from its immutable Episode reference")
        return self


def validate_commit_pair(version, ready):
    """Prevent a workflow result from combining receipts from different storage/version states."""
    if isinstance(version, CollectionVersion) != isinstance(ready, EpisodeDerivedReady):
        raise ValueError("commit result mixes legacy and Episode package references")
    if isinstance(version, CollectionVersion) and (
        ready.data not in version.episodes
        or ready.project_id != version.project_id
        or ready.dataset_id != version.dataset_id
        or ready.dataset_version != version.version
        or ready.collection_version_id != version.collection_version_id
        or ready.content_hash != version.content_hash
    ):
        raise ValueError("commit result differs from the immutable collection")
