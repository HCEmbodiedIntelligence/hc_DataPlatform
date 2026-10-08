"""Shared projection identities, immutable facts and deterministic mappings."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from hc_data_platform.alignment.models import ModalityKind
from hc_data_platform.episode_store.catalog_models import CollectionVersion, EpisodeDerivedReady
from hc_data_platform.lance_catalog.models import DatasetVersionRef, DerivedReadyV1

from .models import (
    DatasetIngestViewerTarget,
)
from .projection_ports import ProjectionSource


class DatasetIngestProjectionConflict(RuntimeError):
    """The durable projection disagrees with immutable Lance lineage."""


DataValueKind = Literal["SCALAR", "VECTOR", "POINTCLOUD_XYZ", "EVENT"]


def _document(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:32]
    return f"{prefix}_{digest}"


def _version_id(version: int | DatasetVersionRef | CollectionVersion) -> str:
    if isinstance(version, CollectionVersion):
        return str(version.version_id)
    number = version.version if isinstance(version, DatasetVersionRef) else version
    return f"version_lance_{number}"


def _previous_id(version: DatasetVersionRef | CollectionVersion) -> str | None:
    if isinstance(version, CollectionVersion) and version.previous_collection_version_id:
        return "version_" + version.previous_collection_version_id
    return _version_id(version.version - 1) if version.version > 1 else None


def _version_token(version: DatasetVersionRef | CollectionVersion) -> str:
    prefix = "episode-collection" if isinstance(version, CollectionVersion) else "lance-version"
    return f"{prefix}:{version.storage_commit_id}"


def _operational_revision(version: DatasetVersionRef | CollectionVersion) -> str:
    return (
        _version_token(version)
        if isinstance(version, CollectionVersion)
        else f"lance:{version.storage_commit_id}"
    )


def projection_target(
    source: ProjectionSource | None,
    version: DatasetVersionRef | CollectionVersion,
    ready: DerivedReadyV1 | EpisodeDerivedReady,
) -> DatasetIngestViewerTarget:
    if isinstance(version, CollectionVersion):
        if not isinstance(ready, EpisodeDerivedReady):
            raise DatasetIngestProjectionConflict("collection target requires Episode readiness")
        return DatasetIngestViewerTarget(
            dataset_id=ready.dataset_id,
            version_id=version.version_id,
            episode_id=ready.data.identity.episode_id,
            revision_id=stable_id("revision", ready.data.data_version_id),
        )
    if source is None:
        raise DatasetIngestProjectionConflict("legacy target requires source identity")
    return DatasetIngestViewerTarget(
        dataset_id=ready.dataset_id,
        version_id=_version_id(ready.dataset_version),
        episode_id=stable_id("episode", source.rollout_id),
        revision_id=stable_id("revision", source.rollout_id, source.source_sha256),
    )


def _revision_hash(
    source: ProjectionSource,
    version: DatasetVersionRef | CollectionVersion,
    episode_id: str,
) -> str:
    if isinstance(version, CollectionVersion):
        return next(e.content_hash for e in version.episodes if e.identity.episode_id == episode_id)
    return source.source_sha256


def _numeric_sequence(value: object) -> tuple[float, ...] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    if not value or not all(
        isinstance(item, (int, float)) and not isinstance(item, bool) for item in value
    ):
        return None
    return tuple(float(item) for item in value)


def _numeric_vector_payload(value: object) -> tuple[float, ...] | None:
    direct = _numeric_sequence(value)
    if direct is not None:
        return direct
    if not isinstance(value, Mapping):
        return None
    for key in ("positions", "values"):
        candidate = _numeric_sequence(value.get(key))
        if candidate is not None:
            return candidate
    position = _numeric_sequence(value.get("position_xyz"))
    orientation = _numeric_sequence(value.get("orientation_wxyz"))
    if position is not None and orientation is not None:
        return (*position, *orientation)
    return None


def _value_kind(values: Sequence[object]) -> DataValueKind:
    candidate = next((value for value in values if value is not None), None)
    if isinstance(candidate, bool):
        return "EVENT"
    if isinstance(candidate, (int, float)):
        return "SCALAR"
    if _numeric_vector_payload(candidate) is not None:
        return "VECTOR"
    return "EVENT"


def _stream_kind(topic: str, modality: ModalityKind, value_kind: DataValueKind) -> str:
    lowered = topic.casefold()
    if modality is ModalityKind.POINT_CLOUD:
        return "POINTCLOUD"
    if value_kind == "EVENT":
        return "EVENT"
    if "end_effector" in lowered and "state" in lowered:
        return "POSE"
    if "joint" in lowered:
        return "JOINT_STATE"
    if "observation/state" in lowered or "raw_state" in lowered:
        return "JOINT_STATE"
    if modality is ModalityKind.ACTION or "action" in lowered or "command" in lowered:
        return "ACTION"
    if modality is ModalityKind.FORCE or "force" in lowered or "wrench" in lowered:
        return "FORCE"
    if modality is ModalityKind.IMU or "imu" in lowered:
        return "IMU"
    if "pose" in lowered:
        return "POSE"
    if "tactile" in lowered or "touch" in lowered:
        return "TACTILE"
    return "EVENT"


def _row_document(raw: object) -> object:
    return raw[0] if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) else raw


@dataclass(frozen=True, slots=True)
class ProjectionFacts:
    collection_task_id: str
    robot_id: str
    started_at: datetime | None
    camera_topics: frozenset[str]
    source_size_bytes: int
    upload_id: str


@dataclass(frozen=True, slots=True)
class TagEpisodeProjectionSource:
    """A derived identity referring to an existing Raw source, not another ingest."""

    organization_id: str
    project_id: str
    region_code: str
    rollout_id: str
    source_sha256: str
    data_package_id: str
    manifest_fingerprint: str
