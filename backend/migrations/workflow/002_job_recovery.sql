ALTER TABLE workflow.jobs
    ADD COLUMN IF NOT EXISTS workflow_run_id text,
    ADD COLUMN IF NOT EXISTS workflow_version text NOT NULL DEFAULT 'v1',
    ADD COLUMN IF NOT EXISTS temporal_namespace text NOT NULL DEFAULT 'default',
    ADD COLUMN IF NOT EXISTS task_queue text NOT NULL DEFAULT 'hc-data-pipeline',
    ADD COLUMN IF NOT EXISTS stage text NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS error_message text,
    ADD COLUMN IF NOT EXISTS cancellation_requested boolean NOT NULL DEFAULT false;

ALTER TABLE workflow.jobs
    DROP CONSTRAINT IF EXISTS jobs_status_check;

ALTER TABLE workflow.jobs
    ADD CONSTRAINT jobs_status_check CHECK (
        status IN (
            'PENDING',
            'RUNNING',
            'SUCCEEDED',
            'TECHNICAL_FAILED',
            'QUALITY_RISK',
            'QUALITY_REJECTED',
            'CANCELLED'
        )
    );

CREATE INDEX IF NOT EXISTS jobs_project_status_updated_idx
    ON workflow.jobs (project_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS workflow.reconciliation_items (
    reconciliation_id uuid PRIMARY KEY,
    workflow_id text NOT NULL UNIQUE,
    project_id text NOT NULL,
    resource_id text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('LANCE_CATALOG', 'PUBLISHING_ASSET')),
    idempotency_key text NOT NULL UNIQUE,
    status text NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'RESOLVED', 'FAILED')),
    payload jsonb NOT NULL,
    attempt integer NOT NULL DEFAULT 0,
    last_error_code text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    resolved_at timestamptz
);

CREATE INDEX IF NOT EXISTS reconciliation_pending_idx
    ON workflow.reconciliation_items (kind, status, updated_at)
    WHERE status IN ('PENDING', 'FAILED');

ALTER TABLE workflow.reconciliation_items ENABLE ROW LEVEL SECURITY;
