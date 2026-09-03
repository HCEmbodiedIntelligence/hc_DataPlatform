-- Durable account recovery with hashed, single-use challenges.
-- Raw verification and recovery tokens never enter PostgreSQL; only SHA-256 digests are stored.

ALTER TABLE access_control.accounts
    ADD COLUMN IF NOT EXISTS recovery_email text;

DO $block$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'accounts_recovery_email_valid'
          AND conrelid = 'access_control.accounts'::regclass
    ) THEN
        ALTER TABLE access_control.accounts
            ADD CONSTRAINT accounts_recovery_email_valid CHECK (
                recovery_email IS NULL OR (
                    recovery_email <> ''
                    AND char_length(recovery_email) <= 254
                    AND recovery_email = lower(recovery_email)
                    AND recovery_email !~ '[[:space:][:cntrl:]]'
                )
            );
    END IF;
END
$block$;

CREATE UNIQUE INDEX IF NOT EXISTS access_accounts_recovery_email_unique_idx
ON access_control.accounts (recovery_email)
WHERE recovery_email IS NOT NULL;

CREATE TABLE IF NOT EXISTS access_control.account_security_challenges (
    challenge_id uuid PRIMARY KEY,
    principal_id uuid NOT NULL REFERENCES access_control.accounts (principal_id),
    purpose text NOT NULL,
    target_email text NOT NULL,
    credential_revision bigint NOT NULL,
    token_hash char(64) NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    created_request_id text NOT NULL,
    CHECK (purpose IN ('RECOVERY_EMAIL_VERIFY', 'PASSWORD_RECOVERY')),
    CHECK (target_email <> '' AND char_length(target_email) <= 254),
    CHECK (target_email = lower(target_email)),
    CHECK (target_email !~ '[[:space:][:cntrl:]]'),
    CHECK (credential_revision >= 1),
    CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    CHECK (expires_at > created_at),
    CHECK (consumed_at IS NULL OR consumed_at >= created_at),
    CHECK (created_request_id <> '')
);

CREATE UNIQUE INDEX IF NOT EXISTS account_security_challenges_one_active_idx
ON access_control.account_security_challenges (principal_id, purpose)
WHERE consumed_at IS NULL;

CREATE INDEX IF NOT EXISTS account_security_challenges_expiry_idx
ON access_control.account_security_challenges (expires_at)
WHERE consumed_at IS NULL;

ALTER TABLE access_control.auth_rate_limit_buckets
    DROP CONSTRAINT IF EXISTS auth_rate_limit_buckets_operation_check;
ALTER TABLE access_control.auth_rate_limit_buckets
    ADD CONSTRAINT auth_rate_limit_buckets_operation_check
    CHECK (operation IN ('LOGIN', 'REGISTER', 'PASSWORD_RECOVERY'));

ALTER TABLE access_control.sessions
    DROP CONSTRAINT IF EXISTS sessions_revocation_reason_valid;
ALTER TABLE access_control.sessions
    ADD CONSTRAINT sessions_revocation_reason_valid CHECK (
        revocation_reason IS NULL OR revocation_reason IN (
            'LOGOUT', 'PASSWORD_CHANGED', 'PASSWORD_RECOVERED', 'ACCOUNT_DISABLED',
            'SESSION_LIMIT', 'EXPIRED', 'ADMIN_REVOKED'
        )
    );
