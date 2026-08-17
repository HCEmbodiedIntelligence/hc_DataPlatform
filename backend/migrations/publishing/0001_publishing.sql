-- BE-11 append-only publication and export-attempt contract.
CREATE SCHEMA IF NOT EXISTS publishing;

CREATE TABLE IF NOT EXISTS publishing.dataset_versions (
    project_id text NOT NULL,
    dataset_id text NOT NULL,
    dataset_version text NOT NULL,
    base_lance_version text NOT NULL,
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    manifest_json jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, dataset_id, dataset_version)
);

CREATE TABLE IF NOT EXISTS publishing.publication_assets (
    project_id text NOT NULL,
    dataset_id text NOT NULL,
    dataset_version text NOT NULL,
    asset_kind text NOT NULL CHECK (asset_kind IN ('ANNOTATIONS_LANCE', 'TRAINING_MANIFEST')),
    artifact_uri text NOT NULL,
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    media_type text NOT NULL,
    size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
    PRIMARY KEY (project_id, dataset_id, dataset_version, asset_kind),
    FOREIGN KEY (project_id, dataset_id, dataset_version)
        REFERENCES publishing.dataset_versions(project_id, dataset_id, dataset_version)
);

CREATE TABLE IF NOT EXISTS publishing.export_attempts (
    project_id text NOT NULL,
    dataset_id text NOT NULL,
    dataset_version text NOT NULL,
    export_format text NOT NULL CHECK (export_format IN ('lance_snapshot', 'lerobot_v3')),
    attempt_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('STAGING', 'VALIDATING', 'FAILED', 'PUBLISHED')),
    staging_uri text NOT NULL,
    staged_content_sha256 text CHECK (
        staged_content_sha256 IS NULL OR staged_content_sha256 ~ '^[0-9a-f]{64}$'
    ),
    failure_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, dataset_id, dataset_version, export_format, attempt_id),
    FOREIGN KEY (project_id, dataset_id, dataset_version)
        REFERENCES publishing.dataset_versions(project_id, dataset_id, dataset_version)
);

CREATE TABLE IF NOT EXISTS publishing.published_exports (
    project_id text NOT NULL,
    dataset_id text NOT NULL,
    dataset_version text NOT NULL,
    export_format text NOT NULL CHECK (export_format IN ('lance_snapshot', 'lerobot_v3')),
    manifest_content_hash text NOT NULL CHECK (manifest_content_hash ~ '^[0-9a-f]{64}$'),
    artifact_uri text NOT NULL,
    artifact_content_sha256 text NOT NULL CHECK (artifact_content_sha256 ~ '^[0-9a-f]{64}$'),
    media_type text NOT NULL,
    row_count bigint NOT NULL CHECK (row_count >= 0),
    promoted_attempt_id text NOT NULL,
    published_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, dataset_id, dataset_version, export_format),
    FOREIGN KEY (project_id, dataset_id, dataset_version, export_format, promoted_attempt_id)
        REFERENCES publishing.export_attempts(
            project_id,
            dataset_id,
            dataset_version,
            export_format,
            attempt_id
        ),
    FOREIGN KEY (project_id, dataset_id, dataset_version)
        REFERENCES publishing.dataset_versions(project_id, dataset_id, dataset_version)
);

CREATE OR REPLACE FUNCTION publishing.reject_immutable_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'published BE-11 records are immutable'
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

CREATE OR REPLACE FUNCTION publishing.require_validated_export_attempt()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    attempt_status text;
    attempt_sha256 text;
BEGIN
    SELECT status, staged_content_sha256
      INTO attempt_status, attempt_sha256
      FROM publishing.export_attempts
     WHERE project_id = NEW.project_id
       AND dataset_id = NEW.dataset_id
       AND dataset_version = NEW.dataset_version
       AND export_format = NEW.export_format
       AND attempt_id = NEW.promoted_attempt_id;

    IF attempt_status IS DISTINCT FROM 'PUBLISHED'
       OR attempt_sha256 IS DISTINCT FROM NEW.artifact_content_sha256 THEN
        RAISE EXCEPTION 'export attempt must be validated before promotion'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'dataset_versions_immutable'
          AND tgrelid = 'publishing.dataset_versions'::regclass
    ) THEN
        CREATE TRIGGER dataset_versions_immutable
        BEFORE UPDATE OR DELETE ON publishing.dataset_versions
        FOR EACH ROW EXECUTE FUNCTION publishing.reject_immutable_change();
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'publication_assets_immutable'
          AND tgrelid = 'publishing.publication_assets'::regclass
    ) THEN
        CREATE TRIGGER publication_assets_immutable
        BEFORE UPDATE OR DELETE ON publishing.publication_assets
        FOR EACH ROW EXECUTE FUNCTION publishing.reject_immutable_change();
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'published_exports_immutable'
          AND tgrelid = 'publishing.published_exports'::regclass
    ) THEN
        CREATE TRIGGER published_exports_immutable
        BEFORE UPDATE OR DELETE ON publishing.published_exports
        FOR EACH ROW EXECUTE FUNCTION publishing.reject_immutable_change();
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'published_exports_require_validated_attempt'
          AND tgrelid = 'publishing.published_exports'::regclass
    ) THEN
        CREATE TRIGGER published_exports_require_validated_attempt
        BEFORE INSERT ON publishing.published_exports
        FOR EACH ROW EXECUTE FUNCTION publishing.require_validated_export_attempt();
    END IF;
END;
$$;

ALTER TABLE publishing.dataset_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE publishing.publication_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE publishing.export_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE publishing.published_exports ENABLE ROW LEVEL SECURITY;

CREATE INDEX IF NOT EXISTS export_attempts_status_idx
ON publishing.export_attempts (project_id, status, updated_at);
