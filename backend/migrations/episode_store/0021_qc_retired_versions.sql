-- Retain QC and immutable admission history when a failed training package is removed.
CREATE TABLE episode_store.qc_retired_versions (
    organization_id text NOT NULL, project_id text NOT NULL, region_code text NOT NULL,
    dataset_id text NOT NULL, episode_id text NOT NULL, data_version_id text NOT NULL,
    recording_id text NOT NULL, annotation_task_id text, report_id text NOT NULL,
    retired_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id,project_id,region_code,data_version_id),
    FOREIGN KEY (organization_id,project_id,region_code,dataset_id,episode_id,data_version_id)
        REFERENCES episode_store.versions (organization_id,project_id,region_code,dataset_id,episode_id,data_version_id)
);
CREATE INDEX qc_retired_annotation ON episode_store.qc_retired_versions
    (organization_id,project_id,region_code,annotation_task_id);
SELECT core.apply_project_rls('episode_store.qc_retired_versions'::regclass);
