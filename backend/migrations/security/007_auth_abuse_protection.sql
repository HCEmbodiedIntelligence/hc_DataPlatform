-- Distributed, privacy-preserving admission and temporary-login-lock state.
-- Keys are application-generated HMAC digests; never store raw usernames, client IPs,
-- network prefixes, challenge tokens, or browser identifiers in these tables.

CREATE TABLE IF NOT EXISTS access_control.auth_login_states (
    subject_key_hash char(64) PRIMARY KEY,
    principal_id uuid UNIQUE REFERENCES access_control.accounts (principal_id),
    consecutive_failures integer NOT NULL DEFAULT 0,
    failure_window_started_at timestamptz,
    last_failed_at timestamptz,
    next_allowed_at timestamptz,
    locked_at timestamptz,
    locked_until timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    CHECK (subject_key_hash ~ '^[0-9a-f]{64}$'),
    CHECK (consecutive_failures >= 0),
    CHECK (failure_window_started_at IS NULL OR last_failed_at IS NULL
        OR failure_window_started_at <= last_failed_at),
    CHECK (locked_at IS NULL OR locked_until IS NOT NULL),
    CHECK (locked_until IS NULL OR locked_at IS NOT NULL),
    CHECK (expires_at >= updated_at)
);

CREATE INDEX IF NOT EXISTS auth_login_states_expiry_idx
ON access_control.auth_login_states (expires_at)
WHERE expires_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS auth_login_states_locked_idx
ON access_control.auth_login_states (locked_until)
WHERE locked_until IS NOT NULL;

CREATE TABLE IF NOT EXISTS access_control.auth_rate_limit_buckets (
    operation text NOT NULL,
    dimension text NOT NULL,
    bucket_key_hash char(64) NOT NULL,
    window_started_at timestamptz NOT NULL,
    attempt_count integer NOT NULL,
    challenge_required_at timestamptz,
    blocked_until timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (operation, dimension, bucket_key_hash),
    CHECK (operation IN ('LOGIN', 'REGISTER')),
    CHECK (dimension IN ('SOURCE', 'SUBJECT', 'GLOBAL')),
    CHECK (bucket_key_hash ~ '^[0-9a-f]{64}$'),
    CHECK (attempt_count >= 0),
    CHECK (blocked_until IS NULL OR blocked_until >= window_started_at),
    CHECK (expires_at >= updated_at)
);

CREATE INDEX IF NOT EXISTS auth_rate_limit_buckets_expiry_idx
ON access_control.auth_rate_limit_buckets (expires_at)
WHERE expires_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS auth_rate_limit_buckets_blocked_idx
ON access_control.auth_rate_limit_buckets (blocked_until)
WHERE blocked_until IS NOT NULL;
