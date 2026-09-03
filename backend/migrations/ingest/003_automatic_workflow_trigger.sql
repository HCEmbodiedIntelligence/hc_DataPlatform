-- BR02: one durable ingest trigger for each committed upload session.

CREATE TABLE IF NOT EXISTS ingest.workflow_triggers (
    session_id uuid PRIMARY KEY REFERENCES ingest.upload_sessions(session_id),
    project_id text NOT NULL,
    region_code text NOT NULL,
    rollout_id text NOT NULL,
    event_id uuid NOT NULL UNIQUE,
    workflow_id text NOT NULL UNIQUE,
    status text NOT NULL CHECK (status IN ('PENDING', 'DISPATCHED', 'RETRY_WAIT')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, region_code, rollout_id),
    FOREIGN KEY (project_id, rollout_id)
        REFERENCES ingest.rollouts(project_id, rollout_id),
    CHECK (workflow_id <> ''),
    CHECK (last_error_code IS NULL OR last_error_code ~ '^[A-Z0-9_]+$')
);

CREATE INDEX IF NOT EXISTS ingest_workflow_triggers_scope_status_idx
ON ingest.workflow_triggers(project_id, region_code, status, updated_at, session_id);

SELECT core.apply_project_rls('ingest.workflow_triggers'::regclass);
