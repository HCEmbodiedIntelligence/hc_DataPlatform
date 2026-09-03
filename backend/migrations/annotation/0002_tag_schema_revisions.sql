-- F1-04/F2-04/F3-03: versioned Tag Schema and reviewable annotation revisions.
-- OPEN-07/08 remain configurable: this migration has no hierarchy-depth limit and
-- stores no hard-coded self-review decision. Apply in one transaction.

CREATE TABLE IF NOT EXISTS annotation.tag_schema_versions (
    schema_id text NOT NULL,
    version bigint NOT NULL CHECK (version > 0),
    project_id text NOT NULL,
    name text NOT NULL,
    status text NOT NULL CHECK (status IN ('DRAFT', 'PUBLISHED')),
    document jsonb NOT NULL,
    content_hash char(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    published_by text,
    published_at timestamptz,
    PRIMARY KEY (schema_id, version),
    CHECK (
        (status = 'DRAFT' AND published_by IS NULL AND published_at IS NULL)
        OR (status = 'PUBLISHED' AND published_by IS NOT NULL AND published_at IS NOT NULL)
    )
);

-- 0001 protects revisions with an UPDATE trigger. Temporarily remove it while
-- the fixed Lance baseline is backfilled; transaction rollback restores it on error.
DROP TRIGGER IF EXISTS annotation_revisions_append_only
ON annotation.annotation_revisions;

ALTER TABLE annotation.annotation_tasks
ADD COLUMN IF NOT EXISTS base_lance_version bigint;
UPDATE annotation.annotation_tasks
SET base_lance_version = dataset_version
WHERE base_lance_version IS NULL;
ALTER TABLE annotation.annotation_tasks
ALTER COLUMN base_lance_version SET NOT NULL;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'annotation_tasks_base_lance_version_positive'
          AND conrelid = 'annotation.annotation_tasks'::regclass
    ) THEN
        ALTER TABLE annotation.annotation_tasks
        ADD CONSTRAINT annotation_tasks_base_lance_version_positive
        CHECK (base_lance_version > 0) NOT VALID;
    END IF;
END
$$;
ALTER TABLE annotation.annotation_tasks
VALIDATE CONSTRAINT annotation_tasks_base_lance_version_positive;

ALTER TABLE annotation.annotation_tasks
ADD COLUMN IF NOT EXISTS base_step_count bigint CHECK (base_step_count > 0);
ALTER TABLE annotation.annotation_tasks
ADD COLUMN IF NOT EXISTS tag_schema_id text NOT NULL DEFAULT 'legacy-flat';
ALTER TABLE annotation.annotation_tasks
ADD COLUMN IF NOT EXISTS tag_schema_version bigint NOT NULL DEFAULT 1
    CHECK (tag_schema_version > 0);
ALTER TABLE annotation.annotation_tasks
ADD COLUMN IF NOT EXISTS current_submission_id text;

ALTER TABLE annotation.annotation_revisions
ADD COLUMN IF NOT EXISTS base_lance_version bigint;
UPDATE annotation.annotation_revisions revision
SET base_lance_version = task.base_lance_version
FROM annotation.annotation_tasks task
WHERE revision.task_id = task.task_id
  AND revision.base_lance_version IS NULL;
ALTER TABLE annotation.annotation_revisions
ALTER COLUMN base_lance_version SET NOT NULL;
ALTER TABLE annotation.annotation_revisions
ADD COLUMN IF NOT EXISTS tag_schema_id text NOT NULL DEFAULT 'legacy-flat';
ALTER TABLE annotation.annotation_revisions
ADD COLUMN IF NOT EXISTS tag_schema_version bigint NOT NULL DEFAULT 1
    CHECK (tag_schema_version > 0);
ALTER TABLE annotation.annotation_revisions
ADD COLUMN IF NOT EXISTS tags jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE annotation.annotation_revisions
ADD COLUMN IF NOT EXISTS origin text NOT NULL DEFAULT 'ANNOTATION'
    CHECK (origin IN ('ANNOTATION', 'LEGACY_CLEANING'));
ALTER TABLE annotation.annotation_revisions
ADD COLUMN IF NOT EXISTS legacy_audit jsonb;
ALTER TABLE annotation.annotation_revisions
ADD COLUMN IF NOT EXISTS content_hash char(64) NOT NULL DEFAULT repeat('0', 64)
    CHECK (content_hash ~ '^[0-9a-f]{64}$');
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'annotation_revision_legacy_audit_pair'
          AND conrelid = 'annotation.annotation_revisions'::regclass
    ) THEN
        ALTER TABLE annotation.annotation_revisions
        ADD CONSTRAINT annotation_revision_legacy_audit_pair
        CHECK ((origin = 'LEGACY_CLEANING') = (legacy_audit IS NOT NULL)) NOT VALID;
    END IF;
END
$$;
ALTER TABLE annotation.annotation_revisions
VALIDATE CONSTRAINT annotation_revision_legacy_audit_pair;

CREATE TRIGGER annotation_revisions_append_only
BEFORE UPDATE OR DELETE ON annotation.annotation_revisions
FOR EACH ROW EXECUTE FUNCTION annotation.reject_immutable_change();

CREATE TABLE IF NOT EXISTS annotation.annotation_submissions (
    submission_id text PRIMARY KEY,
    task_id text NOT NULL,
    revision bigint NOT NULL CHECK (revision >= 0),
    submitted_by text NOT NULL,
    base_lance_version bigint NOT NULL CHECK (base_lance_version > 0),
    tag_schema_id text NOT NULL,
    tag_schema_version bigint NOT NULL CHECK (tag_schema_version > 0),
    tag_schema_hash char(64) NOT NULL CHECK (tag_schema_hash ~ '^[0-9a-f]{64}$'),
    revision_content_hash char(64) NOT NULL
        CHECK (revision_content_hash ~ '^[0-9a-f]{64}$'),
    checks jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (task_id, submission_id),
    FOREIGN KEY (task_id, revision)
        REFERENCES annotation.annotation_revisions(task_id, revision)
);

CREATE TABLE IF NOT EXISTS annotation.annotation_submission_mutations (
    task_id text NOT NULL,
    idempotency_key text NOT NULL,
    actor_id text NOT NULL,
    request_fingerprint char(64) NOT NULL
        CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    submission_id text NOT NULL,
    result_etag text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id, idempotency_key),
    FOREIGN KEY (task_id, submission_id)
        REFERENCES annotation.annotation_submissions(task_id, submission_id)
);

ALTER TABLE annotation.annotation_reviews
ADD COLUMN IF NOT EXISTS submission_id text;
ALTER TABLE annotation.annotation_reviews
ADD COLUMN IF NOT EXISTS checked_kinds jsonb NOT NULL DEFAULT
    '["HIERARCHY","BOUNDARY","REQUIRED_ATTRIBUTES","MUTUAL_EXCLUSION","OBJECT_RELATIONS","SCHEMA_VERSION"]'::jsonb;

CREATE TABLE IF NOT EXISTS annotation.legacy_cleaning_migrations (
    source_draft_id text NOT NULL,
    source_revision bigint NOT NULL CHECK (source_revision >= 0),
    source_audit_event_id text,
    source_actor_id text NOT NULL,
    source_created_at timestamptz NOT NULL,
    target_task_id text NOT NULL,
    target_revision bigint NOT NULL CHECK (target_revision >= 0),
    source_payload jsonb NOT NULL,
    migrated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_draft_id, source_revision),
    UNIQUE (target_task_id, target_revision),
    FOREIGN KEY (target_task_id, target_revision)
        REFERENCES annotation.annotation_revisions(task_id, revision)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'annotation_tasks_current_submission_fk'
          AND conrelid = 'annotation.annotation_tasks'::regclass
    ) THEN
        ALTER TABLE annotation.annotation_tasks
        ADD CONSTRAINT annotation_tasks_current_submission_fk
        FOREIGN KEY (task_id, current_submission_id)
        REFERENCES annotation.annotation_submissions(task_id, submission_id)
        DEFERRABLE INITIALLY DEFERRED;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'annotation_reviews_submission_fk'
          AND conrelid = 'annotation.annotation_reviews'::regclass
    ) THEN
        ALTER TABLE annotation.annotation_reviews
        ADD CONSTRAINT annotation_reviews_submission_fk
        FOREIGN KEY (task_id, submission_id)
        REFERENCES annotation.annotation_submissions(task_id, submission_id)
        DEFERRABLE INITIALLY DEFERRED;
    END IF;
END
$$;

CREATE OR REPLACE VIEW annotation.annotation_current AS
SELECT
    task_id,
    current_revision,
    state_version,
    status,
    submitted_revision,
    submitted_by,
    approved_revision,
    approved_review_id,
    etag,
    updated_at,
    base_lance_version,
    base_step_count,
    tag_schema_id,
    tag_schema_version,
    current_submission_id
FROM annotation.annotation_tasks;

CREATE OR REPLACE VIEW annotation.annotation_drafts AS
SELECT
    task_id,
    current_revision AS revision,
    etag,
    assignee_id,
    updated_at,
    base_lance_version,
    base_step_count,
    tag_schema_id,
    tag_schema_version
FROM annotation.annotation_tasks
WHERE status IN ('DRAFT', 'NEEDS_REVISION', 'REJECTED');

CREATE OR REPLACE FUNCTION annotation.protect_tag_schema_version()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Tag Schema versions cannot be deleted' USING ERRCODE = '55000';
    END IF;
    IF OLD.status = 'PUBLISHED' THEN
        RAISE EXCEPTION 'published Tag Schema versions are immutable' USING ERRCODE = '55000';
    END IF;
    IF NEW.schema_id <> OLD.schema_id
       OR NEW.version <> OLD.version
       OR NEW.project_id <> OLD.project_id
       OR NEW.name <> OLD.name
       OR NEW.document <> OLD.document
       OR NEW.content_hash <> OLD.content_hash
       OR NEW.created_by <> OLD.created_by
       OR NEW.created_at <> OLD.created_at
       OR NEW.status <> 'PUBLISHED'
       OR NEW.published_by IS NULL
       OR NEW.published_at IS NULL THEN
        RAISE EXCEPTION 'publishing may only freeze existing Tag Schema content'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS tag_schema_version_immutable
ON annotation.tag_schema_versions;
CREATE TRIGGER tag_schema_version_immutable
BEFORE UPDATE OR DELETE ON annotation.tag_schema_versions
FOR EACH ROW EXECUTE FUNCTION annotation.protect_tag_schema_version();

DROP TRIGGER IF EXISTS annotation_submissions_append_only
ON annotation.annotation_submissions;
CREATE TRIGGER annotation_submissions_append_only
BEFORE UPDATE OR DELETE ON annotation.annotation_submissions
FOR EACH ROW EXECUTE FUNCTION annotation.reject_immutable_change();

DROP TRIGGER IF EXISTS annotation_submission_mutations_append_only
ON annotation.annotation_submission_mutations;
CREATE TRIGGER annotation_submission_mutations_append_only
BEFORE UPDATE OR DELETE ON annotation.annotation_submission_mutations
FOR EACH ROW EXECUTE FUNCTION annotation.reject_immutable_change();

DROP TRIGGER IF EXISTS legacy_cleaning_migrations_append_only
ON annotation.legacy_cleaning_migrations;
CREATE TRIGGER legacy_cleaning_migrations_append_only
BEFORE UPDATE OR DELETE ON annotation.legacy_cleaning_migrations
FOR EACH ROW EXECUTE FUNCTION annotation.reject_immutable_change();

ALTER TABLE annotation.tag_schema_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE annotation.annotation_submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE annotation.annotation_submission_mutations ENABLE ROW LEVEL SECURITY;
ALTER TABLE annotation.legacy_cleaning_migrations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tag_schema_project_scope ON annotation.tag_schema_versions;
CREATE POLICY tag_schema_project_scope ON annotation.tag_schema_versions
USING (
    current_setting('app.is_admin', true) = 'true'
    OR project_id = ANY (string_to_array(current_setting('app.project_ids', true), ','))
)
WITH CHECK (
    current_setting('app.is_admin', true) = 'true'
    OR project_id = ANY (string_to_array(current_setting('app.project_ids', true), ','))
);

DROP POLICY IF EXISTS annotation_submission_project_scope
ON annotation.annotation_submissions;
CREATE POLICY annotation_submission_project_scope ON annotation.annotation_submissions
USING (EXISTS (
    SELECT 1 FROM annotation.annotation_tasks task
    WHERE task.task_id = annotation_submissions.task_id
))
WITH CHECK (EXISTS (
    SELECT 1 FROM annotation.annotation_tasks task
    WHERE task.task_id = annotation_submissions.task_id
));

DROP POLICY IF EXISTS annotation_submission_mutation_project_scope
ON annotation.annotation_submission_mutations;
CREATE POLICY annotation_submission_mutation_project_scope
ON annotation.annotation_submission_mutations
USING (EXISTS (
    SELECT 1 FROM annotation.annotation_tasks task
    WHERE task.task_id = annotation_submission_mutations.task_id
))
WITH CHECK (EXISTS (
    SELECT 1 FROM annotation.annotation_tasks task
    WHERE task.task_id = annotation_submission_mutations.task_id
));

DROP POLICY IF EXISTS legacy_cleaning_migration_project_scope
ON annotation.legacy_cleaning_migrations;
CREATE POLICY legacy_cleaning_migration_project_scope
ON annotation.legacy_cleaning_migrations
USING (EXISTS (
    SELECT 1 FROM annotation.annotation_tasks task
    WHERE task.task_id = legacy_cleaning_migrations.target_task_id
))
WITH CHECK (EXISTS (
    SELECT 1 FROM annotation.annotation_tasks task
    WHERE task.task_id = legacy_cleaning_migrations.target_task_id
));

CREATE INDEX IF NOT EXISTS tag_schema_versions_project_idx
ON annotation.tag_schema_versions (project_id, schema_id, version DESC);
CREATE INDEX IF NOT EXISTS annotation_submissions_task_idx
ON annotation.annotation_submissions (task_id, created_at, submission_id);
