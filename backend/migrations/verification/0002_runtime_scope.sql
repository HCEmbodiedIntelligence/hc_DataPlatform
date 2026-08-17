ALTER TABLE raw_verification_reports
    ADD COLUMN IF NOT EXISTS region_code text;

UPDATE raw_verification_reports SET region_code = '' WHERE region_code IS NULL;
ALTER TABLE raw_verification_reports ALTER COLUMN region_code SET NOT NULL;

CREATE INDEX IF NOT EXISTS raw_verification_reports_scope_idx
ON raw_verification_reports (project_id, region_code, rollout_id, created_at DESC);

SELECT core.apply_project_rls('raw_verification_reports'::regclass);
