-- Platform-scoped direct-account administration. Project roles never imply these grants.

ALTER TABLE access_control.accounts
    ADD COLUMN IF NOT EXISTS deleted_at timestamptz;

CREATE TABLE IF NOT EXISTS access_control.platform_capability_grants (
    principal_id uuid NOT NULL REFERENCES access_control.accounts (principal_id),
    capability_key text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    granted_at timestamptz NOT NULL DEFAULT now(),
    granted_by text NOT NULL,
    revoked_at timestamptz,
    revoked_by text,
    PRIMARY KEY (principal_id, capability_key),
    CHECK (capability_key IN (
        'platform.account.read',
        'platform.account.manage',
        'platform.account_security.manage'
    )),
    CHECK (granted_by <> ''),
    CHECK (
        (active AND revoked_at IS NULL AND revoked_by IS NULL)
        OR (NOT active AND revoked_at IS NOT NULL AND revoked_by IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS platform_capability_grants_active_idx
ON access_control.platform_capability_grants (principal_id, capability_key)
WHERE active;

-- One-time bootstrap for the deployment's existing administrator. Further grants are
-- managed through the authenticated platform-admin API and are never inferred by username.
INSERT INTO access_control.platform_capability_grants (
    principal_id, capability_key, active, granted_by
)
SELECT account.principal_id, capability.capability_key, true, 'migration:015'
FROM access_control.accounts account
CROSS JOIN (
    VALUES
        ('platform.account.read'),
        ('platform.account.manage'),
        ('platform.account_security.manage')
) AS capability(capability_key)
WHERE account.canonical_username = 'hc-admin'
ON CONFLICT (principal_id, capability_key) DO NOTHING;

UPDATE access_control.accounts
SET capability_revision = capability_revision + 1,
    updated_at = now()
WHERE canonical_username = 'hc-admin'
  AND EXISTS (
      SELECT 1
      FROM access_control.platform_capability_grants capability_grant
      WHERE capability_grant.principal_id = accounts.principal_id
        AND capability_grant.active
  );
