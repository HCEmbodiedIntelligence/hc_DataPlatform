-- Database-clock singleton ownership for platform maintenance loops. A lease ID
-- changes on every takeover so an old process cannot commit after reconnecting.

CREATE TABLE IF NOT EXISTS platform.platform_task_leases (
    environment_id text NOT NULL REFERENCES platform.environment_fences(environment_id),
    task_id text NOT NULL,
    lease_id uuid NOT NULL UNIQUE,
    owner_instance_id uuid NOT NULL,
    fencing_token bigint NOT NULL,
    lease_until timestamptz NOT NULL,
    lease_version bigint NOT NULL DEFAULT 1,
    acquired_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    PRIMARY KEY (environment_id, task_id),
    CHECK (
        environment_id ~ '^[A-Za-z0-9](?:[A-Za-z0-9._:/-]{0,126}[A-Za-z0-9])?$'
    ),
    CHECK (task_id ~ '^[A-Za-z0-9](?:[A-Za-z0-9._:/-]{0,253}[A-Za-z0-9])?$'),
    CHECK (fencing_token > 0),
    CHECK (lease_version > 0),
    CHECK (lease_until >= acquired_at)
);

CREATE INDEX IF NOT EXISTS platform_task_leases_expiry_idx
ON platform.platform_task_leases (lease_until, environment_id, task_id);

CREATE TABLE IF NOT EXISTS platform.platform_task_lease_events (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    environment_id text NOT NULL,
    task_id text NOT NULL,
    lease_id uuid NOT NULL,
    owner_instance_id uuid NOT NULL,
    fencing_token bigint NOT NULL,
    lease_version bigint NOT NULL,
    event_code text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    CHECK (fencing_token > 0),
    CHECK (lease_version > 0),
    CHECK (event_code IN (
        'PLATFORM_TASK_LEASE_ACQUIRED',
        'PLATFORM_TASK_LEASE_REACQUIRED',
        'PLATFORM_TASK_LEASE_TAKEN_OVER',
        'PLATFORM_TASK_LEASE_RELEASED'
    ))
);

CREATE INDEX IF NOT EXISTS platform_task_lease_events_task_idx
ON platform.platform_task_lease_events (environment_id, task_id, event_id);

CREATE OR REPLACE FUNCTION platform.reject_platform_task_lease_event_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'PLATFORM_TASK_LEASE_EVENT_IMMUTABLE' USING ERRCODE = 'P0001';
END;
$function$;

DROP TRIGGER IF EXISTS platform_task_lease_events_immutable
ON platform.platform_task_lease_events;

CREATE TRIGGER platform_task_lease_events_immutable
BEFORE UPDATE OR DELETE ON platform.platform_task_lease_events
FOR EACH ROW EXECUTE FUNCTION platform.reject_platform_task_lease_event_mutation();

CREATE OR REPLACE FUNCTION platform.assert_task_lease(p_lease_id uuid)
RETURNS void
LANGUAGE plpgsql
AS $function$
DECLARE
    task_lease platform.platform_task_leases%ROWTYPE;
    environment_mode text;
BEGIN
    SELECT * INTO task_lease
    FROM platform.platform_task_leases
    WHERE lease_id = p_lease_id
    FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'PLATFORM_TASK_LEASE_STALE' USING ERRCODE = 'P0001';
    END IF;

    SELECT mode INTO environment_mode
    FROM platform.environment_fences
    WHERE environment_id = task_lease.environment_id
    FOR SHARE;
    IF NOT FOUND OR environment_mode <> 'READ_WRITE' THEN
        RAISE EXCEPTION 'PLATFORM_MAINTENANCE' USING ERRCODE = 'P0001';
    END IF;
    IF statement_timestamp() >= task_lease.lease_until THEN
        RAISE EXCEPTION 'PLATFORM_TASK_LEASE_EXPIRED' USING ERRCODE = 'P0001';
    END IF;
END;
$function$;
