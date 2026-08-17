from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

SHA256_PATTERN = r"^[0-9a-f]{64}$"


class QualityStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASS = "PASS"
    RISK = "RISK"
    REJECT = "REJECT"


class DerivedStatus(str, Enum):
    PENDING = "PENDING"
    CONVERTING = "CONVERTING"
    STAGED = "STAGED"
    COMMITTING = "COMMITTING"
    DERIVED_READY = "DERIVED_READY"
    FAILED = "FAILED"


class DatasetVersionStatus(str, Enum):
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class ExportFormat(str, Enum):
    """Phase-one export formats.

    HDF5 and standalone Parquet are intentionally absent.  LeRobot v3 uses
    Parquet internally as required by its native on-disk contract.
    """

    LANCE_SNAPSHOT = "lance_snapshot"
    LEROBOT_V3 = "lerobot_v3"


class StepRangeV1(BaseModel):
    """A half-open logical step range ``[start_step, end_step)``."""

    model_config = ConfigDict(frozen=True)

    start_step: int = Field(ge=0)
    end_step: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> StepRangeV1:
        if self.end_step <= self.start_step:
            raise ValueError("end_step must be greater than start_step")
        return self


class CatalogRolloutSnapshotV1(BaseModel):
    """Immutable catalog and readiness facts for one rollout."""

    model_config = ConfigDict(frozen=True)

    rollout_id: str = Field(min_length=1)
    source_mcap_sha256: str = Field(pattern=SHA256_PATTERN)
    total_steps: int = Field(gt=0)
    quality_status: QualityStatus
    derived_status: DerivedStatus
    quality_profile_version: str = Field(min_length=1)
    alignment_profile_version: str = Field(default="alignment-default", min_length=1)
    alignment_frequency_hz: int = Field(default=30, gt=0)
    converter_version: str = Field(min_length=1)


class ApprovedAnnotationSnapshotV1(BaseModel):
    """The exact currently-approved annotation revision consumed by publication."""

    model_config = ConfigDict(frozen=True)

    rollout_id: str = Field(min_length=1)
    annotation_revision: int = Field(ge=0)
    annotation_task_id: str | None = None
    excluded_step_ranges: tuple[StepRangeV1, ...] = Field(
        default=(),
        validation_alias=AliasChoices("excluded_step_ranges", "excluded_ranges"),
    )

    @property
    def excluded_ranges(self) -> tuple[StepRangeV1, ...]:
        """Compatibility name retained for the original in-memory contract."""

        return self.excluded_step_ranges


class PublishDatasetRequestV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    base_lance_version: str = Field(min_length=1)


class PublishedRolloutV1(BaseModel):
    """Complete frozen training lineage for one eligible rollout."""

    model_config = ConfigDict(frozen=True)

    rollout_id: str = Field(min_length=1)
    source_mcap_sha256: str = Field(pattern=SHA256_PATTERN)
    base_lance_version: str = Field(min_length=1)
    annotation_revision: int = Field(ge=0)
    annotation_task_id: str | None = None
    quality_profile_version: str = Field(min_length=1)
    alignment_profile_version: str = Field(min_length=1)
    alignment_frequency_hz: int = Field(gt=0)
    converter_version: str = Field(min_length=1)
    total_steps: int = Field(gt=0)
    included_step_ranges: tuple[StepRangeV1, ...] = Field(
        validation_alias=AliasChoices("included_step_ranges", "included_ranges")
    )
    excluded_step_ranges: tuple[StepRangeV1, ...] = Field(
        default=(),
        validation_alias=AliasChoices("excluded_step_ranges", "excluded_ranges"),
    )

    @property
    def included_ranges(self) -> tuple[StepRangeV1, ...]:
        """Compatibility name retained for existing BE-11 consumers."""

        return self.included_step_ranges

    @property
    def excluded_ranges(self) -> tuple[StepRangeV1, ...]:
        """Compatibility name retained for existing BE-11 consumers."""

        return self.excluded_step_ranges


class ExcludedRolloutV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    rollout_id: str
    reasons: tuple[str, ...]
    quality_status: QualityStatus
    derived_status: DerivedStatus


class PublishPreflightReportV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str
    dataset_id: str
    dataset_version: str
    eligible_rollouts: tuple[PublishedRolloutV1, ...] = ()
    excluded_rollouts: tuple[ExcludedRolloutV1, ...] = ()


class PublicationAssetV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    uri: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    content_sha256: str = Field(pattern=SHA256_PATTERN)
    size_bytes: int = Field(ge=0)


class PublishedDatasetManifestV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "published-dataset-manifest/v1"
    project_id: str
    dataset_id: str
    dataset_version: str
    base_lance_version: str
    created_at: datetime
    content_hash: str = Field(pattern=SHA256_PATTERN)
    annotations_uri: str
    annotations_content_sha256: str = Field(pattern=SHA256_PATTERN)
    training_manifest_uri: str
    training_manifest_content_sha256: str = Field(pattern=SHA256_PATTERN)
    rollouts: tuple[PublishedRolloutV1, ...]
    excluded_rollouts: tuple[ExcludedRolloutV1, ...] = ()

    @property
    def annotations_asset(self) -> PublicationAssetV1:
        return PublicationAssetV1(
            uri=self.annotations_uri,
            media_type="application/vnd.hc.annotations-lance+json",
            content_sha256=self.annotations_content_sha256,
            size_bytes=0,
        )


class DatasetVersionPublishedV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: str = "DatasetVersionPublishedV1"
    schema_version: int = 1
    project_id: str
    dataset_id: str
    dataset_version: str
    manifest_content_hash: str = Field(pattern=SHA256_PATTERN)


class ExportStepV1(BaseModel):
    """One synchronized logical Step supplied to an exporter."""

    model_config = ConfigDict(frozen=True)

    rollout_id: str
    step_index: int = Field(ge=0)
    timestamp_ns: int = Field(ge=0)
    modalities: dict[str, Any]
    source_timestamps_ns: dict[str, tuple[int, ...]] = Field(default_factory=dict)
    time_error_ns: dict[str, int | None] = Field(default_factory=dict)
    valid: dict[str, bool] = Field(default_factory=dict)
    repeated: dict[str, bool] = Field(default_factory=dict)
    sample_valid: bool = True

    @field_validator("source_timestamps_ns", mode="before")
    @classmethod
    def normalize_source_timestamp_provenance(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        return {
            key: () if item is None else (item,) if isinstance(item, int) else item
            for key, item in value.items()
        }


class ExportDatasetRequestV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str = Field(min_length=1)
    format: ExportFormat
    attempt_id: str | None = Field(default=None, min_length=1)


class ExportResultV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    format: ExportFormat
    project_id: str
    dataset_id: str
    dataset_version: str
    manifest_content_hash: str = Field(pattern=SHA256_PATTERN)
    attempt_id: str = Field(min_length=1)
    artifact_uri: str
    download_uri: str
    artifact_content_hash: str = Field(pattern=SHA256_PATTERN)
    row_count: int = Field(ge=0)
    media_type: str
