-- Bootstrap the fixed platform administrator only when the account store is empty.
-- Existing installations are deliberately left untouched, including installations
-- that use a different administrator name or password.

WITH inserted_account AS (
    INSERT INTO access_control.accounts (
        principal_id,
        canonical_username,
        display_username,
        status,
        password_hash,
        capability_revision,
        display_name,
        account_revision,
        credential_revision,
        password_changed_at
    )
    SELECT
        '617856ca-3688-4393-a7a9-0318f4c27b7b'::uuid,
        'hc_admin',
        'hc_admin',
        'ACTIVE',
        'scrypt$32768$8$1$VZ_pdIUJYyhxfX0-1rLgdg==$L3ivxSa5huZDDmlTC6OpwkMLU_L1dRQbMqKECap3y00=',
        1,
        'hc_admin',
        1,
        1,
        now()
    WHERE NOT EXISTS (
        SELECT 1
        FROM access_control.accounts
    )
    RETURNING principal_id
)
INSERT INTO access_control.platform_capability_grants (
    principal_id,
    capability_key,
    active,
    granted_by
)
SELECT
    inserted_account.principal_id,
    capability.capability_key,
    true,
    'migration:023'
FROM inserted_account
CROSS JOIN (
    VALUES
        ('platform.admin'),
        ('platform.account.read'),
        ('platform.account.manage'),
        ('platform.account_security.manage')
) AS capability(capability_key);
