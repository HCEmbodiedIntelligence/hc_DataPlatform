-- P14 durable robot-model assets.  Asset bytes stay in object storage; this
-- ledger owns their immutable, project-scoped manifest and multipart lifecycle.

ALTER TABLE registry.robot_model_versions
    ADD COLUMN IF NOT EXISTS revision bigint NOT NULL DEFAULT 1;

ALTER TABLE registry.robot_model_versions
    ADD CONSTRAINT registry_robot_model_versions_revision_positive
    CHECK (revision > 0);

CREATE TABLE IF NOT EXISTS registry.robot_model_asset_uploads (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    upload_id uuid NOT NULL,
    version_id text NOT NULL,
    idempotency_key text NOT NULL,
    request_fingerprint char(64) NOT NULL,
    status text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    completed_at timestamptz,
    PRIMARY KEY (organization_id, project_id, upload_id),
    UNIQUE (organization_id, project_id, version_id, idempotency_key),
    FOREIGN KEY (organization_id, version_id)
        REFERENCES registry.robot_model_versions (organization_id, version_id),
    CHECK (organization_id <> ''),
    CHECK (project_id <> ''),
    CHECK (idempotency_key <> ''),
    CHECK (status IN ('UPLOADING', 'COMPLETED', 'CANCELLED', 'FAILED')),
    CHECK ((status = 'COMPLETED') = (completed_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS registry.robot_model_asset_upload_files (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    upload_id uuid NOT NULL,
    relative_path text NOT NULL,
    role text NOT NULL,
    media_type text NOT NULL,
    expected_size bigint NOT NULL,
    expected_sha256 char(64) NOT NULL,
    object_key text NOT NULL,
    multipart_upload_id text NOT NULL,
    status text NOT NULL,
    completed_etag text,
    completed_at timestamptz,
    PRIMARY KEY (organization_id, project_id, upload_id, relative_path),
    FOREIGN KEY (organization_id, project_id, upload_id)
        REFERENCES registry.robot_model_asset_uploads (organization_id, project_id, upload_id)
        ON DELETE CASCADE,
    CHECK (relative_path <> ''),
    CHECK (role IN ('URDF', 'MESH', 'TEXTURE', 'CONFIG', 'DOCUMENTATION')),
    CHECK (media_type <> ''),
    CHECK (expected_size > 0),
    CHECK (object_key <> ''),
    CHECK (multipart_upload_id <> ''),
    CHECK (status IN ('UPLOADING', 'COMPLETED', 'CANCELLED', 'FAILED')),
    CHECK ((status = 'COMPLETED') = (completed_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS registry.robot_model_assets (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    asset_id uuid NOT NULL,
    version_id text NOT NULL,
    relative_path text NOT NULL,
    role text NOT NULL,
    media_type text NOT NULL,
    size_bytes bigint NOT NULL,
    sha256 char(64) NOT NULL,
    object_key text NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, project_id, asset_id),
    UNIQUE (organization_id, project_id, version_id, relative_path),
    FOREIGN KEY (organization_id, version_id)
        REFERENCES registry.robot_model_versions (organization_id, version_id),
    CHECK (relative_path <> ''),
    CHECK (role IN ('URDF', 'MESH', 'TEXTURE', 'CONFIG', 'DOCUMENTATION')),
    CHECK (media_type <> ''),
    CHECK (size_bytes > 0),
    CHECK (object_key <> '')
);

CREATE INDEX IF NOT EXISTS registry_robot_model_asset_uploads_version_idx
ON registry.robot_model_asset_uploads (organization_id, project_id, version_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS registry_robot_model_assets_version_idx
ON registry.robot_model_assets (organization_id, project_id, version_id, relative_path);

SELECT core.apply_project_rls('registry.robot_model_asset_uploads'::regclass);
SELECT core.apply_project_rls('registry.robot_model_asset_upload_files'::regclass);
SELECT core.apply_project_rls('registry.robot_model_assets'::regclass);

-- P14 writes audit failures and denials explicitly.  Keep the historical
-- FEATURE_UNAVAILABLE value readable while newer paths never emit it.
ALTER TABLE registry.audit_events
    DROP CONSTRAINT IF EXISTS registry_audit_events_outcome_check;

ALTER TABLE registry.audit_events
    ADD CONSTRAINT registry_audit_events_outcome_check
    CHECK (outcome IN ('SUCCEEDED', 'FAILED', 'DENIED', 'FEATURE_UNAVAILABLE'));
