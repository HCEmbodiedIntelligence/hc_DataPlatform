"""New package references never borrow a physical Lance version number."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Annotated, Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(min_length=1, max_length=256)]


def canonical(value: object) -> bytes:
    """hc-json/v1: sorted keys, ASCII escapes, compact JSON, no NaN/Inf.

    Manifests contain strings and integer sizes, not floating-point data values.
    File hashes bind their original bytes, including numerical representations.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EpisodeIdentity(FrozenModel):
    organization_id: Identifier
    project_id: Identifier
    region_code: Identifier
    dataset_id: Identifier
    episode_id: Identifier
    rollout_id: Identifier

    @property
    def scope(self) -> tuple[str, str, str]:
        return self.organization_id, self.project_id, self.region_code

    @property
    def key(self) -> tuple[str, ...]:
        return (*self.scope, self.dataset_id, self.episode_id)


class PackageInput(FrozenModel):
    identity: EpisodeIdentity
    source_sha256: Digest
    processor_version: Identifier
    configuration_sha256: Digest
    schema_snapshot_id: Identifier
    required_features: tuple[str, ...] = Field(min_length=1)
    required_depth_features: tuple[str, ...] = ()
    # Frozen source facts determine paths. Never derive these from display names.
    recording_date: str = Field(default="unknown", pattern=r"^(unknown|\d{4}-\d{2}-\d{2})$")
    robot_id: Identifier = "unknown"
    task_id: Identifier = "unknown"

    @property
    def idempotency_key(self) -> str:
        return digest(self.model_dump(mode="json"))

    @property
    def data_version_id(self) -> str:
        return "dv_" + self.idempotency_key

    @property
    def package_prefix(self) -> str:
        parts = (
            *self.identity.scope,
            self.recording_date,
            self.robot_id,
            self.task_id,
            self.identity.episode_id,
            self.data_version_id,
        )
        # Prefix each component: '.'/'..' cannot become traversal even when quoted.
        return "episodes/" + "/".join("id-" + quote(p, safe="-_") for p in parts)


class FileEntry(FrozenModel):
    path: str
    size: int = Field(ge=1)
    sha256: Digest
    media_type: str

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or "\\" in value
            or "\x00" in value
            or any(p in {"", ".", ".."} for p in value.split("/"))
        ):
            raise ValueError("manifest path must be a canonical relative POSIX path")
        if value in {"hc/manifest.json", "hc/commit.json"}:
            raise ValueError("manifest/receipt cannot hash themselves")
        return value


class PackageManifest(FrozenModel):
    format: Literal["hc-episode-package/1"] = "hc-episode-package/1"
    storage_format: Literal["lerobot_v3"] = "lerobot_v3"
    input: PackageInput
    frame_count: int = Field(ge=1)
    files: tuple[FileEntry, ...] = Field(min_length=1)

    @field_validator("files")
    @classmethod
    def unique_sorted(cls, files: tuple[FileEntry, ...]) -> tuple[FileEntry, ...]:
        paths = [item.path for item in files]
        if paths != sorted(set(paths)):
            raise ValueError("manifest paths must be unique and sorted")
        return files

    @property
    def content_hash(self) -> str:
        return digest(self.model_dump(mode="json"))


class DataVersionRef(FrozenModel):
    identity: EpisodeIdentity
    data_version_id: Identifier
    version: int = Field(ge=1)
    content_hash: Digest
    storage_format: Literal["lerobot_v3"] = "lerobot_v3"
    schema_snapshot_id: Identifier
    package_prefix: str
    frame_count: int = Field(ge=1)


class CommitReceipt(FrozenModel):
    format: Literal["hc-episode-commit/1"] = "hc-episode-commit/1"
    data: DataVersionRef
    idempotency_key: Digest
    manifest_sha256: Digest


class CollectionSnapshot(FrozenModel):
    """Collection identity is separate from each immutable Episode version."""

    organization_id: Identifier
    project_id: Identifier
    region_code: Identifier
    dataset_id: Identifier
    collection_version_id: Identifier
    episodes: tuple[DataVersionRef, ...]

    @model_validator(mode="after")
    def same_scope(self) -> CollectionSnapshot:
        identities = [item.identity for item in self.episodes]
        expected = (self.organization_id, self.project_id, self.region_code, self.dataset_id)
        if any((*i.scope, i.dataset_id) != expected for i in identities):
            raise ValueError("collection cannot contain data from another tenant/dataset")
        if len({i.episode_id for i in identities}) != len(identities):
            raise ValueError("collection contains multiple versions of one Episode")
        return self
