"""Versioned public models owned by the Lance catalog module."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

SHA256_PATTERN = r"^[0-9a-f]{64}$"


class DatasetSchemaSnapshot(BaseModel):
    """Immutable logical schema compiled into the shared Lance dataset schema."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1"
    project_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    schema_snapshot_id: str = Field(min_length=1)
    frequency_hz: float = Field(gt=0)
    fields: dict[str, str] = Field(min_length=1)
    fingerprint: str = Field(pattern=SHA256_PATTERN)

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        dataset_id: str,
        schema_snapshot_id: str,
        frequency_hz: float,
        fields: dict[str, str],
    ) -> DatasetSchemaSnapshot:
        """Create a snapshot with its deterministic compiled-schema fingerprint."""

        from .schema import compute_schema_fingerprint

        return cls(
            project_id=project_id,
            dataset_id=dataset_id,
            schema_snapshot_id=schema_snapshot_id,
            frequency_hz=frequency_hz,
            fields=fields,
            fingerprint=compute_schema_fingerprint(fields),
        )


class StepRecord(BaseModel):
    """A stable logical step; physical Lance row addresses are never public."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1"
    rollout_id: str = Field(min_length=1)
    step_index: int = Field(ge=0)
    timestamp_ns: int = Field(ge=0)
    modalities: dict[str, Any] = Field(default_factory=dict)
    source_timestamps_ns: dict[str, tuple[int, ...]] = Field(default_factory=dict)
    time_error_ns: dict[str, int | None] = Field(default_factory=dict)
    valid: dict[str, bool] = Field(default_factory=dict)
    repeated: dict[str, bool] = Field(default_factory=dict)
    sample_valid: bool = True

    @field_validator("source_timestamps_ns", mode="before")
    @classmethod
    def normalize_source_timestamp_provenance(cls, value: object) -> object:
        """Accept scalar v1 inputs while retaining interpolation source pairs."""

        if not isinstance(value, dict):
            return value
        return {
            key: () if item is None else (item,) if isinstance(item, int) else item
            for key, item in value.items()
        }


class AlignedFragmentManifestV1(BaseModel):
    """Immutable hand-off contract for one alignment attempt."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    schema_version: str = "1"
    project_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    schema_snapshot_id: str = Field(min_length=1)
    schema_fingerprint: str = Field(
        validation_alias=AliasChoices("schema_fingerprint", "schema_sha256"),
        pattern=SHA256_PATTERN,
    )
    frequency_hz: float = Field(gt=0)
    rollout_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=SHA256_PATTERN)
    converter_version: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    fragment_uri: str = Field(
        validation_alias=AliasChoices("fragment_uri", "staging_uri"), min_length=1
    )
    step_count: int = Field(validation_alias=AliasChoices("step_count", "row_count"), ge=0)
    content_hash: str = Field(
        validation_alias=AliasChoices("content_hash", "content_sha256"),
        pattern=SHA256_PATTERN,
    )

    @property
    def idempotency_key(self) -> tuple[str, str, str]:
        """The required key, scoped by project and dataset by the catalog."""

        return self.rollout_id, self.source_sha256, self.converter_version


class DatasetVersionRef(BaseModel):
    """Catalog reference to an immutable logical dataset snapshot."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1"
    project_id: str
    dataset_id: str
    version: int = Field(ge=1)
    schema_snapshot_id: str
    schema_fingerprint: str = Field(pattern=SHA256_PATTERN)
    frequency_hz: float = Field(gt=0)
    content_hash: str = Field(pattern=SHA256_PATTERN)
    dataset_uri: str
    lance_version: int = Field(ge=1)
    storage_commit_id: str = Field(pattern=SHA256_PATTERN)
    committed_rollouts: tuple[str, ...]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DerivedReadyV1(BaseModel):
    """Event emitted only after storage commit and catalog indexing both succeed."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1"
    project_id: str
    dataset_id: str
    rollout_id: str
    source_sha256: str
    converter_version: str
    dataset_version: int
    lance_version: int
    step_count: int
    content_hash: str


class StepWindow(BaseModel):
    """A half-open logical step window pinned to a catalog version."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1"
    project_id: str
    dataset_id: str
    dataset_version: int
    rollout_id: str
    start_step: int
    end_step: int
    steps: tuple[StepRecord, ...]


class RolloutLineage(BaseModel):
    """Complete lineage for one rollout in a dataset version."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1"
    project_id: str
    dataset_id: str
    dataset_version: int
    rollout_id: str
    source_sha256: str
    converter_version: str
    schema_snapshot_id: str
    schema_fingerprint: str = Field(pattern=SHA256_PATTERN)
    fragment_uri: str
    fragment_content_hash: str = Field(pattern=SHA256_PATTERN)
    dataset_uri: str
    lance_version: int = Field(ge=1)


class StorageCommitReceipt(BaseModel):
    """Durable receipt embedded in a Lance transaction before catalog indexing."""

    model_config = ConfigDict(frozen=True)

    storage_commit_id: str = Field(pattern=SHA256_PATTERN)
    manifest: AlignedFragmentManifestV1
    version_ref: DatasetVersionRef


class PendingReconciliation(BaseModel):
    """Best-effort PostgreSQL record for a Lance commit awaiting indexing."""

    model_config = ConfigDict(frozen=True)

    storage_commit_id: str = Field(pattern=SHA256_PATTERN)
    project_id: str
    dataset_id: str
    dataset_uri: str
    lance_version: int = Field(ge=1)
    error: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
