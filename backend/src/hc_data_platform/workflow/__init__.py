"""Workflow contracts with lazy compatibility exports for orchestration adapters."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import JobRecord, JobStatus, QualityOutcome, WorkflowKind, workflow_id
    from .service import (
        InMemoryWorkflowLauncher,
        JobStatusPort,
        TemporalWorkflowLauncher,
        WorkflowLauncher,
    )
    from .temporal_workflows import (
        CatalogReconciliationWorkflow as TemporalCatalogReconciliationWorkflow,
    )
    from .temporal_workflows import DatasetWriterWorkflow as TemporalDatasetWriterWorkflow
    from .temporal_workflows import ExportWorkflow as TemporalExportWorkflow
    from .temporal_workflows import IngestRolloutWorkflow as TemporalIngestRolloutWorkflow
    from .temporal_workflows import PublishDatasetWorkflow as TemporalPublishDatasetWorkflow
    from .temporal_workflows import (
        PublishReconciliationWorkflow as TemporalPublishReconciliationWorkflow,
    )
    from .workflows import (
        DatasetWriterWorkflow,
        ExportWorkflow,
        IngestRolloutWorkflow,
        PublishDatasetWorkflow,
    )

__all__ = [
    "DatasetWriterWorkflow",
    "ExportWorkflow",
    "InMemoryWorkflowLauncher",
    "IngestRolloutWorkflow",
    "JobRecord",
    "JobStatus",
    "JobStatusPort",
    "PublishDatasetWorkflow",
    "QualityOutcome",
    "TemporalCatalogReconciliationWorkflow",
    "TemporalDatasetWriterWorkflow",
    "TemporalExportWorkflow",
    "TemporalIngestRolloutWorkflow",
    "TemporalPublishDatasetWorkflow",
    "TemporalPublishReconciliationWorkflow",
    "TemporalWorkflowLauncher",
    "WorkflowKind",
    "WorkflowLauncher",
    "workflow_id",
]

_EXPORTS = {
    "JobRecord": (".models", "JobRecord"),
    "JobStatus": (".models", "JobStatus"),
    "QualityOutcome": (".models", "QualityOutcome"),
    "WorkflowKind": (".models", "WorkflowKind"),
    "workflow_id": (".models", "workflow_id"),
    "InMemoryWorkflowLauncher": (".service", "InMemoryWorkflowLauncher"),
    "JobStatusPort": (".service", "JobStatusPort"),
    "TemporalWorkflowLauncher": (".service", "TemporalWorkflowLauncher"),
    "WorkflowLauncher": (".service", "WorkflowLauncher"),
    "DatasetWriterWorkflow": (".workflows", "DatasetWriterWorkflow"),
    "ExportWorkflow": (".workflows", "ExportWorkflow"),
    "IngestRolloutWorkflow": (".workflows", "IngestRolloutWorkflow"),
    "PublishDatasetWorkflow": (".workflows", "PublishDatasetWorkflow"),
    "TemporalCatalogReconciliationWorkflow": (
        ".temporal_workflows",
        "CatalogReconciliationWorkflow",
    ),
    "TemporalDatasetWriterWorkflow": (".temporal_workflows", "DatasetWriterWorkflow"),
    "TemporalExportWorkflow": (".temporal_workflows", "ExportWorkflow"),
    "TemporalIngestRolloutWorkflow": (".temporal_workflows", "IngestRolloutWorkflow"),
    "TemporalPublishDatasetWorkflow": (".temporal_workflows", "PublishDatasetWorkflow"),
    "TemporalPublishReconciliationWorkflow": (
        ".temporal_workflows",
        "PublishReconciliationWorkflow",
    ),
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
