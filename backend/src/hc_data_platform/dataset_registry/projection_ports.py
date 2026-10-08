"""Narrow readers needed by product projection; no runtime service construction."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from hc_data_platform.episode_store.catalog_models import CollectionVersion
    from hc_data_platform.episode_store.files import AuthorizedPackage
    from hc_data_platform.episode_store.models import EpisodeIdentity


class ProjectionSource(Protocol):
    @property
    def rollout_id(self) -> str: ...
    @property
    def source_sha256(self) -> str: ...


class ProjectionFiles(Protocol):
    def open(self, identity: EpisodeIdentity, data_version_id: str) -> AuthorizedPackage: ...


class ProjectionCollections(Protocol):
    def get(self, dataset_id: str, *, collection_version_id: str) -> CollectionVersion: ...
