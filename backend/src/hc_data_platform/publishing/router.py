from __future__ import annotations

from fastapi import APIRouter

from hc_data_platform.security import Permission
from hc_data_platform.security.http import VerifiedAuth, authorize_read, authorize_scope

from .memory import (
    InMemoryAnnotationSnapshot,
    InMemoryArtifactSink,
    InMemoryCatalogSnapshot,
    InMemoryExportSource,
    InMemoryPublishedManifestRepository,
)
from .models import (
    ExportDatasetRequestV1,
    ExportResultV1,
    PublishDatasetRequestV1,
    PublishedDatasetManifestV1,
    PublishPreflightReportV1,
)
from .service import DatasetPublisher, ExportCoordinator

router = APIRouter(prefix="/api/v1/datasets", tags=["publishing"])

_artifact_sink = InMemoryArtifactSink()
_publisher = DatasetPublisher(
    catalog=InMemoryCatalogSnapshot(),
    annotations=InMemoryAnnotationSnapshot(),
    repository=InMemoryPublishedManifestRepository(),
    artifact_sink=_artifact_sink,
)
_export_coordinator = ExportCoordinator(
    source=InMemoryExportSource(),
    sink=_artifact_sink,
    exporters=(),
)


def configure_dataset_publisher(publisher: DatasetPublisher) -> None:
    """Application composition hook for catalog, annotation and durable manifest adapters."""

    global _publisher
    _publisher = publisher


def configure_export_coordinator(coordinator: ExportCoordinator) -> None:
    """Application composition hook for native exporter and durable staging adapters."""

    global _export_coordinator
    _export_coordinator = coordinator


@router.post("/publication-preflight", response_model=PublishPreflightReportV1)
def publication_preflight(
    request: PublishDatasetRequestV1,
    auth: VerifiedAuth,
) -> PublishPreflightReportV1:
    authorize_scope(auth, request.project_id, Permission.PUBLISH)
    return _publisher.preflight(request)


@router.post("/publications", response_model=PublishedDatasetManifestV1, status_code=201)
def publish_dataset(
    request: PublishDatasetRequestV1,
    auth: VerifiedAuth,
) -> PublishedDatasetManifestV1:
    authorize_scope(auth, request.project_id, Permission.PUBLISH)
    return _publisher.publish(request)


@router.get(
    "/{dataset_id}/versions/{dataset_version}",
    response_model=PublishedDatasetManifestV1,
)
def get_dataset_version(
    dataset_id: str,
    dataset_version: str,
    project_id: str,
    auth: VerifiedAuth,
) -> PublishedDatasetManifestV1:
    authorize_read(auth, project_id)
    return _publisher.get(
        project_id=project_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
    )


@router.post(
    "/{dataset_id}/versions/{dataset_version}/exports",
    response_model=ExportResultV1,
    status_code=201,
)
def export_dataset_version(
    dataset_id: str,
    dataset_version: str,
    request: ExportDatasetRequestV1,
    auth: VerifiedAuth,
) -> ExportResultV1:
    authorize_scope(auth, request.project_id, Permission.PUBLISH)
    manifest = _publisher.get(
        project_id=request.project_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
    )
    return _export_coordinator.export(
        manifest,
        format=request.format,
        attempt_id=request.attempt_id,
    )
