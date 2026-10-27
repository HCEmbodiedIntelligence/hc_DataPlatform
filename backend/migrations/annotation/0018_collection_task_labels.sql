-- Annotation vocabulary belongs to a collection task, independently of its
-- capture plan and immutable data-format snapshots. Existing annotations keep
-- their pinned schema version; this pointer selects rules for new annotations.
CREATE TABLE annotation.collection_task_tag_schema_bindings (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    collection_task_id text NOT NULL,
    dataset_id text NOT NULL,
    task_kind text NOT NULL CHECK (task_kind IN ('TAGGING')),
    schema_id text NOT NULL,
    schema_version bigint NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (organization_id, project_id, region_code, collection_task_id, task_kind),
    FOREIGN KEY (organization_id, project_id, collection_task_id)
        REFERENCES collection_tasks.collection_tasks
        (organization_id, project_id, collection_task_id) ON DELETE CASCADE,
    FOREIGN KEY (schema_id, schema_version)
        REFERENCES annotation.tag_schema_versions(schema_id, version)
);
SELECT core.apply_project_rls('annotation.collection_task_tag_schema_bindings'::regclass);

CREATE FUNCTION annotation.check_collection_task_labels() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM collection_tasks.collection_tasks t
        JOIN annotation.tag_schema_versions s
          ON s.organization_id=t.organization_id AND s.project_id=t.project_id
        WHERE t.organization_id=NEW.organization_id AND t.project_id=NEW.project_id
          AND t.collection_task_id=NEW.collection_task_id
          AND t.upload_region_code=NEW.region_code AND t.dataset_id=NEW.dataset_id
          AND s.schema_id=NEW.schema_id AND s.version=NEW.schema_version
          AND s.status='PUBLISHED'
    ) THEN
        RAISE EXCEPTION 'collection task label scope or published schema mismatch'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER collection_task_labels_scope BEFORE INSERT OR UPDATE
    ON annotation.collection_task_tag_schema_bindings
    FOR EACH ROW EXECUTE FUNCTION annotation.check_collection_task_labels();

-- Migrate only a task's unambiguous existing vocabulary. Do not select another
-- task's rules or guess between conflicting historical schemas.
INSERT INTO annotation.collection_task_tag_schema_bindings
    (organization_id, project_id, region_code, collection_task_id, dataset_id,
     task_kind, schema_id, schema_version)
SELECT t.organization_id,t.project_id,t.upload_region_code,t.collection_task_id,
       t.dataset_id,b.task_kind,min(b.schema_id),min(b.schema_version)
FROM collection_tasks.collection_tasks t
JOIN annotation.tag_schema_bindings b
  ON (b.organization_id,b.project_id,b.region_code,b.dataset_id)=
     (t.organization_id,t.project_id,t.upload_region_code,t.dataset_id)
JOIN annotation.tag_schema_versions s
  ON (s.organization_id,s.project_id,s.schema_id,s.version)=
     (b.organization_id,b.project_id,b.schema_id,b.schema_version)
WHERE s.status='PUBLISHED'
  AND 1=(SELECT count(*) FROM collection_tasks.collection_tasks owner
         WHERE (owner.organization_id,owner.project_id,owner.upload_region_code,owner.dataset_id)=
               (t.organization_id,t.project_id,t.upload_region_code,t.dataset_id))
GROUP BY t.organization_id,t.project_id,t.upload_region_code,t.collection_task_id,
         t.dataset_id,b.task_kind
HAVING count(DISTINCT (b.schema_id,b.schema_version))=1;

-- Existing task creation and explicit label publication both insert a binding.
-- Promote that choice to its single owning task, without copying it per format.
CREATE FUNCTION annotation.bind_collection_task_labels() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF 1=(SELECT count(*) FROM collection_tasks.collection_tasks t
          WHERE (t.organization_id,t.project_id,t.upload_region_code,t.dataset_id)=
                (NEW.organization_id,NEW.project_id,NEW.region_code,NEW.dataset_id)) THEN
        INSERT INTO annotation.collection_task_tag_schema_bindings
            (organization_id,project_id,region_code,collection_task_id,dataset_id,
             task_kind,schema_id,schema_version)
        SELECT NEW.organization_id,NEW.project_id,NEW.region_code,t.collection_task_id,
               NEW.dataset_id,NEW.task_kind,NEW.schema_id,NEW.schema_version
        FROM collection_tasks.collection_tasks t
        WHERE (t.organization_id,t.project_id,t.upload_region_code,t.dataset_id)=
              (NEW.organization_id,NEW.project_id,NEW.region_code,NEW.dataset_id)
        ON CONFLICT (organization_id,project_id,region_code,collection_task_id,task_kind)
        DO UPDATE SET schema_id=EXCLUDED.schema_id,schema_version=EXCLUDED.schema_version,
                      updated_at=clock_timestamp();
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER tag_schema_collection_task_binding AFTER INSERT ON annotation.tag_schema_bindings
    FOR EACH ROW EXECUTE FUNCTION annotation.bind_collection_task_labels();
