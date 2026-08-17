CREATE SCHEMA IF NOT EXISTS workflow;

CREATE TABLE IF NOT EXISTS workflow.jobs (
    job_id uuid PRIMARY KEY,
    workflow_id text NOT NULL UNIQUE,
    project_id text NOT NULL,
    resource_id text NOT NULL,
    job_type text NOT NULL,
    status text NOT NULL,
    attempt integer NOT NULL DEFAULT 0,
    result jsonb,
    error_code text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);

ALTER TABLE workflow.jobs ENABLE ROW LEVEL SECURITY;

