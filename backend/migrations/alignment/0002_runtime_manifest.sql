ALTER TABLE aligned_fragment_attempts
    ADD COLUMN IF NOT EXISTS region_code text,
    ADD COLUMN IF NOT EXISTS manifest_json jsonb;

UPDATE aligned_fragment_attempts SET region_code = '' WHERE region_code IS NULL;
ALTER TABLE aligned_fragment_attempts ALTER COLUMN region_code SET NOT NULL;

CREATE INDEX IF NOT EXISTS aligned_fragment_ready_scope_idx
ON aligned_fragment_attempts (project_id, region_code, rollout_id, updated_at DESC)
WHERE status = 'READY';

SELECT core.apply_project_rls('aligned_fragment_attempts'::regclass);
