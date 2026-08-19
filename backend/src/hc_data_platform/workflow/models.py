from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from urllib.parse import quote, unquote
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hc_data_platform.alignment.models import (
    AlignedFragmentManifestV1 as StagedFragmentManifestV1,
)
from hc_data_platform.alignment.models import AlignmentInputV1, AlignmentProfileV1
from hc_data_platform.annotation.models import (
    AnnotationOperation,
    AnnotationRevision,
    AnnotationTaskKind,
    TagSchemaStatus,
    TagSchemaVersion,
)
from hc_data_platform.ingest.models import ManifestPreflightResultV1
from hc_data_platform.lance_catalog.models import (
    AlignedFragmentManifestV1 as CatalogFragmentManifestV1,
)
from hc_data_platform.lance_catalog.models import DatasetVersionRef, DerivedReadyV1, StepRecord
from hc_data_platform.preview.models import PreviewDescriptorV1, PreviewRequestV1
from hc_data_platform.publishing.models import (
    ExportFormat,
    ExportResultV1,
    PublishDatasetRequestV1,
    PublishedDatasetManifestV1,
)
from hc_data_platform.quality.models import QcReportV1, QualityInputV1, QualityProfileV1
from hc_data_platform.verification.models import RawVerificationReportV1


class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    TECHNICAL_FAILED = "TECHNICAL_FAILED"
    QUALITY_RISK = "QUALITY_RISK"
    QUALITY_REJECTED = "QUALITY_REJECTED"
    CANCELLED = "CANCELLED"


TERMINAL_JOB_STATUSES = frozenset(
    {
        JobStatus.SUCCEEDED,
        JobStatus.TECHNICAL_FAILED,
        JobStatus.QUALITY_RISK,
        JobStatus.QUALITY_REJECTED,
        JobStatus.CANCELLED,
    }
)


class QualityOutcome(str, Enum):
    """Compatibility outcome used by the dependency-free reference workflow."""

    PASS = "PASS"
    RISK = "RISK"
    REJECT = "REJECT"


class WorkflowKind(str, Enum):
    INGEST_ROLLOUT = "ingest-rollout"
    DATASET_WRITER = "dataset-writer"
    PREVIEW = "preview"
    PUBLISH_DATASET = "publish-dataset"
    EXPORT = "export"
    CATALOG_RECONCILIATION = "catalog-reconciliation"
    PUBLISH_RECONCILIATION = "publish-reconciliation"
    ANNOTATION_REVIEW_PREPARATION = "annotation-review-preparation"


class JobRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: str = Field(default_factory=lambda: str(uuid4()))
    workflow_id: str
    workflow_run_id: str | None = None
    workflow_version: str = "v1"
    job_type: str
    project_id: str
    resource_id: str
    status: JobStatus = JobStatus.PENDING
    stage: str = "pending"
    attempt: int = 0
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    cancellation_requested: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class JobListV1(BaseModel):
    items: tuple[JobRecord, ...]
    total: int = Field(ge=0)


def _workflow_part(value: str | Enum) -> str:
    raw = value.value if isinstance(value, Enum) else value
    if not isinstance(raw, str) or not raw:
        raise ValueError("workflow identity parts must be non-empty strings")
    return quote(raw, safe="-._~")


def workflow_id(
    kind: str | WorkflowKind,
    project_id: str,
    resource_id: str,
    version: str = "v1",
) -> str:
    """Build the stable ``kind+version+project+resource`` Temporal identity."""

    return ":".join(
        (
            _workflow_part(kind),
            _workflow_part(version),
            _workflow_part(project_id),
            _workflow_part(resource_id),
        )
    )


def parse_workflow_id(value: str) -> tuple[str, str, str, str]:
    parts = value.split(":")
    if len(parts) != 4 or any(not part for part in parts):
        raise ValueError("workflow ID must contain kind, version, project, and resource")
    return tuple(unquote(part) for part in parts)  # type: ignore[return-value]


class VerificationActivityInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str | None = Field(default=None, min_length=1)
    region_code: str | None = Field(default=None, min_length=1)
    rollout_id: str = Field(min_length=1)
    object_key: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    required_topics: frozenset[str]
    known_optional_topics: frozenset[str] = frozenset()


class VerificationActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    report: RawVerificationReportV1


class ManifestActivityInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str = Field(min_length=1)
    region_code: str = Field(min_length=1)
    rollout_id: str = Field(min_length=1)
    data_package_id: str = Field(min_length=1)
    manifest_key: str = Field(min_length=1, max_length=2048)
    manifest_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ManifestActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    preflight: ManifestPreflightResultV1


class QualityActivityInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str | None = Field(default=None, min_length=1)
    region_code: str | None = Field(default=None, min_length=1)
    data: QualityInputV1
    profile: QualityProfileV1
    decision_source: Literal["AUTOMATIC"] = "AUTOMATIC"


class QualityActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    report: QcReportV1


class AlignmentActivityInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str = Field(min_length=1)
    region_code: str | None = Field(default=None, min_length=1)
    dataset_id: str = Field(min_length=1)
    schema_snapshot_id: str = Field(min_length=1)
    data: AlignmentInputV1
    profile: AlignmentProfileV1


class CatalogFragmentPayloadV1(BaseModel):
    """Adapter output handed unchanged to the Lance catalog port."""

    model_config = ConfigDict(frozen=True)

    manifest: CatalogFragmentManifestV1
    steps: tuple[StepRecord, ...]


class AlignmentActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    staged_manifest: StagedFragmentManifestV1
    catalog_fragment: CatalogFragmentPayloadV1


class CatalogCommitActivityInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    fragment: CatalogFragmentPayloadV1


class CatalogCommitActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: DatasetVersionRef
    derived_ready: DerivedReadyV1


class AutomaticAnnotationActivityInput(BaseModel):
    """Immutable Lance hand-off used only by the ingest workflow."""

    model_config = ConfigDict(frozen=True)

    project_id: str = Field(min_length=1)
    region_code: str = Field(min_length=1)
    rollout_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_version: int = Field(ge=1)
    lance_version: int = Field(ge=1)
    dataset_schema_snapshot_id: str = Field(min_length=1)
    base_step_count: int = Field(gt=0)
    source_workflow_id: str = Field(min_length=1)
    task_kind: AnnotationTaskKind = AnnotationTaskKind.TAGGING


class AutomaticAnnotationActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str = Field(min_length=1)
    status: Literal["CREATED"] = "CREATED"


class PreviewActivityInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    request: PreviewRequestV1


class PreviewActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    descriptor: PreviewDescriptorV1


class PublishActivityInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    request: PublishDatasetRequestV1


class PublishActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    manifest: PublishedDatasetManifestV1


class ExportActivityInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    manifest: PublishedDatasetManifestV1
    format: ExportFormat


class ExportActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    result: ExportResultV1


class CatalogReconciliationActivityInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)


class CatalogReconciliationActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    repaired_versions: tuple[DatasetVersionRef, ...] = ()


class PublishReconciliationActivityInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    request: PublishDatasetRequestV1


class PublishReconciliationActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    manifest: PublishedDatasetManifestV1


class IngestRolloutWorkflowInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str = Field(min_length=1)
    region_code: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    rollout_id: str = Field(min_length=1)
    automatic_qc_run_id: str = Field(default="initial", min_length=1, max_length=128)
    manifest: ManifestActivityInput
    verification: VerificationActivityInput
    quality: QualityActivityInput
    alignment: AlignmentActivityInput

    @model_validator(mode="after")
    def consistent_lineage(self) -> IngestRolloutWorkflowInput:
        rollout_ids = {
            self.rollout_id,
            self.manifest.rollout_id,
            self.verification.rollout_id,
            self.quality.data.rollout_id,
            self.alignment.data.rollout_id,
        }
        source_hashes = {
            self.verification.source_sha256,
            self.quality.data.source_sha256,
            self.alignment.data.source_sha256,
        }
        if len(rollout_ids) != 1:
            raise ValueError("all ingest activity inputs must reference the same rollout")
        if len(source_hashes) != 1:
            raise ValueError("all ingest activity inputs must reference the same Raw hash")
        if self.manifest.source_sha256 not in source_hashes:
            raise ValueError("Manifest and ingest activity inputs must reference the same Raw hash")
        project_ids = {
            self.manifest.project_id,
            self.verification.project_id,
            self.quality.project_id,
            self.alignment.project_id,
        }
        if project_ids != {self.project_id}:
            raise ValueError("all ingest activity project_ids must match workflow project_id")
        region_codes = {
            self.manifest.region_code,
            self.verification.region_code,
            self.quality.region_code,
            self.alignment.region_code,
        }
        if region_codes != {self.region_code}:
            raise ValueError("all ingest activity region_codes must match workflow region_code")
        if self.alignment.dataset_id != self.dataset_id:
            raise ValueError("alignment dataset_id must match workflow dataset_id")
        return self


class DatasetWriterWorkflowInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    commit: CatalogCommitActivityInput


class PreviewWorkflowInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    request: PreviewRequestV1


class PublishDatasetWorkflowInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    request: PublishDatasetRequestV1


class ExportWorkflowInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    manifest: PublishedDatasetManifestV1
    format: ExportFormat


class CatalogReconciliationWorkflowInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)


class PublishReconciliationWorkflowInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    request: PublishDatasetRequestV1


class AnnotationReviewPreparationWorkflowInput(BaseModel):
    """Complete deterministic input for producing one reviewable annotation version."""

    model_config = ConfigDict(frozen=True)

    project_id: str = Field(min_length=1)
    submitted_by: str = Field(min_length=1)
    base_step_count: int | None = Field(default=None, gt=0)
    tag_schema: TagSchemaVersion
    revision: AnnotationRevision
    cumulative_operations: tuple[AnnotationOperation, ...]

    @model_validator(mode="after")
    def fixed_references_match(self) -> AnnotationReviewPreparationWorkflowInput:
        if self.tag_schema.project_id != self.project_id:
            raise ValueError("Tag Schema project_id must match workflow project_id")
        if self.tag_schema.status != TagSchemaStatus.PUBLISHED:
            raise ValueError("review preparation requires a published Tag Schema")
        if self.revision.base_lance_version < 1:
            raise ValueError("revision must pin a positive Lance version")
        if (
            self.revision.tag_schema_id,
            self.revision.tag_schema_version,
        ) != (self.tag_schema.schema_id, self.tag_schema.version):
            raise ValueError("revision must pin the supplied Tag Schema version")
        return self
