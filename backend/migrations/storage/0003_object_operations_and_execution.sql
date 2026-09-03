-- P12/P13 production object controls and approval-bound lifecycle execution.

ALTER TABLE storage.lifecycle_policies
    DROP CONSTRAINT IF EXISTS lifecycle_policies_action_check;
ALTER TABLE storage.lifecycle_policies
    ADD CONSTRAINT lifecycle_policies_action_check
    CHECK (action IN (
        'RETAIN', 'REVIEW_EXPIRATION', 'ARCHIVE', 'TRANSITION_TO_COLD',
        'CLEAN_REBUILDABLE_CACHE'
    ));

ALTER TABLE storage.lifecycle_executions
    DROP CONSTRAINT IF EXISTS lifecycle_executions_action_check,
    DROP CONSTRAINT IF EXISTS lifecycle_executions_check,
    DROP CONSTRAINT IF EXISTS lifecycle_executions_check1,
    DROP CONSTRAINT IF EXISTS lifecycle_executions_status_check;
ALTER TABLE storage.lifecycle_executions
    ADD COLUMN IF NOT EXISTS dry_run boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS approval_id text,
    ADD COLUMN IF NOT EXISTS plan_hash char(64),
    ADD COLUMN IF NOT EXISTS requested_by text NOT NULL DEFAULT 'legacy-worker',
    ADD COLUMN IF NOT EXISTS approved_by text,
    ADD COLUMN IF NOT EXISTS approved_at timestamptz,
    ADD COLUMN IF NOT EXISTS temporal_workflow_id text,
    ADD COLUMN IF NOT EXISTS completed_at timestamptz;
ALTER TABLE storage.lifecycle_executions
    ADD CONSTRAINT lifecycle_executions_action_check
        CHECK (action IN ('ARCHIVE', 'TRANSITION_TO_COLD', 'CLEAN_REBUILDABLE_CACHE')),
    ADD CONSTRAINT lifecycle_executions_plan_hash_check
        CHECK (plan_hash IS NULL OR plan_hash ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT lifecycle_executions_approval_check
        CHECK (
            NOT production
            OR (
                production_execution_approved
                AND approval_id IS NOT NULL
                AND plan_hash IS NOT NULL
                AND approved_by IS NOT NULL
                AND approved_at IS NOT NULL
            )
        ),
    ADD CONSTRAINT lifecycle_executions_status_check
        CHECK (status IN (
            'DRY_RUN', 'AWAITING_APPROVAL', 'APPROVED', 'QUEUED', 'RUNNING',
            'BLOCKED', 'COMPLETED', 'FAILED', 'CANCELLED'
        ));

ALTER TABLE storage.lifecycle_execution_items
    DROP CONSTRAINT IF EXISTS lifecycle_execution_items_check;
ALTER TABLE storage.lifecycle_execution_items
    ADD COLUMN IF NOT EXISTS object_id text,
    ADD COLUMN IF NOT EXISTS retention_active boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS legal_hold boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS governance_hold boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS blocked_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS last_error_code text;
UPDATE storage.lifecycle_execution_items
SET object_id = physical_instance_id
WHERE object_id IS NULL;
ALTER TABLE storage.lifecycle_execution_items
    ALTER COLUMN object_id SET NOT NULL;

CREATE TABLE IF NOT EXISTS storage.managed_objects (
    project_id text NOT NULL,
    object_id text NOT NULL,
    display_key text NOT NULL,
    object_key text NOT NULL,
    original_object_key text NOT NULL,
    physical_bytes numeric(39, 0) NOT NULL,
    checksum_sha256 char(64) NOT NULL,
    business_category text NOT NULL,
    object_role text NOT NULL,
    storage_tier text NOT NULL DEFAULT 'HOT',
    status text NOT NULL DEFAULT 'ACTIVE',
    active_reference_count bigint NOT NULL DEFAULT 0,
    retention_until timestamptz,
    legal_hold boolean NOT NULL DEFAULT false,
    governance_hold boolean NOT NULL DEFAULT false,
    rebuild_source_id text,
    recoverable_until timestamptz,
    version bigint NOT NULL DEFAULT 1,
    etag text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, object_id),
    CHECK (project_id <> ''),
    CHECK (object_id <> ''),
    CHECK (display_key <> ''),
    CHECK (object_key <> ''),
    CHECK (original_object_key <> ''),
    CHECK (physical_bytes >= 0),
    CHECK (checksum_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (business_category IN (
        'RAW', 'ANNOTATION_COMPLETE', 'PENDING_ANNOTATION', 'ISSUE_DATA'
    )),
    CHECK (object_role IN (
        'RAW', 'MANIFEST', 'PUBLISHED_MANIFEST', 'REBUILDABLE_DERIVATIVE', 'OTHER'
    )),
    CHECK (storage_tier IN ('HOT', 'COLD', 'ARCHIVE')),
    CHECK (status IN ('ACTIVE', 'TRASHED', 'ARCHIVED', 'TRANSITIONING', 'FAILED')),
    CHECK (active_reference_count >= 0),
    CHECK (version > 0),
    CHECK (etag <> ''),
    CHECK (
        (status = 'TRASHED' AND recoverable_until IS NOT NULL)
        OR (status <> 'TRASHED' AND recoverable_until IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS storage_managed_objects_page_idx
ON storage.managed_objects (project_id, object_id);
CREATE INDEX IF NOT EXISTS storage_managed_objects_policy_idx
ON storage.managed_objects (
    project_id, status, business_category, object_role, updated_at, object_id
);
CREATE INDEX IF NOT EXISTS storage_managed_objects_trash_expiry_idx
ON storage.managed_objects (recoverable_until, project_id, object_id)
WHERE status = 'TRASHED';

CREATE TABLE IF NOT EXISTS storage.managed_multipart_uploads (
    project_id text NOT NULL,
    multipart_id text NOT NULL,
    upload_id text NOT NULL,
    object_key text NOT NULL,
    display_key text NOT NULL,
    received_bytes numeric(39, 0) NOT NULL DEFAULT 0,
    part_count integer NOT NULL DEFAULT 0,
    status text NOT NULL DEFAULT 'ACTIVE',
    version bigint NOT NULL DEFAULT 1,
    etag text NOT NULL,
    started_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, multipart_id),
    UNIQUE (project_id, upload_id),
    CHECK (project_id <> ''),
    CHECK (multipart_id <> ''),
    CHECK (upload_id <> ''),
    CHECK (object_key <> ''),
    CHECK (display_key <> ''),
    CHECK (received_bytes >= 0),
    CHECK (part_count >= 0),
    CHECK (status IN ('ACTIVE', 'ABORTING', 'ABORTED', 'COMPLETED', 'FAILED')),
    CHECK (version > 0),
    CHECK (etag <> '')
);

CREATE TABLE IF NOT EXISTS storage.object_operations (
    project_id text NOT NULL,
    operation_id text NOT NULL,
    object_id text,
    multipart_id text,
    action text NOT NULL,
    status text NOT NULL,
    idempotency_key text NOT NULL,
    request_fingerprint char(64) NOT NULL,
    actor_id text NOT NULL,
    request_id text NOT NULL,
    attempt integer NOT NULL DEFAULT 0,
    error_code text,
    created_at timestamptz NOT NULL,
    completed_at timestamptz,
    PRIMARY KEY (project_id, operation_id),
    CHECK (project_id <> ''),
    CHECK (operation_id <> ''),
    CHECK ((object_id IS NULL) <> (multipart_id IS NULL)),
    CHECK (action IN (
        'DOWNLOAD', 'TRASH', 'RESTORE', 'ARCHIVE', 'TRANSITION_TO_COLD',
        'ABORT_MULTIPART', 'PURGE'
    )),
    CHECK (status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')),
    CHECK (idempotency_key <> ''),
    CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    CHECK (actor_id <> ''),
    CHECK (request_id <> ''),
    CHECK (attempt >= 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS storage_object_operations_replay_idx
ON storage.object_operations (
    project_id, action, COALESCE(object_id, multipart_id), idempotency_key
);

CREATE TABLE IF NOT EXISTS storage.lifecycle_execution_approvals (
    project_id text NOT NULL,
    approval_id text NOT NULL,
    execution_id text NOT NULL,
    plan_hash char(64) NOT NULL,
    requested_by text NOT NULL,
    approved_by text NOT NULL,
    justification text NOT NULL,
    approved_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    PRIMARY KEY (project_id, approval_id),
    UNIQUE (project_id, execution_id),
    FOREIGN KEY (project_id, execution_id)
        REFERENCES storage.lifecycle_executions (project_id, execution_id)
        ON DELETE RESTRICT,
    CHECK (approval_id <> ''),
    CHECK (plan_hash ~ '^[0-9a-f]{64}$'),
    CHECK (requested_by <> approved_by),
    CHECK (justification <> ''),
    CHECK (expires_at > approved_at)
);

CREATE TABLE IF NOT EXISTS storage.lifecycle_execution_logs (
    project_id text NOT NULL,
    execution_id text NOT NULL,
    sequence bigint GENERATED ALWAYS AS IDENTITY,
    level text NOT NULL,
    event text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, execution_id, sequence),
    FOREIGN KEY (project_id, execution_id)
        REFERENCES storage.lifecycle_executions (project_id, execution_id)
        ON DELETE RESTRICT,
    CHECK (level IN ('INFO', 'WARNING', 'ERROR')),
    CHECK (event <> ''),
    CHECK (jsonb_typeof(details) = 'object')
);

CREATE TABLE IF NOT EXISTS storage.lifecycle_schedules (
    project_id text NOT NULL,
    schedule_id text NOT NULL,
    policy_id text NOT NULL,
    interval_seconds integer NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    next_run_at timestamptz NOT NULL,
    last_execution_id text,
    version bigint NOT NULL DEFAULT 1,
    etag text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, schedule_id),
    FOREIGN KEY (project_id, policy_id)
        REFERENCES storage.lifecycle_policies (project_id, policy_id)
        ON DELETE RESTRICT,
    CHECK (interval_seconds BETWEEN 300 AND 2678400),
    CHECK (version > 0),
    CHECK (etag <> '')
);
CREATE INDEX IF NOT EXISTS storage_lifecycle_schedules_due_idx
ON storage.lifecycle_schedules (next_run_at, project_id, schedule_id)
WHERE enabled;

CREATE OR REPLACE FUNCTION storage.reject_execution_log_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'storage lifecycle execution logs are append-only';
END
$function$;
DROP TRIGGER IF EXISTS storage_lifecycle_execution_log_immutable
ON storage.lifecycle_execution_logs;
CREATE TRIGGER storage_lifecycle_execution_log_immutable
BEFORE UPDATE OR DELETE ON storage.lifecycle_execution_logs
FOR EACH ROW EXECUTE FUNCTION storage.reject_execution_log_mutation();

SELECT core.apply_project_rls('storage.managed_objects'::regclass);
SELECT core.apply_project_rls('storage.managed_multipart_uploads'::regclass);
SELECT core.apply_project_rls('storage.object_operations'::regclass);
SELECT core.apply_project_rls('storage.lifecycle_execution_approvals'::regclass);
SELECT core.apply_project_rls('storage.lifecycle_execution_logs'::regclass);
SELECT core.apply_project_rls('storage.lifecycle_schedules'::regclass);
