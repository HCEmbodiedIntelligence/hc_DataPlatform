ALTER TABLE access_control.memberships
    ADD COLUMN assigned_roles text[] NOT NULL DEFAULT '{}',
    ADD CONSTRAINT ordinary_member_roles CHECK (assigned_roles <@ ARRAY[
        'role.observer','role.collector','role.annotator','role.reviewer','role.publisher']::text[]);

CREATE FUNCTION access_control.clear_inactive_project_roles() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT NEW.active THEN NEW.assigned_roles := '{}'; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER project_membership_assigned_roles BEFORE INSERT OR UPDATE ON access_control.memberships
FOR EACH ROW EXECUTE FUNCTION access_control.clear_inactive_project_roles();
