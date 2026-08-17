-- A report digest identifies content, not a tenant. The same valid MCAP can be
-- registered independently by multiple projects and regions without a global-key collision.
ALTER TABLE raw_verification_reports
    DROP CONSTRAINT IF EXISTS raw_verification_reports_pkey,
    DROP CONSTRAINT IF EXISTS raw_verification_reports_project_id_rollout_id_source_sha256_key;

ALTER TABLE raw_verification_reports
    ADD CONSTRAINT raw_verification_reports_pkey
        PRIMARY KEY (project_id, region_code, report_sha256),
    ADD CONSTRAINT raw_verification_reports_rollout_source_key
        UNIQUE (project_id, region_code, rollout_id, source_sha256);
