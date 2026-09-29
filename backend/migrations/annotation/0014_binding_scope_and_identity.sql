-- Schema compatibility is selected within the organization, including when two
-- organizations use the same project/dataset/snapshot labels. No content rewrite.
ALTER TABLE annotation.tag_schema_bindings DROP CONSTRAINT tag_schema_bindings_pkey;
ALTER TABLE annotation.tag_schema_bindings ADD PRIMARY KEY (
    organization_id, project_id, region_code, dataset_id, dataset_schema_snapshot_id, task_kind
);
CREATE FUNCTION annotation.protect_task_baseline() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.base_lance_version, NEW.base_step_count, NEW.tag_schema_id,
           NEW.tag_schema_version, NEW.creation_source, NEW.source_workflow_id, NEW.task_kind)
       IS DISTINCT FROM
       ROW(OLD.base_lance_version, OLD.base_step_count, OLD.tag_schema_id,
           OLD.tag_schema_version, OLD.creation_source, OLD.source_workflow_id, OLD.task_kind) THEN
        RAISE EXCEPTION 'annotation baseline is immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER annotation_task_baseline BEFORE UPDATE ON annotation.annotation_tasks
    FOR EACH ROW EXECUTE FUNCTION annotation.protect_task_baseline();
