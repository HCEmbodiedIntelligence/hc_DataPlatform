-- Separate user-visible, manually published Dataset versions from the internal
-- Lance snapshots used by the working Episode/Schema views.

ALTER TABLE dataset_registry.dataset_versions
ADD COLUMN IF NOT EXISTS version_scope text NOT NULL DEFAULT 'DATASET_RELEASE';

UPDATE dataset_registry.dataset_versions
SET version_scope = 'INTERNAL'
WHERE version_id LIKE 'version_lance_%';

-- Older ingest projections exposed the latest internal Lance snapshot through
-- the dataset summary as if it were a published Dataset version.  Keep the
-- working snapshot in dataset_versions, but clear that business-facing pointer.
UPDATE dataset_registry.datasets
SET dataset_document = jsonb_set(
    dataset_document,
    '{current_ready_version}',
    'null'::jsonb,
    true
)
WHERE dataset_document -> 'current_ready_version' ->> 'version_id'
      LIKE 'version_lance_%';

ALTER TABLE dataset_registry.dataset_versions
DROP CONSTRAINT IF EXISTS dataset_versions_version_scope_check;

ALTER TABLE dataset_registry.dataset_versions
ADD CONSTRAINT dataset_versions_version_scope_check
CHECK (version_scope IN ('INTERNAL', 'DATASET_RELEASE'));

CREATE INDEX IF NOT EXISTS dataset_versions_scope_created_idx
ON dataset_registry.dataset_versions (
    organization_id, project_id, region_code, dataset_id,
    version_scope, created_at DESC, version_id DESC
);

COMMENT ON COLUMN dataset_registry.dataset_versions.version_scope IS
'INTERNAL is an implementation snapshot (for example Lance); DATASET_RELEASE is created only by an explicit publication.';
