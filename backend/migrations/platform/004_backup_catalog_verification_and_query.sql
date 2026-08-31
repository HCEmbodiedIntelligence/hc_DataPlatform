-- BAK2-06 adds repeatable integrity-verification facts and the exact keyset
-- ordering consumed by the redacted online backup-catalog query. Entries and
-- facts remain append-only; a repeated verification does not rewrite history
-- and can never promote a backup to RESTORE_VERIFIED.

CREATE INDEX IF NOT EXISTS backup_catalog_environment_completed_keyset_idx
ON platform.backup_catalog_entries (
    source_environment_id,
    backup_completed_at DESC,
    backup_id DESC
);

CREATE OR REPLACE FUNCTION platform.validate_backup_catalog_status_append()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    previous_status text;
BEGIN
    PERFORM 1
      FROM platform.backup_catalog_entries
     WHERE backup_id = NEW.backup_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'PLATFORM_BACKUP_CATALOG_ENTRY_NOT_FOUND' USING ERRCODE = 'P0001';
    END IF;

    SELECT status INTO previous_status
      FROM platform.backup_catalog_status_facts
     WHERE backup_id = NEW.backup_id
     ORDER BY status_event_id DESC
     LIMIT 1;

    IF previous_status IS NULL THEN
        IF NOT (
            (NEW.source_kind = 'CREATE' AND NEW.status = 'CREATING')
            OR (
                NEW.source_kind = 'REBUILD'
                AND NEW.status IN ('INTEGRITY_VERIFIED', 'RESTORE_VERIFIED')
            )
        ) THEN
            RAISE EXCEPTION 'PLATFORM_BACKUP_CATALOG_INITIAL_STATUS_INVALID'
                USING ERRCODE = 'P0001';
        END IF;
        RETURN NEW;
    END IF;

    IF previous_status = 'CREATING'
       AND NEW.status IN ('CREATED', 'FAILED', 'CORRUPT', 'EXPIRED') THEN
        RETURN NEW;
    ELSIF previous_status = 'CREATED'
       AND NEW.status IN ('INTEGRITY_VERIFIED', 'FAILED', 'CORRUPT', 'EXPIRED') THEN
        RETURN NEW;
    ELSIF previous_status = 'INTEGRITY_VERIFIED'
       AND NEW.status = 'INTEGRITY_VERIFIED'
       AND NEW.source_kind = 'VERIFY' THEN
        RETURN NEW;
    ELSIF previous_status = 'INTEGRITY_VERIFIED'
       AND NEW.status IN ('RESTORE_VERIFIED', 'CORRUPT', 'EXPIRED') THEN
        RETURN NEW;
    ELSIF previous_status = 'RESTORE_VERIFIED'
       AND NEW.status IN ('CORRUPT', 'EXPIRED') THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION 'PLATFORM_BACKUP_CATALOG_TRANSITION_INVALID'
        USING ERRCODE = 'P0001';
END;
$function$;
