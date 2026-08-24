-- P19 establishes a tamper-evident, project-and-region scoped digest chain
-- over the durable cross-domain audit stream.  The chain stores no browser
-- visible payload: the P19 API verifies it server-side and returns only safe
-- aggregate facts.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS core.audit_integrity_heads (
    project_id text NOT NULL,
    region_code text NOT NULL DEFAULT '',
    last_sequence bigint NOT NULL DEFAULT 0,
    last_event_hash char(64),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, region_code),
    CHECK (project_id <> ''),
    CHECK (last_sequence >= 0),
    CHECK (
        (last_sequence = 0 AND last_event_hash IS NULL)
        OR (last_sequence > 0 AND last_event_hash ~ '^[0-9a-f]{64}$')
    )
);

CREATE TABLE IF NOT EXISTS core.audit_integrity_entries (
    audit_id uuid PRIMARY KEY REFERENCES core.audit_events(audit_id) ON DELETE CASCADE,
    project_id text NOT NULL,
    region_code text NOT NULL DEFAULT '',
    sequence_no bigint NOT NULL,
    previous_event_hash char(64),
    event_hash char(64) NOT NULL,
    occurred_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, region_code, sequence_no),
    CHECK (project_id <> ''),
    CHECK (sequence_no > 0),
    CHECK (previous_event_hash IS NULL OR previous_event_hash ~ '^[0-9a-f]{64}$'),
    CHECK (event_hash ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS audit_integrity_entries_scope_sequence_idx
ON core.audit_integrity_entries (project_id, region_code, sequence_no);

-- jsonb text has deterministic key ordering.  Digest every durable source
-- field, including details, but never return this digest or the details to a
-- client.  The previous digest binds otherwise identical events into an order.
CREATE OR REPLACE FUNCTION core.audit_integrity_event_hash(
    previous_hash text,
    event_audit_id uuid,
    event_project_id text,
    event_region_code text,
    event_actor_id text,
    event_action text,
    event_resource_type text,
    event_resource_id text,
    event_request_id text,
    event_before_hash text,
    event_after_hash text,
    event_details jsonb,
    event_occurred_at timestamptz
) RETURNS char(64)
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $function$
    SELECT encode(
        digest(
            convert_to(
                jsonb_build_object(
                    'after_hash', event_after_hash,
                    'audit_id', event_audit_id,
                    'before_hash', event_before_hash,
                    'details', event_details,
                    'occurred_at', event_occurred_at,
                    'previous_hash', previous_hash,
                    'project_id', event_project_id,
                    'region_code', event_region_code,
                    'request_id', event_request_id,
                    'resource_id', event_resource_id,
                    'resource_type', event_resource_type,
                    'actor_id', event_actor_id,
                    'action', event_action
                )::text,
                'UTF8'
            ),
            'sha256'
        ),
        'hex'
    )::char(64)
$function$;

CREATE OR REPLACE FUNCTION core.append_audit_integrity_entry(target_audit_id uuid)
RETURNS void
LANGUAGE plpgsql
AS $function$
DECLARE
    audit_event core.audit_events%ROWTYPE;
    head core.audit_integrity_heads%ROWTYPE;
    normalized_region text;
    next_sequence bigint;
    next_hash char(64);
BEGIN
    SELECT * INTO audit_event
      FROM core.audit_events
     WHERE audit_id = target_audit_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'audit event % is not available for integrity chaining', target_audit_id;
    END IF;

    normalized_region := COALESCE(audit_event.region_code, '');
    INSERT INTO core.audit_integrity_heads (project_id, region_code)
    VALUES (audit_event.project_id, normalized_region)
    ON CONFLICT (project_id, region_code) DO NOTHING;

    SELECT * INTO head
      FROM core.audit_integrity_heads
     WHERE project_id = audit_event.project_id
       AND region_code = normalized_region
     FOR UPDATE;

    IF EXISTS (
        SELECT 1 FROM core.audit_integrity_entries WHERE audit_id = target_audit_id
    ) THEN
        RAISE EXCEPTION 'audit event % already has an integrity entry', target_audit_id;
    END IF;

    next_sequence := head.last_sequence + 1;
    next_hash := core.audit_integrity_event_hash(
        head.last_event_hash,
        audit_event.audit_id,
        audit_event.project_id,
        audit_event.region_code,
        audit_event.actor_id,
        audit_event.action,
        audit_event.resource_type,
        audit_event.resource_id,
        audit_event.request_id,
        audit_event.before_hash,
        audit_event.after_hash,
        audit_event.details,
        audit_event.occurred_at
    );

    INSERT INTO core.audit_integrity_entries (
        audit_id, project_id, region_code, sequence_no, previous_event_hash,
        event_hash, occurred_at
    ) VALUES (
        audit_event.audit_id, audit_event.project_id, normalized_region,
        next_sequence, head.last_event_hash, next_hash, audit_event.occurred_at
    );

    UPDATE core.audit_integrity_heads
       SET last_sequence = next_sequence,
           last_event_hash = next_hash,
           updated_at = now()
     WHERE project_id = audit_event.project_id
       AND region_code = normalized_region;
END
$function$;

-- Backfill in the same stable order that the verifier expects.  New inserts do
-- not receive a trigger until this historical sequence is complete.
DO $block$
DECLARE
    event_row record;
BEGIN
    FOR event_row IN
        SELECT audit_id
          FROM core.audit_events
         ORDER BY project_id, COALESCE(region_code, ''), occurred_at, audit_id
    LOOP
        PERFORM core.append_audit_integrity_entry(event_row.audit_id);
    END LOOP;
END
$block$;

CREATE OR REPLACE FUNCTION core.append_audit_integrity_entry_trigger()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    PERFORM core.append_audit_integrity_entry(NEW.audit_id);
    RETURN NEW;
END
$function$;

DROP TRIGGER IF EXISTS audit_events_integrity_append ON core.audit_events;
CREATE TRIGGER audit_events_integrity_append
AFTER INSERT ON core.audit_events
FOR EACH ROW EXECUTE FUNCTION core.append_audit_integrity_entry_trigger();

-- Both auxiliary tables follow the same effective project/region visibility
-- rule as the source stream.  The stored empty region is the canonical form of
-- a source NULL (project-wide) region.
ALTER TABLE core.audit_integrity_heads ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.audit_integrity_heads FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS hc_scope_isolation ON core.audit_integrity_heads;
CREATE POLICY hc_scope_isolation ON core.audit_integrity_heads
USING (core.scope_matches(project_id, NULLIF(region_code, '')))
WITH CHECK (core.scope_matches(project_id, NULLIF(region_code, '')));

ALTER TABLE core.audit_integrity_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.audit_integrity_entries FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS hc_scope_isolation ON core.audit_integrity_entries;
CREATE POLICY hc_scope_isolation ON core.audit_integrity_entries
USING (core.scope_matches(project_id, NULLIF(region_code, '')))
WITH CHECK (core.scope_matches(project_id, NULLIF(region_code, '')));
