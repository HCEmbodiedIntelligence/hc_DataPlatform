-- Explicit Tag interval -> new primary identity; cancellation never hides started publication.
CREATE TABLE episode_store.tag_derivation_jobs (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    job_id text PRIMARY KEY CHECK (job_id ~ '^td_[0-9a-f]{64}$'),
    plan jsonb NOT NULL,
    actor_id text NOT NULL,
    task_id text NOT NULL,
    episode_id text NOT NULL,
    data_version_id text NOT NULL CHECK (data_version_id ~ '^dv_[0-9a-f]{64}$'),
    status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','RUNNING','READY','FAILED','CANCELLED')),
    phase text NOT NULL DEFAULT 'queued',
    generation bigint NOT NULL DEFAULT 1 CHECK (generation > 0),
    attempts bigint NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    lease_token uuid,
    lease_expires_at timestamptz,
    committed_data jsonb,
    collection_version_id text,
    viewer_target jsonb,
    annotation_task_id text,
    error_code text CHECK (error_code ~ '^[A-Z0-9_]{1,128}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (organization_id <> '' AND project_id <> '' AND region_code <> ''),
    CHECK ((status='RUNNING')=(lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CHECK ((lease_token IS NULL)=(lease_expires_at IS NULL)),
    CHECK ((status='FAILED')=(error_code IS NOT NULL)),
    CHECK ((collection_version_id IS NULL)=(viewer_target IS NULL)),
    CHECK (viewer_target IS NULL OR committed_data IS NOT NULL),
    CHECK (annotation_task_id IS NULL OR viewer_target IS NOT NULL),
    CHECK (status <> 'READY' OR annotation_task_id IS NOT NULL),
    CHECK (status <> 'CANCELLED' OR (attempts=0 AND committed_data IS NULL)),
    CHECK ((status='PENDING' AND phase='queued') OR
           (status='RUNNING' AND phase IN ('verifying','deriving','committing','projecting','annotating')) OR
           (status='READY' AND phase='completed') OR (status='FAILED' AND phase='failed') OR
           (status='CANCELLED' AND phase='cancelled'))
);
SELECT core.apply_project_rls('episode_store.tag_derivation_jobs'::regclass);
CREATE INDEX tag_derivation_page ON episode_store.tag_derivation_jobs
    (organization_id,project_id,region_code,task_id,created_at DESC,job_id DESC);
CREATE TABLE episode_store.tag_derivation_requests (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    task_id text NOT NULL,
    actor_id text NOT NULL,
    key_sha256 text NOT NULL CHECK (key_sha256 ~ '^[0-9a-f]{64}$'),
    request_sha256 text NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    job_id text NOT NULL REFERENCES episode_store.tag_derivation_jobs(job_id),
    PRIMARY KEY (organization_id,project_id,region_code,task_id,actor_id,key_sha256)
);
SELECT core.apply_project_rls('episode_store.tag_derivation_requests'::regclass);

CREATE FUNCTION episode_store.protect_tag_derivation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent episode_store.versions%ROWTYPE;
DECLARE child episode_store.versions%ROWTYPE;
BEGIN
    IF NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.organization_id',true),'')
       OR NEW.project_id IS DISTINCT FROM NULLIF(current_setting('app.project_id',true),'')
       OR NEW.region_code IS DISTINCT FROM NULLIF(current_setting('app.region_code',true),'') THEN
        RAISE EXCEPTION 'Tag derivation scope mismatch' USING ERRCODE='42501';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.status <> 'PENDING' OR NEW.generation <> 1 OR NEW.attempts <> 0
           OR NEW.committed_data IS NOT NULL OR NEW.viewer_target IS NOT NULL
           OR NEW.plan->>'format' IS DISTINCT FROM 'hc-tag-derivation-job/1'
           OR NEW.plan->'episode'->>'format' IS DISTINCT FROM 'hc-tag-episode-plan/1'
           OR NEW.task_id IS DISTINCT FROM NEW.plan->'episode'->'annotation'->'binding'->>'task_id'
           OR NEW.episode_id=NEW.plan->'episode'->'parent'->'identity'->>'episode_id' THEN
            RAISE EXCEPTION 'invalid initial Tag derivation' USING ERRCODE='23514';
        END IF;
        SELECT * INTO parent FROM episode_store.versions
        WHERE organization_id=NEW.organization_id AND project_id=NEW.project_id
          AND region_code=NEW.region_code AND status='COMMITTED'
          AND data_version_id=NEW.plan->'episode'->'parent'->>'data_version_id';
        IF NOT FOUND OR parent.receipt_document->'data' IS DISTINCT FROM NEW.plan->'episode'->'parent'
           OR NOT EXISTS (SELECT 1 FROM annotation.annotation_tasks t WHERE t.task_id::text=NEW.task_id
             AND t.organization_id=NEW.organization_id AND t.project_id=NEW.project_id
             AND t.region_code=NEW.region_code AND t.data_ref=parent.receipt_document->'data') THEN
            RAISE EXCEPTION 'Tag requires exact committed parent and annotation task' USING ERRCODE='23514';
        END IF;
    ELSE
        IF ROW(NEW.organization_id,NEW.project_id,NEW.region_code,NEW.job_id,NEW.plan,NEW.actor_id,
               NEW.task_id,NEW.episode_id,NEW.data_version_id,NEW.created_at)
           IS DISTINCT FROM ROW(OLD.organization_id,OLD.project_id,OLD.region_code,OLD.job_id,OLD.plan,
               OLD.actor_id,OLD.task_id,OLD.episode_id,OLD.data_version_id,OLD.created_at)
           OR (OLD.committed_data IS NOT NULL AND NEW.committed_data IS DISTINCT FROM OLD.committed_data)
           OR (OLD.viewer_target IS NOT NULL AND ROW(NEW.viewer_target,NEW.collection_version_id)
               IS DISTINCT FROM ROW(OLD.viewer_target,OLD.collection_version_id))
           OR (OLD.annotation_task_id IS NOT NULL AND NEW.annotation_task_id IS DISTINCT FROM OLD.annotation_task_id) THEN
            RAISE EXCEPTION 'Tag plan and publication receipts are immutable' USING ERRCODE='23514';
        END IF;
        IF NEW.generation <> OLD.generation AND (NEW.generation <> OLD.generation+1
           OR NEW.status <> 'PENDING' OR OLD.status NOT IN ('FAILED','CANCELLED')) THEN
            RAISE EXCEPTION 'invalid Tag retry' USING ERRCODE='23514';
        END IF;
        IF NEW.status='PENDING' AND OLD.status<>'PENDING' AND NEW.generation=OLD.generation THEN
            RAISE EXCEPTION 'Tag retry requires a new generation' USING ERRCODE='23514';
        END IF;
        IF NEW.status='CANCELLED' AND OLD.status NOT IN ('PENDING','CANCELLED') THEN
            RAISE EXCEPTION 'started Tag publication cannot be cancelled' USING ERRCODE='23514';
        END IF;
        IF OLD.status='READY' AND NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'ready Tag publication is immutable' USING ERRCODE='23514';
        END IF;
        IF NEW.status='READY' AND OLD.status<>'RUNNING' THEN
            RAISE EXCEPTION 'Tag READY requires a worker lease' USING ERRCODE='23514';
        END IF;
    END IF;
    IF NEW.status IN ('RUNNING','READY','FAILED') AND
       current_setting('app.service_identity',true) IS DISTINCT FROM 'true' THEN
        RAISE EXCEPTION 'Tag processing requires worker identity' USING ERRCODE='42501';
    END IF;
    IF NEW.committed_data IS NOT NULL THEN
        SELECT * INTO child FROM episode_store.versions
        WHERE organization_id=NEW.organization_id AND project_id=NEW.project_id
          AND region_code=NEW.region_code AND episode_id=NEW.episode_id
          AND data_version_id=NEW.data_version_id AND status='COMMITTED';
        IF NOT FOUND OR child.receipt_document->'data' IS DISTINCT FROM NEW.committed_data THEN
            RAISE EXCEPTION 'Tag result requires its exact primary commit' USING ERRCODE='23514';
        END IF;
    END IF;
    IF NEW.viewer_target IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM dataset_registry.dataset_version_episode_revisions r
        JOIN episode_store.collections c ON c.organization_id=r.organization_id
          AND c.project_id=r.project_id AND c.region_code=r.region_code AND c.dataset_id=r.dataset_id
          AND 'version_'||c.collection_version_id=r.version_id
        WHERE r.organization_id=NEW.organization_id AND r.project_id=NEW.project_id
          AND r.region_code=NEW.region_code AND r.episode_id=NEW.episode_id
          AND r.dataset_id=NEW.viewer_target->>'dataset_id'
          AND r.version_id=NEW.viewer_target->>'version_id'
          AND r.revision_id=NEW.viewer_target->>'revision_id'
          AND r.episode_id=NEW.viewer_target->>'episode_id'
          AND c.collection_version_id=NEW.collection_version_id
          AND r.revision_document->>'content_sha256'=NEW.committed_data->>'content_hash') THEN
        RAISE EXCEPTION 'Tag result requires its exact product projection' USING ERRCODE='23514';
    END IF;
    IF NEW.annotation_task_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM annotation.annotation_tasks t WHERE t.task_id::text=NEW.annotation_task_id
          AND t.organization_id=NEW.organization_id AND t.project_id=NEW.project_id
          AND t.region_code=NEW.region_code AND t.data_ref=NEW.committed_data) THEN
        RAISE EXCEPTION 'Tag result requires its own annotation task' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER tag_derivation_identity BEFORE INSERT OR UPDATE ON episode_store.tag_derivation_jobs
    FOR EACH ROW EXECUTE FUNCTION episode_store.protect_tag_derivation();
CREATE TRIGGER tag_derivation_no_delete BEFORE DELETE ON episode_store.tag_derivation_jobs
    FOR EACH ROW EXECUTE FUNCTION episode_store.reject_delivery_change();
CREATE TRIGGER tag_derivation_request_immutable BEFORE UPDATE OR DELETE ON episode_store.tag_derivation_requests
    FOR EACH ROW EXECUTE FUNCTION episode_store.reject_delivery_change();
CREATE FUNCTION episode_store.protect_tag_request() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM episode_store.tag_derivation_jobs j WHERE j.job_id=NEW.job_id
        AND j.task_id=NEW.task_id AND j.organization_id=NEW.organization_id
        AND j.project_id=NEW.project_id AND j.region_code=NEW.region_code
        AND j.organization_id=NULLIF(current_setting('app.organization_id',true),'')
        AND j.project_id=NULLIF(current_setting('app.project_id',true),'')
        AND j.region_code=NULLIF(current_setting('app.region_code',true),'')) THEN
        RAISE EXCEPTION 'Tag request scope mismatch' USING ERRCODE='42501';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER tag_derivation_request_scope BEFORE INSERT ON episode_store.tag_derivation_requests
    FOR EACH ROW EXECUTE FUNCTION episode_store.protect_tag_request();
CREATE FUNCTION episode_store.enqueue_tag_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE event_id uuid;
DECLARE occurred timestamptz := clock_timestamp();
BEGIN
    IF TG_OP='UPDATE' AND NEW.generation=OLD.generation THEN RETURN NEW; END IF;
    event_id := md5('hc-tag-episode/1:'||jsonb_build_array(
        NEW.organization_id,NEW.project_id,NEW.region_code,NEW.job_id,NEW.generation)::text)::uuid;
    INSERT INTO core.outbox_events
        (event_id,organization_id,project_id,region_code,event_type,envelope,occurred_at)
    VALUES (event_id,NEW.organization_id,NEW.project_id,NEW.region_code,'episode.tag.requested.v1',
        jsonb_build_object('event_id',event_id::text,'event_type','episode.tag.requested.v1',
            'schema_version',1,'aggregate_type','tag_episode','aggregate_id',NEW.job_id,
            'organization_id',NEW.organization_id,'project_id',NEW.project_id,'region_code',NEW.region_code,
            'occurred_at',occurred,'payload',jsonb_build_object('job_id',NEW.job_id,'generation',NEW.generation)),occurred);
    RETURN NEW;
END $$;
CREATE TRIGGER tag_derivation_event AFTER INSERT OR UPDATE ON episode_store.tag_derivation_jobs
    FOR EACH ROW EXECUTE FUNCTION episode_store.enqueue_tag_event();
