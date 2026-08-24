-- P19: versioned retention, legal holds, and durable redacted export jobs.

CREATE TABLE IF NOT EXISTS core.audit_retention_policies (
    project_id text NOT NULL,
    region_code text NOT NULL DEFAULT '',
    organization_id text NOT NULL,
    policy_version bigint NOT NULL CHECK (policy_version > 0),
    standard_days integer NOT NULL CHECK (standard_days BETWEEN 30 AND 3650),
    security_days integer NOT NULL CHECK (security_days BETWEEN 90 AND 3650),
    etag text NOT NULL,
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, region_code),
    CHECK (project_id <> '' AND organization_id <> '' AND updated_by <> '' AND etag <> '')
);

CREATE TABLE IF NOT EXISTS core.audit_legal_holds (
    hold_id uuid PRIMARY KEY,
    project_id text NOT NULL,
    region_code text NOT NULL DEFAULT '',
    organization_id text NOT NULL,
    reason text NOT NULL,
    occurred_from timestamptz NOT NULL,
    occurred_to timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('ACTIVE', 'RELEASED')),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    released_by text,
    released_at timestamptz,
    CHECK (project_id <> '' AND organization_id <> '' AND length(reason) >= 3),
    CHECK (occurred_from < occurred_to),
    CHECK (
        (status = 'ACTIVE' AND released_by IS NULL AND released_at IS NULL)
        OR (status = 'RELEASED' AND released_by IS NOT NULL AND released_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS audit_legal_holds_scope_status_time_idx
ON core.audit_legal_holds (project_id, region_code, status, occurred_from, occurred_to);

CREATE TABLE IF NOT EXISTS core.audit_export_jobs (
    job_id uuid PRIMARY KEY,
    project_id text NOT NULL,
    region_code text NOT NULL DEFAULT '',
    organization_id text NOT NULL,
    idempotency_key text NOT NULL,
    request_fingerprint char(64) NOT NULL,
    status text NOT NULL CHECK (
        status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')
    ),
    occurred_from timestamptz NOT NULL,
    occurred_to timestamptz NOT NULL,
    exported_event_count bigint NOT NULL DEFAULT 0 CHECK (exported_event_count >= 0),
    scanned_page_count bigint NOT NULL DEFAULT 0 CHECK (scanned_page_count >= 0),
    artifact_key text,
    artifact_sha256 char(64),
    artifact_size_bytes bigint CHECK (artifact_size_bytes >= 0),
    error_code text,
    error_message text,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    UNIQUE (project_id, region_code, idempotency_key),
    CHECK (project_id <> '' AND organization_id <> '' AND idempotency_key <> ''),
    CHECK (occurred_from < occurred_to),
    CHECK (
        (status = 'SUCCEEDED' AND artifact_key IS NOT NULL
            AND artifact_sha256 IS NOT NULL AND artifact_size_bytes IS NOT NULL
            AND error_code IS NULL AND error_message IS NULL)
        OR (status <> 'SUCCEEDED' AND artifact_key IS NULL
            AND artifact_sha256 IS NULL AND artifact_size_bytes IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS audit_export_jobs_scope_created_idx
ON core.audit_export_jobs (project_id, region_code, created_at DESC, job_id DESC);

SELECT core.apply_project_rls('core.audit_retention_policies'::regclass);
SELECT core.apply_project_rls('core.audit_legal_holds'::regclass);
SELECT core.apply_project_rls('core.audit_export_jobs'::regclass);

-- Retention and holds are governance evidence. They may be appended/versioned or
-- explicitly released through the API, never deleted by an application role.
REVOKE DELETE ON core.audit_retention_policies FROM PUBLIC;
REVOKE DELETE ON core.audit_legal_holds FROM PUBLIC;
REVOKE DELETE ON core.audit_export_jobs FROM PUBLIC;
