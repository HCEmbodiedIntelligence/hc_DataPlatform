-- Resolve one-to-many Dataset membership without requiring Dataset readers to
-- receive direct privileges on the collection_tasks schema.
-- The task table is authoritative; the singular Dataset projection field is
-- retained only as legacy display metadata.
ALTER TABLE dataset_registry.datasets
    DROP CONSTRAINT IF EXISTS dataset_registry_collection_task_fk,
    DROP CONSTRAINT IF EXISTS dataset_registry_collection_task_identity;

DROP INDEX IF EXISTS dataset_registry.dataset_registry_storage_task_uq;

CREATE INDEX collection_tasks_dataset_membership_idx
ON collection_tasks.collection_tasks (
    organization_id,
    project_id,
    dataset_id,
    collection_task_id
);

CREATE OR REPLACE FUNCTION dataset_registry.dataset_has_collection_task(
    selected_organization_id text,
    selected_project_id text,
    selected_dataset_id text,
    selected_collection_task_id text
) RETURNS boolean
LANGUAGE sql
STABLE
STRICT
SECURITY DEFINER
SET search_path = pg_catalog, dataset_registry, collection_tasks, core
AS $function$
    SELECT core.organization_scope_matches(
               selected_organization_id,
               selected_project_id
           )
       AND EXISTS (
            SELECT 1
              FROM collection_tasks.collection_tasks AS linked_task
             WHERE linked_task.organization_id = selected_organization_id
               AND linked_task.project_id = selected_project_id
               AND linked_task.dataset_id = selected_dataset_id
               AND linked_task.collection_task_id = selected_collection_task_id
       )
$function$;

COMMENT ON FUNCTION dataset_registry.dataset_has_collection_task(text, text, text, text)
IS 'Scope-checked membership lookup for Dataset list filtering.';
