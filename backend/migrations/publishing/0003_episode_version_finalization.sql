-- A Dataset publication freezes the exact submitted Episode versions used by
-- its immutable manifest. This is lineage metadata only; no Episode/Lance data
-- is copied.

CREATE TABLE IF NOT EXISTS publishing.episode_version_finalizations (
    project_id text NOT NULL,
    dataset_id text NOT NULL,
    dataset_version text NOT NULL,
    rollout_id text NOT NULL,
    annotation_task_id text NOT NULL,
    annotation_submission_id text NOT NULL,
    finalized_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, dataset_id, dataset_version, rollout_id),
    FOREIGN KEY (project_id, dataset_id, dataset_version)
        REFERENCES publishing.dataset_versions(project_id, dataset_id, dataset_version),
    FOREIGN KEY (annotation_task_id, annotation_submission_id)
        REFERENCES annotation.annotation_submissions(task_id, submission_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS episode_version_finalization_submission_uq
ON publishing.episode_version_finalizations (
    project_id, dataset_id, dataset_version,
    annotation_task_id, annotation_submission_id
);

ALTER TABLE publishing.episode_version_finalizations ENABLE ROW LEVEL SECURITY;
SELECT core.apply_project_rls('publishing.episode_version_finalizations'::regclass);

DROP TRIGGER IF EXISTS episode_version_finalizations_immutable
ON publishing.episode_version_finalizations;
CREATE TRIGGER episode_version_finalizations_immutable
BEFORE UPDATE OR DELETE ON publishing.episode_version_finalizations
FOR EACH ROW EXECUTE FUNCTION publishing.reject_immutable_change();
