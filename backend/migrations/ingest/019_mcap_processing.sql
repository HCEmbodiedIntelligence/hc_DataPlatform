CREATE TABLE ingest.mcap_robot_profiles (
    organization_id text NOT NULL, project_id text NOT NULL, region_code text NOT NULL,
    robot_id text NOT NULL, configuration jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id,project_id,region_code,robot_id)
);
CREATE TABLE ingest.mcap_processing_files (
    organization_id text NOT NULL, project_id text NOT NULL, region_code text NOT NULL,
    raw_source_id text NOT NULL, processing_id text NOT NULL, source_path text NOT NULL,
    source_document jsonb NOT NULL, configuration jsonb NOT NULL,
    collection_task_id text NOT NULL, robot_id text NOT NULL, dataset_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('PENDING','RUNNING','READY','FAILED')),
    attempts integer NOT NULL DEFAULT 0, error text, recording_id text,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id,project_id,region_code,processing_id),
    FOREIGN KEY (organization_id,project_id,region_code,raw_source_id)
        REFERENCES ingest.raw_sources (organization_id,project_id,region_code,raw_source_id)
);
SELECT core.apply_project_rls('ingest.mcap_robot_profiles'::regclass);
SELECT core.apply_project_rls('ingest.mcap_processing_files'::regclass);
