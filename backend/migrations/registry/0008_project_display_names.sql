-- Organization and project identities are stable machine keys; display names are
-- separate operator-managed labels used by the application shell and directories.

ALTER TABLE registry.organization_projects
    ADD COLUMN display_name text;

UPDATE registry.organization_projects
SET display_name = project_id
WHERE display_name IS NULL;

ALTER TABLE registry.organization_projects
    ALTER COLUMN display_name SET NOT NULL,
    ADD CONSTRAINT organization_projects_display_name_not_empty
        CHECK (display_name <> '');

