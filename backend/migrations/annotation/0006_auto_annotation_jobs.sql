-- Replace the phase-one 501 stub with durable, provider-neutral job orchestration.

CREATE TABLE IF NOT EXISTS annotation.auto_annotation_jobs (
    job_id uuid PRIMARY KEY,
    project_id text NOT NULL,
    region_code text NOT NULL,
    task_id text NOT NULL REFERENCES annotation.annotation_tasks(task_id),
    source_revision bigint NOT NULL CHECK (source_revision >= 0),
    provider text NOT NULL,
    model_name text NOT NULL,
    input_selection jsonb NOT NULL,
    idempotency_key text NOT NULL,
    request_fingerprint char(64) NOT NULL,
    status text NOT NULL CHECK (
        status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'APPLIED')
    ),
    progress_percent integer NOT NULL DEFAULT 0 CHECK (progress_percent BETWEEN 0 AND 100),
    result_tags jsonb,
    result_operations jsonb,
    estimated_cost_micros bigint NOT NULL CHECK (estimated_cost_micros >= 0),
    actual_cost_micros bigint CHECK (actual_cost_micros >= 0),
    input_units bigint CHECK (input_units >= 0),
    output_units bigint CHECK (output_units >= 0),
    applied_revision bigint,
    error_code text,
    error_message text,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    UNIQUE (project_id, region_code, task_id, idempotency_key),
    CHECK (project_id <> '' AND region_code <> '' AND provider <> '' AND model_name <> ''),
    CHECK (idempotency_key <> '' AND created_by <> ''),
    CHECK (
        (status IN ('SUCCEEDED', 'APPLIED')
            AND result_tags IS NOT NULL AND result_operations IS NOT NULL
            AND actual_cost_micros IS NOT NULL AND error_code IS NULL)
        OR (status NOT IN ('SUCCEEDED', 'APPLIED')
            AND result_tags IS NULL AND result_operations IS NULL)
    ),
    CHECK ((status = 'APPLIED') = (applied_revision IS NOT NULL)),
    FOREIGN KEY (task_id, source_revision)
        REFERENCES annotation.annotation_revisions(task_id, revision),
    FOREIGN KEY (task_id, applied_revision)
        REFERENCES annotation.annotation_revisions(task_id, revision)
);

CREATE INDEX IF NOT EXISTS auto_annotation_jobs_scope_created_idx
ON annotation.auto_annotation_jobs(project_id, region_code, created_at DESC, job_id DESC);

CREATE INDEX IF NOT EXISTS auto_annotation_jobs_project_budget_idx
ON annotation.auto_annotation_jobs(project_id, created_at, status);

SELECT core.apply_project_rls('annotation.auto_annotation_jobs'::regclass);

REVOKE DELETE ON annotation.auto_annotation_jobs FROM PUBLIC;
