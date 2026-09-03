-- Add business package identity, bounded Manifest discovery, external object registration,
-- and explicit failed-part retry state without changing P20 task configuration.

ALTER TABLE ingest.rollouts
    ADD COLUMN IF NOT EXISTS collection_session_id text,
    ADD COLUMN IF NOT EXISTS recording_request_id text,
    ADD COLUMN IF NOT EXISTS data_package_id text,
    ADD COLUMN IF NOT EXISTS pico_instance_id text;

UPDATE ingest.rollouts
SET collection_session_id = COALESCE(collection_session_id, collection_job_id),
    recording_request_id = COALESCE(recording_request_id, rollout_id),
    data_package_id = COALESCE(data_package_id, rollout_id)
WHERE collection_session_id IS NULL
   OR recording_request_id IS NULL
   OR data_package_id IS NULL;

ALTER TABLE ingest.rollouts
    ALTER COLUMN collection_session_id SET NOT NULL,
    ALTER COLUMN recording_request_id SET NOT NULL,
    ALTER COLUMN data_package_id SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS rollouts_project_data_package_uidx
ON ingest.rollouts (project_id, data_package_id);

ALTER TABLE ingest.upload_sessions
    ADD COLUMN IF NOT EXISTS data_package_id text,
    ADD COLUMN IF NOT EXISTS source_type text NOT NULL DEFAULT 'BROWSER_MULTIPART';

UPDATE ingest.upload_sessions AS session
SET data_package_id = rollout.data_package_id
FROM ingest.rollouts AS rollout
WHERE session.project_id = rollout.project_id
  AND session.rollout_id = rollout.rollout_id
  AND session.data_package_id IS NULL;

ALTER TABLE ingest.upload_sessions
    ALTER COLUMN data_package_id SET NOT NULL,
    ALTER COLUMN multipart_upload_id DROP NOT NULL;

ALTER TABLE ingest.upload_sessions
    DROP CONSTRAINT IF EXISTS upload_sessions_source_type_check;
ALTER TABLE ingest.upload_sessions
    ADD CONSTRAINT upload_sessions_source_type_check
    CHECK (source_type IN ('BROWSER_MULTIPART', 'OBJECT_STORAGE_REFERENCE'));

CREATE UNIQUE INDEX IF NOT EXISTS upload_sessions_project_data_package_uidx
ON ingest.upload_sessions (project_id, data_package_id);

ALTER TABLE ingest.upload_parts
    ADD COLUMN IF NOT EXISTS retry_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS failure_code text;

ALTER TABLE ingest.upload_parts
    DROP CONSTRAINT IF EXISTS upload_parts_status_check;
ALTER TABLE ingest.upload_parts
    ADD CONSTRAINT upload_parts_status_check
    CHECK (status IN ('AUTHORIZED', 'UPLOADED', 'FAILED'));
ALTER TABLE ingest.upload_parts
    DROP CONSTRAINT IF EXISTS upload_parts_retry_count_check;
ALTER TABLE ingest.upload_parts
    ADD CONSTRAINT upload_parts_retry_count_check
    CHECK (retry_count BETWEEN 0 AND 10);

ALTER TABLE ingest.rollout_objects
    ADD COLUMN IF NOT EXISTS data_package_id text;

UPDATE ingest.rollout_objects AS object
SET data_package_id = rollout.data_package_id
FROM ingest.rollouts AS rollout
WHERE object.project_id = rollout.project_id
  AND object.rollout_id = rollout.rollout_id
  AND object.data_package_id IS NULL;

ALTER TABLE ingest.rollout_objects
    ALTER COLUMN data_package_id SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS rollout_objects_project_data_package_uidx
ON ingest.rollout_objects (project_id, data_package_id);

CREATE TABLE IF NOT EXISTS ingest.manifest_discoveries (
    session_id uuid PRIMARY KEY REFERENCES ingest.upload_sessions (session_id),
    project_id text NOT NULL,
    region_code text NOT NULL,
    data_package_id text NOT NULL,
    manifest_fingerprint char(64) NOT NULL
        CHECK (manifest_fingerprint ~ '^[a-f0-9]{64}$'),
    preflight_json jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    UNIQUE (project_id, data_package_id),
    CHECK (jsonb_typeof(preflight_json) = 'object')
);

CREATE INDEX IF NOT EXISTS manifest_discoveries_scope_idx
ON ingest.manifest_discoveries (project_id, region_code, created_at DESC);

DO $block$
BEGIN
    IF to_regprocedure('core.apply_project_rls(regclass)') IS NOT NULL THEN
        PERFORM core.apply_project_rls('ingest.manifest_discoveries'::regclass);
    ELSE
        ALTER TABLE ingest.manifest_discoveries ENABLE ROW LEVEL SECURITY;
        ALTER TABLE ingest.manifest_discoveries FORCE ROW LEVEL SECURITY;
    END IF;
END
$block$;
