-- Rows which carry both organization_id and project_id must never be selected
-- by project ID alone.  Project IDs are scoped by organization in the product
-- model, so the generic RLS reconciler needs an exact two-part tenant match.

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
        row_organization_id = NULLIF(current_setting('app.organization_id', true), '')
        AND row_project_id = NULLIF(current_setting('app.project_id', true), '')
        AND (
            row_region_code IS NULL
            OR row_region_code = COALESCE(current_setting('app.region_code', true), '')
        )
$function$;

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
    IF has_organization_id AND has_region_code THEN
        EXECUTE format(
            'CREATE POLICY hc_scope_isolation ON %s '
            'USING (core.organization_scope_matches(organization_id, project_id, region_code)) '
            'WITH CHECK (core.organization_scope_matches(organization_id, project_id, region_code))',
            target_table
        );
    ELSIF has_organization_id THEN
        EXECUTE format(
            'CREATE POLICY hc_scope_isolation ON %s '
            'USING (core.organization_scope_matches(organization_id, project_id)) '
            'WITH CHECK (core.organization_scope_matches(organization_id, project_id))',
            target_table
        );
    ELSIF has_region_code THEN
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

SELECT core.reconcile_project_rls();
