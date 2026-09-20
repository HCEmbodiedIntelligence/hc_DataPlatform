from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from urllib.parse import quote, unquote
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hc_data_platform.aligned_media.models import (
    AlignedMediaArtifactV1,
    AlignedMediaGenerationRequestV1,
    AlignmentStagingArtifactV1,
)
from hc_data_platform.alignment.models import (
    AlignedFragmentManifestV1 as StagedFragmentManifestV1,
)
from hc_data_platform.alignment.models import AlignmentInputV1, AlignmentProfileV1
from hc_data_platform.annotation.models import (
    AnnotationOperation,
    AnnotationRevision,
    AnnotationTaskKind,
    AutoAnnotationSamplingReference,
    TagSchemaStatus,
    TagSchemaVersion,
)
from hc_data_platform.continuous_recordings.asset_models import (
    EpisodeProcessing,
    EpisodeProcessingStatus,
    RecordingAssetRole,
    RecordingConfigurationV1,
)
from hc_data_platform.dataset_registry.models import DatasetIngestViewerTarget
from hc_data_platform.ingest.models import ManifestPreflightResultV1
from hc_data_platform.lance_catalog.models import (
    AlignedFragmentManifestV1 as CatalogFragmentManifestV1,
)
from hc_data_platform.lance_catalog.models import DatasetVersionRef, DerivedReadyV1, StepRecord
from hc_data_platform.lerobot_imports.orchestration import LeRobotEpisodeSourceRefV1
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
    CONTINUOUS_EPISODE = "continuous-episode"
    DATASET_WRITER = "dataset-writer"
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


class WorkflowJobPersistenceActivityInput(BaseModel):
    """Verified tenant scope plus the latest durable workflow job snapshot."""

    model_config = ConfigDict(frozen=True)

    organization_id: str = Field(min_length=1)
    region_code: str = Field(min_length=1)
    job: JobRecord


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

    organization_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    region_code: str = Field(min_length=1)
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


class FrameSelectionManifestRefV1(BaseModel):
    """Small immutable reference; candidate frames never enter Temporal history."""

    model_config = ConfigDict(frozen=True)

    object_key: str = Field(min_length=1, max_length=2048)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    sampling_version: str = Field(default="adaptive-2fps-v1", min_length=1)
    source_frame_count: int = Field(ge=0)
    selected_group_count: int = Field(ge=0)
    camera_set: tuple[str, ...] = ()


class ProjectionMaterializationV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    object_key: str = Field(min_length=1, max_length=2048)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    projection_version: str = Field(default="arrow-projection-v1", min_length=1)
    created_at: datetime
    expires_at: datetime
    frame_selection: FrameSelectionManifestRefV1

    @model_validator(mode="after")
    def validate_expiry(self) -> ProjectionMaterializationV1:
        if self.expires_at <= self.created_at:
            raise ValueError("projection materialization must expire after creation")
        return self


class IngestProjectionSourceV1(BaseModel):
    """Compact immutable locator resolved inside Worker activities.

    Raw observations never cross the Temporal history boundary.  Activities use
    this locator to reload and validate the committed MCAP before evaluating QC or
    writing aligned fragments.
    """

    model_config = ConfigDict(frozen=True)

    organization_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    region_code: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    rollout_id: str = Field(min_length=1)
    data_package_id: str = Field(min_length=1)
    object_key: str = Field(min_length=1, max_length=2048)
    manifest_key: str = Field(min_length=1, max_length=2048)
    manifest_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    materialization: ProjectionMaterializationV1 | None = None
    lerobot: LeRobotEpisodeSourceRefV1 | None = None


class ProjectionMaterializationActivityInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: IngestProjectionSourceV1


class ProjectionMaterializationActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: IngestProjectionSourceV1


class ProjectionCleanupActivityInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: IngestProjectionSourceV1


class ProjectionCleanupActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    deleted: bool = True


class QualityActivityInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    organization_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    region_code: str = Field(min_length=1)
    data: QualityInputV1 | None = None
    source: IngestProjectionSourceV1 | None = None
    profile: QualityProfileV1
    decision_source: Literal["AUTOMATIC"] = "AUTOMATIC"

    @model_validator(mode="after")
    def exactly_one_data_source(self) -> QualityActivityInput:
        if (self.data is None) == (self.source is None):
            raise ValueError("quality input requires exactly one of data or source")
        if self.source is not None and (
            self.organization_id != self.source.organization_id
            or self.project_id != self.source.project_id
            or self.region_code != self.source.region_code
        ):
            raise ValueError("quality source scope must match the activity scope")
        return self


class QualityActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    report: QcReportV1


class AlignmentActivityInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    organization_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    region_code: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    schema_snapshot_id: str = Field(min_length=1)
    data: AlignmentInputV1 | None = None
    source: IngestProjectionSourceV1 | None = None
    profile: AlignmentProfileV1

    @model_validator(mode="after")
    def exactly_one_data_source(self) -> AlignmentActivityInput:
        if (self.data is None) == (self.source is None):
            raise ValueError("alignment input requires exactly one of data or source")
        if self.source is not None and (
            self.project_id != self.source.project_id or self.region_code != self.source.region_code
        ):
            raise ValueError("alignment source scope must match the activity scope")
        return self


class CatalogFragmentPayloadV1(BaseModel):
    """Adapter output handed unchanged to the Lance catalog port."""

    model_config = ConfigDict(frozen=True)

    manifest: CatalogFragmentManifestV1
    steps: tuple[StepRecord, ...]


class AlignmentActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    staged_manifest: StagedFragmentManifestV1
    catalog_fragment: CatalogFragmentPayloadV1 | None = None
    dataset_version: DatasetVersionRef | None = None
    derived_ready: DerivedReadyV1 | None = None
    viewer_target: DatasetIngestViewerTarget | None = None
    alignment_staging: AlignmentStagingArtifactV1 | None = None
    expected_dataset_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def complete_inline_commit(self) -> AlignmentActivityOutput:
        if (self.dataset_version is None) != (self.derived_ready is None):
            raise ValueError("inline alignment commit must return both version results")
        if (
            self.dataset_version is not None
            and self.catalog_fragment is not None
            and self.catalog_fragment.steps
        ):
            raise ValueError("inline alignment commit must not return Step payloads")
        if self.viewer_target is not None:
            if self.dataset_version is None or self.derived_ready is None:
                raise ValueError("viewer target requires a complete inline Lance commit")
            if (
                self.viewer_target.dataset_id != self.dataset_version.dataset_id
                or self.viewer_target.version_id != f"version_lance_{self.dataset_version.version}"
            ):
                raise ValueError("viewer target must match the immutable Dataset version")
        return self


class IngestSourceProcessingActivityInput(BaseModel):
    """One worker-local Raw download used for verification, QC, and alignment."""

    model_config = ConfigDict(frozen=True)

    verification: VerificationActivityInput
    quality: QualityActivityInput
    alignment: AlignmentActivityInput

    @model_validator(mode="after")
    def matching_source_lineage(self) -> IngestSourceProcessingActivityInput:
        quality_source = self.quality.source
        alignment_source = self.alignment.source
        if quality_source is None or alignment_source is None or quality_source != alignment_source:
            raise ValueError("combined ingest processing requires one shared raw source")
        if (
            self.verification.project_id != quality_source.project_id
            or self.verification.region_code != quality_source.region_code
            or self.verification.rollout_id != quality_source.rollout_id
            or self.verification.object_key != quality_source.object_key
            or self.verification.source_sha256 != quality_source.source_sha256
        ):
            raise ValueError("verification lineage must match the shared raw source")
        return self


class IngestSourceProcessingActivityOutput(BaseModel):
    """Bounded results only; raw samples and temporary files never enter history."""

    model_config = ConfigDict(frozen=True)

    verification: VerificationActivityOutput
    quality: QualityActivityOutput | None = None
    alignment: AlignmentActivityOutput | None = None
    frame_selection: FrameSelectionManifestRefV1 | None = None


class AlignedBundleCommitActivityInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    alignment: AlignmentActivityInput
    staged_manifest: StagedFragmentManifestV1
    alignment_staging: AlignmentStagingArtifactV1
    expected_dataset_version: int = Field(ge=1)
    expected_camera_ids: tuple[str, ...]
    media_artifacts: tuple[AlignedMediaArtifactV1, ...]


class AlignedBundleCommitActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: DatasetVersionRef
    derived_ready: DerivedReadyV1
    viewer_target: DatasetIngestViewerTarget


class ContinuousEpisodeAssetV1(BaseModel):
    """Immutable raw object receipt carried in the internal Temporal input."""

    model_config = ConfigDict(frozen=True)

    asset_id: str = Field(min_length=1)
    role: RecordingAssetRole
    camera_id: str | None = Field(default=None, min_length=1)
    modality_key: str | None = Field(default=None, min_length=1, max_length=512)
    media_type: str = Field(min_length=1, max_length=255)
    object_key: str = Field(min_length=1, max_length=2048)
    size_bytes: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ContinuousEpisodeProjectionSourceV1(BaseModel):
    """Product projection facts for one soft-sliced recording Episode."""

    model_config = ConfigDict(frozen=True)

    organization_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    region_code: str = Field(min_length=1)
    recording_id: str = Field(min_length=1)
    recording_upload_id: str = Field(min_length=1)
    episode_id: str = Field(min_length=1)
    rollout_id: str = Field(min_length=1)
    data_package_id: str = Field(min_length=1)
    collection_task_id: str = Field(min_length=1)
    robot_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_size_bytes: int = Field(ge=1)
    started_at: datetime
    ended_at: datetime
    camera_modality_keys: tuple[str, ...] = Field(min_length=1, max_length=128)


class ContinuousEpisodeWorkflowInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    projection: ContinuousEpisodeProjectionSourceV1
    dataset_id: str = Field(min_length=1)
    schema_snapshot_id: str = Field(min_length=1)
    recording_config: RecordingConfigurationV1
    assets: tuple[ContinuousEpisodeAssetV1, ...] = Field(min_length=3, max_length=256)
    quality_profile: QualityProfileV1
    alignment_profile: AlignmentProfileV1
    start_offset_ns: int = Field(ge=0)
    end_offset_ns: int = Field(gt=0)
    expected_dataset_version: int = Field(ge=1)
    media_task_queue: str = Field(default="hc-media-pipeline", min_length=1, max_length=255)

    @model_validator(mode="after")
    def validate_lineage(self) -> ContinuousEpisodeWorkflowInput:
        projection = self.projection
        if self.end_offset_ns <= self.start_offset_ns:
            raise ValueError("continuous Episode requires a non-empty source window")
        camera_assets = [item for item in self.assets if item.role is RecordingAssetRole.RAW_VIDEO]
        sensor_assets = [
            item for item in self.assets if item.role is RecordingAssetRole.SENSOR_DATA
        ]
        config_assets = [
            item for item in self.assets if item.role is RecordingAssetRole.RECORDING_CONFIG
        ]
        configured = {
            camera.camera_id: camera.modality_key for camera in self.recording_config.cameras
        }
        observed = {item.camera_id: item.modality_key for item in camera_assets}
        if observed != configured:
            raise ValueError("continuous Episode video assets do not match recording config")
        if not sensor_assets or len(config_assets) != 1:
            raise ValueError("continuous Episode requires sensor and config assets")
        if set(projection.camera_modality_keys) != set(configured.values()):
            raise ValueError("projection camera keys do not match recording config")
        expected_modalities = set(projection.camera_modality_keys) | {
            sensor.topic for sensor in self.recording_config.sensors
        }
        if self.quality_profile.required_topics != expected_modalities:
            raise ValueError("quality profile must exactly match configured Episode modalities")
        if self.alignment_profile.required_modalities != expected_modalities:
            raise ValueError("alignment profile must exactly match configured Episode modalities")
        if self.alignment_profile.frequency_hz != 30:
            raise ValueError("continuous Episode alignment must use the canonical 30 Hz timeline")
        return self


class ContinuousEpisodeStateActivityInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    organization_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    region_code: str = Field(min_length=1)
    recording_id: str = Field(min_length=1)
    episode_id: str = Field(min_length=1)
    expected_status: EpisodeProcessingStatus
    new_status: EpisodeProcessingStatus
    qc_report_id: str | None = Field(default=None, min_length=1, max_length=256)
    alignment_attempt_id: str | None = Field(default=None, min_length=1, max_length=256)
    dataset_id: str | None = Field(default=None, min_length=1, max_length=128)
    dataset_version: int | None = Field(default=None, ge=1)
    lance_version: int | None = Field(default=None, ge=1)
    annotation_task_id: str | None = Field(default=None, min_length=1, max_length=128)
    aligned_media_camera_count: int | None = Field(default=None, ge=1, le=128)
    failure_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]+$")
    failure_stage: str | None = Field(default=None, min_length=1, max_length=128)


class ContinuousEpisodeStateActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    episode: EpisodeProcessing


class ContinuousEpisodeQcActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    report_id: str = Field(min_length=1, max_length=256)
    status: Literal["PASS", "REJECT"]
    finding_codes: tuple[str, ...] = ()
    inspected_video_count: int = Field(ge=1, le=128)
    inspected_sensor_message_count: int = Field(ge=0)


class ContinuousEpisodeAlignmentActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    alignment: AlignmentActivityInput
    staged_manifest: StagedFragmentManifestV1
    alignment_staging: AlignmentStagingArtifactV1
    expected_dataset_version: int = Field(ge=1)


class ContinuousEpisodeBundleCommitActivityInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    workflow_input: ContinuousEpisodeWorkflowInput
    alignment: AlignmentActivityInput
    staged_manifest: StagedFragmentManifestV1
    alignment_staging: AlignmentStagingArtifactV1
    expected_dataset_version: int = Field(ge=1)
    expected_camera_ids: tuple[str, ...] = Field(min_length=1, max_length=128)
    media_artifacts: tuple[AlignedMediaArtifactV1, ...] = Field(min_length=1, max_length=128)


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

    # Optional only so an activity already scheduled by a pre-018 workflow can
    # still be decoded during a rolling deployment. New workflows always carry
    # the verified organization scope and the activity rejects a missing value.
    organization_id: str | None = Field(default=None, min_length=1)
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
    frame_selection: AutoAnnotationSamplingReference | None = None


class AutomaticAnnotationActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str = Field(min_length=1)
    status: Literal["CREATED"] = "CREATED"


class AlignedMediaActivityInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    organization_id: str = Field(min_length=1)
    region_code: str = Field(min_length=1)
    request: AlignedMediaGenerationRequestV1


class AlignedMediaActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact: AlignedMediaArtifactV1


class AlignedMediaCleanupActivityInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    organization_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    region_code: str = Field(min_length=1)
    artifacts: tuple[AlignedMediaArtifactV1, ...] = Field(min_length=1)


class AlignedMediaCleanupActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    deleted_bytes: int = Field(ge=0)


class AlignmentStagingCleanupActivityInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    organization_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    region_code: str = Field(min_length=1)
    staging: AlignmentStagingArtifactV1


class AlignmentStagingCleanupActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    deleted: bool = True


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
    attempt_id: str = Field(min_length=1)


class ExportActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    result: ExportResultV1


class ExportPreflightActivityInput(BaseModel):
    """Validate immutable source facts before export materialization."""

    model_config = ConfigDict(frozen=True)

    manifest: PublishedDatasetManifestV1
    format: ExportFormat


class ExportPreflightActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    row_count: int = Field(ge=0)


class ExportArtifactVerificationActivityInput(BaseModel):
    """Internal-only result used to re-read and verify a promoted artifact."""

    model_config = ConfigDict(frozen=True)

    result: ExportResultV1


class ExportArtifactVerificationActivityOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    verified: bool = True


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

    organization_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    region_code: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    rollout_id: str = Field(min_length=1)
    automatic_qc_run_id: str = Field(default="initial", min_length=1, max_length=128)
    media_task_queue: str = Field(min_length=1, max_length=255)
    manifest: ManifestActivityInput
    verification: VerificationActivityInput
    quality: QualityActivityInput
    alignment: AlignmentActivityInput

    @model_validator(mode="after")
    def consistent_lineage(self) -> IngestRolloutWorkflowInput:
        quality_lineage = self.quality.data or self.quality.source
        alignment_lineage = self.alignment.data or self.alignment.source
        if quality_lineage is None or alignment_lineage is None:
            raise ValueError("ingest activity lineage is missing")
        rollout_ids = {
            self.rollout_id,
            self.manifest.rollout_id,
            self.verification.rollout_id,
            quality_lineage.rollout_id,
            alignment_lineage.rollout_id,
        }
        source_hashes = {
            self.verification.source_sha256,
            quality_lineage.source_sha256,
            alignment_lineage.source_sha256,
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
        source_organizations = {
            source.organization_id
            for source in (self.quality.source, self.alignment.source)
            if source is not None
        }
        if len(source_organizations) > 1:
            raise ValueError("all ingest projection sources must match one organization_id")
        if source_organizations and source_organizations != {self.organization_id}:
            raise ValueError("ingest organization_id must match every projection source")
        if self.verification.organization_id != self.organization_id:
            raise ValueError("verification organization_id must match workflow organization_id")
        if self.alignment.organization_id != self.organization_id:
            raise ValueError("alignment organization_id must match workflow organization_id")
        return self


class DatasetWriterWorkflowInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    commit: CatalogCommitActivityInput


class PublishDatasetWorkflowInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    request: PublishDatasetRequestV1


class ExportWorkflowInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    manifest: PublishedDatasetManifestV1
    format: ExportFormat
    attempt_id: str = Field(min_length=1)
    selection: dict[str, Any] | None = None


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
