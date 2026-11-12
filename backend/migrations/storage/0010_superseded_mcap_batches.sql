-- Keep a durable deletion plan; historical QC and slice decisions remain auditable.
ALTER TABLE storage.lifecycle_policies DROP CONSTRAINT lifecycle_policies_action_check;
ALTER TABLE storage.lifecycle_policies ADD CONSTRAINT lifecycle_policies_action_check
CHECK (action IN ('RETAIN', 'REVIEW_EXPIRATION', 'ARCHIVE', 'TRANSITION_TO_COLD',
    'CLEAN_REBUILDABLE_CACHE', 'CLEAN_PROCESSING_CACHE', 'CLEAN_RECORDING_PREVIEWS',
    'CLEAN_EPISODE_PREVIEWS', 'CLEAN_EXPORT_ARCHIVES', 'CLEAN_PROCESSING_STAGING',
    'CLEAN_MEDIA_STAGING', 'CLEAN_OSS_TEST_COPIES', 'CLEAN_INVENTORY_DETAILS',
    'CLEAN_INVENTORY_HISTORY', 'CLEAN_SUPERSEDED_MCAP_BATCHES'));
ALTER TABLE storage.lifecycle_policies ADD CONSTRAINT superseded_mcap_policy_target
CHECK (action <> 'CLEAN_SUPERSEDED_MCAP_BATCHES' OR
       (object_role='REBUILDABLE_DERIVATIVE' AND business_category='PENDING_ANNOTATION'));

ALTER TABLE ingest.continuous_recordings ADD COLUMN superseded_by_processing_id text;
CREATE TABLE ingest.mcap_batch_retirements (
    organization_id text NOT NULL, project_id text NOT NULL, region_code text NOT NULL,
    processing_id text NOT NULL CHECK (processing_id ~ '^[a-f0-9]{64}$'),
    replacement_id text NOT NULL CHECK (replacement_id ~ '^[a-f0-9]{64}$' AND replacement_id <> processing_id),
    replacement_ready_at timestamptz NOT NULL,
    plan jsonb NOT NULL CHECK (jsonb_typeof(plan)='array'),
    started_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
    PRIMARY KEY (organization_id,project_id,region_code,processing_id),
    FOREIGN KEY (organization_id,project_id,region_code,processing_id)
      REFERENCES ingest.mcap_processing_files (organization_id,project_id,region_code,processing_id) ON DELETE CASCADE
);
SELECT core.apply_project_rls('ingest.mcap_batch_retirements'::regclass);
