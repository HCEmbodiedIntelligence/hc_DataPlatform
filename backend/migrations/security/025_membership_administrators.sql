ALTER TABLE access_control.organization_memberships
    ADD COLUMN is_administrator boolean NOT NULL DEFAULT false;
ALTER TABLE access_control.memberships
    ADD COLUMN is_administrator boolean NOT NULL DEFAULT false;

CREATE FUNCTION access_control.clear_inactive_administrator() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT NEW.active THEN NEW.is_administrator := false; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER organization_membership_admin_status
    BEFORE INSERT OR UPDATE ON access_control.organization_memberships
    FOR EACH ROW EXECUTE FUNCTION access_control.clear_inactive_administrator();
CREATE TRIGGER project_membership_admin_status
    BEFORE INSERT OR UPDATE ON access_control.memberships
    FOR EACH ROW EXECUTE FUNCTION access_control.clear_inactive_administrator();
