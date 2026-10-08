"""Build a dataset schema from immutable ingest and quality facts."""

from __future__ import annotations

from typing import Protocol

from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot
from hc_data_platform.quality.models import QualityProfileV1

from .models import ManifestPreflightResultV1
from .planning_errors import IngestPlanBlocked


class DatasetSchemaBuilder(Protocol):
    def __call__(
        self,
        *,
        project_id: str,
        dataset_id: str,
        schema_snapshot_id: str,
        preflight: ManifestPreflightResultV1,
        profile: QualityProfileV1,
    ) -> DatasetSchemaSnapshot: ...


def build_dataset_schema(
    *,
    project_id: str,
    dataset_id: str,
    schema_snapshot_id: str,
    preflight: ManifestPreflightResultV1,
    profile: QualityProfileV1,
) -> DatasetSchemaSnapshot:
    frequency_hz = profile.default_timing.target_frequency_hz
    if not 1 <= frequency_hz <= 1000:
        raise IngestPlanBlocked("the quality profile frequency cannot be used for alignment")
    # Camera rows carry compact aligned-media frame references. Raw JPEG bytes
    # remain only in short-lived alignment staging and are never committed to Lance.
    fields = {topic: "json" for topic in preflight.manifest.actual_topics}
    return DatasetSchemaSnapshot.create(
        project_id=project_id,
        dataset_id=dataset_id,
        schema_snapshot_id=schema_snapshot_id,
        frequency_hz=float(frequency_hz),
        fields=fields,
    )
