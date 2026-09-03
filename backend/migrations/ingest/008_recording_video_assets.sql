-- Multi-object continuous recordings: original videos, sensor data, and the
-- recorder configuration are separate immutable Raw OSS objects. Episodes are
-- time-window indexes over those objects and enter QC/alignment only after a
-- human finalizes a slice revision.

CREATE TABLE IF NOT EXISTS ingest.recording_uploads (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    upload_id uuid NOT NULL,
    recording_id text NOT NULL,
    manifest_sha256 char(64) NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    status text NOT NULL CHECK (
        status IN ('UPLOADING', 'READY_TO_COMMIT', 'COMMITTED', 'FAILED')
    ),
    upload_document jsonb NOT NULL CHECK (jsonb_typeof(upload_document) = 'object'),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, upload_id),
    UNIQUE (organization_id, project_id, region_code, recording_id),
    CHECK (
        organization_id <> '' AND project_id <> '' AND region_code <> ''
        AND recording_id <> '' AND created_by <> ''
    )
);

CREATE TABLE IF NOT EXISTS ingest.recording_upload_assets (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    upload_id uuid NOT NULL,
    asset_id uuid NOT NULL,
    asset_path text NOT NULL,
    role text NOT NULL CHECK (
        role IN ('RAW_VIDEO', 'SENSOR_DATA', 'RECORDING_CONFIG', 'CALIBRATION', 'AUXILIARY')
    ),
    camera_id text,
    media_type text NOT NULL,
    expected_size bigint NOT NULL CHECK (expected_size > 0),
    expected_sha256 char(64) NOT NULL CHECK (expected_sha256 ~ '^[0-9a-f]{64}$'),
    expected_crc64 numeric(20, 0),
    object_key text NOT NULL,
    multipart_upload_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('UPLOADING', 'COMMITTED', 'FAILED')),
    object_etag text,
    committed_at timestamptz,
    failure_code text,
    asset_document jsonb NOT NULL CHECK (jsonb_typeof(asset_document) = 'object'),
    PRIMARY KEY (organization_id, project_id, region_code, upload_id, asset_id),
    UNIQUE (organization_id, project_id, region_code, upload_id, asset_path),
    UNIQUE (object_key),
    FOREIGN KEY (organization_id, project_id, region_code, upload_id)
        REFERENCES ingest.recording_uploads (
            organization_id, project_id, region_code, upload_id
        ),
    CHECK (asset_path <> '' AND media_type <> '' AND object_key <> ''),
    CHECK (
        (role = 'RAW_VIDEO' AND camera_id IS NOT NULL AND media_type LIKE 'video/%')
        OR (role <> 'RAW_VIDEO' AND camera_id IS NULL)
    ),
    CHECK (
        (status = 'UPLOADING' AND object_etag IS NULL AND committed_at IS NULL
            AND failure_code IS NULL)
        OR (status = 'COMMITTED' AND object_etag IS NOT NULL AND committed_at IS NOT NULL
            AND failure_code IS NULL)
        OR (status = 'FAILED' AND failure_code IS NOT NULL)
    )
);

ALTER TABLE ingest.continuous_recordings
    ALTER COLUMN upload_session_id DROP NOT NULL;

ALTER TABLE ingest.continuous_recordings
    ADD COLUMN IF NOT EXISTS recording_upload_id uuid,
    ADD COLUMN IF NOT EXISTS video_asset_count integer NOT NULL DEFAULT 0
        CHECK (video_asset_count BETWEEN 0 AND 128);

ALTER TABLE ingest.continuous_recordings
    DROP CONSTRAINT IF EXISTS continuous_recordings_source_upload_check;
ALTER TABLE ingest.continuous_recordings
    ADD CONSTRAINT continuous_recordings_source_upload_check CHECK (
        (upload_session_id IS NOT NULL)::integer
        + (recording_upload_id IS NOT NULL)::integer = 1
    );

ALTER TABLE ingest.continuous_recordings
    DROP CONSTRAINT IF EXISTS continuous_recordings_recording_upload_fkey;
ALTER TABLE ingest.continuous_recordings
    ADD CONSTRAINT continuous_recordings_recording_upload_fkey
    FOREIGN KEY (organization_id, project_id, region_code, recording_upload_id)
    REFERENCES ingest.recording_uploads (
        organization_id, project_id, region_code, upload_id
    );

CREATE UNIQUE INDEX IF NOT EXISTS continuous_recordings_recording_upload_uidx
ON ingest.continuous_recordings (
    organization_id, project_id, region_code, recording_upload_id
)
WHERE recording_upload_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS ingest.recording_episode_processing (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    recording_id text NOT NULL,
    episode_id text NOT NULL,
    finalized_revision integer NOT NULL CHECK (finalized_revision > 0),
    start_offset_ns bigint NOT NULL CHECK (start_offset_ns >= 0),
    end_offset_ns bigint NOT NULL,
    status text NOT NULL CHECK (
        status IN (
            'PENDING_QC', 'QC_RUNNING', 'QC_FAILED', 'PENDING_ALIGNMENT',
            'ALIGNING', 'READY', 'FAILED'
        )
    ),
    qc_report_id text,
    alignment_attempt_id text,
    episode_document jsonb NOT NULL CHECK (jsonb_typeof(episode_document) = 'object'),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, recording_id, episode_id),
    FOREIGN KEY (
        organization_id, project_id, region_code, recording_id,
        finalized_revision, episode_id
    ) REFERENCES ingest.recording_episode_slices (
        organization_id, project_id, region_code, recording_id, revision, episode_id
    ),
    CHECK (end_offset_ns > start_offset_ns)
);

CREATE TABLE IF NOT EXISTS ingest.recording_episode_asset_windows (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    recording_id text NOT NULL,
    episode_id text NOT NULL,
    upload_id uuid NOT NULL,
    asset_id uuid NOT NULL,
    role text NOT NULL,
    camera_id text,
    media_type text NOT NULL,
    source_sha256 char(64) NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    start_offset_ns bigint NOT NULL CHECK (start_offset_ns >= 0),
    end_offset_ns bigint NOT NULL,
    materialization text NOT NULL DEFAULT 'SOFT_TIME_WINDOW'
        CHECK (materialization IN ('SOFT_TIME_WINDOW', 'MATERIALIZED_CLIP')),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (
        organization_id, project_id, region_code, recording_id, episode_id, asset_id
    ),
    FOREIGN KEY (organization_id, project_id, region_code, recording_id, episode_id)
        REFERENCES ingest.recording_episode_processing (
            organization_id, project_id, region_code, recording_id, episode_id
        ),
    FOREIGN KEY (organization_id, project_id, region_code, upload_id, asset_id)
        REFERENCES ingest.recording_upload_assets (
            organization_id, project_id, region_code, upload_id, asset_id
        ),
    CHECK (end_offset_ns > start_offset_ns),
    CHECK (
        role IN ('RAW_VIDEO', 'SENSOR_DATA', 'RECORDING_CONFIG', 'CALIBRATION', 'AUXILIARY')
    )
);

CREATE INDEX IF NOT EXISTS recording_upload_assets_camera_idx
ON ingest.recording_upload_assets (
    organization_id, project_id, region_code, upload_id, role, camera_id
);

CREATE INDEX IF NOT EXISTS recording_episode_processing_queue_idx
ON ingest.recording_episode_processing (
    organization_id, project_id, region_code, status, created_at, recording_id, episode_id
);

CREATE INDEX IF NOT EXISTS recording_episode_asset_windows_camera_idx
ON ingest.recording_episode_asset_windows (
    organization_id, project_id, region_code, recording_id, episode_id, role, camera_id
);

SELECT core.apply_project_rls('ingest.recording_uploads'::regclass);
SELECT core.apply_project_rls('ingest.recording_upload_assets'::regclass);
SELECT core.apply_project_rls('ingest.recording_episode_processing'::regclass);
SELECT core.apply_project_rls('ingest.recording_episode_asset_windows'::regclass);

CREATE OR REPLACE FUNCTION ingest.protect_recording_raw_asset()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF TG_OP = 'DELETE' OR OLD.status = 'COMMITTED' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'RECORDING_RAW_ASSET_IMMUTABLE',
            DETAIL = 'Committed recording assets are immutable Raw facts.';
    END IF;
    IF NEW.organization_id <> OLD.organization_id
        OR NEW.project_id <> OLD.project_id
        OR NEW.region_code <> OLD.region_code
        OR NEW.upload_id <> OLD.upload_id
        OR NEW.asset_id <> OLD.asset_id
        OR NEW.asset_path <> OLD.asset_path
        OR NEW.role <> OLD.role
        OR NEW.camera_id IS DISTINCT FROM OLD.camera_id
        OR NEW.expected_size <> OLD.expected_size
        OR NEW.expected_sha256 <> OLD.expected_sha256
        OR NEW.expected_crc64 IS DISTINCT FROM OLD.expected_crc64
        OR NEW.object_key <> OLD.object_key
        OR NEW.multipart_upload_id <> OLD.multipart_upload_id
    THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'RECORDING_RAW_ASSET_IDENTITY_IMMUTABLE',
            DETAIL = 'Only the upload verification state may change.';
    END IF;
    RETURN NEW;
END
$function$;

DROP TRIGGER IF EXISTS recording_raw_asset_immutable
ON ingest.recording_upload_assets;
CREATE TRIGGER recording_raw_asset_immutable
BEFORE UPDATE OR DELETE ON ingest.recording_upload_assets
FOR EACH ROW EXECUTE FUNCTION ingest.protect_recording_raw_asset();

DROP TRIGGER IF EXISTS recording_episode_asset_windows_immutable
ON ingest.recording_episode_asset_windows;
CREATE TRIGGER recording_episode_asset_windows_immutable
BEFORE UPDATE OR DELETE ON ingest.recording_episode_asset_windows
FOR EACH ROW EXECUTE FUNCTION ingest.reject_recording_slice_mutation();

REVOKE DELETE, TRUNCATE ON ingest.recording_uploads,
    ingest.recording_upload_assets, ingest.recording_episode_processing,
    ingest.recording_episode_asset_windows FROM PUBLIC;
