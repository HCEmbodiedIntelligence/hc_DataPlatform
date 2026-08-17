CREATE TABLE IF NOT EXISTS alignment_profiles (
    project_id text NOT NULL,
    profile_id text NOT NULL,
    profile_json jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (profile_id <> ''),
    CHECK (profile_json ->> 'schema_version' = 'alignment-profile/v1'),
    PRIMARY KEY (project_id, profile_id)
);

CREATE TABLE IF NOT EXISTS aligned_fragment_attempts (
    project_id text NOT NULL,
    rollout_id text NOT NULL,
    source_sha256 char(64) NOT NULL,
    converter_version text NOT NULL,
    attempt_id text NOT NULL,
    content_sha256 char(64),
    schema_sha256 char(64),
    row_count bigint,
    staging_uri text,
    staging_format text CHECK (staging_format = 'arrow-ipc/v1'),
    status text NOT NULL CHECK (status IN ('WRITING', 'READY', 'ABORTED')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (schema_sha256 IS NULL OR schema_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (row_count IS NULL OR row_count >= 0),
    CHECK (
        status <> 'READY'
        OR (
            content_sha256 IS NOT NULL
            AND schema_sha256 IS NOT NULL
            AND row_count IS NOT NULL
            AND staging_uri IS NOT NULL
            AND staging_format = 'arrow-ipc/v1'
        )
    ),
    PRIMARY KEY (project_id, rollout_id, source_sha256, converter_version, attempt_id)
);

ALTER TABLE alignment_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE aligned_fragment_attempts ENABLE ROW LEVEL SECURITY;
