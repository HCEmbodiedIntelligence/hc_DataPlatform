"""Prometheus domain metrics with mandatory cross-signal locator labels."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

LOCATOR_LABELS = ("project_id", "resource_id", "workflow_id")

UPLOAD_BACKLOG = Gauge(
    "hc_data_upload_backlog",
    "Upload sessions waiting for raw verification.",
    LOCATOR_LABELS,
)
WORKFLOW_FAILURES = Counter(
    "hc_data_workflow_failures_total",
    "Durable workflow or activity failures.",
    (*LOCATOR_LABELS, "workflow_kind", "error_code"),
)
QC_OUTCOMES = Counter(
    "hc_data_qc_outcomes_total",
    "Deterministic quality outcomes.",
    (*LOCATOR_LABELS, "outcome", "profile_id"),
)
LANCE_COMMITS = Counter(
    "hc_data_lance_commits_total",
    "Lance commit and catalog registration outcomes.",
    (*LOCATOR_LABELS, "outcome", "dataset_id"),
)
TRANSCODE_DURATION = Histogram(
    "hc_data_transcode_duration_seconds",
    "Preview transcode latency through atomic cache publication.",
    (*LOCATOR_LABELS, "outcome", "view_mode"),
)
EXPORTS = Counter(
    "hc_data_exports_total",
    "Immutable training export attempts.",
    (*LOCATOR_LABELS, "outcome", "format"),
)


def locator_workflow_id(kind: str, project_id: str, resource_id: str) -> str:
    return f"{kind}:v1:{project_id}:{resource_id}"
