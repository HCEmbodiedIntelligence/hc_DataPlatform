"""Durable workflow contracts and deterministic reference orchestration."""

from .models import JobRecord, JobStatus, QualityOutcome, WorkflowKind, workflow_id
from .service import (
    InMemoryWorkflowLauncher,
    JobStatusPort,
    TemporalWorkflowLauncher,
    WorkflowLauncher,
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

_TEMPORAL_EXPORTS = {
    "TemporalCatalogReconciliationWorkflow": "CatalogReconciliationWorkflow",
    "TemporalDatasetWriterWorkflow": "DatasetWriterWorkflow",
    "TemporalExportWorkflow": "ExportWorkflow",
    "TemporalIngestRolloutWorkflow": "IngestRolloutWorkflow",
    "TemporalPublishDatasetWorkflow": "PublishDatasetWorkflow",
    "TemporalPublishReconciliationWorkflow": "PublishReconciliationWorkflow",
}


def __getattr__(name: str) -> object:
    temporal_name = _TEMPORAL_EXPORTS.get(name)
    if temporal_name is None:
        raise AttributeError(name)
    from . import temporal_workflows

    return getattr(temporal_workflows, temporal_name)
