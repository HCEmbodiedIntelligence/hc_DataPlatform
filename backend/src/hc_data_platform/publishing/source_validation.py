"""Shared validation policy for exporters that consume synchronized steps."""

from hc_data_platform.core.errors import problem

from .models import ExportStepV1, PublishedDatasetManifestV1
from .ports import ExportSourcePort


def collect_and_validate_export_steps(
    manifest: PublishedDatasetManifestV1, source: ExportSourcePort
) -> list[ExportStepV1]:
    collected: list[ExportStepV1] = []
    modality_names: set[str] | None = None
    for rollout in manifest.rollouts:
        steps = sorted(
            source.read_steps(
                project_id=manifest.project_id,
                dataset_id=manifest.dataset_id,
                rollout_id=rollout.rollout_id,
                lance_version=rollout.base_lance_version,
                ranges=rollout.included_step_ranges,
            ),
            key=lambda item: item.step_index,
        )
        expected = [
            index
            for item in rollout.included_step_ranges
            for index in range(item.start_step, item.end_step)
        ]
        actual = [step.step_index for step in steps]
        if actual != expected or any(step.rollout_id != rollout.rollout_id for step in steps):
            raise problem(
                status=409,
                code="EXPORT_SOURCE_INCOMPLETE",
                title="Export source is incomplete",
                detail=(
                    f"Synchronized source steps do not match the manifest for {rollout.rollout_id}."
                ),
                details={"expected_steps": expected, "actual_steps": actual},
            )
        timestamps = [step.timestamp_ns for step in steps]
        if any(right <= left for left, right in zip(timestamps, timestamps[1:], strict=False)):
            raise problem(
                status=409,
                code="EXPORT_SOURCE_TIME_ORDER_INVALID",
                title="Export source timestamps are not ordered",
                detail=f"Rollout {rollout.rollout_id} is not strictly time ordered.",
            )
        for step in steps:
            names = set(step.modalities)
            if not names:
                raise problem(
                    status=409,
                    code="EXPORT_MODALITIES_MISSING",
                    title="Export modalities are missing",
                    detail="Every exported Step must contain synchronized modality values.",
                )
            if modality_names is None:
                modality_names = names
            elif names != modality_names:
                raise problem(
                    status=409,
                    code="EXPORT_MODALITY_SCHEMA_MISMATCH",
                    title="Export modality schema mismatch",
                    detail="All exported Steps must have the same modality keys.",
                    details={
                        "expected": sorted(modality_names),
                        "actual": sorted(names),
                        "rollout_id": step.rollout_id,
                        "step_index": step.step_index,
                    },
                )
        collected.extend(steps)
    return collected
