from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .models import (
    ApprovedAnnotationSnapshotV1,
    CatalogRolloutSnapshotV1,
    ExportFormat,
    ExportResultV1,
    ExportStepV1,
    PublishedDatasetManifestV1,
    StepRangeV1,
)


@runtime_checkable
class CatalogSnapshotPort(Protocol):
    def list_rollouts(
        self, *, project_id: str, dataset_id: str, lance_version: str
    ) -> Sequence[CatalogRolloutSnapshotV1]: ...


@runtime_checkable
class AnnotationSnapshotPort(Protocol):
    """Returns only an approved, immutable revision; drafts are never publishable."""

    def get_approved_revision(
        self,
        *,
        project_id: str,
        rollout_id: str,
        dataset_id: str,
        lance_version: str,
    ) -> ApprovedAnnotationSnapshotV1 | None: ...


@runtime_checkable
class PublishedManifestRepository(Protocol):
    def create_immutable(
        self, manifest: PublishedDatasetManifestV1
    ) -> PublishedDatasetManifestV1: ...

    def get(
        self, *, project_id: str, dataset_id: str, dataset_version: str
    ) -> PublishedDatasetManifestV1 | None: ...


@runtime_checkable
class PublicationAssetSinkPort(Protocol):
    """Immutable storage for deterministic publication-owned logical assets."""

    def put_immutable(self, artifact_uri: str, content: bytes) -> None: ...


@runtime_checkable
class ExportSourcePort(Protocol):
    """A production implementation reads synchronized logical steps from Lance."""

    def read_steps(
        self,
        *,
        project_id: str,
        dataset_id: str,
        rollout_id: str,
        lance_version: str,
        ranges: Sequence[StepRangeV1],
    ) -> Sequence[ExportStepV1]: ...


@runtime_checkable
class ArtifactSinkPort(Protocol):
    """Attempt-isolated export storage with explicit validated promotion."""

    def stage_attempt(self, attempt_id: str, content: bytes) -> str: ...

    def read_attempt(self, attempt_id: str) -> bytes: ...

    def publish_attempt(
        self,
        *,
        attempt_id: str,
        artifact_uri: str,
        expected_sha256: str,
    ) -> str:
        """Atomically promote staged bytes and return a download authorization URI."""

    def get_published(self, artifact_uri: str) -> bytes | None: ...

    def get_download_uri(self, artifact_uri: str) -> str | None: ...


@runtime_checkable
class ExporterPort(Protocol):
    @property
    def format(self) -> ExportFormat: ...

    def export(
        self,
        *,
        manifest: PublishedDatasetManifestV1,
        source: ExportSourcePort,
        sink: ArtifactSinkPort,
        attempt_id: str,
    ) -> ExportResultV1: ...
