-- Self-service account profile and credential lifecycle. This is forward-only because
-- security/002_access_control.sql may already be applied in production.

ALTER TABLE access_control.accounts
    ADD COLUMN IF NOT EXISTS display_name text,
    ADD COLUMN IF NOT EXISTS account_revision bigint NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS credential_revision bigint NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS password_changed_at timestamptz;

UPDATE access_control.accounts
SET display_name = display_username
WHERE display_name IS NULL;

UPDATE access_control.accounts
SET password_changed_at = created_at
WHERE password_changed_at IS NULL;

ALTER TABLE access_control.accounts
    ALTER COLUMN display_name SET NOT NULL,
    ALTER COLUMN password_changed_at SET NOT NULL;

DO $block$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'accounts_display_name_valid'
          AND conrelid = 'access_control.accounts'::regclass
    ) THEN
        ALTER TABLE access_control.accounts
            ADD CONSTRAINT accounts_display_name_valid CHECK (
                display_name <> ''
                AND char_length(display_name) <= 128
                AND display_name !~ '[[:cntrl:]]'
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'accounts_account_revision_positive'
          AND conrelid = 'access_control.accounts'::regclass
    ) THEN
        ALTER TABLE access_control.accounts
            ADD CONSTRAINT accounts_account_revision_positive CHECK (account_revision >= 1);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'accounts_credential_revision_positive'
          AND conrelid = 'access_control.accounts'::regclass
    ) THEN
        ALTER TABLE access_control.accounts
            ADD CONSTRAINT accounts_credential_revision_positive
            CHECK (credential_revision >= 1);
    END IF;
END
$block$;

ALTER TABLE access_control.sessions
    ADD COLUMN IF NOT EXISTS credential_revision bigint NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS revocation_reason text;

UPDATE access_control.sessions AS session
SET credential_revision = account.credential_revision
FROM access_control.accounts AS account
WHERE account.principal_id = session.principal_id
  AND session.credential_revision <> account.credential_revision;

DO $block$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'sessions_credential_revision_positive'
          AND conrelid = 'access_control.sessions'::regclass
    ) THEN
        ALTER TABLE access_control.sessions
            ADD CONSTRAINT sessions_credential_revision_positive
            CHECK (credential_revision >= 1);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'sessions_revocation_reason_valid'
          AND conrelid = 'access_control.sessions'::regclass
    ) THEN
        ALTER TABLE access_control.sessions
            ADD CONSTRAINT sessions_revocation_reason_valid CHECK (
                revocation_reason IS NULL OR revocation_reason IN (
                    'LOGOUT', 'PASSWORD_CHANGED', 'ACCOUNT_DISABLED',
                    'SESSION_LIMIT', 'EXPIRED', 'ADMIN_REVOKED'
                )
            );
    END IF;
END
$block$;

ALTER TABLE access_control.command_idempotency
    ADD COLUMN IF NOT EXISTS result_payload jsonb;

CREATE INDEX IF NOT EXISTS access_accounts_updated_idx
ON access_control.accounts (updated_at DESC, principal_id);

CREATE INDEX IF NOT EXISTS access_sessions_active_credential_idx
ON access_control.sessions (principal_id, credential_revision, issued_at DESC)
WHERE revoked_at IS NULL;
