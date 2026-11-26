CREATE TABLE episode_store.export_file_names (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    filename text NOT NULL,
    data_version_id text NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, filename, data_version_id),
    FOREIGN KEY (organization_id, project_id, region_code, data_version_id)
        REFERENCES episode_store.versions (organization_id, project_id, region_code, data_version_id)
);
SELECT core.apply_project_rls('episode_store.export_file_names'::regclass);
