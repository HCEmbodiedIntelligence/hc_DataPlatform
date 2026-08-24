-- Core tenant hardening: project identifiers are organization-local.  The
-- idempotency ledger, cross-domain audit stream, integrity chain, and durable
-- outbox must therefore preserve the exact organization/project identity.
--
-- Historical rows predate that identity.  Backfill is permitted only when the
-- registry has exactly one organization for every referenced project.  A
-- missing or ambiguous mapping aborts the whole migration instead of guessing
-- and potentially moving durable security facts across tenants.

DO $preflight$
DECLARE
    invalid_scopes text;
BEGIN
    WITH referenced AS (
        SELECT 'core.idempotency_records'::text AS relation_name, project_id
          FROM core.idempotency_records
         GROUP BY project_id
        UNION ALL
        SELECT 'core.audit_events', project_id
          FROM core.audit_events
         GROUP BY project_id
        UNION ALL
        SELECT 'core.outbox_events', project_id
          FROM core.outbox_events
         GROUP BY project_id
        UNION ALL
        SELECT 'core.audit_integrity_heads', project_id
          FROM core.audit_integrity_heads
         GROUP BY project_id
    ), invalid AS (
        SELECT referenced.relation_name, referenced.project_id
          FROM referenced
          LEFT JOIN registry.organization_projects project
            ON project.project_id = referenced.project_id
         GROUP BY referenced.relation_name, referenced.project_id
        HAVING count(DISTINCT project.organization_id) <> 1
    )
    SELECT string_agg(
               relation_name || ':' || project_id,
               ', ' ORDER BY relation_name, project_id
           )
      INTO invalid_scopes
      FROM invalid;

    IF invalid_scopes IS NOT NULL THEN
        RAISE EXCEPTION
            'organization-scoped core upgrade requires exactly one registry organization for every historical project: %',
            invalid_scopes;
    END IF;
END
$preflight$;

ALTER TABLE core.idempotency_records
    ADD COLUMN IF NOT EXISTS organization_id text;
ALTER TABLE core.audit_events
    ADD COLUMN IF NOT EXISTS organization_id text;
ALTER TABLE core.outbox_events
    ADD COLUMN IF NOT EXISTS organization_id text;
ALTER TABLE core.audit_integrity_heads
    ADD COLUMN IF NOT EXISTS organization_id text;
ALTER TABLE core.audit_integrity_entries
    ADD COLUMN IF NOT EXISTS organization_id text;

UPDATE core.idempotency_records record
   SET organization_id = project.organization_id
  FROM registry.organization_projects project
 WHERE project.project_id = record.project_id
   AND record.organization_id IS NULL;

UPDATE core.audit_events event
   SET organization_id = project.organization_id
  FROM registry.organization_projects project
 WHERE project.project_id = event.project_id
   AND event.organization_id IS NULL;

UPDATE core.outbox_events event
   SET organization_id = project.organization_id
  FROM registry.organization_projects project
 WHERE project.project_id = event.project_id
   AND event.organization_id IS NULL;

UPDATE core.audit_integrity_entries entry
   SET organization_id = event.organization_id
  FROM core.audit_events event
 WHERE event.audit_id = entry.audit_id
   AND entry.organization_id IS NULL;

UPDATE core.audit_integrity_heads head
   SET organization_id = project.organization_id
  FROM registry.organization_projects project
 WHERE project.project_id = head.project_id
   AND head.organization_id IS NULL;

-- New writers receive organization identity from the verified database scope.
-- An unscoped writer gets NULL and is rejected by NOT NULL; the foreign key
-- independently proves that the organization owns the selected project.
ALTER TABLE core.idempotency_records
    ALTER COLUMN organization_id SET DEFAULT
        NULLIF(current_setting('app.organization_id', true), ''),
    ALTER COLUMN organization_id SET NOT NULL,
    ADD CONSTRAINT idempotency_records_organization_nonempty
        CHECK (organization_id <> ''),
    ADD CONSTRAINT idempotency_records_organization_project_fk
        FOREIGN KEY (organization_id, project_id)
        REFERENCES registry.organization_projects (organization_id, project_id);

ALTER TABLE core.audit_events
    ALTER COLUMN organization_id SET DEFAULT
        NULLIF(current_setting('app.organization_id', true), ''),
    ALTER COLUMN organization_id SET NOT NULL,
    ADD CONSTRAINT audit_events_organization_nonempty
        CHECK (organization_id <> ''),
    ADD CONSTRAINT audit_events_organization_project_fk
        FOREIGN KEY (organization_id, project_id)
        REFERENCES registry.organization_projects (organization_id, project_id);

ALTER TABLE core.outbox_events
    ALTER COLUMN organization_id SET DEFAULT
        NULLIF(current_setting('app.organization_id', true), ''),
    ALTER COLUMN organization_id SET NOT NULL,
    ADD CONSTRAINT outbox_events_organization_nonempty
        CHECK (organization_id <> ''),
    ADD CONSTRAINT outbox_events_organization_project_fk
        FOREIGN KEY (organization_id, project_id)
        REFERENCES registry.organization_projects (organization_id, project_id),
    ADD CONSTRAINT outbox_events_envelope_organization_matches
        CHECK (
            NOT (envelope ? 'organization_id')
            OR (
                envelope->>'organization_id' IS NOT NULL
                AND envelope->>'organization_id' = organization_id
            )
        );

ALTER TABLE core.audit_integrity_heads
    ALTER COLUMN organization_id SET NOT NULL,
    ADD CONSTRAINT audit_integrity_heads_organization_nonempty
        CHECK (organization_id <> ''),
    ADD CONSTRAINT audit_integrity_heads_organization_project_fk
        FOREIGN KEY (organization_id, project_id)
        REFERENCES registry.organization_projects (organization_id, project_id);

ALTER TABLE core.audit_integrity_entries
    ALTER COLUMN organization_id SET NOT NULL,
    ADD CONSTRAINT audit_integrity_entries_organization_nonempty
        CHECK (organization_id <> ''),
    ADD CONSTRAINT audit_integrity_entries_organization_project_fk
        FOREIGN KEY (organization_id, project_id)
        REFERENCES registry.organization_projects (organization_id, project_id);

ALTER TABLE core.idempotency_records
    DROP CONSTRAINT idempotency_records_pkey,
    ADD CONSTRAINT idempotency_records_pkey PRIMARY KEY (
        organization_id, project_id, region_code, scope_key, idempotency_key
    );

ALTER TABLE core.audit_integrity_heads
    DROP CONSTRAINT audit_integrity_heads_pkey,
    ADD CONSTRAINT audit_integrity_heads_pkey PRIMARY KEY (
        organization_id, project_id, region_code
    );

ALTER TABLE core.audit_integrity_entries
    DROP CONSTRAINT audit_integrity_entries_project_id_region_code_sequence_no_key,
    ADD CONSTRAINT audit_integrity_entries_organization_scope_sequence_key UNIQUE (
        organization_id, project_id, region_code, sequence_no
    );

DROP INDEX IF EXISTS core.audit_events_project_occurred_idx;
-- Preserve the bootstrap index name so repeatable security/001 cannot recreate
-- its historical project-only definition after this forward migration.
CREATE INDEX audit_events_project_occurred_idx
ON core.audit_events (
    organization_id, project_id, occurred_at DESC, audit_id
);

DROP INDEX IF EXISTS core.audit_events_p19_scope_keyset_idx;
CREATE INDEX audit_events_p19_organization_scope_keyset_idx
ON core.audit_events (
    organization_id,
    project_id,
    region_code,
    occurred_at DESC,
    audit_id DESC
)
INCLUDE (
    actor_id,
    action,
    resource_type,
    resource_id,
    request_id,
    before_hash,
    after_hash
);

DROP INDEX IF EXISTS core.outbox_events_pending_idx;
CREATE INDEX outbox_events_pending_idx
ON core.outbox_events (organization_id, available_at, occurred_at)
WHERE published_at IS NULL;

DROP INDEX IF EXISTS core.outbox_events_claimable_idx;
CREATE INDEX outbox_events_organization_claimable_idx
ON core.outbox_events (organization_id, available_at, occurred_at, event_id)
WHERE published_at IS NULL;

DROP INDEX IF EXISTS core.audit_integrity_entries_scope_sequence_idx;
CREATE INDEX audit_integrity_entries_organization_scope_sequence_idx
ON core.audit_integrity_entries (
    organization_id, project_id, region_code, sequence_no
);

-- Organization is part of the digest, so moving an otherwise identical event
-- between organizations is detectable.  This replaces the historical helper
-- with an organization-aware signature.
CREATE OR REPLACE FUNCTION core.audit_integrity_event_hash(
    previous_hash text,
    event_audit_id uuid,
    event_organization_id text,
    event_project_id text,
    event_region_code text,
    event_actor_id text,
    event_action text,
    event_resource_type text,
    event_resource_id text,
    event_request_id text,
    event_before_hash text,
    event_after_hash text,
    event_details jsonb,
    event_occurred_at timestamptz
) RETURNS char(64)
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
SET search_path = pg_catalog, core, public
AS $function$
    SELECT encode(
        digest(
            convert_to(
                jsonb_build_object(
                    'after_hash', event_after_hash,
                    'audit_id', event_audit_id,
                    'before_hash', event_before_hash,
                    'details', event_details,
                    'occurred_at', event_occurred_at,
                    'organization_id', event_organization_id,
                    'previous_hash', previous_hash,
                    'project_id', event_project_id,
                    'region_code', event_region_code,
                    'request_id', event_request_id,
                    'resource_id', event_resource_id,
                    'resource_type', event_resource_type,
                    'actor_id', event_actor_id,
                    'action', event_action
                )::text,
                'UTF8'
            ),
            'sha256'
        ),
        'hex'
    )::char(64)
$function$;

CREATE OR REPLACE FUNCTION core.append_audit_integrity_entry(target_audit_id uuid)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, core, public
AS $function$
DECLARE
    audit_event core.audit_events%ROWTYPE;
    head core.audit_integrity_heads%ROWTYPE;
    normalized_region text;
    next_sequence bigint;
    next_hash char(64);
BEGIN
    SELECT * INTO audit_event
      FROM core.audit_events
     WHERE audit_id = target_audit_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'audit event % is not available for integrity chaining', target_audit_id;
    END IF;

    normalized_region := COALESCE(audit_event.region_code, '');
    INSERT INTO core.audit_integrity_heads (
        organization_id, project_id, region_code
    ) VALUES (
        audit_event.organization_id, audit_event.project_id, normalized_region
    )
    ON CONFLICT (organization_id, project_id, region_code) DO NOTHING;

    SELECT * INTO head
      FROM core.audit_integrity_heads
     WHERE organization_id = audit_event.organization_id
       AND project_id = audit_event.project_id
       AND region_code = normalized_region
     FOR UPDATE;

    IF EXISTS (
        SELECT 1 FROM core.audit_integrity_entries WHERE audit_id = target_audit_id
    ) THEN
        RAISE EXCEPTION 'audit event % already has an integrity entry', target_audit_id;
    END IF;

    next_sequence := head.last_sequence + 1;
    next_hash := core.audit_integrity_event_hash(
        head.last_event_hash,
        audit_event.audit_id,
        audit_event.organization_id,
        audit_event.project_id,
        audit_event.region_code,
        audit_event.actor_id,
        audit_event.action,
        audit_event.resource_type,
        audit_event.resource_id,
        audit_event.request_id,
        audit_event.before_hash,
        audit_event.after_hash,
        audit_event.details,
        audit_event.occurred_at
    );

    INSERT INTO core.audit_integrity_entries (
        audit_id, organization_id, project_id, region_code, sequence_no,
        previous_event_hash, event_hash, occurred_at
    ) VALUES (
        audit_event.audit_id, audit_event.organization_id, audit_event.project_id,
        normalized_region, next_sequence, head.last_event_hash, next_hash,
        audit_event.occurred_at
    );

    UPDATE core.audit_integrity_heads
       SET last_sequence = next_sequence,
           last_event_hash = next_hash,
           updated_at = now()
     WHERE organization_id = audit_event.organization_id
       AND project_id = audit_event.project_id
       AND region_code = normalized_region;
END
$function$;

-- Rebuild only the derived chain.  The immutable source audit stream remains
-- untouched apart from the proven organization backfill above.
TRUNCATE TABLE core.audit_integrity_entries, core.audit_integrity_heads;

DO $rebuild$
DECLARE
    event_row record;
BEGIN
    FOR event_row IN
        SELECT audit_id
          FROM core.audit_events
         ORDER BY organization_id, project_id, COALESCE(region_code, ''), occurred_at, audit_id
    LOOP
        PERFORM core.append_audit_integrity_entry(event_row.audit_id);
    END LOOP;
END
$rebuild$;

REVOKE ALL ON FUNCTION core.audit_integrity_event_hash(
    text, uuid, text, text, text, text, text, text, text, text, text, text, jsonb, timestamptz
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.audit_integrity_event_hash(
    text, uuid, text, text, text, text, text, text, text, text, text, text, jsonb, timestamptz
) TO PUBLIC;

DROP FUNCTION core.audit_integrity_event_hash(
    text, uuid, text, text, text, text, text, text, text, text, text, jsonb, timestamptz
);

SELECT core.apply_project_rls('core.idempotency_records'::regclass);
SELECT core.apply_project_rls('core.audit_events'::regclass);
SELECT core.apply_project_rls('core.outbox_events'::regclass);
SELECT core.apply_project_rls('core.audit_integrity_heads'::regclass);
SELECT core.apply_project_rls('core.audit_integrity_entries'::regclass);
