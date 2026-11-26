ALTER TABLE access_control.organization_join_codes
    ALTER COLUMN join_code DROP NOT NULL,
    ADD COLUMN allow_id_application boolean NOT NULL DEFAULT true;

-- Reserve retired identifiers. Reusing a tenant identifier would attach old
-- storage references or stale client selections to an unrelated new scope.
CREATE TABLE access_control.retired_scopes (
    organization_id text NOT NULL,
    project_id text NOT NULL DEFAULT '',
    display_name text NOT NULL,
    status text NOT NULL CHECK (status IN ('ARCHIVED', 'DELETED')),
    purge_after timestamptz,
    completed_at timestamptz,
    retired_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, project_id)
);

CREATE VIEW access_control.active_projects WITH (security_invoker=true) AS
SELECT p.* FROM registry.organization_projects p WHERE NOT EXISTS (
    SELECT 1 FROM access_control.retired_scopes r WHERE r.organization_id=p.organization_id AND r.project_id IN ('', p.project_id)
);

-- Prevent a concurrent approval or legacy provisioning call from reopening a retired scope.
CREATE FUNCTION access_control.guard_directory_membership() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE project text := COALESCE(to_jsonb(NEW)->>'project_id','');
BEGIN
    PERFORM pg_advisory_xact_lock_shared(hashtextextended('directory/' || NEW.organization_id,0));
    IF EXISTS (SELECT 1 FROM access_control.retired_scopes r
        WHERE r.organization_id=NEW.organization_id AND r.project_id IN ('',project)) THEN
        RAISE EXCEPTION 'Directory scope has been retired' USING ERRCODE='23514', CONSTRAINT='active_directory_required';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER active_org_member BEFORE INSERT OR UPDATE ON access_control.organization_memberships
    FOR EACH ROW WHEN (NEW.active) EXECUTE FUNCTION access_control.guard_directory_membership();
CREATE TRIGGER active_project_member BEFORE INSERT OR UPDATE ON access_control.memberships
    FOR EACH ROW WHEN (NEW.active) EXECUTE FUNCTION access_control.guard_directory_membership();
CREATE TRIGGER open_org_application BEFORE INSERT OR UPDATE ON access_control.organization_membership_requests
    FOR EACH ROW WHEN (NEW.status IN ('PENDING','APPROVED')) EXECUTE FUNCTION access_control.guard_directory_membership();
CREATE TRIGGER open_project_application BEFORE INSERT OR UPDATE ON access_control.membership_requests
    FOR EACH ROW WHEN (NEW.status IN ('PENDING','APPROVED')) EXECUTE FUNCTION access_control.guard_directory_membership();
CREATE TRIGGER open_capability_application BEFORE INSERT OR UPDATE ON access_control.capability_requests
    FOR EACH ROW WHEN (NEW.status IN ('PENDING','APPROVED')) EXECUTE FUNCTION access_control.guard_directory_membership();
CREATE TRIGGER active_project_grant BEFORE INSERT OR UPDATE ON access_control.capability_grants
    FOR EACH ROW WHEN (NEW.active) EXECUTE FUNCTION access_control.guard_directory_membership();
CREATE TRIGGER active_project_directory BEFORE INSERT ON registry.organization_projects
    FOR EACH ROW EXECUTE FUNCTION access_control.guard_directory_membership();
