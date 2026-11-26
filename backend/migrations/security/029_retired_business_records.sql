-- An authorizing row must be claimed by THIS transaction after its deadline.
-- There are no exceptions for audit, integrity, inventory or legal-hold tables.
ALTER TABLE access_control.retired_scopes ADD COLUMN cleanup_xid xid8;

CREATE FUNCTION access_control.due_scope_owns(org text, project text) RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
    SELECT EXISTS (SELECT 1 FROM access_control.retired_scopes r
        WHERE r.organization_id=org AND (r.project_id='' OR r.project_id=project)
        AND r.status='DELETED' AND r.purge_after<=now() AND r.completed_at IS NULL
        AND r.cleanup_xid=pg_current_xact_id_if_assigned())
$$;

CREATE FUNCTION access_control.retired_business_record(rel text, data jsonb) RETURNS boolean
LANGUAGE plpgsql STABLE SET search_path=pg_catalog AS $$
BEGIN
    IF current_setting('app.retired_directory_cleanup',true) IS DISTINCT FROM 'true' THEN RETURN false; END IF;
    IF rel IN ('annotation.annotation_revisions','annotation.annotation_operations','annotation.annotation_reviews',
        'annotation.annotation_mutations','annotation.annotation_submissions','annotation.annotation_submission_mutations') THEN
        RETURN EXISTS(SELECT 1 FROM annotation.annotation_tasks a WHERE a.task_id=data->>'task_id'
            AND access_control.due_scope_owns(a.organization_id,a.project_id));
    END IF;
    IF split_part(rel,'.',1) NOT IN ('annotation','aligned_media','episode_store','ingest','manual_cleaning','public','publishing','robotics')
        OR rel LIKE '%audit%' THEN RETURN false; END IF;
    RETURN access_control.due_scope_owns(data->>'organization_id',data->>'project_id');
END $$;

CREATE OR REPLACE FUNCTION collection_tasks.purge_permitted(rel text, row_data jsonb)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM collection_tasks.deletion_targets t
        JOIN collection_tasks.deletions d USING (organization_id,project_id,collection_task_id)
        WHERE t.organization_id=current_setting('app.organization_id',true)
          AND t.project_id=current_setting('app.project_id',true)
          AND t.collection_task_id=current_setting('app.collection_task_purge_id',true)
          AND d.state IN ('DELETING','FAILED') AND t.relation=rel
          AND row_data @> t.selector
    ) OR access_control.retired_business_record(rel,row_data)
$$;

CREATE OR REPLACE FUNCTION annotation.protect_tag_schema_version()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP='DELETE' AND access_control.retired_business_record('annotation.tag_schema_versions',to_jsonb(OLD)) THEN RETURN OLD; END IF;
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
