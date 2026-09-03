-- BR02: recoverable, concurrently claimed delivery for the existing core outbox.

ALTER TABLE core.outbox_events
    ADD COLUMN IF NOT EXISTS claim_token uuid,
    ADD COLUMN IF NOT EXISTS claimed_by text,
    ADD COLUMN IF NOT EXISTS claimed_until timestamptz,
    ADD COLUMN IF NOT EXISTS last_error_code text;

ALTER TABLE core.outbox_events
    DROP CONSTRAINT IF EXISTS outbox_claim_tuple_check;
ALTER TABLE core.outbox_events
    ADD CONSTRAINT outbox_claim_tuple_check CHECK (
        (claim_token IS NULL AND claimed_by IS NULL AND claimed_until IS NULL)
        OR (claim_token IS NOT NULL AND claimed_by IS NOT NULL AND claimed_until IS NOT NULL)
    );

ALTER TABLE core.outbox_events
    DROP CONSTRAINT IF EXISTS outbox_last_error_code_check;
ALTER TABLE core.outbox_events
    ADD CONSTRAINT outbox_last_error_code_check CHECK (
        last_error_code IS NULL OR last_error_code ~ '^[A-Z0-9_]+$'
    );

CREATE INDEX IF NOT EXISTS outbox_events_claimable_idx
ON core.outbox_events(available_at, occurred_at, event_id)
WHERE published_at IS NULL;

SELECT core.apply_project_rls('core.outbox_events'::regclass);
SELECT core.apply_project_rls('core.audit_events'::regclass);
