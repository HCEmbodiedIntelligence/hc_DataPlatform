-- QC report hashes are canonical content hashes and can legitimately repeat in another tenant.
ALTER TABLE quality_rollout_summaries
    DROP CONSTRAINT IF EXISTS quality_rollout_summaries_report_sha256_fkey;

ALTER TABLE qc_reports
    DROP CONSTRAINT IF EXISTS qc_reports_pkey;

ALTER TABLE qc_reports
    ADD CONSTRAINT qc_reports_pkey
        PRIMARY KEY (project_id, region_code, report_sha256);

ALTER TABLE quality_rollout_summaries
    ADD CONSTRAINT quality_rollout_summaries_report_scope_fkey
        FOREIGN KEY (project_id, region_code, report_sha256)
        REFERENCES qc_reports (project_id, region_code, report_sha256);
