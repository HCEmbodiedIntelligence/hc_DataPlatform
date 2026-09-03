-- BE-09: immutable annotation revisions and reviewer decisions.
-- Apply the entire file in one transaction. It is safe to re-apply.

CREATE SCHEMA IF NOT EXISTS annotation;

CREATE TABLE IF NOT EXISTS annotation.annotation_tasks (
    task_id text PRIMARY KEY,
    project_id text NOT NULL,
    dataset_id text NOT NULL,
    dataset_version bigint NOT NULL CHECK (dataset_version > 0),
    rollout_id text NOT NULL,
    assignee_id text,
    current_revision bigint NOT NULL DEFAULT 0 CHECK (current_revision >= 0),
    state_version bigint NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    status text NOT NULL CHECK (
        status IN ('DRAFT', 'SUBMITTED', 'APPROVED', 'NEEDS_REVISION', 'REJECTED')
    ),
    submitted_revision bigint CHECK (submitted_revision >= 0),
    submitted_by text,
    approved_revision bigint CHECK (approved_revision >= 0),
    approved_review_id text,
    etag text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, rollout_id),
    CHECK ((submitted_revision IS NULL) = (submitted_by IS NULL)),
    CHECK ((approved_revision IS NULL) = (approved_review_id IS NULL)),
    CHECK (
        (status = 'APPROVED' AND approved_revision IS NOT NULL)
        OR (status <> 'APPROVED' AND approved_revision IS NULL)
    ),
    CHECK (submitted_revision IS NULL OR submitted_revision <= current_revision),
    CHECK (approved_revision IS NULL OR approved_revision <= current_revision)
);

CREATE TABLE IF NOT EXISTS annotation.annotation_revisions (
    task_id text NOT NULL REFERENCES annotation.annotation_tasks(task_id),
    revision bigint NOT NULL CHECK (revision >= 0),
    parent_revision bigint,
    author_id text NOT NULL,
    client_mutation_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id, revision),
    CHECK (
        (revision = 0 AND parent_revision IS NULL)
        OR (revision > 0 AND parent_revision = revision - 1)
    )
);

CREATE TABLE IF NOT EXISTS annotation.annotation_operations (
    task_id text NOT NULL,
    revision bigint NOT NULL,
    operation_id text NOT NULL,
    operation_sequence integer NOT NULL CHECK (operation_sequence >= 0),
    kind text NOT NULL CHECK (kind IN ('EXCLUDE', 'RESTORE')),
    start_step bigint NOT NULL CHECK (start_step >= 0),
    end_step bigint NOT NULL,
    reason text NOT NULL DEFAULT '',
    modality_scope text NOT NULL DEFAULT 'ALL_MODALITIES'
        CHECK (modality_scope = 'ALL_MODALITIES'),
    PRIMARY KEY (task_id, revision, operation_id),
    UNIQUE (task_id, operation_id),
    UNIQUE (task_id, revision, operation_sequence),
    CHECK (end_step > start_step),
    FOREIGN KEY (task_id, revision)
        REFERENCES annotation.annotation_revisions(task_id, revision)
);

CREATE TABLE IF NOT EXISTS annotation.annotation_reviews (
    review_id text PRIMARY KEY,
    task_id text NOT NULL,
    revision bigint NOT NULL CHECK (revision >= 0),
    reviewer_id text NOT NULL,
    decision text NOT NULL CHECK (decision IN ('APPROVE', 'NEEDS_REVISION', 'REJECT')),
    comment text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (task_id, revision, review_id),
    FOREIGN KEY (task_id, revision)
        REFERENCES annotation.annotation_revisions(task_id, revision)
);

CREATE TABLE IF NOT EXISTS annotation.annotation_mutations (
    task_id text NOT NULL,
    client_mutation_id text NOT NULL,
    actor_id text NOT NULL,
    request_fingerprint char(64) NOT NULL,
    expected_revision bigint NOT NULL CHECK (expected_revision >= 0),
    request_etag text NOT NULL,
    result_revision bigint NOT NULL CHECK (result_revision > 0),
    result_etag text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id, client_mutation_id),
    FOREIGN KEY (task_id, result_revision)
        REFERENCES annotation.annotation_revisions(task_id, revision)
);

-- Deferred cyclic pointers let task creation insert revision zero in the same transaction.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'annotation_tasks_current_revision_fk'
          AND conrelid = 'annotation.annotation_tasks'::regclass
    ) THEN
        ALTER TABLE annotation.annotation_tasks
        ADD CONSTRAINT annotation_tasks_current_revision_fk
        FOREIGN KEY (task_id, current_revision)
        REFERENCES annotation.annotation_revisions(task_id, revision)
        DEFERRABLE INITIALLY DEFERRED;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'annotation_tasks_submitted_revision_fk'
          AND conrelid = 'annotation.annotation_tasks'::regclass
    ) THEN
        ALTER TABLE annotation.annotation_tasks
        ADD CONSTRAINT annotation_tasks_submitted_revision_fk
        FOREIGN KEY (task_id, submitted_revision)
        REFERENCES annotation.annotation_revisions(task_id, revision)
        DEFERRABLE INITIALLY DEFERRED;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'annotation_tasks_approved_review_fk'
          AND conrelid = 'annotation.annotation_tasks'::regclass
    ) THEN
        ALTER TABLE annotation.annotation_tasks
        ADD CONSTRAINT annotation_tasks_approved_review_fk
        FOREIGN KEY (task_id, approved_revision, approved_review_id)
        REFERENCES annotation.annotation_reviews(task_id, revision, review_id)
        DEFERRABLE INITIALLY DEFERRED;
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS annotation_tasks_project_status_idx
ON annotation.annotation_tasks (project_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS annotation_reviews_task_revision_idx
ON annotation.annotation_reviews (task_id, revision, created_at);

-- Draft and Current are explicit persisted read models over the canonical task pointers.
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
    updated_at
FROM annotation.annotation_tasks;

CREATE OR REPLACE VIEW annotation.annotation_drafts AS
SELECT
    task_id,
    current_revision AS revision,
    etag,
    assignee_id,
    updated_at
FROM annotation.annotation_tasks
WHERE status IN ('DRAFT', 'NEEDS_REVISION', 'REJECTED');

-- Immutable records cannot be rewritten or removed; RESTORE is always another operation.
CREATE OR REPLACE FUNCTION annotation.reject_immutable_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% rows are append-only', TG_TABLE_NAME
        USING ERRCODE = '55000';
END
$$;

DROP TRIGGER IF EXISTS annotation_revisions_append_only
ON annotation.annotation_revisions;
CREATE TRIGGER annotation_revisions_append_only
BEFORE UPDATE OR DELETE ON annotation.annotation_revisions
FOR EACH ROW EXECUTE FUNCTION annotation.reject_immutable_change();

DROP TRIGGER IF EXISTS annotation_operations_append_only
ON annotation.annotation_operations;
CREATE TRIGGER annotation_operations_append_only
BEFORE UPDATE OR DELETE ON annotation.annotation_operations
FOR EACH ROW EXECUTE FUNCTION annotation.reject_immutable_change();

DROP TRIGGER IF EXISTS annotation_reviews_append_only
ON annotation.annotation_reviews;
CREATE TRIGGER annotation_reviews_append_only
BEFORE UPDATE OR DELETE ON annotation.annotation_reviews
FOR EACH ROW EXECUTE FUNCTION annotation.reject_immutable_change();

DROP TRIGGER IF EXISTS annotation_mutations_append_only
ON annotation.annotation_mutations;
CREATE TRIGGER annotation_mutations_append_only
BEFORE UPDATE OR DELETE ON annotation.annotation_mutations
FOR EACH ROW EXECUTE FUNCTION annotation.reject_immutable_change();

-- BE-02 sets app.project_ids and app.is_admin on scoped database transactions.
ALTER TABLE annotation.annotation_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE annotation.annotation_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE annotation.annotation_operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE annotation.annotation_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE annotation.annotation_mutations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS annotation_task_project_scope ON annotation.annotation_tasks;
CREATE POLICY annotation_task_project_scope ON annotation.annotation_tasks
USING (
    current_setting('app.is_admin', true) = 'true'
    OR project_id = ANY (
        string_to_array(current_setting('app.project_ids', true), ',')
    )
)
WITH CHECK (
    current_setting('app.is_admin', true) = 'true'
    OR project_id = ANY (
        string_to_array(current_setting('app.project_ids', true), ',')
    )
);

DROP POLICY IF EXISTS annotation_revision_project_scope ON annotation.annotation_revisions;
CREATE POLICY annotation_revision_project_scope ON annotation.annotation_revisions
USING (
    EXISTS (
        SELECT 1 FROM annotation.annotation_tasks task
        WHERE task.task_id = annotation_revisions.task_id
    )
)
WITH CHECK (
    EXISTS (
        SELECT 1 FROM annotation.annotation_tasks task
        WHERE task.task_id = annotation_revisions.task_id
    )
);

DROP POLICY IF EXISTS annotation_operation_project_scope ON annotation.annotation_operations;
CREATE POLICY annotation_operation_project_scope ON annotation.annotation_operations
USING (
    EXISTS (
        SELECT 1 FROM annotation.annotation_tasks task
        WHERE task.task_id = annotation_operations.task_id
    )
)
WITH CHECK (
    EXISTS (
        SELECT 1 FROM annotation.annotation_tasks task
        WHERE task.task_id = annotation_operations.task_id
    )
);

DROP POLICY IF EXISTS annotation_review_project_scope ON annotation.annotation_reviews;
CREATE POLICY annotation_review_project_scope ON annotation.annotation_reviews
USING (
    EXISTS (
        SELECT 1 FROM annotation.annotation_tasks task
        WHERE task.task_id = annotation_reviews.task_id
    )
)
WITH CHECK (
    EXISTS (
        SELECT 1 FROM annotation.annotation_tasks task
        WHERE task.task_id = annotation_reviews.task_id
    )
);

DROP POLICY IF EXISTS annotation_mutation_project_scope ON annotation.annotation_mutations;
CREATE POLICY annotation_mutation_project_scope ON annotation.annotation_mutations
USING (
    EXISTS (
        SELECT 1 FROM annotation.annotation_tasks task
        WHERE task.task_id = annotation_mutations.task_id
    )
)
WITH CHECK (
    EXISTS (
        SELECT 1 FROM annotation.annotation_tasks task
        WHERE task.task_id = annotation_mutations.task_id
    )
);
