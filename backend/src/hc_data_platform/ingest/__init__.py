"""Immutable rollout ingestion and object-storage contracts."""

from .adapters import OssObjectStorage, S3ObjectStorage
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
from .persistence import IngestPersistencePort, InMemoryIngestPersistence
from .ports import InMemoryObjectStorage, ObjectStoragePort
from .service import UploadSessionService

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
