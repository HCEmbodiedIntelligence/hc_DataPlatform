-- BE-08 owns this idempotent PostgreSQL migration.
CREATE TABLE IF NOT EXISTS lance_schema_snapshots (
    project_id text NOT NULL,
    dataset_id text NOT NULL,
    schema_snapshot_id text NOT NULL,
    frequency_hz double precision NOT NULL CHECK (frequency_hz > 0),
    fields_json jsonb NOT NULL,
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    snapshot_json jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, dataset_id, schema_snapshot_id),
    UNIQUE (project_id, dataset_id, fingerprint)
);

CREATE TABLE IF NOT EXISTS lance_datasets (
    project_id text NOT NULL,
    dataset_id text NOT NULL,
    schema_snapshot_id text NOT NULL,
    frequency_hz double precision NOT NULL CHECK (frequency_hz > 0),
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    dataset_uri text NOT NULL UNIQUE,
    current_version bigint NOT NULL DEFAULT 0 CHECK (current_version >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, dataset_id),
    FOREIGN KEY (project_id, dataset_id, schema_snapshot_id)
        REFERENCES lance_schema_snapshots(project_id, dataset_id, schema_snapshot_id),
    UNIQUE (project_id, dataset_id, fingerprint)
);

CREATE TABLE IF NOT EXISTS lance_dataset_versions (
    project_id text NOT NULL,
    dataset_id text NOT NULL,
    version bigint NOT NULL CHECK (version > 0),
    schema_snapshot_id text NOT NULL,
    frequency_hz double precision NOT NULL CHECK (frequency_hz > 0),
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    dataset_uri text NOT NULL,
    lance_version bigint NOT NULL CHECK (lance_version > 0),
    storage_commit_id text NOT NULL UNIQUE CHECK (storage_commit_id ~ '^[0-9a-f]{64}$'),
    rollout_id text NOT NULL,
    source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    converter_version text NOT NULL,
    committed_rollouts jsonb NOT NULL,
    receipt_json jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, dataset_id, version),
    FOREIGN KEY (project_id, dataset_id)
        REFERENCES lance_datasets(project_id, dataset_id),
    UNIQUE (
        project_id, dataset_id, rollout_id, source_sha256, converter_version
    )
);

CREATE TABLE IF NOT EXISTS lance_rollout_lineage (
    project_id text NOT NULL,
    dataset_id text NOT NULL,
    rollout_id text NOT NULL,
    version_added bigint NOT NULL CHECK (version_added > 0),
    source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    converter_version text NOT NULL,
    schema_snapshot_id text NOT NULL,
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    fragment_uri text NOT NULL,
    fragment_content_hash text NOT NULL CHECK (fragment_content_hash ~ '^[0-9a-f]{64}$'),
    step_count bigint NOT NULL CHECK (step_count >= 0),
    storage_commit_id text NOT NULL UNIQUE
        REFERENCES lance_dataset_versions(storage_commit_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, dataset_id, rollout_id),
    FOREIGN KEY (project_id, dataset_id, version_added)
        REFERENCES lance_dataset_versions(project_id, dataset_id, version)
);

CREATE TABLE IF NOT EXISTS lance_pending_reconciliation (
    storage_commit_id text PRIMARY KEY CHECK (storage_commit_id ~ '^[0-9a-f]{64}$'),
    project_id text NOT NULL,
    dataset_id text NOT NULL,
    dataset_uri text NOT NULL,
    lance_version bigint NOT NULL CHECK (lance_version > 0),
    last_error text NOT NULL,
    attempt_count integer NOT NULL DEFAULT 1 CHECK (attempt_count > 0),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_attempt_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (project_id, dataset_id)
        REFERENCES lance_datasets(project_id, dataset_id)
);

CREATE INDEX IF NOT EXISTS lance_versions_lance_snapshot_idx
    ON lance_dataset_versions (dataset_uri, lance_version);

CREATE INDEX IF NOT EXISTS lance_pending_dataset_idx
    ON lance_pending_reconciliation (project_id, dataset_id, first_seen_at);
