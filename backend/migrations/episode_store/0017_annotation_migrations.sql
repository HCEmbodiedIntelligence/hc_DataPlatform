-- Append-only equivalence mapping; original annotation hashes/rows remain intact.
CREATE TABLE episode_store.annotation_migrations (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    mapping_id text NOT NULL CHECK (mapping_id ~ '^am_[0-9a-f]{64}$'),
    batch_id text NOT NULL,
    source_task_id text NOT NULL REFERENCES annotation.annotation_tasks(task_id),
    target_task_id text NOT NULL,
    target_data jsonb NOT NULL,
    plan jsonb NOT NULL,
    status text NOT NULL DEFAULT 'PREPARED' CHECK (status IN ('PREPARED','APPLIED')),
    result jsonb,
    document_key text NOT NULL,
    document_sha256 text CHECK (document_sha256 ~ '^[0-9a-f]{64}$'),
    document_size bigint CHECK (document_size BETWEEN 1 AND 16777216),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    applied_at timestamptz,
    PRIMARY KEY (organization_id,project_id,region_code,dataset_id,mapping_id),
    UNIQUE (target_task_id),
    FOREIGN KEY (organization_id,project_id,region_code,dataset_id,batch_id)
        REFERENCES episode_store.migration_batches,
    CHECK ((status='APPLIED')=(result IS NOT NULL)),
    CHECK ((status='APPLIED')=(document_sha256 IS NOT NULL)),
    CHECK ((status='APPLIED')=(document_size IS NOT NULL)),
    CHECK ((status='APPLIED')=(applied_at IS NOT NULL))
);
SELECT core.apply_project_rls('episode_store.annotation_migrations'::regclass);

CREATE FUNCTION episode_store.guard_annotation_migration() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE original jsonb;
BEGIN
    IF current_setting('app.service_identity',true) IS DISTINCT FROM 'true'
       OR NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.organization_id',true),'')
       OR NEW.project_id IS DISTINCT FROM NULLIF(current_setting('app.project_id',true),'')
       OR NEW.region_code IS DISTINCT FROM NULLIF(current_setting('app.region_code',true),'') THEN
        RAISE EXCEPTION 'annotation migration requires exact operator scope' USING ERRCODE='42501';
    END IF;
    IF TG_OP='UPDATE' THEN
        IF OLD.status<>'PREPARED' OR NEW.status<>'APPLIED'
           OR ROW(NEW.organization_id,NEW.project_id,NEW.region_code,NEW.dataset_id,NEW.mapping_id,
                  NEW.batch_id,NEW.source_task_id,NEW.target_task_id,NEW.target_data,NEW.plan,
                  NEW.document_key,NEW.created_at) IS DISTINCT FROM
              ROW(OLD.organization_id,OLD.project_id,OLD.region_code,OLD.dataset_id,OLD.mapping_id,
                  OLD.batch_id,OLD.source_task_id,OLD.target_task_id,OLD.target_data,OLD.plan,
                  OLD.document_key,OLD.created_at) THEN
            RAISE EXCEPTION 'annotation migration plan/receipt is immutable' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.status<>'PREPARED' THEN
        RAISE EXCEPTION 'annotation migration must start prepared' USING ERRCODE='23514';
    END IF;
    IF NEW.plan->>'format' IS DISTINCT FROM 'hc-annotation-migration-plan/1'
       OR NEW.plan->>'mapping_id' IS DISTINCT FROM NEW.mapping_id
       OR NEW.plan->>'batch_id' IS DISTINCT FROM NEW.batch_id
       OR NEW.plan->>'source_task_id' IS DISTINCT FROM NEW.source_task_id
       OR NEW.plan->>'target_task_id' IS DISTINCT FROM NEW.target_task_id
       OR NEW.plan->'target_data' IS DISTINCT FROM NEW.target_data
       OR NEW.plan->'scope' IS DISTINCT FROM jsonb_build_array(
            NEW.organization_id,NEW.project_id,NEW.region_code,NEW.dataset_id)
       OR NOT EXISTS (
           SELECT 1 FROM episode_store.migration_batches b,
               LATERAL jsonb_array_elements(b.plan->'members') member
           WHERE b.organization_id=NEW.organization_id AND b.project_id=NEW.project_id
             AND b.region_code=NEW.region_code AND b.dataset_id=NEW.dataset_id
             AND b.batch_id=NEW.batch_id AND b.status IN ('PUBLISHED','ROLLED_BACK')
             AND b.plan->'collection'->>'collection_version_id'
                 =NEW.plan->>'collection_version_id'
             AND member->'receipt'->'data'=NEW.target_data
             AND member->'plan'->'legacy'->>'content_hash'
                 =NEW.plan->'source_data'->>'content_hash'
       ) OR NOT EXISTS (
           SELECT 1 FROM episode_store.versions v
           WHERE v.organization_id=NEW.organization_id AND v.project_id=NEW.project_id
             AND v.region_code=NEW.region_code AND v.dataset_id=NEW.dataset_id
             AND v.status='COMMITTED' AND v.receipt_document->'data'=NEW.target_data
       ) THEN
        RAISE EXCEPTION 'annotation migration lacks verified batch/package binding' USING ERRCODE='23514';
    END IF;
    SELECT to_jsonb(t) INTO original FROM annotation.annotation_tasks t
        WHERE t.task_id=NEW.source_task_id AND t.organization_id=NEW.organization_id
          AND t.project_id=NEW.project_id AND t.region_code=NEW.region_code
          AND t.dataset_id=NEW.dataset_id AND t.data_ref IS NULL
          AND t.rollout_id=NEW.target_data->'identity'->>'rollout_id';
    IF original IS NULL OR original IS DISTINCT FROM NEW.plan->'source_snapshot'->'task' THEN
        RAISE EXCEPTION 'original annotation task differs from frozen snapshot' USING ERRCODE='23514';
    END IF;
    IF NEW.status='APPLIED' AND (
        NEW.result->>'mapping_id' IS DISTINCT FROM NEW.mapping_id
        OR NEW.result->>'target_task_id' IS DISTINCT FROM NEW.target_task_id
        OR NEW.result->'target_data' IS DISTINCT FROM NEW.target_data
        OR NEW.result->>'review_automatically_transferred' IS DISTINCT FROM 'false'
        OR NOT EXISTS (
            SELECT 1 FROM annotation.annotation_tasks t
            JOIN annotation.annotation_revisions r ON r.task_id=t.task_id AND r.revision=1
            WHERE t.task_id=NEW.target_task_id AND t.data_ref=NEW.target_data
              AND t.current_revision=1 AND t.status='DRAFT' AND t.base_lance_version IS NULL
              AND t.current_submission_id IS NULL AND t.approved_review_id IS NULL
              AND t.dataset_version=(NEW.plan->>'dataset_version')::bigint
              AND t.creation_source='SYSTEM_EPISODE'
              AND t.source_workflow_id='annotation-migration:'||NEW.mapping_id
              AND r.content_hash=NEW.result->>'target_revision_content_hash'
              AND r.origin='ANNOTATION_RESTORE'
        )
    ) THEN
        RAISE EXCEPTION 'annotation migration requires atomic independent draft' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER annotation_migration_guard BEFORE INSERT OR UPDATE ON episode_store.annotation_migrations
    FOR EACH ROW EXECUTE FUNCTION episode_store.guard_annotation_migration();
CREATE TRIGGER annotation_migration_no_delete BEFORE DELETE ON episode_store.annotation_migrations
    FOR EACH ROW EXECUTE FUNCTION episode_store.reject_delivery_change();
