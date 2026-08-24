-- Bounded opaque-session lifecycle. TTL values remain runtime configuration so
-- operators can tighten policy without rewriting stored expiry timestamps.

ALTER TABLE access_control.sessions
    ADD COLUMN IF NOT EXISTS last_seen_at timestamptz;

UPDATE access_control.sessions
SET last_seen_at = issued_at
WHERE last_seen_at IS NULL;

ALTER TABLE access_control.sessions
    ALTER COLUMN last_seen_at SET DEFAULT now(),
    ALTER COLUMN last_seen_at SET NOT NULL;

CREATE INDEX IF NOT EXISTS access_sessions_active_lifecycle_idx
ON access_control.sessions (principal_id, issued_at, session_id)
INCLUDE (last_seen_at, credential_revision)
WHERE revoked_at IS NULL;
