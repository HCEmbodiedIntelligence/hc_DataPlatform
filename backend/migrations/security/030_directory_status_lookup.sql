-- A project-scoped caller must see its parent's retirement even under FORCE RLS.
-- Expose only the inactive/active decision for the exact supplied identifiers.
CREATE FUNCTION access_control.directory_retired(org text, project text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog
SET app.platform_admin='true' AS $$
    SELECT EXISTS (SELECT 1 FROM access_control.retired_scopes r
        WHERE r.organization_id=org AND r.project_id IN ('',coalesce(project,'')))
$$;

CREATE OR REPLACE FUNCTION access_control.guard_directory_membership() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock_shared(hashtextextended('directory/' || NEW.organization_id,0));
    IF access_control.directory_retired(NEW.organization_id,to_jsonb(NEW)->>'project_id') THEN
        RAISE EXCEPTION 'Directory scope has been retired' USING ERRCODE='23514', CONSTRAINT='active_directory_required';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE VIEW access_control.active_projects WITH (security_invoker=true) AS
SELECT p.* FROM registry.organization_projects p
WHERE NOT access_control.directory_retired(p.organization_id,p.project_id);

REVOKE ALL ON FUNCTION access_control.directory_retired(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION access_control.directory_retired(text,text) TO CURRENT_USER;

CREATE FUNCTION access_control.next_directory_cleanup(target_org text DEFAULT NULL, target_project text DEFAULT NULL)
RETURNS TABLE(org text, project text) LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog SET app.platform_admin='true' AS $$
    SELECT r.organization_id,r.project_id FROM access_control.retired_scopes r
    WHERE (target_org IS NULL OR (r.organization_id=target_org AND r.project_id=target_project))
      AND r.status='DELETED' AND r.purge_after<=now() AND r.completed_at IS NULL
      AND (r.next_attempt_at IS NULL OR r.next_attempt_at<=now())
    ORDER BY r.purge_after,r.organization_id,r.project_id LIMIT 1
$$;
REVOKE ALL ON FUNCTION access_control.next_directory_cleanup(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION access_control.next_directory_cleanup(text,text) TO CURRENT_USER;

CREATE FUNCTION access_control.managed_directory_history(actor text)
RETURNS TABLE(organization_id text,project_id text,display_name text,status text,purge_after timestamptz,retrying boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog SET app.platform_admin='true' AS $$
    SELECT r.organization_id,r.project_id,r.display_name,r.status,r.purge_after,r.last_error IS NOT NULL
    FROM access_control.retired_scopes r WHERE r.completed_at IS NULL AND (
      EXISTS(SELECT 1 FROM access_control.platform_capability_grants g WHERE g.principal_id::text=actor AND g.active AND g.capability_key='platform.admin')
      OR (r.project_id<>'' AND EXISTS(SELECT 1 FROM access_control.organization_memberships m
             WHERE m.principal_id::text=actor AND m.organization_id=r.organization_id AND m.active AND m.is_administrator)))
    AND (r.project_id='' OR NOT EXISTS (SELECT 1 FROM access_control.retired_scopes p WHERE p.organization_id=r.organization_id AND p.project_id=''))
    ORDER BY 1,2
$$;
REVOKE ALL ON FUNCTION access_control.managed_directory_history(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION access_control.managed_directory_history(text) TO CURRENT_USER;
