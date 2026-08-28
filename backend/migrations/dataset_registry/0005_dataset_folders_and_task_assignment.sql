-- Dataset organization and collection-task assignment are separate concerns:
-- folders form a presentation hierarchy, while each task targets one Dataset
-- and many tasks may intentionally contribute to the same Dataset.

ALTER TABLE collection_tasks.collection_tasks
    DROP CONSTRAINT IF EXISTS collection_tasks_dataset_id_uq;

CREATE OR REPLACE FUNCTION dataset_registry.valid_folder_path(selected_path text[])
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
AS $function$
    SELECT cardinality(selected_path) <= 16
       AND NOT EXISTS (
            SELECT 1
              FROM unnest(selected_path) AS segment
             WHERE segment = ''
                OR length(segment) > 128
                OR segment LIKE '%/%'
                OR position(chr(92) in segment) > 0
       )
$function$;

ALTER TABLE dataset_registry.datasets
    ADD COLUMN IF NOT EXISTS folder_path text[] NOT NULL DEFAULT ARRAY[]::text[];

UPDATE dataset_registry.datasets
   SET dataset_document = jsonb_set(
       dataset_document,
       '{folder_path}',
       to_jsonb(folder_path),
       true
   )
 WHERE dataset_document -> 'folder_path' IS DISTINCT FROM to_jsonb(folder_path);

ALTER TABLE dataset_registry.datasets
    ADD CONSTRAINT dataset_registry_folder_path_valid
        CHECK (dataset_registry.valid_folder_path(folder_path)),
    ADD CONSTRAINT dataset_registry_folder_path_document
        CHECK (dataset_document -> 'folder_path' = to_jsonb(folder_path));

DROP INDEX IF EXISTS dataset_registry.dataset_page_scope_name_uq;
CREATE UNIQUE INDEX dataset_page_scope_folder_name_uq
ON dataset_registry.datasets (
    organization_id,
    project_id,
    region_code,
    folder_path,
    lower(name)
);

CREATE INDEX dataset_page_scope_folder_idx
ON dataset_registry.datasets (
    organization_id,
    project_id,
    region_code,
    folder_path,
    name,
    dataset_id
);

CREATE OR REPLACE FUNCTION collection_tasks.reject_dataset_reassignment_after_ingest()
RETURNS trigger
LANGUAGE plpgsql
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

DROP TRIGGER IF EXISTS collection_task_dataset_reassignment_guard
ON collection_tasks.collection_tasks;
CREATE TRIGGER collection_task_dataset_reassignment_guard
BEFORE UPDATE OF dataset_id
ON collection_tasks.collection_tasks
FOR EACH ROW
EXECUTE FUNCTION collection_tasks.reject_dataset_reassignment_after_ingest();

SELECT core.apply_project_rls('collection_tasks.collection_tasks'::regclass);
SELECT core.apply_project_rls('dataset_registry.datasets'::regclass);
