-- Durable, tenant-scoped preview control-plane state. Media bytes remain in
-- object storage and are always deleted by the exact object_manifest below.

CREATE SCHEMA IF NOT EXISTS preview;

CREATE TABLE preview.artifacts (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    artifact_id uuid NOT NULL,
    artifact_key char(64) NOT NULL,
    dataset_id text NOT NULL,
    rollout_id text NOT NULL,
    lance_version text NOT NULL,
    camera_id text NOT NULL,
    profile_id text NOT NULL,
    pipeline_revision text NOT NULL,
    source_start_step bigint,
    source_end_step bigint,
    status text NOT NULL,
    object_prefix text,
    playlist_key text,
    object_manifest jsonb NOT NULL DEFAULT '[]'::jsonb,
    total_bytes bigint NOT NULL DEFAULT 0,
    frame_count bigint NOT NULL DEFAULT 0,
    duration_seconds double precision NOT NULL DEFAULT 0,
    content_sha256 char(64),
    rebuild_source_id text NOT NULL,
    storage_class text NOT NULL DEFAULT 'REBUILDABLE_DERIVATIVE',
    created_at timestamptz NOT NULL,
    ready_at timestamptz,
    last_accessed_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    failure_code text,
    version bigint NOT NULL DEFAULT 1,
    active_reference_count bigint NOT NULL DEFAULT 0,
    legal_hold boolean NOT NULL DEFAULT false,
    governance_hold boolean NOT NULL DEFAULT false,
    retention_until timestamptz,
    PRIMARY KEY (organization_id, project_id, region_code, artifact_id),
    UNIQUE (organization_id, project_id, region_code, artifact_key),
    CHECK (organization_id <> '' AND project_id <> '' AND region_code <> ''),
    CHECK (dataset_id <> '' AND rollout_id <> '' AND lance_version <> ''),
    CHECK (camera_id <> '' AND profile_id <> '' AND pipeline_revision <> ''),
    CHECK (artifact_key ~ '^[0-9a-f]{64}$'),
    CHECK (status IN ('GENERATING', 'READY', 'FAILED', 'DELETING')),
    CHECK (storage_class = 'REBUILDABLE_DERIVATIVE'),
    CHECK (source_start_step IS NULL OR source_start_step >= 0),
    CHECK (source_end_step IS NULL OR source_end_step > 0),
    CHECK (
        source_start_step IS NULL OR source_end_step IS NULL
        OR source_end_step > source_start_step
    ),
    CHECK (total_bytes >= 0 AND frame_count >= 0 AND duration_seconds >= 0),
    CHECK (version >= 1 AND active_reference_count >= 0),
    CHECK (jsonb_typeof(object_manifest) = 'array'),
    CHECK (
        (status = 'READY'
            AND object_prefix IS NOT NULL AND playlist_key IS NOT NULL
            AND ready_at IS NOT NULL AND content_sha256 IS NOT NULL
            AND failure_code IS NULL)
        OR status <> 'READY'
    )
);

CREATE INDEX preview_artifacts_scope_status_lru_idx
ON preview.artifacts (
    organization_id, project_id, region_code, status, last_accessed_at, artifact_id
);

CREATE INDEX preview_artifacts_gc_idx
ON preview.artifacts (expires_at, last_accessed_at, artifact_id)
WHERE status IN ('READY', 'FAILED') AND active_reference_count = 0;

CREATE TABLE preview.jobs (
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
    PRIMARY KEY (organization_id, project_id, region_code, job_id),
    FOREIGN KEY (organization_id, project_id, region_code, artifact_id)
        REFERENCES preview.artifacts (
            organization_id, project_id, region_code, artifact_id
        ) ON DELETE CASCADE,
    CHECK (artifact_key ~ '^[0-9a-f]{64}$'),
    CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')),
    CHECK (attempt >= 0 AND progress BETWEEN 0 AND 100),
    CHECK (jsonb_typeof(request_json) = 'object')
);

CREATE UNIQUE INDEX preview_jobs_one_active_artifact_idx
ON preview.jobs (organization_id, project_id, region_code, artifact_key)
WHERE status IN ('QUEUED', 'RUNNING');

CREATE INDEX preview_jobs_scope_status_created_idx
ON preview.jobs (
    organization_id, project_id, region_code, status, created_at, job_id
);

CREATE TABLE preview.sessions (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    session_id uuid NOT NULL,
    artifact_id uuid NOT NULL,
    artifact_key char(64) NOT NULL,
    request_json jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, session_id),
    FOREIGN KEY (organization_id, project_id, region_code, artifact_id)
        REFERENCES preview.artifacts (
            organization_id, project_id, region_code, artifact_id
        ) ON DELETE CASCADE,
    CHECK (artifact_key ~ '^[0-9a-f]{64}$'),
    CHECK (jsonb_typeof(request_json) = 'object'),
    CHECK (expires_at > created_at)
);

CREATE INDEX preview_sessions_expiry_idx
ON preview.sessions (expires_at, session_id);

CREATE INDEX preview_sessions_artifact_idx
ON preview.sessions (
    organization_id, project_id, region_code, artifact_id, expires_at
);

SELECT core.apply_project_rls('preview.artifacts'::regclass);
SELECT core.apply_project_rls('preview.jobs'::regclass);
SELECT core.apply_project_rls('preview.sessions'::regclass);

REVOKE TRUNCATE ON preview.artifacts, preview.jobs, preview.sessions FROM PUBLIC;
