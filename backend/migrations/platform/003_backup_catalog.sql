-- Rebuildable whole-platform backup catalog. The independent backup repository
-- remains authoritative; PostgreSQL stores only immutable logical references,
-- digests, and append-only lifecycle facts for discovery and auditing.

CREATE TABLE IF NOT EXISTS platform.backup_catalog_entries (
    backup_id text PRIMARY KEY,
    format_version text NOT NULL,
    mode text NOT NULL,
    source_platform_id text NOT NULL,
    source_environment_id text NOT NULL,
    repository_id text NOT NULL,
    manifest_uri text NOT NULL,
    manifest_sha256 char(64) NOT NULL,
    signature_sha256 char(64) NOT NULL,
    signer_public_key_sha256 char(64) NOT NULL,
    release_manifest_sha256 char(64) NOT NULL,
    migration_manifest_sha256 char(64) NOT NULL,
    backup_created_at timestamptz NOT NULL,
    backup_completed_at timestamptz NOT NULL,
    cataloged_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    CHECK (backup_id ~ '^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$'),
    CHECK (format_version = 'hc-platform-backup/v1'),
    CHECK (mode IN ('portable', 'snapshot')),
    CHECK (source_platform_id ~ '^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$'),
    CHECK (source_environment_id ~ '^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$'),
    CHECK (repository_id ~ '^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$'),
    CHECK (
        manifest_uri = 'backup-repository://' || repository_id || '/' || backup_id || '/manifest.json'
    ),
    CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (signature_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (signer_public_key_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (release_manifest_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (migration_manifest_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (backup_completed_at >= backup_created_at),
    UNIQUE (repository_id, manifest_uri)
);

CREATE INDEX IF NOT EXISTS backup_catalog_environment_completed_idx
ON platform.backup_catalog_entries (
    source_environment_id,
    backup_completed_at DESC,
    backup_id
);

CREATE TABLE IF NOT EXISTS platform.backup_catalog_status_facts (
    status_event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    backup_id text NOT NULL REFERENCES platform.backup_catalog_entries(backup_id),
    status text NOT NULL,
    source_kind text NOT NULL,
    operation_id text NOT NULL,
    evidence_uri text,
    evidence_sha256 char(64),
    failure_code text,
    recorded_by text NOT NULL,
    request_id text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    CHECK (status IN (
        'CREATING', 'CREATED', 'INTEGRITY_VERIFIED', 'RESTORE_VERIFIED',
        'FAILED', 'CORRUPT', 'EXPIRED'
    )),
    CHECK (source_kind IN ('CREATE', 'REBUILD', 'VERIFY', 'RESTORE', 'LIFECYCLE')),
    CHECK (operation_id ~ '^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$'),
    CHECK (
        evidence_uri IS NULL
        OR evidence_uri ~ '^backup-repository://[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}/[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}/[a-zA-Z0-9._/-]+$'
    ),
    CHECK (evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK ((evidence_uri IS NULL) = (evidence_sha256 IS NULL)),
    CHECK (
        status NOT IN ('INTEGRITY_VERIFIED', 'RESTORE_VERIFIED', 'CORRUPT')
        OR evidence_uri IS NOT NULL
    ),
    CHECK (
        (status IN ('FAILED', 'CORRUPT')
            AND failure_code ~ '^BACKUP_[A-Z0-9_]{1,120}$')
        OR (status NOT IN ('FAILED', 'CORRUPT') AND failure_code IS NULL)
    ),
    CHECK (recorded_by <> '' AND length(recorded_by) <= 255),
    CHECK (request_id <> '' AND length(request_id) <= 255),
    UNIQUE (backup_id, operation_id)
);

CREATE INDEX IF NOT EXISTS backup_catalog_status_latest_idx
ON platform.backup_catalog_status_facts (backup_id, status_event_id DESC);

CREATE OR REPLACE FUNCTION platform.reject_backup_catalog_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'PLATFORM_BACKUP_CATALOG_IMMUTABLE' USING ERRCODE = 'P0001';
END;
$function$;

DROP TRIGGER IF EXISTS backup_catalog_entries_immutable
ON platform.backup_catalog_entries;

CREATE TRIGGER backup_catalog_entries_immutable
BEFORE UPDATE OR DELETE ON platform.backup_catalog_entries
FOR EACH ROW EXECUTE FUNCTION platform.reject_backup_catalog_mutation();

DROP TRIGGER IF EXISTS backup_catalog_status_facts_immutable
ON platform.backup_catalog_status_facts;

CREATE TRIGGER backup_catalog_status_facts_immutable
BEFORE UPDATE OR DELETE ON platform.backup_catalog_status_facts
FOR EACH ROW EXECUTE FUNCTION platform.reject_backup_catalog_mutation();

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

DROP TRIGGER IF EXISTS backup_catalog_status_transition
ON platform.backup_catalog_status_facts;

CREATE TRIGGER backup_catalog_status_transition
BEFORE INSERT ON platform.backup_catalog_status_facts
FOR EACH ROW EXECUTE FUNCTION platform.validate_backup_catalog_status_append();

CREATE OR REPLACE VIEW platform.backup_catalog_current AS
SELECT entry.*,
       latest.status,
       latest.status_event_id,
       latest.operation_id AS status_operation_id,
       latest.occurred_at AS status_occurred_at
  FROM platform.backup_catalog_entries AS entry
  JOIN LATERAL (
      SELECT fact.status, fact.status_event_id, fact.operation_id, fact.occurred_at
        FROM platform.backup_catalog_status_facts AS fact
       WHERE fact.backup_id = entry.backup_id
       ORDER BY fact.status_event_id DESC
       LIMIT 1
  ) AS latest ON true;
