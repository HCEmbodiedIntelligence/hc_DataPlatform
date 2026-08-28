-- One collection task owns one logical Dataset inside an organization/project.
-- region_code remains a physical storage/provenance attribute and must not
-- participate in Dataset identity selection.

CREATE OR REPLACE FUNCTION dataset_registry.collection_task_dataset_id(
    selected_organization_id text,
    selected_project_id text,
    selected_collection_task_id text
) RETURNS text
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
AS $function$
    SELECT 'dataset_task_' || substr(
        encode(
            digest(
                convert_to(
                    selected_organization_id || chr(31) ||
                    selected_project_id || chr(31) ||
                    selected_collection_task_id,
                    'UTF8'
                ),
                'sha256'
            ),
            'hex'
        ),
        1,
        32
    )
$function$;

DO $preflight$
DECLARE
    ambiguous_tasks text;
    reused_datasets text;
BEGIN
    WITH candidates AS (
        SELECT DISTINCT
               dataset.organization_id,
               dataset.project_id,
               dataset.task AS collection_task_id,
               dataset.dataset_id
          FROM dataset_registry.datasets dataset
          JOIN collection_tasks.collection_tasks task
            ON task.organization_id = dataset.organization_id
           AND task.project_id = dataset.project_id
           AND task.collection_task_id = dataset.task
         WHERE dataset.task IS NOT NULL
    ), invalid AS (
        SELECT organization_id, project_id, collection_task_id
          FROM candidates
         GROUP BY organization_id, project_id, collection_task_id
        HAVING count(DISTINCT dataset_id) > 1
    )
    SELECT string_agg(
               organization_id || '/' || project_id || '/' || collection_task_id,
               ', ' ORDER BY organization_id, project_id, collection_task_id
           )
      INTO ambiguous_tasks
      FROM invalid;

    IF ambiguous_tasks IS NOT NULL THEN
        RAISE EXCEPTION
            'one collection task is linked to multiple Dataset IDs: %',
            ambiguous_tasks;
    END IF;

    WITH candidates AS (
        SELECT DISTINCT
               dataset.organization_id,
               dataset.project_id,
               dataset.task AS collection_task_id,
               dataset.dataset_id
          FROM dataset_registry.datasets dataset
          JOIN collection_tasks.collection_tasks task
            ON task.organization_id = dataset.organization_id
           AND task.project_id = dataset.project_id
           AND task.collection_task_id = dataset.task
         WHERE dataset.task IS NOT NULL
    ), invalid AS (
        SELECT organization_id, project_id, dataset_id
          FROM candidates
         GROUP BY organization_id, project_id, dataset_id
        HAVING count(DISTINCT collection_task_id) > 1
    )
    SELECT string_agg(
               organization_id || '/' || project_id || '/' || dataset_id,
               ', ' ORDER BY organization_id, project_id, dataset_id
           )
      INTO reused_datasets
      FROM invalid;

    IF reused_datasets IS NOT NULL THEN
        RAISE EXCEPTION
            'one Dataset ID is linked to multiple collection tasks: %',
            reused_datasets;
    END IF;
END
$preflight$;

ALTER TABLE collection_tasks.collection_tasks
    ADD COLUMN IF NOT EXISTS dataset_id text;

WITH candidates AS (
    SELECT DISTINCT ON (
               dataset.organization_id,
               dataset.project_id,
               dataset.task
           )
           dataset.organization_id,
           dataset.project_id,
           dataset.task AS collection_task_id,
           dataset.dataset_id
      FROM dataset_registry.datasets dataset
      JOIN collection_tasks.collection_tasks task
        ON task.organization_id = dataset.organization_id
       AND task.project_id = dataset.project_id
       AND task.collection_task_id = dataset.task
     WHERE dataset.task IS NOT NULL
     ORDER BY dataset.organization_id, dataset.project_id, dataset.task,
              dataset.created_at, dataset.dataset_id
)
UPDATE collection_tasks.collection_tasks task
   SET dataset_id = candidate.dataset_id
  FROM candidates candidate
 WHERE task.organization_id = candidate.organization_id
   AND task.project_id = candidate.project_id
   AND task.collection_task_id = candidate.collection_task_id
   AND task.dataset_id IS NULL;

UPDATE collection_tasks.collection_tasks task
   SET dataset_id = dataset_registry.collection_task_dataset_id(
       task.organization_id,
       task.project_id,
       task.collection_task_id
   )
 WHERE task.dataset_id IS NULL;

ALTER TABLE collection_tasks.collection_tasks
    ALTER COLUMN dataset_id SET NOT NULL,
    ADD CONSTRAINT collection_tasks_dataset_id_format
        CHECK (dataset_id ~ '^dataset_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    ADD CONSTRAINT collection_tasks_dataset_id_uq
        UNIQUE (organization_id, project_id, dataset_id),
    ADD CONSTRAINT collection_tasks_task_dataset_uq
        UNIQUE (organization_id, project_id, collection_task_id, dataset_id);

ALTER TABLE dataset_registry.datasets
    ADD COLUMN IF NOT EXISTS collection_task_id text;

UPDATE dataset_registry.datasets dataset
   SET collection_task_id = task.collection_task_id
  FROM collection_tasks.collection_tasks task
 WHERE dataset.organization_id = task.organization_id
   AND dataset.project_id = task.project_id
   AND dataset.dataset_id = task.dataset_id
   AND dataset.task = task.collection_task_id
   AND dataset.collection_task_id IS NULL;

UPDATE dataset_registry.datasets
   SET dataset_document = jsonb_set(
       dataset_document,
       '{metadata,collection_task_id}',
       to_jsonb(collection_task_id),
       true
   )
 WHERE collection_task_id IS NOT NULL
   AND dataset_document #>> '{metadata,collection_task_id}' IS DISTINCT FROM collection_task_id;

ALTER TABLE dataset_registry.datasets
    ADD CONSTRAINT dataset_registry_collection_task_fk
        FOREIGN KEY (
            organization_id,
            project_id,
            collection_task_id,
            dataset_id
        ) REFERENCES collection_tasks.collection_tasks (
            organization_id,
            project_id,
            collection_task_id,
            dataset_id
        ),
    ADD CONSTRAINT dataset_registry_collection_task_identity
        CHECK (collection_task_id IS NULL OR task = collection_task_id),
    ADD CONSTRAINT dataset_registry_collection_task_document
        CHECK (
            collection_task_id IS NULL
            OR (dataset_document #>> '{metadata,collection_task_id}')
                IS NOT DISTINCT FROM collection_task_id
        );

CREATE UNIQUE INDEX dataset_registry_storage_task_uq
ON dataset_registry.datasets (
    organization_id,
    project_id,
    region_code,
    collection_task_id
)
WHERE collection_task_id IS NOT NULL;

CREATE INDEX dataset_registry_task_lookup_idx
ON dataset_registry.datasets (
    organization_id,
    project_id,
    collection_task_id,
    region_code
)
WHERE collection_task_id IS NOT NULL;

ALTER TABLE dataset_registry.dataset_version_episodes
    ADD COLUMN IF NOT EXISTS storage_region_code text;

UPDATE dataset_registry.dataset_version_episodes
   SET storage_region_code = region_code,
       episode_document = jsonb_set(
           episode_document,
           '{storage_region_code}',
           to_jsonb(region_code),
           true
       )
 WHERE storage_region_code IS NULL;

CREATE OR REPLACE FUNCTION dataset_registry.sync_episode_storage_region()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.storage_region_code := coalesce(NEW.storage_region_code, NEW.region_code);
    NEW.episode_document := jsonb_set(
        NEW.episode_document,
        '{storage_region_code}',
        to_jsonb(NEW.storage_region_code),
        true
    );
    RETURN NEW;
END
$function$;

DROP TRIGGER IF EXISTS dataset_episode_storage_region_sync
ON dataset_registry.dataset_version_episodes;
CREATE TRIGGER dataset_episode_storage_region_sync
BEFORE INSERT OR UPDATE OF region_code, storage_region_code, episode_document
ON dataset_registry.dataset_version_episodes
FOR EACH ROW
EXECUTE FUNCTION dataset_registry.sync_episode_storage_region();

ALTER TABLE dataset_registry.dataset_version_episodes
    ALTER COLUMN storage_region_code SET NOT NULL,
    ADD CONSTRAINT dataset_episode_storage_region_nonempty
        CHECK (storage_region_code <> ''),
    ADD CONSTRAINT dataset_episode_storage_region_document
        CHECK (episode_document ->> 'storage_region_code' = storage_region_code);

CREATE INDEX dataset_episode_storage_region_idx
ON dataset_registry.dataset_version_episodes (
    organization_id,
    project_id,
    dataset_id,
    version_id,
    storage_region_code,
    episode_id
);

ALTER TABLE dataset_registry.dataset_version_source_provenance
    ADD COLUMN IF NOT EXISTS storage_region_code text;

UPDATE dataset_registry.dataset_version_source_provenance
   SET storage_region_code = region_code,
       provenance_document = jsonb_set(
           provenance_document,
           '{storage_region_code}',
           to_jsonb(region_code),
           true
       )
 WHERE storage_region_code IS NULL;

CREATE OR REPLACE FUNCTION dataset_registry.sync_provenance_storage_region()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.storage_region_code := coalesce(NEW.storage_region_code, NEW.region_code);
    NEW.provenance_document := jsonb_set(
        NEW.provenance_document,
        '{storage_region_code}',
        to_jsonb(NEW.storage_region_code),
        true
    );
    RETURN NEW;
END
$function$;

DROP TRIGGER IF EXISTS dataset_provenance_storage_region_sync
ON dataset_registry.dataset_version_source_provenance;
CREATE TRIGGER dataset_provenance_storage_region_sync
BEFORE INSERT OR UPDATE OF region_code, storage_region_code, provenance_document
ON dataset_registry.dataset_version_source_provenance
FOR EACH ROW
EXECUTE FUNCTION dataset_registry.sync_provenance_storage_region();

ALTER TABLE dataset_registry.dataset_version_source_provenance
    ALTER COLUMN storage_region_code SET NOT NULL,
    ADD CONSTRAINT dataset_provenance_storage_region_nonempty
        CHECK (storage_region_code <> ''),
    ADD CONSTRAINT dataset_provenance_storage_region_document
        CHECK (provenance_document ->> 'storage_region_code' = storage_region_code);

CREATE INDEX dataset_provenance_storage_region_idx
ON dataset_registry.dataset_version_source_provenance (
    organization_id,
    project_id,
    dataset_id,
    version_id,
    storage_region_code,
    provenance_id
);

-- Reinstall the region-free restrictive policy after adding the project-level
-- Dataset identity to collection tasks. Dataset projection rows retain their
-- storage-region policy because they reference physical regional facts.
SELECT core.apply_project_rls('collection_tasks.collection_tasks'::regclass);
