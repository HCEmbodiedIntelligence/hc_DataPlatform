-- OBS5-03 extends P19's tamper-evident audit guarantees to global platform
-- operations. The source access-control stream remains append-only; this
-- migration adds a single ordered hash chain for PLATFORM events only.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS platform.platform_audit_integrity_head (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    last_sequence bigint NOT NULL DEFAULT 0,
    last_event_hash char(64),
    updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    CHECK (last_sequence >= 0),
    CHECK (
        (last_sequence = 0 AND last_event_hash IS NULL)
        OR (last_sequence > 0 AND last_event_hash ~ '^[0-9a-f]{64}$')
    )
);

CREATE TABLE IF NOT EXISTS platform.platform_audit_integrity_entries (
    event_id uuid PRIMARY KEY
        REFERENCES access_control.audit_events(event_id) ON DELETE RESTRICT,
    sequence_no bigint NOT NULL UNIQUE,
    previous_event_hash char(64),
    event_hash char(64) NOT NULL,
    occurred_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    CHECK (sequence_no > 0),
    CHECK (previous_event_hash IS NULL OR previous_event_hash ~ '^[0-9a-f]{64}$'),
    CHECK (event_hash ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS platform_audit_integrity_occurred_idx
ON platform.platform_audit_integrity_entries (occurred_at DESC, sequence_no DESC);

CREATE OR REPLACE FUNCTION platform.platform_audit_event_hash(
    previous_hash text,
    audit_event_id uuid,
    audit_actor_id text,
    audit_action text,
    audit_resource_type text,
    audit_resource_id text,
    audit_request_id text,
    audit_outcome text,
    audit_safe_details jsonb,
    audit_occurred_at timestamptz
) RETURNS char(64)
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $function$
    SELECT encode(
        digest(
            convert_to(
                jsonb_build_object(
                    'action', audit_action,
                    'actor_id', audit_actor_id,
                    'event_id', audit_event_id,
                    'occurred_at', audit_occurred_at,
                    'outcome', audit_outcome,
                    'previous_hash', previous_hash,
                    'request_id', audit_request_id,
                    'resource_id', audit_resource_id,
                    'resource_type', audit_resource_type,
                    'safe_details', audit_safe_details,
                    'scope_kind', 'PLATFORM'
                )::text,
                'UTF8'
            ),
            'sha256'
        ),
        'hex'
    )::char(64)
$function$;

CREATE OR REPLACE FUNCTION platform.append_platform_audit_integrity_entry(
    target_event_id uuid
) RETURNS void
LANGUAGE plpgsql
AS $function$
DECLARE
    audit_event access_control.audit_events%ROWTYPE;
    head platform.platform_audit_integrity_head%ROWTYPE;
    next_sequence bigint;
    next_hash char(64);
BEGIN
    SELECT * INTO audit_event
      FROM access_control.audit_events
     WHERE event_id = target_event_id
       AND scope_kind = 'PLATFORM';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'platform audit event % is not available for chaining', target_event_id;
    END IF;

    INSERT INTO platform.platform_audit_integrity_head (singleton)
    VALUES (true)
    ON CONFLICT (singleton) DO NOTHING;

    SELECT * INTO head
      FROM platform.platform_audit_integrity_head
     WHERE singleton
     FOR UPDATE;

    IF EXISTS (
        SELECT 1
          FROM platform.platform_audit_integrity_entries
         WHERE event_id = target_event_id
    ) THEN
        RAISE EXCEPTION 'platform audit event % already has an integrity entry', target_event_id;
    END IF;

    next_sequence := head.last_sequence + 1;
    next_hash := platform.platform_audit_event_hash(
        head.last_event_hash,
        audit_event.event_id,
        audit_event.actor_id,
        audit_event.action,
        audit_event.resource_type,
        audit_event.resource_id,
        audit_event.request_id,
        audit_event.outcome,
        audit_event.safe_details,
        audit_event.occurred_at
    );

    INSERT INTO platform.platform_audit_integrity_entries (
        event_id, sequence_no, previous_event_hash, event_hash, occurred_at
    ) VALUES (
        audit_event.event_id, next_sequence, head.last_event_hash, next_hash,
        audit_event.occurred_at
    );

    UPDATE platform.platform_audit_integrity_head
       SET last_sequence = next_sequence,
           last_event_hash = next_hash,
           updated_at = statement_timestamp()
     WHERE singleton;
END
$function$;

-- Backfill before installing the trigger so historical order is deterministic.
DO $block$
DECLARE
    event_row record;
BEGIN
    FOR event_row IN
        SELECT audit.event_id
          FROM access_control.audit_events audit
         WHERE audit.scope_kind = 'PLATFORM'
           AND NOT EXISTS (
               SELECT 1
                 FROM platform.platform_audit_integrity_entries entry
                WHERE entry.event_id = audit.event_id
           )
         ORDER BY audit.occurred_at, audit.event_id
    LOOP
        PERFORM platform.append_platform_audit_integrity_entry(event_row.event_id);
    END LOOP;
END
$block$;

CREATE OR REPLACE FUNCTION platform.append_platform_audit_integrity_entry_trigger()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.scope_kind = 'PLATFORM' THEN
        PERFORM platform.append_platform_audit_integrity_entry(NEW.event_id);
    END IF;
    RETURN NEW;
END
$function$;

DROP TRIGGER IF EXISTS platform_audit_integrity_append
ON access_control.audit_events;
CREATE TRIGGER platform_audit_integrity_append
AFTER INSERT ON access_control.audit_events
FOR EACH ROW EXECUTE FUNCTION platform.append_platform_audit_integrity_entry_trigger();

CREATE OR REPLACE FUNCTION platform.reject_platform_audit_integrity_entry_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'PLATFORM_AUDIT_INTEGRITY_ENTRY_IMMUTABLE' USING ERRCODE = 'P0001';
END
$function$;

DROP TRIGGER IF EXISTS platform_audit_integrity_entries_immutable
ON platform.platform_audit_integrity_entries;
CREATE TRIGGER platform_audit_integrity_entries_immutable
BEFORE UPDATE OR DELETE ON platform.platform_audit_integrity_entries
FOR EACH ROW EXECUTE FUNCTION platform.reject_platform_audit_integrity_entry_mutation();
