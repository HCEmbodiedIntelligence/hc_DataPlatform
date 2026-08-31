"""Bounded-cardinality Prometheus domain metrics and locator helpers."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

UPLOAD_BACKLOG = Gauge(
    "hc_data_upload_backlog",
    "Upload sessions waiting for raw verification.",
    ("queue",),
)
WORKFLOW_FAILURES = Counter(
    "hc_data_workflow_failures_total",
    "Durable workflow or activity failures.",
    ("workflow_kind", "error_code"),
)
QC_OUTCOMES = Counter(
    "hc_data_qc_outcomes_total",
    "Deterministic quality outcomes.",
    ("outcome", "profile_id"),
)
LANCE_COMMITS = Counter(
    "hc_data_lance_commits_total",
    "Lance commit and catalog registration outcomes.",
    ("outcome",),
)
TRANSCODE_DURATION = Histogram(
    "hc_data_transcode_duration_seconds",
    "Canonical aligned MP4 transcode latency through immutable publication.",
    ("outcome", "profile_id"),
)
EXPORTS = Counter(
    "hc_data_exports_total",
    "Immutable training export attempts.",
    ("outcome", "format"),
)

HTTP_REQUESTS = Counter(
    "hc_platform_http_requests_total",
    "HTTP requests by bounded route template, method, status class, and status code.",
    ("route", "method", "status_class", "status_code"),
)
HTTP_REQUEST_DURATION = Histogram(
    "hc_platform_http_request_duration_seconds",
    "HTTP request latency by bounded route template and method.",
    ("route", "method"),
)


def locator_workflow_id(kind: str, project_id: str, resource_id: str) -> str:
    return f"{kind}:v1:{project_id}:{resource_id}"
