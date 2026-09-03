-- Long-running capture bundles remain immutable Raw facts. Manual slicing creates
-- versioned, soft Episode windows without copying or rewriting source media.

CREATE TABLE IF NOT EXISTS ingest.continuous_recordings (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    recording_id text NOT NULL,
    upload_session_id uuid NOT NULL,
    rollout_id text NOT NULL,
    data_package_id text NOT NULL,
    collection_task_id text NOT NULL,
    collection_job_id text NOT NULL,
    robot_id text NOT NULL,
    device_id text NOT NULL,
    capture_started_at timestamptz NOT NULL,
    capture_ended_at timestamptz NOT NULL,
    duration_ns bigint NOT NULL CHECK (duration_ns > 0),
    source_sha256 char(64) NOT NULL CHECK (source_sha256 ~ '^[a-f0-9]{64}$'),
    manifest_fingerprint char(64) NOT NULL
        CHECK (manifest_fingerprint ~ '^[a-f0-9]{64}$'),
    status text NOT NULL CHECK (status IN ('READY_FOR_SLICING', 'SLICED')),
    current_revision integer NOT NULL DEFAULT 0 CHECK (current_revision >= 0),
    finalized_revision integer CHECK (finalized_revision > 0),
    resource_version bigint NOT NULL DEFAULT 1 CHECK (resource_version > 0),
    recording_document jsonb NOT NULL CHECK (jsonb_typeof(recording_document) = 'object'),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, recording_id),
    UNIQUE (organization_id, project_id, region_code, upload_session_id),
    FOREIGN KEY (upload_session_id) REFERENCES ingest.upload_sessions (session_id),
    CHECK (organization_id <> '' AND project_id <> '' AND region_code <> ''),
    CHECK (recording_id <> '' AND device_id <> ''),
    CHECK (capture_ended_at > capture_started_at),
    CHECK (
        (status = 'READY_FOR_SLICING' AND finalized_revision IS NULL)
        OR
        (status = 'SLICED' AND finalized_revision = current_revision)
    )
);

CREATE TABLE IF NOT EXISTS ingest.recording_slice_revisions (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    recording_id text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    status text NOT NULL CHECK (status IN ('DRAFT', 'FINALIZED')),
    revision_document jsonb NOT NULL CHECK (jsonb_typeof(revision_document) = 'object'),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, recording_id, revision),
    FOREIGN KEY (organization_id, project_id, region_code, recording_id)
        REFERENCES ingest.continuous_recordings (
            organization_id, project_id, region_code, recording_id
        )
);

CREATE TABLE IF NOT EXISTS ingest.recording_episode_slices (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    recording_id text NOT NULL,
    revision integer NOT NULL,
    episode_id text NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    start_offset_ns bigint NOT NULL CHECK (start_offset_ns >= 0),
    end_offset_ns bigint NOT NULL,
    started_at timestamptz NOT NULL,
    ended_at timestamptz NOT NULL,
    episode_document jsonb NOT NULL CHECK (jsonb_typeof(episode_document) = 'object'),
    PRIMARY KEY (
        organization_id, project_id, region_code, recording_id, revision, episode_id
    ),
    UNIQUE (organization_id, project_id, region_code, recording_id, revision, ordinal),
    FOREIGN KEY (organization_id, project_id, region_code, recording_id, revision)
        REFERENCES ingest.recording_slice_revisions (
            organization_id, project_id, region_code, recording_id, revision
        ),
    CHECK (episode_id ~ '^episode_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (end_offset_ns > start_offset_ns),
    CHECK (ended_at > started_at)
);

CREATE INDEX IF NOT EXISTS continuous_recordings_scope_status_idx
ON ingest.continuous_recordings (
    organization_id, project_id, region_code, status, created_at DESC, recording_id DESC
);

CREATE INDEX IF NOT EXISTS recording_episode_slices_timeline_idx
ON ingest.recording_episode_slices (
    organization_id, project_id, region_code, recording_id, revision,
    start_offset_ns, end_offset_ns
);

SELECT core.apply_project_rls('ingest.continuous_recordings'::regclass);
SELECT core.apply_project_rls('ingest.recording_slice_revisions'::regclass);
SELECT core.apply_project_rls('ingest.recording_episode_slices'::regclass);

CREATE OR REPLACE FUNCTION ingest.reject_recording_slice_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '23514',
        MESSAGE = 'RECORDING_SLICE_REVISION_IMMUTABLE',
        DETAIL = 'Slice revisions and their Episode windows are append-only.';
END
$function$;

DROP TRIGGER IF EXISTS recording_slice_revisions_immutable
ON ingest.recording_slice_revisions;
CREATE TRIGGER recording_slice_revisions_immutable
BEFORE UPDATE OR DELETE ON ingest.recording_slice_revisions
FOR EACH ROW EXECUTE FUNCTION ingest.reject_recording_slice_mutation();

DROP TRIGGER IF EXISTS recording_episode_slices_immutable
ON ingest.recording_episode_slices;
CREATE TRIGGER recording_episode_slices_immutable
BEFORE UPDATE OR DELETE ON ingest.recording_episode_slices
FOR EACH ROW EXECUTE FUNCTION ingest.reject_recording_slice_mutation();

REVOKE UPDATE, DELETE ON ingest.recording_slice_revisions FROM PUBLIC;
REVOKE UPDATE, DELETE ON ingest.recording_episode_slices FROM PUBLIC;
