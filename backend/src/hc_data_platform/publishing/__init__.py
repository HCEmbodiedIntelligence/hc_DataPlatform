"""Compatibility exports. New callers use owned models/ports and adapter modules.

Resolving a contract does not initialize unrelated services, SDKs or workers.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .adapters import (
        ApprovedAnnotationSnapshotAdapter,
        CatalogSnapshotAdapter,
        StepReaderAdapter,
    )
    from .exporters import LanceSnapshotExporter, LeRobotV3Exporter
    from .models import (
        DatasetVersionPublishedV1,
        ExportDatasetRequestV1,
        ExportFormat,
        ExportResultV1,
        PublishDatasetRequestV1,
        PublishedDatasetManifestV1,
    )
    from .ports import ExporterPort
    from .service import DatasetPublisher, ExportCoordinator

__all__ = [
    "ApprovedAnnotationSnapshotAdapter",
    "CatalogSnapshotAdapter",
    "DatasetPublisher",
    "DatasetVersionPublishedV1",
    "ExportCoordinator",
    "ExportDatasetRequestV1",
    "ExportFormat",
    "ExportResultV1",
    "ExporterPort",
    "LanceSnapshotExporter",
    "LeRobotV3Exporter",
    "PublishDatasetRequestV1",
    "PublishedDatasetManifestV1",
    "StepReaderAdapter",
]

_EXPORTS = {
    "ApprovedAnnotationSnapshotAdapter": (".adapters", "ApprovedAnnotationSnapshotAdapter"),
    "CatalogSnapshotAdapter": (".adapters", "CatalogSnapshotAdapter"),
    "StepReaderAdapter": (".adapters", "StepReaderAdapter"),
    "LanceSnapshotExporter": (".exporters", "LanceSnapshotExporter"),
    "LeRobotV3Exporter": (".exporters", "LeRobotV3Exporter"),
    "DatasetVersionPublishedV1": (".models", "DatasetVersionPublishedV1"),
    "ExportDatasetRequestV1": (".models", "ExportDatasetRequestV1"),
    "ExportFormat": (".models", "ExportFormat"),
    "ExportResultV1": (".models", "ExportResultV1"),
    "PublishDatasetRequestV1": (".models", "PublishDatasetRequestV1"),
    "PublishedDatasetManifestV1": (".models", "PublishedDatasetManifestV1"),
    "ExporterPort": (".ports", "ExporterPort"),
    "DatasetPublisher": (".service", "DatasetPublisher"),
    "ExportCoordinator": (".service", "ExportCoordinator"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module, attribute = target
    value = getattr(import_module(module, __name__), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
