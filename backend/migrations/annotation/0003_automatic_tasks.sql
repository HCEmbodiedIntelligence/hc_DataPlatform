-- BR02: explicit Tag Schema compatibility and replay-safe system-created tasks.

ALTER TABLE annotation.tag_schema_versions
    ADD COLUMN IF NOT EXISTS compatible_targets jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE annotation.annotation_tasks
    ADD COLUMN IF NOT EXISTS region_code text,
    ADD COLUMN IF NOT EXISTS task_kind text NOT NULL DEFAULT 'TAGGING',
    ADD COLUMN IF NOT EXISTS creation_source text NOT NULL DEFAULT 'LEGACY',
    ADD COLUMN IF NOT EXISTS source_workflow_id text;

ALTER TABLE annotation.annotation_tasks
    DROP CONSTRAINT IF EXISTS annotation_tasks_project_id_rollout_id_key;
ALTER TABLE annotation.annotation_tasks
    DROP CONSTRAINT IF EXISTS annotation_tasks_task_kind_check;
ALTER TABLE annotation.annotation_tasks
    ADD CONSTRAINT annotation_tasks_task_kind_check CHECK (task_kind IN ('TAGGING'));
ALTER TABLE annotation.annotation_tasks
    DROP CONSTRAINT IF EXISTS annotation_tasks_creation_source_check;
ALTER TABLE annotation.annotation_tasks
    ADD CONSTRAINT annotation_tasks_creation_source_check
    CHECK (creation_source IN ('LEGACY', 'SYSTEM_LANCE'));
ALTER TABLE annotation.annotation_tasks
    DROP CONSTRAINT IF EXISTS annotation_tasks_system_lineage_check;
ALTER TABLE annotation.annotation_tasks
    ADD CONSTRAINT annotation_tasks_system_lineage_check CHECK (
        (creation_source = 'LEGACY')
        OR (region_code IS NOT NULL AND source_workflow_id IS NOT NULL
            AND base_step_count IS NOT NULL)
    );

CREATE UNIQUE INDEX IF NOT EXISTS annotation_tasks_automatic_target_uidx
ON annotation.annotation_tasks(
    project_id, region_code, rollout_id, dataset_id,
    dataset_version, base_lance_version, task_kind
)
WHERE region_code IS NOT NULL;

CREATE TABLE IF NOT EXISTS annotation.tag_schema_bindings (
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    dataset_schema_snapshot_id text NOT NULL,
    task_kind text NOT NULL CHECK (task_kind IN ('TAGGING')),
    schema_id text NOT NULL,
    schema_version bigint NOT NULL CHECK (schema_version > 0),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (
        project_id, region_code, dataset_id, dataset_schema_snapshot_id, task_kind
    ),
    FOREIGN KEY (schema_id, schema_version)
        REFERENCES annotation.tag_schema_versions(schema_id, version),
    CHECK (project_id <> '' AND region_code <> '' AND dataset_id <> ''),
    CHECK (dataset_schema_snapshot_id <> '')
);

CREATE TABLE IF NOT EXISTS annotation.annotation_task_triggers (
    trigger_id uuid PRIMARY KEY,
    project_id text NOT NULL,
    region_code text NOT NULL,
    rollout_id text NOT NULL,
    dataset_id text NOT NULL,
    dataset_version bigint NOT NULL CHECK (dataset_version > 0),
    lance_version bigint NOT NULL CHECK (lance_version > 0),
    dataset_schema_snapshot_id text NOT NULL,
    task_kind text NOT NULL CHECK (task_kind IN ('TAGGING')),
    source_workflow_id text NOT NULL,
    task_id text,
    status text NOT NULL CHECK (status IN ('CREATED', 'BLOCKED_RETRYABLE')),
    error_code text,
    attempts integer NOT NULL DEFAULT 1 CHECK (attempts > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (
        project_id, region_code, rollout_id, dataset_id,
        dataset_version, lance_version, task_kind
    ),
    FOREIGN KEY (task_id) REFERENCES annotation.annotation_tasks(task_id),
    CHECK (source_workflow_id <> ''),
    CHECK (
        (status = 'CREATED' AND task_id IS NOT NULL AND error_code IS NULL)
        OR (status = 'BLOCKED_RETRYABLE' AND task_id IS NULL
            AND error_code = 'ANNOTATION_SCHEMA_BINDING_MISSING')
    )
);

CREATE INDEX IF NOT EXISTS annotation_task_triggers_retry_idx
ON annotation.annotation_task_triggers(project_id, region_code, status, updated_at)
WHERE status = 'BLOCKED_RETRYABLE';

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
       OR NEW.compatible_targets <> OLD.compatible_targets
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

DROP TRIGGER IF EXISTS tag_schema_binding_append_only
ON annotation.tag_schema_bindings;
CREATE TRIGGER tag_schema_binding_append_only
BEFORE UPDATE OR DELETE ON annotation.tag_schema_bindings
FOR EACH ROW EXECUTE FUNCTION annotation.reject_immutable_change();

DROP POLICY IF EXISTS annotation_task_project_scope
ON annotation.annotation_tasks;
SELECT core.apply_project_rls('annotation.annotation_tasks'::regclass);
SELECT core.apply_project_rls('annotation.tag_schema_bindings'::regclass);
SELECT core.apply_project_rls('annotation.annotation_task_triggers'::regclass);

-- Child rows inherit the exact project and region from their owning task.  Drop
-- every older project-only policy first because PostgreSQL combines permissive
-- policies with OR.
DO $scope$
DECLARE
    child_table text;
    task_column text;
    old_policy text;
BEGIN
    FOR child_table, task_column, old_policy IN
        SELECT * FROM (VALUES
            ('annotation_revisions', 'task_id', 'annotation_revision_project_scope'),
            ('annotation_operations', 'task_id', 'annotation_operation_project_scope'),
            ('annotation_reviews', 'task_id', 'annotation_review_project_scope'),
            ('annotation_mutations', 'task_id', 'annotation_mutation_project_scope'),
            ('annotation_submissions', 'task_id', 'annotation_submission_project_scope'),
            (
                'annotation_submission_mutations',
                'task_id',
                'annotation_submission_mutation_project_scope'
            ),
            (
                'legacy_cleaning_migrations',
                'target_task_id',
                'legacy_cleaning_migration_project_scope'
            )
        ) AS child(table_name, owner_column, policy_name)
    LOOP
        IF to_regclass('annotation.' || child_table) IS NOT NULL THEN
            EXECUTE format(
                'ALTER TABLE annotation.%I ENABLE ROW LEVEL SECURITY', child_table
            );
            EXECUTE format(
                'ALTER TABLE annotation.%I FORCE ROW LEVEL SECURITY', child_table
            );
            EXECUTE format(
                'DROP POLICY IF EXISTS hc_scope_isolation ON annotation.%I', child_table
            );
            EXECUTE format(
                'DROP POLICY IF EXISTS %I ON annotation.%I', old_policy, child_table
            );
            EXECUTE format(
                'CREATE POLICY hc_scope_isolation ON annotation.%I '
                'USING (EXISTS (SELECT 1 FROM annotation.annotation_tasks task '
                'WHERE task.task_id = %I.%I '
                'AND core.scope_matches(task.project_id, task.region_code))) '
                'WITH CHECK (EXISTS (SELECT 1 FROM annotation.annotation_tasks task '
                'WHERE task.task_id = %I.%I '
                'AND core.scope_matches(task.project_id, task.region_code)))',
                child_table, child_table, task_column, child_table, task_column
            );
        END IF;
    END LOOP;
END
$scope$;
