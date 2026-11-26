ALTER TABLE access_control.retired_scopes
    ADD COLUMN cleanup_planned_at timestamptz,
    ADD COLUMN next_attempt_at timestamptz,
    ADD COLUMN last_error text;
CREATE INDEX directory_data_due ON access_control.retired_scopes(purge_after)
    WHERE status='DELETED' AND completed_at IS NULL;

-- Individual, platform-owned file locators only. Never store a bucket sweep.
CREATE TABLE access_control.directory_cleanup_files (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    file_kind text NOT NULL CHECK(file_kind IN ('object','local','multipart')),
    locator text NOT NULL,
    upload_id text NOT NULL DEFAULT '',
    storage_identity text NOT NULL,
    PRIMARY KEY(organization_id,project_id,file_kind,locator,upload_id),
    FOREIGN KEY(organization_id,project_id) REFERENCES access_control.retired_scopes
);
