-- C-owned migration. E must append this file to the migration manifest/phases.
-- No historical sources are automatically scheduled by this migration.
CREATE TABLE ingest.robot_processing (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    raw_source_id text NOT NULL,
    upload_id text NOT NULL,
    generation integer NOT NULL CHECK (generation >= 0),
    workflow_id text NOT NULL UNIQUE,
    document jsonb NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, raw_source_id),
    UNIQUE (organization_id, upload_id),
    FOREIGN KEY (organization_id, project_id, region_code, raw_source_id)
        REFERENCES ingest.raw_sources (organization_id, project_id, region_code, raw_source_id)
);
CREATE TABLE ingest.robot_processing_requests (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    raw_source_id text NOT NULL,
    request_id uuid NOT NULL,
    generation integer NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, raw_source_id, request_id),
    FOREIGN KEY (organization_id, project_id, region_code, raw_source_id)
        REFERENCES ingest.robot_processing (organization_id, project_id, region_code, raw_source_id)
);
SELECT core.apply_project_rls('ingest.robot_processing'::regclass);
SELECT core.apply_project_rls('ingest.robot_processing_requests'::regclass);

-- A completed QC rejection need not publish a training dataset/Lance version.
ALTER TABLE ingest.raw_source_episodes DROP CONSTRAINT raw_source_episodes_check;
ALTER TABLE ingest.raw_source_episodes ADD CONSTRAINT raw_source_episodes_ready_receipt CHECK (
    status <> 'READY' OR (dataset_version IS NOT NULL AND lance_version IS NOT NULL)
    OR quality_status IN ('RISK', 'REJECT')
);
