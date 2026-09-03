-- Global maintenance coordination. PostgreSQL database time and the monotonic
-- sequence are authoritative; process clocks and platform instance heartbeats
-- are deliberately not used for ownership or fencing decisions.

CREATE SEQUENCE IF NOT EXISTS platform.maintenance_fencing_token_seq
AS bigint MINVALUE 1 NO CYCLE;

CREATE TABLE IF NOT EXISTS platform.environment_fences (
    environment_id text PRIMARY KEY,
    mode text NOT NULL DEFAULT 'READ_WRITE',
    fencing_token bigint NOT NULL DEFAULT nextval('platform.maintenance_fencing_token_seq'),
    mode_changed_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    write_enabled_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    CHECK (
        environment_id ~ '^[A-Za-z0-9](?:[A-Za-z0-9._:/-]{0,126}[A-Za-z0-9])?$'
    ),
    CHECK (mode IN ('READ_WRITE', 'READ_ONLY_MAINTENANCE')),
    CHECK (fencing_token > 0),
    CHECK (write_enabled_at <= mode_changed_at)
);

CREATE TABLE IF NOT EXISTS platform.maintenance_operations (
    operation_id text PRIMARY KEY,
    environment_id text NOT NULL REFERENCES platform.environment_fences(environment_id),
    operation_kind text NOT NULL,
    plan_digest text NOT NULL,
    requested_by text NOT NULL,
    state text NOT NULL DEFAULT 'REQUESTED',
    owner_instance_id uuid,
    fencing_token bigint,
    lease_until timestamptz,
    state_version bigint NOT NULL DEFAULT 0,
    reconciliation_passed boolean NOT NULL DEFAULT false,
    manual_approval_id text,
    created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    CHECK (operation_id <> '' AND length(operation_id) <= 128),
    CHECK (operation_kind IN ('BACKUP', 'RESTORE', 'MIGRATION', 'RELEASE', 'OTHER')),
    CHECK (plan_digest ~ '^sha256:[0-9a-f]{64}$'),
    CHECK (requested_by <> '' AND length(requested_by) <= 255),
    CHECK (state IN (
        'REQUESTED', 'LEASED', 'READ_ONLY', 'DRAINING', 'FENCED', 'EXECUTING',
        'VERIFYING', 'RELEASING', 'WRITE_ENABLE_PENDING', 'SUCCEEDED',
        'FAILED_RELEASED', 'FAILED_READ_ONLY', 'CANCELLED'
    )),
    CHECK (state_version >= 0),
    CHECK (fencing_token IS NULL OR fencing_token > 0),
    CHECK (manual_approval_id IS NULL OR (
        manual_approval_id <> '' AND length(manual_approval_id) <= 255
    )),
    CHECK (
        (state IN ('REQUESTED', 'CANCELLED')
            AND owner_instance_id IS NULL
            AND fencing_token IS NULL
            AND lease_until IS NULL)
        OR
        (state NOT IN ('REQUESTED', 'CANCELLED')
            AND owner_instance_id IS NOT NULL
            AND fencing_token IS NOT NULL
            AND lease_until IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS maintenance_operations_one_active_environment_idx
ON platform.maintenance_operations (environment_id)
WHERE state IN (
    'REQUESTED', 'LEASED', 'READ_ONLY', 'DRAINING', 'FENCED', 'EXECUTING',
    'VERIFYING', 'RELEASING', 'WRITE_ENABLE_PENDING', 'FAILED_READ_ONLY'
);

CREATE INDEX IF NOT EXISTS maintenance_operations_environment_created_idx
ON platform.maintenance_operations (environment_id, created_at DESC, operation_id);

CREATE TABLE IF NOT EXISTS platform.writer_permits (
    permit_id uuid PRIMARY KEY,
    environment_id text NOT NULL REFERENCES platform.environment_fences(environment_id),
    operation_id text REFERENCES platform.maintenance_operations(operation_id),
    writer_id text NOT NULL,
    writer_kind text NOT NULL,
    fencing_token bigint NOT NULL,
    lease_until timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    released_at timestamptz,
    CHECK (writer_id <> '' AND length(writer_id) <= 255),
    CHECK (writer_kind IN (
        'api_command', 'presigned_upload_grant', 'outbox_claim',
        'temporal_activity', 'preview_attempt', 'maintenance_controller',
        'kubernetes_job'
    )),
    CHECK (fencing_token > 0),
    CHECK (lease_until > created_at),
    CHECK (released_at IS NULL OR released_at >= created_at)
);

CREATE UNIQUE INDEX IF NOT EXISTS writer_permits_one_active_writer_idx
ON platform.writer_permits (environment_id, writer_kind, writer_id)
WHERE released_at IS NULL;

CREATE INDEX IF NOT EXISTS writer_permits_inventory_idx
ON platform.writer_permits (environment_id, writer_kind, lease_until)
WHERE released_at IS NULL;

CREATE TABLE IF NOT EXISTS platform.maintenance_events (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    operation_id text NOT NULL REFERENCES platform.maintenance_operations(operation_id),
    environment_id text NOT NULL,
    event_code text NOT NULL,
    owner_instance_id uuid,
    fencing_token bigint,
    state text NOT NULL,
    state_version bigint NOT NULL,
    approval_id text,
    occurred_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    CHECK (event_code ~ '^PLATFORM_[A-Z0-9_]{1,119}$'),
    CHECK (state_version >= 0),
    CHECK (approval_id IS NULL OR (approval_id <> '' AND length(approval_id) <= 255))
);

CREATE INDEX IF NOT EXISTS maintenance_events_operation_idx
ON platform.maintenance_events (operation_id, event_id);

CREATE OR REPLACE FUNCTION platform.reject_maintenance_event_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'PLATFORM_MAINTENANCE_EVENT_IMMUTABLE' USING ERRCODE = 'P0001';
END;
$function$;

DROP TRIGGER IF EXISTS maintenance_events_immutable
ON platform.maintenance_events;

CREATE TRIGGER maintenance_events_immutable
BEFORE UPDATE OR DELETE ON platform.maintenance_events
FOR EACH ROW EXECUTE FUNCTION platform.reject_maintenance_event_mutation();

CREATE OR REPLACE FUNCTION platform.assert_writer_permit(p_permit_id uuid)
RETURNS void
LANGUAGE plpgsql
AS $function$
DECLARE
    permit platform.writer_permits%ROWTYPE;
    fence platform.environment_fences%ROWTYPE;
BEGIN
    SELECT * INTO permit
    FROM platform.writer_permits
    WHERE permit_id = p_permit_id AND released_at IS NULL
    FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'PLATFORM_WRITER_PERMIT_NOT_FOUND' USING ERRCODE = 'P0001';
    END IF;

    SELECT * INTO fence
    FROM platform.environment_fences
    WHERE environment_id = permit.environment_id
    FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'PLATFORM_WRITER_ENVIRONMENT_MISMATCH' USING ERRCODE = 'P0001';
    END IF;
    IF fence.mode <> 'READ_WRITE' THEN
        RAISE EXCEPTION 'PLATFORM_MAINTENANCE' USING ERRCODE = 'P0001';
    END IF;
    IF fence.fencing_token <> permit.fencing_token THEN
        RAISE EXCEPTION 'PLATFORM_WRITER_FENCING_TOKEN_STALE' USING ERRCODE = 'P0001';
    END IF;
    IF statement_timestamp() >= permit.lease_until THEN
        RAISE EXCEPTION 'PLATFORM_WRITER_PERMIT_EXPIRED' USING ERRCODE = 'P0001';
    END IF;
END;
$function$;

CREATE OR REPLACE FUNCTION platform.assert_environment_writable(p_environment_id text)
RETURNS void
LANGUAGE plpgsql
AS $function$
DECLARE
    current_mode text;
BEGIN
    SELECT mode INTO current_mode
    FROM platform.environment_fences
    WHERE environment_id = p_environment_id;
    IF NOT FOUND OR current_mode <> 'READ_WRITE' THEN
        RAISE EXCEPTION 'PLATFORM_MAINTENANCE' USING ERRCODE = 'P0001';
    END IF;
END;
$function$;
