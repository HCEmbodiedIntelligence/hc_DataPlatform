-- P20 task definitions must use the same organization/project identity as the
-- reviewed access grant. A legacy project with zero or multiple registry
-- organizations is intentionally an upgrade error: choosing one would attach
-- a task and its close/cancel gate to the wrong tenant.

DO $preflight$
DECLARE
    invalid_projects text;
BEGIN
    WITH referenced_projects AS (
        SELECT project_id FROM collection_tasks.collection_tasks
    ), invalid AS (
        SELECT referenced.project_id
        FROM referenced_projects referenced
        LEFT JOIN registry.organization_projects project
          ON project.project_id = referenced.project_id
        GROUP BY referenced.project_id
        HAVING count(project.organization_id) <> 1
    )
    SELECT string_agg(project_id, ', ' ORDER BY project_id)
      INTO invalid_projects
      FROM invalid;

    IF invalid_projects IS NOT NULL THEN
        RAISE EXCEPTION
            'organization-scoped collection-task upgrade requires exactly one registry organization for every existing project: %',
            invalid_projects;
    END IF;
END
$preflight$;

ALTER TABLE collection_tasks.collection_tasks
    ADD COLUMN IF NOT EXISTS organization_id text,
    ADD COLUMN IF NOT EXISTS created_by text;

UPDATE collection_tasks.collection_tasks task
   SET organization_id = project.organization_id
  FROM registry.organization_projects project
 WHERE task.project_id = project.project_id
   AND task.organization_id IS NULL;

ALTER TABLE collection_tasks.collection_tasks
    ALTER COLUMN organization_id
        SET DEFAULT NULLIF(current_setting('app.organization_id', true), ''),
    ALTER COLUMN organization_id SET NOT NULL,
    ALTER COLUMN created_by
        SET DEFAULT NULLIF(current_setting('app.subject_id', true), ''),
    ADD CONSTRAINT collection_tasks_organization_nonempty
        CHECK (organization_id <> ''),
    ADD CONSTRAINT collection_tasks_creator_nonempty
        CHECK (created_by IS NULL OR created_by <> ''),
    ADD CONSTRAINT collection_tasks_organization_project_fk
        FOREIGN KEY (organization_id, project_id)
        REFERENCES registry.organization_projects (organization_id, project_id);

ALTER TABLE collection_tasks.collection_tasks
    DROP CONSTRAINT collection_tasks_pkey,
    ADD CONSTRAINT collection_tasks_pkey
        PRIMARY KEY (organization_id, project_id, collection_task_id);

DROP INDEX IF EXISTS collection_tasks.collection_tasks_project_status_created_idx;
CREATE INDEX collection_tasks_organization_project_status_created_idx
ON collection_tasks.collection_tasks (
    organization_id, project_id, status, created_at DESC, collection_task_id DESC
);

-- The ingest records themselves are migrated separately. Until then, a P20
-- association can only be recognized when the writer supplied the exact
-- organization scope. A task ID that belongs to another organization is a
-- fail-closed tenant violation, not an unmanaged historical identifier.
CREATE OR REPLACE FUNCTION collection_tasks.reject_new_rollout_for_closed_task()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    selected_organization_id text := NULLIF(current_setting('app.organization_id', true), '');
    matched_status text;
BEGIN
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
       AND task.organization_id = selected_organization_id
     WHERE job.project_id = NEW.project_id
       AND job.collection_job_id = NEW.collection_job_id
       FOR SHARE OF task;

    IF matched_status IS NULL AND EXISTS (
        SELECT 1
        FROM ingest.collection_jobs job
        JOIN collection_tasks.collection_tasks task
          ON task.project_id = job.project_id
         AND task.collection_task_id = job.task_id
       WHERE job.project_id = NEW.project_id
         AND job.collection_job_id = NEW.collection_job_id
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'COLLECTION_TASK_ORGANIZATION_SCOPE_DENIED',
            DETAIL = 'The ingest association does not belong to the selected organization.';
    END IF;

    IF matched_status IS NULL THEN
        RETURN NEW;
    END IF;
    IF matched_status <> 'ACTIVE' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = CASE
                WHEN matched_status = 'CANCELLED' THEN 'COLLECTION_TASK_CANCELLED'
                ELSE 'COLLECTION_TASK_CLOSED'
            END,
            DETAIL = 'An inactive collection task cannot accept a new rollout.';
    END IF;
    RETURN NEW;
END
$function$;

SELECT core.apply_project_rls('collection_tasks.collection_tasks'::regclass);
