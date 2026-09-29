"""Serializable processing receipts; importing these never loads media codecs."""

from dataclasses import dataclass

from .models import EpisodeIdentity, FileEntry


@dataclass(frozen=True)
class FrozenObject:
    key: str
    size: int
    sha256: str


@dataclass(frozen=True)
class SourceInventory:
    identity: EpisodeIdentity
    source_sha256: str
    objects: tuple[FrozenObject, ...]


@dataclass(frozen=True)
class DepthAssetContract:
    feature: dict
    evidence: tuple[tuple[FileEntry, FrozenObject], ...]
