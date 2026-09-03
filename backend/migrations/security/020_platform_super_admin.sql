-- Global platform-super-admin semantics.  The marker is granted from the durable
-- platform role bundle, never from a username or project membership.

DO $constraint$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'access_control.platform_capability_grants'::regclass
          AND conname = 'platform_capability_grants_capability_key_check'
          AND pg_get_constraintdef(oid) NOT LIKE '%platform.admin%'
    ) THEN
        ALTER TABLE access_control.platform_capability_grants
            DROP CONSTRAINT platform_capability_grants_capability_key_check;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'access_control.platform_capability_grants'::regclass
          AND conname = 'platform_capability_grants_capability_key_check'
    ) THEN
        ALTER TABLE access_control.platform_capability_grants
            ADD CONSTRAINT platform_capability_grants_capability_key_check
            CHECK (capability_key IN (
                'platform.admin',
                'platform.account.read',
                'platform.account.manage',
                'platform.account_security.manage'
            ));
    END IF;
END
$constraint$;

-- Existing PLATFORM_ADMIN accounts are identified by their durable role bundle.  Only an
-- inserted or reactivated marker changes the revision, so rerunning this migration is safe.
-- Those changed accounts must log in again; their pre-migration opaque sessions are revoked.
WITH changed_admins AS (
    INSERT INTO access_control.platform_capability_grants AS platform_grant (
        principal_id, capability_key, active, granted_at, granted_by, revoked_at, revoked_by
    )
    SELECT DISTINCT account.principal_id, 'platform.admin', true, now(),
           'migration:020', NULL::timestamptz, NULL::text
    FROM access_control.accounts account
    JOIN access_control.platform_capability_grants legacy_admin
      ON legacy_admin.principal_id = account.principal_id
     AND legacy_admin.capability_key = 'platform.account.manage'
     AND legacy_admin.active
    ON CONFLICT (principal_id, capability_key) DO UPDATE
    SET active = true,
        granted_at = now(),
        granted_by = EXCLUDED.granted_by,
        revoked_at = NULL,
        revoked_by = NULL
    WHERE NOT platform_grant.active
    RETURNING principal_id
), revised AS (
    UPDATE access_control.accounts account
       SET capability_revision = account.capability_revision + 1,
           updated_at = now()
      FROM changed_admins changed
     WHERE changed.principal_id = account.principal_id
    RETURNING account.principal_id
), revoked AS (
    UPDATE access_control.sessions session
       SET revoked_at = now(),
           revocation_reason = 'ADMIN_REVOKED'
      FROM revised account
     WHERE account.principal_id = session.principal_id
       AND session.revoked_at IS NULL
    RETURNING session.session_id
)
SELECT (SELECT count(*) FROM revised) AS revised_accounts,
       (SELECT count(*) FROM revoked) AS revoked_sessions;

-- RLS receives the marker only from the verified AuthContext.  API scope validation still
-- proves that the organization/project exists before a connection is opened; this database
-- branch removes membership-based row filtering after that proof.
CREATE OR REPLACE FUNCTION core.scope_matches(
    row_project_id text,
    row_region_code text DEFAULT NULL
) RETURNS boolean
LANGUAGE sql
STABLE
PARALLEL SAFE
AS $function$
    SELECT
        current_setting('app.platform_admin', true) = 'true'
        OR (
            row_project_id = NULLIF(current_setting('app.project_id', true), '')
            AND (
                row_region_code IS NULL
                OR row_region_code = COALESCE(current_setting('app.region_code', true), '')
            )
        )
$function$;

CREATE OR REPLACE FUNCTION core.organization_scope_matches(
    row_organization_id text,
    row_project_id text,
    row_region_code text DEFAULT NULL
) RETURNS boolean
LANGUAGE sql
STABLE
PARALLEL SAFE
AS $function$
    SELECT
        current_setting('app.platform_admin', true) = 'true'
        OR (
            row_organization_id = NULLIF(current_setting('app.organization_id', true), '')
            AND row_project_id = NULLIF(current_setting('app.project_id', true), '')
            AND (
                row_region_code IS NULL
                OR row_region_code = COALESCE(current_setting('app.region_code', true), '')
            )
        )
$function$;
