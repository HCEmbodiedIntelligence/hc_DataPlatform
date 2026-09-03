-- P02 data-source registry, credential vault metadata, and connection-test jobs.
-- Source documents are safe projections only. Secret input is encrypted with pgcrypto in
-- a separate table and is never embedded in source_document or audit details.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS ingest.data_sources (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    source_id text NOT NULL,
    name text NOT NULL,
    source_type text NOT NULL,
    source_format text NOT NULL,
    administrative_state text NOT NULL,
    connectivity_state text NOT NULL,
    credential_state text NOT NULL,
    version bigint NOT NULL,
    source_document jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, source_id),
    FOREIGN KEY (organization_id, project_id)
        REFERENCES registry.organization_projects (organization_id, project_id),
    CHECK (organization_id <> ''),
    CHECK (project_id <> ''),
    CHECK (region_code <> ''),
    CHECK (source_id <> ''),
    CHECK (name <> ''),
    CHECK (source_type IN ('ROBOT', 'EDGE_AGENT', 'OSS_IMPORT')),
    CHECK (source_format <> ''),
    CHECK (administrative_state IN ('ENABLED', 'DISABLED')),
    CHECK (connectivity_state IN (
        'UNKNOWN', 'ONLINE', 'DEGRADED', 'OFFLINE', 'AUTH_FAILED', 'CONFIG_ERROR'
    )),
    CHECK (credential_state IN (
        'NOT_REQUIRED', 'MISSING', 'CONFIGURED', 'ROTATION_DUE', 'EXPIRED', 'REVOKED', 'INVALID'
    )),
    CHECK (version > 0),
    CHECK (jsonb_typeof(source_document) = 'object'),
    CHECK (source_document ->> 'id' = source_id),
    CHECK (source_document ->> 'source_type' = source_type),
    CHECK (source_document ->> 'administrative_state' = administrative_state),
    CHECK (source_document -> 'binding' ->> 'kind' = source_type),
    CHECK (source_document -> 'configuration' ->> 'kind' = source_type),
    CHECK (NOT (source_document ? 'credential_input')),
    CHECK (NOT (source_document ? 'token')),
    CHECK (updated_at >= created_at)
);

CREATE UNIQUE INDEX IF NOT EXISTS ingest_data_sources_scope_name_uq
ON ingest.data_sources (organization_id, project_id, region_code, lower(name));

CREATE INDEX IF NOT EXISTS ingest_data_sources_scope_updated_idx
ON ingest.data_sources (organization_id, project_id, region_code, updated_at DESC, source_id DESC);

CREATE INDEX IF NOT EXISTS ingest_data_sources_scope_filters_idx
ON ingest.data_sources (
    organization_id, project_id, region_code, source_type, administrative_state,
    connectivity_state, credential_state
);

CREATE TABLE IF NOT EXISTS ingest.data_source_credentials (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    source_id text NOT NULL,
    credential_version bigint NOT NULL,
    credential_kind text NOT NULL,
    secret_ciphertext bytea NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (
        organization_id, project_id, region_code, source_id, credential_version
    ),
    FOREIGN KEY (organization_id, project_id, region_code, source_id)
        REFERENCES ingest.data_sources (organization_id, project_id, region_code, source_id),
    CHECK (credential_version > 0),
    CHECK (credential_kind IN ('TOKEN', 'BASIC', 'DEVICE_CERTIFICATE')),
    CHECK (octet_length(secret_ciphertext) > 0)
);

CREATE TABLE IF NOT EXISTS ingest.data_source_connection_test_jobs (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    job_id text NOT NULL,
    source_id text NOT NULL,
    status text NOT NULL,
    resource_version bigint NOT NULL,
    job_document jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, job_id),
    FOREIGN KEY (organization_id, project_id, region_code, source_id)
        REFERENCES ingest.data_sources (organization_id, project_id, region_code, source_id),
    CHECK (job_id <> ''),
    CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')),
    CHECK (resource_version > 0),
    CHECK (jsonb_typeof(job_document) = 'object'),
    CHECK (job_document -> 'connection_test' ->> 'id' = job_id),
    CHECK (job_document -> 'async_job' ->> 'job_id' = job_id),
    CHECK (updated_at >= created_at)
);

CREATE INDEX IF NOT EXISTS ingest_data_source_connection_jobs_source_idx
ON ingest.data_source_connection_test_jobs (
    organization_id, project_id, region_code, source_id, created_at DESC, job_id DESC
);

SELECT core.apply_project_rls('ingest.data_sources'::regclass);
SELECT core.apply_project_rls('ingest.data_source_credentials'::regclass);
SELECT core.apply_project_rls('ingest.data_source_connection_test_jobs'::regclass);
