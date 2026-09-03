-- A fixed pool of globally visible leases prevents horizontally scaled media
-- workers from exceeding the deployment-wide FFmpeg budget. Rows are not
-- tenant data and deliberately do not carry project RLS.

CREATE TABLE IF NOT EXISTS preview.media_capacity_slots (
    slot_id integer PRIMARY KEY CHECK (slot_id BETWEEN 1 AND 128),
    owner_id text,
    attempt_token uuid,
    lease_expires_at timestamptz,
    heartbeat_at timestamptz,
    CHECK (
        (owner_id IS NULL AND attempt_token IS NULL AND lease_expires_at IS NULL)
        OR (owner_id IS NOT NULL AND attempt_token IS NOT NULL AND lease_expires_at IS NOT NULL)
    )
);

INSERT INTO preview.media_capacity_slots (slot_id)
SELECT value FROM generate_series(1, 128) AS value
ON CONFLICT (slot_id) DO NOTHING;

CREATE INDEX IF NOT EXISTS media_capacity_available_idx
ON preview.media_capacity_slots (lease_expires_at, slot_id);
