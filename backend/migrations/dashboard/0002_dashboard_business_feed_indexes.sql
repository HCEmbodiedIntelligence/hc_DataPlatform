-- BR01: measured/query-shaped indexes for the factual event feed and four pending sources.
-- These indexes do not persist an SLA, freshness target, coverage denominator, or metric rollup.

CREATE INDEX IF NOT EXISTS dashboard_upload_sessions_state_opened_idx
ON ingest.upload_sessions (
    project_id,
    region_code,
    status,
    updated_at,
    session_id
)
INCLUDE (rollout_id);

CREATE INDEX IF NOT EXISTS dashboard_qc_reports_scope_event_idx
ON qc_reports (
    project_id,
    region_code,
    created_at DESC,
    report_sha256 DESC
)
INCLUDE (rollout_id, status);

CREATE INDEX IF NOT EXISTS dashboard_quality_pending_scope_idx
ON quality_rollout_summaries (
    project_id,
    region_code,
    status,
    updated_at,
    rollout_id
)
INCLUDE (report_sha256);

CREATE INDEX IF NOT EXISTS dashboard_annotation_tasks_state_opened_idx
ON annotation.annotation_tasks (
    project_id,
    status,
    updated_at,
    task_id
)
INCLUDE (rollout_id, dataset_id, dataset_version, current_revision);

CREATE INDEX IF NOT EXISTS dashboard_annotation_reviews_event_idx
ON annotation.annotation_reviews (created_at DESC, review_id DESC)
INCLUDE (task_id, revision, decision);
