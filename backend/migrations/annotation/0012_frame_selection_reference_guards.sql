-- Forward-only follow-up to the applied frame-selection lifecycle migration.
-- Bind every new manifest to immutable task lineage and reject job references
-- to selection objects which are no longer ACTIVE.

CREATE OR REPLACE FUNCTION annotation.reserve_frame_selection_quota()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    task_dataset_id text;
    task_rollout_id text;
BEGIN
    SELECT task.dataset_id, task.rollout_id
      INTO task_dataset_id, task_rollout_id
    FROM annotation.annotation_tasks AS task
    WHERE task.task_id = NEW.task_id
      AND task.organization_id = NEW.organization_id
      AND task.project_id = NEW.project_id
      AND task.region_code = NEW.region_code;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'frame-selection task scope does not exist'
            USING ERRCODE = '23503';
    END IF;
    IF NEW.dataset_id IS NOT NULL AND NEW.dataset_id <> task_dataset_id THEN
        RAISE EXCEPTION 'frame-selection dataset lineage does not match its task'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.rollout_id IS NOT NULL AND NEW.rollout_id <> task_rollout_id THEN
        RAISE EXCEPTION 'frame-selection rollout lineage does not match its task'
            USING ERRCODE = '23514';
    END IF;
    NEW.dataset_id := task_dataset_id;
    NEW.rollout_id := task_rollout_id;
    NEW.retention_until := COALESCE(
        NEW.retention_until, NEW.created_at + interval '90 days'
    );
    NEW.expires_at := COALESCE(NEW.expires_at, NEW.retention_until);
    NEW.last_accessed_at := COALESCE(NEW.last_accessed_at, NEW.created_at);

    INSERT INTO annotation.frame_selection_project_quotas (
        organization_id, project_id, region_code
    ) VALUES (NEW.organization_id, NEW.project_id, NEW.region_code)
    ON CONFLICT DO NOTHING;

    UPDATE annotation.frame_selection_project_quotas
    SET used_bytes = used_bytes + NEW.size_bytes,
        updated_at = now()
    WHERE organization_id = NEW.organization_id
      AND project_id = NEW.project_id
      AND region_code = NEW.region_code
      AND used_bytes + NEW.size_bytes <= quota_bytes;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'frame-selection project quota exceeded'
            USING ERRCODE = '53100';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION annotation.guard_frame_selection_job_reference()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.sampling_reference IS NULL THEN
        RETURN NEW;
    END IF;
    PERFORM 1
    FROM annotation.frame_selection_manifests AS manifest
    WHERE manifest.task_id = NEW.task_id
      AND manifest.project_id = NEW.project_id
      AND manifest.region_code = NEW.region_code
      AND manifest.object_key = NEW.sampling_reference->>'object_key'
      AND manifest.lifecycle_status = 'ACTIVE'
    FOR KEY SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'frame-selection object is not active'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS auto_annotation_job_active_selection
ON annotation.auto_annotation_jobs;
CREATE TRIGGER auto_annotation_job_active_selection
BEFORE INSERT ON annotation.auto_annotation_jobs
FOR EACH ROW EXECUTE FUNCTION annotation.guard_frame_selection_job_reference();
