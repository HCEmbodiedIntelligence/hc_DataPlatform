from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from app.core.context import RequestContext


@dataclass
class FakeDatasetVersionPort:
    versions: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)

    async def get_version(
        self, dataset_id: str, version_id: str, ctx: RequestContext
    ) -> Mapping[str, Any] | None:
        value = self.versions.get((dataset_id, version_id))
        return value.copy() if value else None


@dataclass
class FakeEpisodePort:
    episodes: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def get_episode(self, episode_id: str, ctx: RequestContext) -> Mapping[str, Any] | None:
        value = self.episodes.get(episode_id)
        return value.copy() if value else None


@dataclass
class FakeRobotAssetPort:
    calibration_sets: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def get_calibration_set(
        self, calibration_set_id: str, ctx: RequestContext
    ) -> Mapping[str, Any] | None:
        value = self.calibration_sets.get(calibration_set_id)
        return value.copy() if value else None


@dataclass
class FakeSchemaRegistryPort:
    schema_versions: dict[tuple[str, str | None], dict[str, Any]] = field(default_factory=dict)

    async def get_schema_version(
        self,
        schema_ref: str,
        schema_version: str | None,
        ctx: RequestContext,
    ) -> Mapping[str, Any] | None:
        value = self.schema_versions.get((schema_ref, schema_version))
        return value.copy() if value else None


@dataclass
class FakeObjectStoragePort:
    objects: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    calls: list[tuple[str, str, str]] = field(default_factory=list)

    async def presign_put(
        self,
        bucket: str,
        object_key: str,
        expires_in: int = 300,
        content_type: str | None = None,
    ) -> str:
        self.calls.append(("presign_put", bucket, object_key))
        return (
            f"https://storage.test/{quote(bucket)}/{quote(object_key)}?method=PUT&ttl={expires_in}"
        )

    async def presign_get(
        self,
        bucket: str,
        object_key: str,
        expires_in: int = 300,
        download_name: str | None = None,
    ) -> str:
        self.calls.append(("presign_get", bucket, object_key))
        return (
            f"https://storage.test/{quote(bucket)}/{quote(object_key)}?method=GET&ttl={expires_in}"
        )

    async def head(self, bucket: str, object_key: str) -> Mapping[str, Any] | None:
        self.calls.append(("head", bucket, object_key))
        value = self.objects.get((bucket, object_key))
        return value.copy() if value else None

    def put_object_fact(
        self,
        bucket: str,
        object_key: str,
        *,
        size_bytes: int,
        etag: str,
        sha256: str | None = None,
    ) -> None:
        self.objects[(bucket, object_key)] = {
            "size_bytes": size_bytes,
            "etag": etag,
            "sha256": sha256,
        }
