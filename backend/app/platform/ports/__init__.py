from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from app.core.context import RequestContext


@runtime_checkable
class DatasetVersionPort(Protocol):
    async def get_version(
        self,
        dataset_id: str,
        version_id: str,
        ctx: RequestContext,
    ) -> Mapping[str, Any] | None: ...


@runtime_checkable
class EpisodePort(Protocol):
    async def get_episode(
        self,
        episode_id: str,
        ctx: RequestContext,
    ) -> Mapping[str, Any] | None: ...


@runtime_checkable
class RobotAssetPort(Protocol):
    async def get_calibration_set(
        self,
        calibration_set_id: str,
        ctx: RequestContext,
    ) -> Mapping[str, Any] | None: ...


@runtime_checkable
class SchemaRegistryPort(Protocol):
    async def get_schema_version(
        self,
        schema_ref: str,
        schema_version: str | None,
        ctx: RequestContext,
    ) -> Mapping[str, Any] | None: ...


@runtime_checkable
class ObjectStoragePort(Protocol):
    async def presign_put(
        self,
        bucket: str,
        object_key: str,
        expires_in: int = 300,
        content_type: str | None = None,
    ) -> str: ...

    async def presign_get(
        self,
        bucket: str,
        object_key: str,
        expires_in: int = 300,
        download_name: str | None = None,
    ) -> str: ...

    async def head(self, bucket: str, object_key: str) -> Mapping[str, Any] | None: ...


__all__ = [
    "DatasetVersionPort",
    "EpisodePort",
    "ObjectStoragePort",
    "RobotAssetPort",
    "SchemaRegistryPort",
]
