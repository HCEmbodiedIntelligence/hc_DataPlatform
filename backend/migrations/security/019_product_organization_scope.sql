-- Close the remaining project-only product-table isolation gap.
--
-- Project identifiers are scoped by organization.  A project-only RLS policy is
-- therefore unsafe when two organizations legitimately reuse the same project
-- identifier.  This migration discovers every durable product table that carries
-- project_id but not organization_id, refuses an ambiguous historical backfill,
-- adds the exact organization identity, and installs a restrictive tenant policy.

CREATE OR REPLACE FUNCTION core.apply_project_rls(target_table regclass)
RETURNS void
LANGUAGE plpgsql
AS $function$
DECLARE
    has_project_id boolean;
    has_organization_id boolean;
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
          AND attname = 'organization_id'
          AND attnum > 0
          AND NOT attisdropped
    ) INTO has_organization_id;

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
    EXECUTE format('DROP POLICY IF EXISTS hc_scope_allow ON %s', target_table);

    -- At least one permissive policy is required before restrictive policies can
    -- admit a row.  Existing domain policies may be more selective; this neutral
    -- policy only supplies the PostgreSQL policy-composition baseline.
    EXECUTE format(
        'CREATE POLICY hc_scope_allow ON %s AS PERMISSIVE FOR ALL '
        'USING (true) WITH CHECK (true)',
        target_table
    );

    -- A restrictive policy is ANDed with every permissive domain policy.  Older
    -- project-only policies can therefore never weaken the organization boundary.
    IF has_organization_id AND has_region_code THEN
        EXECUTE format(
            'CREATE POLICY hc_scope_isolation ON %s AS RESTRICTIVE FOR ALL '
            'USING (core.organization_scope_matches(organization_id, project_id, region_code)) '
            'WITH CHECK (core.organization_scope_matches(organization_id, project_id, region_code))',
            target_table
        );
    ELSIF has_organization_id THEN
        EXECUTE format(
            'CREATE POLICY hc_scope_isolation ON %s AS RESTRICTIVE FOR ALL '
            'USING (core.organization_scope_matches(organization_id, project_id)) '
            'WITH CHECK (core.organization_scope_matches(organization_id, project_id))',
            target_table
        );
    ELSIF has_region_code THEN
        EXECUTE format(
            'CREATE POLICY hc_scope_isolation ON %s AS RESTRICTIVE FOR ALL '
            'USING (core.scope_matches(project_id, region_code)) '
            'WITH CHECK (core.scope_matches(project_id, region_code))',
            target_table
        );
    ELSE
        EXECUTE format(
            'CREATE POLICY hc_scope_isolation ON %s AS RESTRICTIVE FOR ALL '
            'USING (core.scope_matches(project_id)) '
            'WITH CHECK (core.scope_matches(project_id))',
            target_table
        );
    END IF;
END
$function$;

-- Preflight the complete historical state before adding the first column.  The
-- migration runner wraps this file in one transaction, but an early preflight also
-- avoids taking table-rewrite locks when an operator must repair registry identity.
DO $preflight$
DECLARE
    target record;
    has_ambiguous_identity boolean;
BEGIN
    FOR target IN
        SELECT project_column.table_schema, project_column.table_name
        FROM information_schema.columns AS project_column
        JOIN information_schema.tables AS relation
          ON relation.table_schema = project_column.table_schema
         AND relation.table_name = project_column.table_name
        WHERE project_column.column_name = 'project_id'
          AND relation.table_type = 'BASE TABLE'
          AND project_column.table_schema NOT IN ('pg_catalog', 'information_schema')
          AND NOT EXISTS (
              SELECT 1
              FROM information_schema.columns AS organization_column
              WHERE organization_column.table_schema = project_column.table_schema
                AND organization_column.table_name = project_column.table_name
                AND organization_column.column_name = 'organization_id'
          )
        ORDER BY project_column.table_schema, project_column.table_name
    LOOP
        EXECUTE format(
            'SELECT EXISTS ('
            '  SELECT 1 '
            '  FROM (SELECT DISTINCT project_id FROM %I.%I) AS historical '
            '  LEFT JOIN registry.organization_projects AS project '
            '    ON project.project_id = historical.project_id '
            '  GROUP BY historical.project_id '
            '  HAVING historical.project_id IS NULL '
            '      OR count(DISTINCT project.organization_id) <> 1'
            ')',
            target.table_schema,
            target.table_name
        ) INTO has_ambiguous_identity;

        IF has_ambiguous_identity THEN
            RAISE EXCEPTION
                'organization-scoped product upgrade requires exactly one registry organization for every historical project in %.%',
                target.table_schema,
                target.table_name;
        END IF;
    END LOOP;
END
$preflight$;

DO $upgrade$
DECLARE
    target record;
    constraint_prefix text;
    index_name text;
    has_region_code boolean;
BEGIN
    FOR target IN
        SELECT project_column.table_schema, project_column.table_name
        FROM information_schema.columns AS project_column
        JOIN information_schema.tables AS relation
          ON relation.table_schema = project_column.table_schema
         AND relation.table_name = project_column.table_name
        WHERE project_column.column_name = 'project_id'
          AND relation.table_type = 'BASE TABLE'
          AND project_column.table_schema NOT IN ('pg_catalog', 'information_schema')
          AND NOT EXISTS (
              SELECT 1
              FROM information_schema.columns AS organization_column
              WHERE organization_column.table_schema = project_column.table_schema
                AND organization_column.table_name = project_column.table_name
                AND organization_column.column_name = 'organization_id'
          )
        ORDER BY project_column.table_schema, project_column.table_name
    LOOP
        constraint_prefix := target.table_name;
        index_name := target.table_name || '_organization_project_scope_idx';

        EXECUTE format(
            'ALTER TABLE %I.%I ADD COLUMN organization_id text',
            target.table_schema,
            target.table_name
        );
        -- Append-only and immutable relations legitimately reject ordinary
        -- updates.  The migration owns an exclusive DDL lock and changes only
        -- this derived tenant identity, so user triggers are suspended for the
        -- backfill and restored before constraints/RLS are installed.  Internal
        -- foreign-key triggers remain enabled throughout.
        EXECUTE format(
            'ALTER TABLE %I.%I DISABLE TRIGGER USER',
            target.table_schema,
            target.table_name
        );
        EXECUTE format(
            'UPDATE %I.%I AS row '
            'SET organization_id = project.organization_id '
            'FROM ('
            '  SELECT project_id, min(organization_id) AS organization_id '
            '  FROM registry.organization_projects '
            '  GROUP BY project_id '
            '  HAVING count(DISTINCT organization_id) = 1'
            ') AS project '
            'WHERE row.project_id = project.project_id '
            '  AND row.organization_id IS NULL',
            target.table_schema,
            target.table_name
        );
        EXECUTE format(
            'ALTER TABLE %I.%I ENABLE TRIGGER USER',
            target.table_schema,
            target.table_name
        );
        EXECUTE format(
            'ALTER TABLE %I.%I '
            'ALTER COLUMN organization_id SET DEFAULT '
            'NULLIF(current_setting(''app.organization_id'', true), ''''), '
            'ALTER COLUMN organization_id SET NOT NULL, '
            'ADD CONSTRAINT %I CHECK (organization_id <> ''''), '
            'ADD CONSTRAINT %I FOREIGN KEY (organization_id, project_id) '
            'REFERENCES registry.organization_projects (organization_id, project_id)',
            target.table_schema,
            target.table_name,
            constraint_prefix || '_organization_nonempty',
            constraint_prefix || '_organization_project_fk'
        );

        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns AS region_column
            WHERE region_column.table_schema = target.table_schema
              AND region_column.table_name = target.table_name
              AND region_column.column_name = 'region_code'
        ) INTO has_region_code;
        IF has_region_code THEN
            EXECUTE format(
                'CREATE INDEX %I ON %I.%I '
                '(organization_id, project_id, region_code)',
                index_name,
                target.table_schema,
                target.table_name
            );
        ELSE
            EXECUTE format(
                'CREATE INDEX %I ON %I.%I (organization_id, project_id)',
                index_name,
                target.table_schema,
                target.table_name
            );
        END IF;

        EXECUTE format(
            'COMMENT ON COLUMN %I.%I.organization_id IS '
            '''Exact tenant identity; populated from registry.organization_projects during security/019.''',
            target.table_schema,
            target.table_name
        );
    END LOOP;
END
$upgrade$;

-- Reconcile every project table, including tables already upgraded by a domain
-- migration, so they all receive the restrictive organization-aware policy form.
SELECT core.reconcile_project_rls();

-- Account notifications are intentionally readable before a project is selected:
-- their tenant metadata identifies the referenced resource, while the security
-- boundary is the recipient_id policy installed by security/009.  Remove only the
-- generic project policies and retain FORCE RLS plus recipient isolation.
DO $account_scope_exception$
BEGIN
    IF to_regclass('access_control.account_notifications') IS NOT NULL THEN
        DROP POLICY IF EXISTS hc_scope_isolation
            ON access_control.account_notifications;
        DROP POLICY IF EXISTS hc_scope_allow
            ON access_control.account_notifications;
        ALTER TABLE access_control.account_notifications ENABLE ROW LEVEL SECURITY;
        ALTER TABLE access_control.account_notifications FORCE ROW LEVEL SECURITY;
    END IF;
END
$account_scope_exception$;

DO $assert_complete$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns AS project_column
        JOIN information_schema.tables AS relation
          ON relation.table_schema = project_column.table_schema
         AND relation.table_name = project_column.table_name
        WHERE project_column.column_name = 'project_id'
          AND relation.table_type = 'BASE TABLE'
          AND project_column.table_schema NOT IN ('pg_catalog', 'information_schema')
          AND NOT EXISTS (
              SELECT 1
              FROM information_schema.columns AS organization_column
              WHERE organization_column.table_schema = project_column.table_schema
                AND organization_column.table_name = project_column.table_name
                AND organization_column.column_name = 'organization_id'
          )
    ) THEN
        RAISE EXCEPTION 'project-only durable tables remain after security/019';
    END IF;
END
$assert_complete$;
