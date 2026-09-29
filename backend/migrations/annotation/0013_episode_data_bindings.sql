-- G1 additive binding: legacy Lance hashes and immutable documents are untouched.
ALTER TABLE annotation.annotation_tasks ADD COLUMN data_ref jsonb;
ALTER TABLE annotation.annotation_revisions ADD COLUMN data_ref jsonb;
ALTER TABLE annotation.annotation_submissions ADD COLUMN data_ref jsonb;
ALTER TABLE annotation.annotation_reviews ADD COLUMN data_ref jsonb;
ALTER TABLE annotation.annotation_task_triggers ADD COLUMN data_ref jsonb;
ALTER TABLE annotation.annotation_tasks ALTER COLUMN base_lance_version DROP NOT NULL;
ALTER TABLE annotation.annotation_revisions ALTER COLUMN base_lance_version DROP NOT NULL;
ALTER TABLE annotation.annotation_submissions ALTER COLUMN base_lance_version DROP NOT NULL;
ALTER TABLE annotation.annotation_task_triggers ALTER COLUMN lance_version DROP NOT NULL;

DO $$
DECLARE name text;
BEGIN
    FOREACH name IN ARRAY ARRAY['annotation_tasks','annotation_revisions','annotation_submissions'] LOOP
        EXECUTE format('ALTER TABLE annotation.%I ADD CONSTRAINT %I CHECK (
            (base_lance_version IS NOT NULL AND data_ref IS NULL) OR
            (base_lance_version IS NULL AND data_ref IS NOT NULL AND jsonb_typeof(data_ref) = ''object''))',
            name, name || '_data_binding');
    END LOOP;
END $$;
ALTER TABLE annotation.annotation_task_triggers ADD CONSTRAINT trigger_data_binding CHECK (
    (lance_version IS NOT NULL AND data_ref IS NULL) OR
    (lance_version IS NULL AND data_ref IS NOT NULL AND jsonb_typeof(data_ref) = 'object')
);
ALTER TABLE annotation.annotation_tasks DROP CONSTRAINT annotation_tasks_creation_source_check;
ALTER TABLE annotation.annotation_tasks ADD CONSTRAINT annotation_tasks_creation_source_check
    CHECK (creation_source IN ('LEGACY', 'SYSTEM_LANCE', 'SYSTEM_EPISODE'));
ALTER TABLE annotation.annotation_tasks ADD CONSTRAINT episode_creation_source CHECK (
    (data_ref IS NOT NULL) = (creation_source = 'SYSTEM_EPISODE')
);
CREATE UNIQUE INDEX annotation_tasks_episode_target_uidx
ON annotation.annotation_tasks(organization_id, project_id, region_code, dataset_id,
    rollout_id, dataset_version, (data_ref->>'data_version_id'), task_kind)
WHERE data_ref IS NOT NULL;
CREATE UNIQUE INDEX annotation_triggers_episode_target_uidx
ON annotation.annotation_task_triggers(organization_id, project_id, region_code, dataset_id,
    rollout_id, dataset_version, (data_ref->>'data_version_id'), task_kind)
WHERE data_ref IS NOT NULL;

-- Includes the entire immutable receipt, source identity, schema, frame count,
-- path and hash. A known version ID alone never authorizes a task target.
CREATE FUNCTION annotation.verify_episode_binding() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND (
        NEW.data_ref IS DISTINCT FROM OLD.data_ref OR
        NEW.organization_id IS DISTINCT FROM OLD.organization_id OR
        NEW.project_id IS DISTINCT FROM OLD.project_id OR
        NEW.region_code IS DISTINCT FROM OLD.region_code OR
        NEW.dataset_id IS DISTINCT FROM OLD.dataset_id OR
        NEW.rollout_id IS DISTINCT FROM OLD.rollout_id OR
        NEW.dataset_version IS DISTINCT FROM OLD.dataset_version
    ) THEN
        RAISE EXCEPTION 'annotation data target is immutable' USING ERRCODE = '23514';
    END IF;
    IF NEW.data_ref IS NULL THEN RETURN NEW; END IF;
    IF NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.organization_id', true), '')
       OR NEW.project_id IS DISTINCT FROM NULLIF(current_setting('app.project_id', true), '')
       OR NEW.region_code IS DISTINCT FROM NULLIF(current_setting('app.region_code', true), '') THEN
        RAISE EXCEPTION 'annotation Episode scope mismatch' USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM episode_store.versions v
        WHERE v.organization_id = NEW.organization_id AND v.project_id = NEW.project_id
          AND v.region_code = NEW.region_code AND v.dataset_id = NEW.dataset_id
          AND v.data_version_id = NEW.data_ref->>'data_version_id'
          AND v.status = 'COMMITTED' AND v.receipt_document->'data' = NEW.data_ref
          AND NEW.data_ref->'identity'->>'rollout_id' = NEW.rollout_id
    ) OR NOT EXISTS (
        SELECT 1 FROM episode_store.collections c,
            LATERAL jsonb_array_elements(c.document->'episodes') member
        WHERE c.organization_id = NEW.organization_id AND c.project_id = NEW.project_id
          AND c.region_code = NEW.region_code AND c.dataset_id = NEW.dataset_id
          AND c.version = NEW.dataset_version AND member = NEW.data_ref
    ) THEN
        RAISE EXCEPTION 'annotation requires a committed Episode in the exact collection'
            USING ERRCODE = '23514';
    END IF;
    IF TG_TABLE_NAME = 'annotation_tasks' THEN
        IF NEW.base_step_count IS DISTINCT FROM (NEW.data_ref->>'frame_count')::bigint THEN
            RAISE EXCEPTION 'annotation frame count mismatch' USING ERRCODE = '23514';
        END IF;
    ELSE
        IF NEW.dataset_schema_snapshot_id IS DISTINCT FROM NEW.data_ref->>'schema_snapshot_id' THEN
            RAISE EXCEPTION 'annotation schema snapshot mismatch' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER annotation_episode_binding BEFORE INSERT OR UPDATE ON annotation.annotation_tasks
    FOR EACH ROW EXECUTE FUNCTION annotation.verify_episode_binding();
CREATE TRIGGER annotation_trigger_episode_binding BEFORE INSERT OR UPDATE ON annotation.annotation_task_triggers
    FOR EACH ROW EXECUTE FUNCTION annotation.verify_episode_binding();

-- Child rows inherit RLS from their owner; exact equality adds a version fence.
CREATE FUNCTION annotation.verify_child_data_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner annotation.annotation_tasks%ROWTYPE;
DECLARE submitted annotation.annotation_submissions%ROWTYPE;
DECLARE revised annotation.annotation_revisions%ROWTYPE;
BEGIN
    SELECT * INTO STRICT owner FROM annotation.annotation_tasks WHERE task_id = NEW.task_id;
    IF NEW.data_ref IS DISTINCT FROM owner.data_ref THEN
        RAISE EXCEPTION 'annotation child data binding mismatch' USING ERRCODE = '23514';
    END IF;
    IF TG_TABLE_NAME = 'annotation_reviews' THEN
        SELECT * INTO STRICT submitted FROM annotation.annotation_submissions
            WHERE task_id = NEW.task_id AND submission_id = NEW.submission_id;
        IF NEW.revision IS DISTINCT FROM submitted.revision THEN
            RAISE EXCEPTION 'review revision differs from submission' USING ERRCODE = '23514';
        END IF;
    ELSE
        IF NEW.base_lance_version IS DISTINCT FROM owner.base_lance_version
           OR NEW.tag_schema_id IS DISTINCT FROM owner.tag_schema_id
           OR NEW.tag_schema_version IS DISTINCT FROM owner.tag_schema_version THEN
            RAISE EXCEPTION 'annotation child baseline mismatch' USING ERRCODE = '23514';
        END IF;
        IF TG_TABLE_NAME = 'annotation_submissions' THEN
            SELECT * INTO STRICT revised FROM annotation.annotation_revisions
                WHERE task_id = NEW.task_id AND revision = NEW.revision;
            IF NEW.revision_content_hash IS DISTINCT FROM revised.content_hash THEN
                RAISE EXCEPTION 'submission revision hash mismatch' USING ERRCODE = '23514';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER annotation_revision_data_binding BEFORE INSERT ON annotation.annotation_revisions
    FOR EACH ROW EXECUTE FUNCTION annotation.verify_child_data_binding();
CREATE TRIGGER annotation_submission_data_binding BEFORE INSERT ON annotation.annotation_submissions
    FOR EACH ROW EXECUTE FUNCTION annotation.verify_child_data_binding();
CREATE TRIGGER annotation_review_data_binding BEFORE INSERT ON annotation.annotation_reviews
    FOR EACH ROW EXECUTE FUNCTION annotation.verify_child_data_binding();

CREATE OR REPLACE VIEW annotation.annotation_current AS
SELECT task_id, current_revision, state_version, status, submitted_revision, submitted_by,
    approved_revision, approved_review_id, etag, updated_at, base_lance_version,
    base_step_count, tag_schema_id, tag_schema_version, current_submission_id, data_ref
FROM annotation.annotation_tasks;
CREATE OR REPLACE VIEW annotation.annotation_drafts AS
SELECT task_id, current_revision AS revision, etag, assignee_id, updated_at,
    base_lance_version, base_step_count, tag_schema_id, tag_schema_version, data_ref
FROM annotation.annotation_tasks WHERE status IN ('DRAFT', 'NEEDS_REVISION', 'REJECTED');
