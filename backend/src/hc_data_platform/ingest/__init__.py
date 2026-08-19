"""Immutable rollout ingestion and object-storage contracts."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from .models import (
    CollectionJob,
    CompletedPart,
    RawObjectCommittedV1,
    Rollout,
    RolloutManifestV1,
    UploadObject,
    UploadPart,
    UploadSession,
)

__all__ = [
    "CollectionJob",
    "CompletedPart",
    "InMemoryIngestPersistence",
    "InMemoryObjectStorage",
    "IngestPersistencePort",
    "ObjectStoragePort",
    "OssObjectStorage",
    "RawObjectCommittedV1",
    "Rollout",
    "RolloutManifestV1",
    "S3ObjectStorage",
    "UploadObject",
    "UploadPart",
    "UploadSession",
    "UploadSessionService",
]

_LAZY_EXPORTS = {
    "InMemoryIngestPersistence": ("persistence", "InMemoryIngestPersistence"),
    "IngestPersistencePort": ("persistence", "IngestPersistencePort"),
    "InMemoryObjectStorage": ("ports", "InMemoryObjectStorage"),
    "ObjectStoragePort": ("ports", "ObjectStoragePort"),
    "OssObjectStorage": ("adapters", "OssObjectStorage"),
    "S3ObjectStorage": ("adapters", "S3ObjectStorage"),
    "UploadSessionService": ("service", "UploadSessionService"),
}


def __getattr__(name: str) -> Any:
    """Keep workflow model imports free of storage, auth, and SDK side effects."""

    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    module = import_module(f"{__name__}.{module_name}")
    value = getattr(module, attribute)
    globals()[name] = value
    return value
