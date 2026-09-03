-- A machine upload names only collection_task_id. Make that identifier globally
-- unambiguous and persist the task-owned Region/processing context used after robot
-- authentication. The short human task_code remains display/search metadata only.

DO $preflight$
DECLARE
    duplicates text;
BEGIN
    SELECT string_agg(collection_task_id, ', ' ORDER BY collection_task_id)
      INTO duplicates
      FROM (
          SELECT collection_task_id
            FROM collection_tasks.collection_tasks
           GROUP BY collection_task_id
          HAVING count(*) > 1
      ) duplicate_ids;
    IF duplicates IS NOT NULL THEN
        RAISE EXCEPTION
            'collection_task_id must be globally unique before robot ingestion is enabled: %',
            duplicates;
    END IF;
END
$preflight$;

CREATE UNIQUE INDEX IF NOT EXISTS collection_tasks_global_upload_identity_uq
ON collection_tasks.collection_tasks (collection_task_id);

ALTER TABLE collection_tasks.collection_tasks
    ADD COLUMN IF NOT EXISTS upload_region_code text,
    ADD COLUMN IF NOT EXISTS upload_processing_config jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Prefer an already-provisioned Dataset Region, then an existing collection Job.
-- A task without either remains explicitly platform-global until an operator assigns a
-- more specific Region; the client cannot choose or override this value.
UPDATE collection_tasks.collection_tasks task
   SET upload_region_code = COALESCE(
       (
           SELECT min(dataset.region_code)
             FROM dataset_registry.datasets dataset
            WHERE dataset.organization_id = task.organization_id
              AND dataset.project_id = task.project_id
              AND dataset.dataset_id = task.dataset_id
           HAVING count(DISTINCT dataset.region_code) = 1
       ),
       (
           SELECT min(job.region_code)
             FROM ingest.collection_jobs job
            WHERE job.organization_id = task.organization_id
              AND job.project_id = task.project_id
              AND job.task_id = task.collection_task_id
           HAVING count(DISTINCT job.region_code) = 1
       ),
       'global'
   )
 WHERE task.upload_region_code IS NULL;

ALTER TABLE collection_tasks.collection_tasks
    ALTER COLUMN upload_region_code SET DEFAULT 'global',
    ALTER COLUMN upload_region_code SET NOT NULL,
    ADD CONSTRAINT collection_tasks_upload_region_code_nonempty
        CHECK (upload_region_code ~ '^[A-Za-z0-9._-]{1,128}$'),
    ADD CONSTRAINT collection_tasks_upload_processing_config_object
        CHECK (jsonb_typeof(upload_processing_config) = 'object');

CREATE OR REPLACE FUNCTION collection_tasks.resolve_robot_upload_target(
    selected_collection_task_id text
) RETURNS TABLE (
    collection_task_id text,
    organization_id text,
    project_id text,
    dataset_id text,
    region_code text,
    task_status text,
    processing_config jsonb
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, collection_tasks
AS $function$
    SELECT task.collection_task_id,
           task.organization_id,
           task.project_id,
           task.dataset_id,
           COALESCE(
               NULLIF(task.upload_region_code, 'global'),
               (
                   SELECT min(dataset.region_code)
                     FROM dataset_registry.datasets dataset
                    WHERE dataset.organization_id = task.organization_id
                      AND dataset.project_id = task.project_id
                      AND dataset.dataset_id = task.dataset_id
                   HAVING count(DISTINCT dataset.region_code) = 1
               ),
               (
                   SELECT min(job.region_code)
                     FROM ingest.collection_jobs job
                    WHERE job.organization_id = task.organization_id
                      AND job.project_id = task.project_id
                      AND job.task_id = task.collection_task_id
                   HAVING count(DISTINCT job.region_code) = 1
               ),
               'global'
           ),
           task.status,
           task.upload_processing_config
      FROM collection_tasks.collection_tasks task
     WHERE task.collection_task_id = selected_collection_task_id
$function$;

REVOKE ALL ON FUNCTION collection_tasks.resolve_robot_upload_target(text) FROM PUBLIC;
