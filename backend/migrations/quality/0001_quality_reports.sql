-- BE-06 owns versioned profiles, immutable full reports, and mutable summaries.
CREATE TABLE IF NOT EXISTS quality_profiles (
    project_id text NOT NULL,
    profile_id text NOT NULL,
    profile_version integer NOT NULL CHECK (profile_version > 0),
    schema_version text NOT NULL CHECK (schema_version = 'quality-profile/v1'),
    profile_sha256 char(64) NOT NULL CHECK (profile_sha256 ~ '^[0-9a-f]{64}$'),
    profile_json jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, profile_id, profile_version),
    UNIQUE (project_id, profile_id, profile_version, profile_sha256),
    UNIQUE (project_id, profile_sha256)
);

CREATE TABLE IF NOT EXISTS qc_reports (
    report_sha256 char(64) PRIMARY KEY CHECK (report_sha256 ~ '^[0-9a-f]{64}$'),
    project_id text NOT NULL,
    region_code text NOT NULL,
    rollout_id text NOT NULL,
    source_sha256 char(64) NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    profile_id text NOT NULL,
    profile_version integer NOT NULL CHECK (profile_version > 0),
    profile_sha256 char(64) NOT NULL CHECK (profile_sha256 ~ '^[0-9a-f]{64}$'),
    engine_version text NOT NULL,
    schema_version text NOT NULL CHECK (schema_version = 'qc-report/v1'),
    status text NOT NULL CHECK (status IN ('PASS', 'RISK', 'REJECT')),
    report_json jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (
        project_id,
        region_code,
        rollout_id,
        source_sha256,
        profile_id,
        profile_version,
        engine_version
    ),
    FOREIGN KEY (project_id, profile_id, profile_version, profile_sha256)
        REFERENCES quality_profiles(project_id, profile_id, profile_version, profile_sha256)
);

CREATE TABLE IF NOT EXISTS quality_rollout_summaries (
    project_id text NOT NULL,
    region_code text NOT NULL,
    rollout_id text NOT NULL,
    source_sha256 char(64) NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    profile_id text NOT NULL,
    profile_version integer NOT NULL CHECK (profile_version > 0),
    engine_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('PASS', 'RISK', 'REJECT')),
    report_sha256 char(64) NOT NULL REFERENCES qc_reports(report_sha256),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, region_code, rollout_id)
);

CREATE INDEX IF NOT EXISTS qc_reports_rollout_created_idx
ON qc_reports (project_id, region_code, rollout_id, created_at DESC);

ALTER TABLE quality_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE qc_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE quality_rollout_summaries ENABLE ROW LEVEL SECURITY;
