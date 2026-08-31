-- Separated-duty global platform operations. These grants are intentionally absent
-- from the platform.admin bundle: ordinary administrators may inspect the control
-- plane but cannot execute backup/release or use break-glass authority by wildcard.

ALTER TABLE access_control.platform_capability_grants
    DROP CONSTRAINT platform_capability_grants_capability_key_check;

ALTER TABLE access_control.platform_capability_grants
    ADD CONSTRAINT platform_capability_grants_capability_key_check
    CHECK (capability_key IN (
        'platform.admin',
        'platform.account.read',
        'platform.account.manage',
        'platform.account_security.manage',
        'platform.operations.read',
        'platform.maintenance.operate',
        'platform.release.operate',
        'platform.maintenance.verify',
        'platform.break_glass'
    ));

ALTER TABLE access_control.sessions
    DROP CONSTRAINT sessions_revocation_reason_valid;

ALTER TABLE access_control.sessions
    ADD CONSTRAINT sessions_revocation_reason_valid CHECK (
        revocation_reason IS NULL OR revocation_reason IN (
            'LOGOUT', 'PASSWORD_CHANGED', 'PASSWORD_RECOVERED', 'ACCOUNT_DISABLED',
            'SESSION_LIMIT', 'EXPIRED', 'ADMIN_REVOKED',
            'PLATFORM_CAPABILITY_CHANGED'
        )
    );

CREATE OR REPLACE FUNCTION access_control.reject_parallel_platform_operation_grants()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.active
       AND NEW.capability_key IN (
           'platform.operations.read',
           'platform.maintenance.operate',
           'platform.release.operate',
           'platform.maintenance.verify',
           'platform.break_glass'
       )
       AND EXISTS (
           SELECT 1
             FROM access_control.platform_capability_grants existing
            WHERE existing.principal_id = NEW.principal_id
              AND existing.active
              AND existing.capability_key IN (
                  'platform.operations.read',
                  'platform.maintenance.operate',
                  'platform.release.operate',
                  'platform.maintenance.verify',
                  'platform.break_glass'
              )
              AND existing.capability_key <> NEW.capability_key
       ) THEN
        RAISE EXCEPTION 'PLATFORM_OPERATION_CAPABILITY_CONFLICT'
            USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS platform_operation_capability_separation
ON access_control.platform_capability_grants;

CREATE TRIGGER platform_operation_capability_separation
BEFORE INSERT OR UPDATE OF capability_key, active
ON access_control.platform_capability_grants
FOR EACH ROW EXECUTE FUNCTION access_control.reject_parallel_platform_operation_grants();

CREATE OR REPLACE FUNCTION access_control.revoke_sessions_after_platform_operation_grant_change()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    affected_principal uuid;
    changed boolean;
    operation_capability_changed boolean;
BEGIN
    IF TG_OP = 'DELETE' THEN
        affected_principal := OLD.principal_id;
        changed := true;
        operation_capability_changed := OLD.capability_key IN (
            'platform.operations.read',
            'platform.maintenance.operate',
            'platform.release.operate',
            'platform.maintenance.verify',
            'platform.break_glass'
        );
    ELSIF TG_OP = 'INSERT' THEN
        affected_principal := NEW.principal_id;
        changed := true;
        operation_capability_changed := NEW.capability_key IN (
            'platform.operations.read',
            'platform.maintenance.operate',
            'platform.release.operate',
            'platform.maintenance.verify',
            'platform.break_glass'
        );
    ELSE
        affected_principal := NEW.principal_id;
        changed := NEW.active IS DISTINCT FROM OLD.active
            OR NEW.capability_key IS DISTINCT FROM OLD.capability_key;
        operation_capability_changed := NEW.capability_key IN (
            'platform.operations.read',
            'platform.maintenance.operate',
            'platform.release.operate',
            'platform.maintenance.verify',
            'platform.break_glass'
        ) OR OLD.capability_key IN (
            'platform.operations.read',
            'platform.maintenance.operate',
            'platform.release.operate',
            'platform.maintenance.verify',
            'platform.break_glass'
        );
    END IF;

    IF changed AND operation_capability_changed THEN
        UPDATE access_control.accounts
           SET capability_revision = capability_revision + 1,
               updated_at = statement_timestamp()
         WHERE principal_id = affected_principal;

        UPDATE access_control.sessions
           SET revoked_at = statement_timestamp(),
               revocation_reason = 'PLATFORM_CAPABILITY_CHANGED'
         WHERE principal_id = affected_principal
           AND revoked_at IS NULL;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS platform_operation_capability_session_revocation
ON access_control.platform_capability_grants;

CREATE TRIGGER platform_operation_capability_session_revocation
AFTER INSERT OR DELETE OR UPDATE OF capability_key, active
ON access_control.platform_capability_grants
FOR EACH ROW EXECUTE FUNCTION
    access_control.revoke_sessions_after_platform_operation_grant_change();
