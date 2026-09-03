-- BE21: measured indexes for bounded exact-scope raw fact pages only.
-- No metric rollup, cache, materialized view, or unconfirmed product formula is persisted.

CREATE INDEX IF NOT EXISTS dashboard_rollout_objects_scope_committed_idx
ON ingest.rollout_objects (project_id, region_code, committed_at DESC, rollout_id DESC)
INCLUDE (data_package_id, file_size);

CREATE INDEX IF NOT EXISTS dashboard_rollouts_scope_created_idx
ON ingest.rollouts (project_id, region_code, created_at DESC, rollout_id DESC)
INCLUDE (collection_job_id, robot_id);
