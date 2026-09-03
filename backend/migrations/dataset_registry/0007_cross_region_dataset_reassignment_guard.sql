-- Dataset identity is project-wide, so reassignment must consider received data
-- in every storage region, not only the region selected in the current request.
CREATE OR REPLACE FUNCTION collection_tasks.reject_dataset_reassignment_after_ingest()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, collection_tasks, ingest, core
AS $function$
BEGIN
    IF NEW.dataset_id IS NOT DISTINCT FROM OLD.dataset_id THEN
        RETURN NEW;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM ingest.collection_jobs job
          JOIN ingest.rollouts rollout
            ON rollout.organization_id = job.organization_id
           AND rollout.project_id = job.project_id
           AND rollout.collection_job_id = job.collection_job_id
          JOIN ingest.rollout_objects object
            ON object.organization_id = rollout.organization_id
           AND object.project_id = rollout.project_id
           AND object.rollout_id = rollout.rollout_id
         WHERE job.organization_id = OLD.organization_id
           AND job.project_id = OLD.project_id
           AND job.task_id = OLD.collection_task_id
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'COLLECTION_TASK_DATASET_REASSIGNMENT_BLOCKED',
            DETAIL = 'A task with received data requires an explicit migration or split.';
    END IF;
    RETURN NEW;
END
$function$;

COMMENT ON FUNCTION collection_tasks.reject_dataset_reassignment_after_ingest()
IS 'Project-wide guard that prevents Dataset reassignment after any regional ingest.';
