ALTER TABLE access_control.memberships
    ADD COLUMN assigned_permissions text[] NOT NULL DEFAULT '{}';

CREATE OR REPLACE FUNCTION access_control.clear_inactive_project_roles() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT NEW.active THEN
        NEW.assigned_roles := '{}';
        NEW.assigned_permissions := '{}';
    END IF;
    RETURN NEW;
END;
$$;
