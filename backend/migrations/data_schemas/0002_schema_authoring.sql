-- P17 durable schema authoring. Existing registry rows remain readable; new
-- drafts, validation evidence, preflights and command receipts are forward-only.

ALTER TABLE data_schemas.stream_schema_versions
    ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'SEED',
    ADD COLUMN IF NOT EXISTS revision bigint NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS created_by text NOT NULL DEFAULT 'system',
    ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS published_by text,
    ADD COLUMN IF NOT EXISTS published_at timestamptz;

DO $block$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'stream_schema_versions_source_valid'
          AND conrelid = 'data_schemas.stream_schema_versions'::regclass
    ) THEN
        ALTER TABLE data_schemas.stream_schema_versions
            ADD CONSTRAINT stream_schema_versions_source_valid
            CHECK (source IN ('SEED', 'MANUAL', 'IMPORT'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'stream_schema_versions_revision_positive'
          AND conrelid = 'data_schemas.stream_schema_versions'::regclass
    ) THEN
        ALTER TABLE data_schemas.stream_schema_versions
            ADD CONSTRAINT stream_schema_versions_revision_positive CHECK (revision >= 1);
    END IF;
END
$block$;

-- This registry is organization-owned, but every access is selected through an
-- already verified project membership. Its historical shape has no project_id,
-- so core.apply_project_rls cannot be used directly.
ALTER TABLE data_schemas.stream_schema_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_schemas.stream_schema_versions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS hc_organization_scope_isolation
ON data_schemas.stream_schema_versions;
CREATE POLICY hc_organization_scope_isolation
ON data_schemas.stream_schema_versions
USING (
    EXISTS (
        SELECT 1
          FROM registry.organization_projects membership
         WHERE membership.organization_id = stream_schema_versions.organization_id
           AND core.scope_matches(membership.project_id)
    )
)
WITH CHECK (
    EXISTS (
        SELECT 1
          FROM registry.organization_projects membership
         WHERE membership.organization_id = stream_schema_versions.organization_id
           AND core.scope_matches(membership.project_id)
    )
);

CREATE TABLE IF NOT EXISTS data_schemas.schema_validation_reports (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    report_id text NOT NULL,
    schema_id text NOT NULL,
    schema_version bigint NOT NULL,
    content_hash text NOT NULL,
    compatibility_check_id text NOT NULL,
    compatibility_result text NOT NULL,
    status text NOT NULL,
    findings jsonb NOT NULL DEFAULT '[]'::jsonb,
    checked_by text NOT NULL,
    checked_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, project_id, report_id),
    FOREIGN KEY (organization_id, schema_id, schema_version)
        REFERENCES data_schemas.stream_schema_versions (organization_id, schema_id, schema_version),
    FOREIGN KEY (organization_id, project_id)
        REFERENCES registry.organization_projects (organization_id, project_id),
    CHECK (report_id <> ''), CHECK (schema_id <> ''), CHECK (schema_version > 0),
    CHECK (content_hash ~ '^[0-9a-f]{64}$'), CHECK (compatibility_check_id <> ''),
    CHECK (compatibility_result IN ('PASSED', 'FAILED')),
    CHECK (status IN ('PASSED', 'FAILED')),
    CHECK (jsonb_typeof(findings) = 'array'), CHECK (checked_by <> '')
);

CREATE INDEX IF NOT EXISTS schema_validation_reports_version_idx
ON data_schemas.schema_validation_reports
    (organization_id, project_id, schema_id, schema_version, checked_at DESC, report_id DESC);

CREATE TABLE IF NOT EXISTS data_schemas.schema_publish_preflights (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    preflight_id text NOT NULL,
    schema_id text NOT NULL,
    schema_version bigint NOT NULL,
    idempotency_key text NOT NULL,
    expected_etag text NOT NULL,
    expected_hash text NOT NULL,
    validation_report_id text NOT NULL,
    compatibility_check_id text NOT NULL,
    token_hash text NOT NULL,
    allowed boolean NOT NULL,
    status text NOT NULL DEFAULT 'ISSUED',
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, project_id, preflight_id),
    UNIQUE (organization_id, project_id, schema_id, schema_version, idempotency_key),
    FOREIGN KEY (organization_id, schema_id, schema_version)
        REFERENCES data_schemas.stream_schema_versions (organization_id, schema_id, schema_version),
    FOREIGN KEY (organization_id, project_id, validation_report_id)
        REFERENCES data_schemas.schema_validation_reports (organization_id, project_id, report_id),
    FOREIGN KEY (organization_id, project_id)
        REFERENCES registry.organization_projects (organization_id, project_id),
    CHECK (preflight_id <> ''), CHECK (schema_id <> ''), CHECK (schema_version > 0),
    CHECK (idempotency_key <> ''), CHECK (expected_etag <> ''),
    CHECK (expected_hash ~ '^[0-9a-f]{64}$'), CHECK (validation_report_id <> ''),
    CHECK (compatibility_check_id <> ''), CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    CHECK (status IN ('ISSUED', 'CONSUMED', 'EXPIRED')), CHECK (created_by <> ''),
    CHECK ((status = 'CONSUMED') = (consumed_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS schema_publish_preflights_active_idx
ON data_schemas.schema_publish_preflights
    (organization_id, project_id, schema_id, schema_version, expires_at)
WHERE status = 'ISSUED';

CREATE TABLE IF NOT EXISTS data_schemas.schema_command_receipts (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    resource_id text NOT NULL,
    operation text NOT NULL,
    idempotency_key text NOT NULL,
    request_fingerprint text NOT NULL,
    response jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, project_id, resource_id, operation, idempotency_key),
    FOREIGN KEY (organization_id, project_id)
        REFERENCES registry.organization_projects (organization_id, project_id),
    CHECK (resource_id <> ''),
    CHECK (operation IN ('CREATE', 'IMPORT', 'UPDATE', 'VALIDATE', 'PREFLIGHT', 'PUBLISH')),
    CHECK (idempotency_key <> ''), CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    CHECK (jsonb_typeof(response) = 'object')
);

SELECT core.apply_project_rls('data_schemas.schema_validation_reports'::regclass);
SELECT core.apply_project_rls('data_schemas.schema_publish_preflights'::regclass);
SELECT core.apply_project_rls('data_schemas.schema_command_receipts'::regclass);
