-- Forward-only convergence to ingest-time canonical MP4. The preview schema is
-- intentionally left untouched for migration history, but no canonical runtime
-- code reads or writes it after this migration.

CREATE SCHEMA IF NOT EXISTS aligned_media;

CREATE TABLE aligned_media.artifacts (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    artifact_id uuid NOT NULL,
    artifact_key char(64) NOT NULL,
    dataset_id text NOT NULL,
    rollout_id text NOT NULL,
    dataset_version bigint NOT NULL,
    camera_id text NOT NULL,
    profile_id text NOT NULL,
    profile_version text NOT NULL,
    pipeline_revision text NOT NULL,
    alignment_version text NOT NULL,
    source_sha256 char(64) NOT NULL,
    status text NOT NULL,
    object_prefix text,
    media_object_key text,
    object_manifest jsonb NOT NULL DEFAULT '[]'::jsonb,
    total_bytes bigint NOT NULL DEFAULT 0,
    frame_count bigint NOT NULL DEFAULT 0,
    duration_seconds double precision NOT NULL DEFAULT 0,
    fps integer NOT NULL DEFAULT 30,
    media_width integer,
    media_height integer,
    content_sha256 char(64),
    publication_token uuid,
    publication_receipt_at timestamptz,
    timeline_json jsonb,
    placeholder_count bigint NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL,
    ready_at timestamptz,
    commit_lease_expires_at timestamptz,
    dataset_committed_at timestamptz,
    failure_code text,
    version bigint NOT NULL DEFAULT 1,
    PRIMARY KEY (organization_id, project_id, region_code, artifact_id),
    UNIQUE (organization_id, project_id, region_code, artifact_key),
    UNIQUE (
        organization_id, project_id, region_code, dataset_id, rollout_id,
        dataset_version, camera_id, profile_id
    ),
    CHECK (organization_id <> '' AND project_id <> '' AND region_code <> ''),
    CHECK (dataset_id <> '' AND rollout_id <> '' AND camera_id <> ''),
    CHECK (profile_id <> '' AND profile_version <> '' AND pipeline_revision <> ''),
    CHECK (alignment_version <> '' AND dataset_version > 0),
    CHECK (artifact_key ~ '^[0-9a-f]{64}$'),
    CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (status IN ('GENERATING', 'READY', 'ABANDONING', 'FAILED')),
    CHECK (fps = 30),
    CHECK (
        total_bytes >= 0 AND frame_count >= 0 AND duration_seconds >= 0
        AND placeholder_count >= 0 AND version >= 1
    ),
    CHECK (jsonb_typeof(object_manifest) = 'array'),
    CHECK (timeline_json IS NULL OR jsonb_typeof(timeline_json) = 'object'),
    CHECK (
        (publication_token IS NULL AND publication_receipt_at IS NULL)
        OR (publication_token IS NOT NULL AND publication_receipt_at IS NOT NULL)
    ),
    CHECK (
        status <> 'READY'
        OR (
            object_prefix IS NOT NULL AND media_object_key IS NOT NULL
            AND jsonb_array_length(object_manifest) >= 2
            AND total_bytes > 0 AND frame_count > 0 AND duration_seconds > 0
            AND media_width > 0 AND media_height > 0
            AND content_sha256 IS NOT NULL AND publication_token IS NOT NULL
            AND timeline_json IS NOT NULL AND ready_at IS NOT NULL
            AND failure_code IS NULL
        )
    ),
    CHECK (dataset_committed_at IS NULL OR status = 'READY')
);

CREATE INDEX aligned_media_artifacts_scope_status_idx
ON aligned_media.artifacts (
    organization_id, project_id, region_code, status, dataset_id, dataset_version
);

CREATE INDEX aligned_media_artifacts_publication_idx
ON aligned_media.artifacts (
    organization_id, project_id, region_code, artifact_key, publication_token
)
WHERE publication_token IS NOT NULL;

CREATE INDEX aligned_media_artifacts_commit_lease_idx
ON aligned_media.artifacts (commit_lease_expires_at, artifact_id)
WHERE dataset_committed_at IS NULL AND commit_lease_expires_at IS NOT NULL;

CREATE TABLE aligned_media.jobs (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    job_id uuid NOT NULL,
    artifact_id uuid NOT NULL,
    artifact_key char(64) NOT NULL,
    status text NOT NULL,
    attempt integer NOT NULL DEFAULT 0,
    progress integer NOT NULL DEFAULT 0,
    request_json jsonb NOT NULL,
    error_code text,
    created_at timestamptz NOT NULL,
    started_at timestamptz,
    completed_at timestamptz,
    owner_id text,
    lease_expires_at timestamptz,
    attempt_token uuid,
    heartbeat_at timestamptz,
    PRIMARY KEY (organization_id, project_id, region_code, job_id),
    FOREIGN KEY (organization_id, project_id, region_code, artifact_id)
        REFERENCES aligned_media.artifacts (
            organization_id, project_id, region_code, artifact_id
        ) ON DELETE CASCADE,
    CHECK (artifact_key ~ '^[0-9a-f]{64}$'),
    CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')),
    CHECK (attempt >= 0 AND progress BETWEEN 0 AND 100),
    CHECK (jsonb_typeof(request_json) = 'object'),
    CHECK (
        (owner_id IS NULL AND lease_expires_at IS NULL AND attempt_token IS NULL)
        OR (owner_id IS NOT NULL AND owner_id <> ''
            AND lease_expires_at IS NOT NULL AND attempt_token IS NOT NULL)
    )
);

CREATE UNIQUE INDEX aligned_media_jobs_one_active_artifact_idx
ON aligned_media.jobs (organization_id, project_id, region_code, artifact_key)
WHERE status IN ('QUEUED', 'RUNNING');

CREATE INDEX aligned_media_jobs_expired_lease_idx
ON aligned_media.jobs (lease_expires_at, created_at, job_id)
WHERE status = 'RUNNING';

-- Deployment-global capacity is deliberately not tenant-scoped.
CREATE TABLE aligned_media.media_capacity_slots (
    slot_id integer PRIMARY KEY CHECK (slot_id BETWEEN 1 AND 128),
    owner_id text,
    attempt_token uuid,
    lease_expires_at timestamptz,
    heartbeat_at timestamptz,
    CHECK (
        (owner_id IS NULL AND attempt_token IS NULL AND lease_expires_at IS NULL)
        OR (owner_id IS NOT NULL AND attempt_token IS NOT NULL AND lease_expires_at IS NOT NULL)
    )
);

INSERT INTO aligned_media.media_capacity_slots (slot_id)
SELECT value FROM generate_series(1, 128) AS value
ON CONFLICT (slot_id) DO NOTHING;

CREATE INDEX aligned_media_capacity_available_idx
ON aligned_media.media_capacity_slots (lease_expires_at, slot_id);

SELECT core.apply_project_rls('aligned_media.artifacts'::regclass);
SELECT core.apply_project_rls('aligned_media.jobs'::regclass);

REVOKE TRUNCATE ON aligned_media.artifacts, aligned_media.jobs,
    aligned_media.media_capacity_slots FROM PUBLIC;

COMMENT ON TABLE preview.artifacts IS
    'DEPRECATED after preview/0004; retained for historical preview migrations only.';
COMMENT ON TABLE preview.jobs IS
    'DEPRECATED after preview/0004; retained for historical preview migrations only.';
COMMENT ON TABLE preview.sessions IS
    'DEPRECATED after preview/0004; retained for historical preview migrations only.';
