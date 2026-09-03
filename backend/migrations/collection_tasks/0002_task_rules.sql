-- P20 frozen task-state and attainment rules.  A target remains optional for
-- exploratory work, but all configured targets are positive.  Attainment is
-- derived from immutable package/Manifest/QC facts and never changes status.

ALTER TABLE collection_tasks.collection_tasks
    DROP CONSTRAINT IF EXISTS collection_tasks_status_check;
ALTER TABLE collection_tasks.collection_tasks
    ADD CONSTRAINT collection_tasks_status_check
    CHECK (status IN ('ACTIVE', 'CLOSED', 'CANCELLED'));

CREATE OR REPLACE FUNCTION collection_tasks.reject_new_rollout_for_closed_task()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    matched_status text;
BEGIN
    -- An existing rollout is an idempotent retry, not a new package association.
    IF EXISTS (
        SELECT 1
        FROM ingest.rollouts existing
        WHERE existing.project_id = NEW.project_id
          AND existing.rollout_id = NEW.rollout_id
    ) THEN
        RETURN NEW;
    END IF;

    SELECT task.status
      INTO matched_status
      FROM ingest.collection_jobs job
      JOIN collection_tasks.collection_tasks task
        ON task.project_id = job.project_id
       AND task.collection_task_id = job.task_id
     WHERE job.project_id = NEW.project_id
       AND job.collection_job_id = NEW.collection_job_id
       FOR SHARE OF task;

    -- Historical/unmanaged ingest identifiers stay compatible. Only a formal P20
    -- association is governed by the P20 task state.
    IF matched_status IS NULL OR matched_status = 'ACTIVE' THEN
        RETURN NEW;
    END IF;
    IF matched_status = 'CLOSED' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'COLLECTION_TASK_CLOSED',
            DETAIL = 'A closed collection task cannot accept a new rollout.';
    END IF;
    RAISE EXCEPTION USING
        ERRCODE = '23514',
        MESSAGE = 'COLLECTION_TASK_CANCELLED',
        DETAIL = 'A cancelled collection task must be reopened before it can accept a new rollout.';
END
$function$;
