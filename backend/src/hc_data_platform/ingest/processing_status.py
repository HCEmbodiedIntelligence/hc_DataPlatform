"""Current interruptions shared by import progress and the task dashboard."""

from typing import Literal


def processing_interruption(
    *,
    qc_status: str | None,
    lance_ready: bool,
    workflow_status: str | None,
    workflow_error_code: str | None,
    alignment_status: str | None,
    source_episode_status: str | None = None,
    source_processing_status: str | None = None,
    resolution_status: str | None = None,
    duplicate_of_rollout_id: str | None = None,
) -> Literal["PROCESSING_CONFLICT", "RESUME_REQUIRED"] | None:
    if (
        lance_ready
        or duplicate_of_rollout_id is not None
        or resolution_status == "DISCARDED"
        or source_episode_status == "DISCARDED"
        or qc_status in {"RISK", "REJECT"}
    ):
        return None
    if resolution_status in {"PENDING", "RUNNING"}:
        return None
    if source_episode_status in {"PENDING", "PROCESSING"} and source_processing_status in {
        "PENDING",
        "RUNNING",
        "PROCESSING",
    }:
        return None
    if resolution_status == "FAILED" or (
        workflow_status == "TECHNICAL_FAILED"
        and workflow_error_code == "ALIGNMENT_ATTEMPT_IMMUTABLE"
        and alignment_status == "READY"
    ):
        return "PROCESSING_CONFLICT"
    # A revised QC decision does not resume a workflow that already stopped.
    if qc_status == "PASS" and (
        workflow_status in {"QUALITY_RISK", "QUALITY_REJECTED"}
        or (source_episode_status == "FAILED" and workflow_status is None)
    ):
        return "RESUME_REQUIRED"
    return None
