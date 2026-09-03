-- BE-02 owns this repeatable PostgreSQL migration. It is safe to execute on an empty
-- database and safe to execute again after other module migrations have added tables.

CREATE SCHEMA IF NOT EXISTS core;

CREATE TABLE IF NOT EXISTS core.idempotency_records (
    project_id text NOT NULL,
    region_code text NOT NULL DEFAULT '',
    scope_key text NOT NULL,
    idempotency_key text NOT NULL,
    request_fingerprint char(64) NOT NULL,
    response_json jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, region_code, scope_key, idempotency_key),
    CHECK (project_id <> ''),
    CHECK (scope_key <> ''),
    CHECK (idempotency_key <> '')
);

CREATE INDEX IF NOT EXISTS idempotency_records_expiry_idx
ON core.idempotency_records (expires_at);

CREATE TABLE IF NOT EXISTS core.audit_events (
    audit_id uuid PRIMARY KEY,
    project_id text NOT NULL,
    region_code text,
    actor_id text NOT NULL,
    action text NOT NULL,
    resource_type text NOT NULL,
    resource_id text NOT NULL,
    request_id text NOT NULL,
    before_hash char(64),
    after_hash char(64),
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL,
    CHECK (project_id <> ''),
    CHECK (actor_id <> ''),
    CHECK (action <> ''),
    CHECK (request_id <> '')
);

CREATE INDEX IF NOT EXISTS audit_events_project_occurred_idx
ON core.audit_events (project_id, occurred_at DESC, audit_id);

CREATE TABLE IF NOT EXISTS core.outbox_events (
    event_id uuid PRIMARY KEY,
    project_id text NOT NULL,
    region_code text,
    event_type text NOT NULL,
    envelope jsonb NOT NULL,
    occurred_at timestamptz NOT NULL,
    published_at timestamptz,
    publish_attempts integer NOT NULL DEFAULT 0,
    available_at timestamptz NOT NULL DEFAULT now(),
    last_error text,
    CHECK (project_id <> ''),
    CHECK (event_type <> ''),
    CHECK (publish_attempts >= 0)
);

CREATE INDEX IF NOT EXISTS outbox_events_pending_idx
ON core.outbox_events (available_at, occurred_at)
WHERE published_at IS NULL;

-- Empty or absent app.project_id never matches. Project-only rows use NULL for
-- region_code and remain visible inside any explicitly selected region of that project.
-- Region-scoped rows require an exact app.region_code match. Idempotency uses '' for its
-- project-only region so NULL never weakens its unique constraint.
CREATE OR REPLACE FUNCTION core.scope_matches(
    row_project_id text,
    row_region_code text DEFAULT NULL
) RETURNS boolean
LANGUAGE sql
STABLE
PARALLEL SAFE
AS $function$
    SELECT
        row_project_id = NULLIF(current_setting('app.project_id', true), '')
        AND (
            row_region_code IS NULL
            OR row_region_code = COALESCE(current_setting('app.region_code', true), '')
        )
$function$;

-- Other module migrations can call this helper for every table that owns project_id.
-- FORCE closes PostgreSQL's table-owner RLS bypass; superusers remain PostgreSQL's
-- documented exception and must not be used by API or worker processes.
CREATE OR REPLACE FUNCTION core.apply_project_rls(target_table regclass)
RETURNS void
LANGUAGE plpgsql
AS $function$
DECLARE
    has_project_id boolean;
    has_region_code boolean;
BEGIN
    SELECT EXISTS (
        SELECT 1
        FROM pg_attribute
        WHERE attrelid = target_table
          AND attname = 'project_id'
          AND attnum > 0
          AND NOT attisdropped
    ) INTO has_project_id;
    IF NOT has_project_id THEN
        RAISE EXCEPTION 'table % has no project_id column', target_table;
    END IF;

    SELECT EXISTS (
        SELECT 1
        FROM pg_attribute
        WHERE attrelid = target_table
          AND attname = 'region_code'
          AND attnum > 0
          AND NOT attisdropped
    ) INTO has_region_code;

    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', target_table);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', target_table);
    EXECUTE format('DROP POLICY IF EXISTS hc_scope_isolation ON %s', target_table);
    IF has_region_code THEN
        EXECUTE format(
            'CREATE POLICY hc_scope_isolation ON %s '
            'USING (core.scope_matches(project_id, region_code)) '
            'WITH CHECK (core.scope_matches(project_id, region_code))',
            target_table
        );
    ELSE
        EXECUTE format(
            'CREATE POLICY hc_scope_isolation ON %s '
            'USING (core.scope_matches(project_id)) '
            'WITH CHECK (core.scope_matches(project_id))',
            target_table
        );
    END IF;
END
$function$;

CREATE OR REPLACE FUNCTION core.reconcile_project_rls()
RETURNS void
LANGUAGE plpgsql
AS $function$
DECLARE
    project_table regclass;
BEGIN
    FOR project_table IN
        SELECT DISTINCT attrelid::regclass
        FROM pg_attribute
        JOIN pg_class ON pg_class.oid = pg_attribute.attrelid
        JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace
        WHERE pg_attribute.attname = 'project_id'
          AND pg_attribute.attnum > 0
          AND NOT pg_attribute.attisdropped
          AND pg_class.relkind IN ('r', 'p')
          AND pg_namespace.nspname NOT IN ('pg_catalog', 'information_schema')
          AND NOT pg_namespace.nspname LIKE 'pg_toast%'
    LOOP
        PERFORM core.apply_project_rls(project_table);
    END LOOP;
END
$function$;

SELECT core.reconcile_project_rls();

-- Annotation child tables inherit their tenant through annotation_tasks instead of
-- carrying a duplicate project_id. Install equivalent policies when BE-09 is present.
DO $block$
BEGIN
    IF to_regclass('annotation.annotation_revisions') IS NOT NULL
       AND to_regclass('annotation.annotation_tasks') IS NOT NULL THEN
        ALTER TABLE annotation.annotation_revisions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE annotation.annotation_revisions FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS hc_scope_isolation ON annotation.annotation_revisions;
        CREATE POLICY hc_scope_isolation ON annotation.annotation_revisions
        USING (
            EXISTS (
                SELECT 1 FROM annotation.annotation_tasks task
                WHERE task.task_id = annotation_revisions.task_id
                  AND core.scope_matches(task.project_id)
            )
        )
        WITH CHECK (
            EXISTS (
                SELECT 1 FROM annotation.annotation_tasks task
                WHERE task.task_id = annotation_revisions.task_id
                  AND core.scope_matches(task.project_id)
            )
        );
    END IF;

    IF to_regclass('annotation.annotation_operations') IS NOT NULL
       AND to_regclass('annotation.annotation_tasks') IS NOT NULL THEN
        ALTER TABLE annotation.annotation_operations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE annotation.annotation_operations FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS hc_scope_isolation ON annotation.annotation_operations;
        CREATE POLICY hc_scope_isolation ON annotation.annotation_operations
        USING (
            EXISTS (
                SELECT 1 FROM annotation.annotation_tasks task
                WHERE task.task_id = annotation_operations.task_id
                  AND core.scope_matches(task.project_id)
            )
        )
        WITH CHECK (
            EXISTS (
                SELECT 1 FROM annotation.annotation_tasks task
                WHERE task.task_id = annotation_operations.task_id
                  AND core.scope_matches(task.project_id)
            )
        );
    END IF;

    IF to_regclass('annotation.annotation_reviews') IS NOT NULL
       AND to_regclass('annotation.annotation_tasks') IS NOT NULL THEN
        ALTER TABLE annotation.annotation_reviews ENABLE ROW LEVEL SECURITY;
        ALTER TABLE annotation.annotation_reviews FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS hc_scope_isolation ON annotation.annotation_reviews;
        CREATE POLICY hc_scope_isolation ON annotation.annotation_reviews
        USING (
            EXISTS (
                SELECT 1 FROM annotation.annotation_tasks task
                WHERE task.task_id = annotation_reviews.task_id
                  AND core.scope_matches(task.project_id)
            )
        )
        WITH CHECK (
            EXISTS (
                SELECT 1 FROM annotation.annotation_tasks task
                WHERE task.task_id = annotation_reviews.task_id
                  AND core.scope_matches(task.project_id)
            )
        );
    END IF;

    IF to_regclass('annotation.annotation_mutations') IS NOT NULL
       AND to_regclass('annotation.annotation_tasks') IS NOT NULL THEN
        ALTER TABLE annotation.annotation_mutations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE annotation.annotation_mutations FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS hc_scope_isolation ON annotation.annotation_mutations;
        CREATE POLICY hc_scope_isolation ON annotation.annotation_mutations
        USING (
            EXISTS (
                SELECT 1 FROM annotation.annotation_tasks task
                WHERE task.task_id = annotation_mutations.task_id
                  AND core.scope_matches(task.project_id)
            )
        )
        WITH CHECK (
            EXISTS (
                SELECT 1 FROM annotation.annotation_tasks task
                WHERE task.task_id = annotation_mutations.task_id
                  AND core.scope_matches(task.project_id)
            )
        );
    END IF;
END
$block$;
