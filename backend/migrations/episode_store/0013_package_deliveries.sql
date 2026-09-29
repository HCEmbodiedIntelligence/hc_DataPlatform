-- Complete single/batch independent packages on the existing transactional outbox.
CREATE TABLE episode_store.delivery_jobs (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    job_id text PRIMARY KEY CHECK (job_id ~ '^ed_[0-9a-f]{64}$'),
    plan jsonb NOT NULL,
    actor_id text NOT NULL,
    status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','RUNNING','READY','FAILED','CANCELLED')),
    phase text NOT NULL DEFAULT 'queued' CHECK (phase IN ('queued','verifying','archiving','uploading','completed','failed','cancelled')),
    generation bigint NOT NULL DEFAULT 1 CHECK (generation > 0),
    attempts bigint NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    total_files bigint NOT NULL CHECK (total_files > 0),
    total_bytes bigint NOT NULL CHECK (total_bytes > 0),
    processed_files bigint NOT NULL DEFAULT 0 CHECK (processed_files BETWEEN 0 AND total_files),
    processed_bytes bigint NOT NULL DEFAULT 0 CHECK (processed_bytes BETWEEN 0 AND total_bytes),
    uploaded_bytes bigint NOT NULL DEFAULT 0 CHECK (uploaded_bytes >= 0),
    lease_token uuid,
    lease_expires_at timestamptz,
    artifact_key text,
    artifact_sha256 text CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
    size_bytes bigint CHECK (size_bytes > 0),
    error_code text CHECK (error_code ~ '^[A-Z0-9_]{1,128}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (organization_id <> '' AND project_id <> '' AND region_code <> ''),
    CHECK ((status = 'RUNNING') = (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CHECK ((lease_token IS NULL) = (lease_expires_at IS NULL)),
    CHECK ((status = 'FAILED') = (error_code IS NOT NULL)),
    CHECK ((artifact_key IS NULL) = (artifact_sha256 IS NULL)),
    CHECK ((artifact_sha256 IS NULL) = (size_bytes IS NULL)),
    CHECK (uploaded_bytes <= COALESCE(size_bytes,0)),
    CHECK (status <> 'READY' OR (artifact_sha256 IS NOT NULL AND processed_files = total_files
           AND processed_bytes = total_bytes AND uploaded_bytes = size_bytes)),
    CHECK ((status = 'PENDING' AND phase = 'queued') OR
           (status = 'RUNNING' AND phase IN ('verifying','archiving','uploading')) OR
           (status = 'READY' AND phase = 'completed') OR
           (status = 'FAILED' AND phase = 'failed') OR
           (status = 'CANCELLED' AND phase = 'cancelled'))
);
SELECT core.apply_project_rls('episode_store.delivery_jobs'::regclass);
CREATE INDEX package_delivery_page ON episode_store.delivery_jobs
    (organization_id, project_id, region_code, created_at DESC, job_id DESC);
CREATE INDEX package_delivery_members ON episode_store.delivery_jobs USING gin(plan jsonb_path_ops);
CREATE TABLE episode_store.delivery_requests (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    actor_id text NOT NULL,
    key_sha256 text NOT NULL CHECK (key_sha256 ~ '^[0-9a-f]{64}$'),
    request_sha256 text NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    job_id text NOT NULL REFERENCES episode_store.delivery_jobs(job_id),
    PRIMARY KEY (organization_id, project_id, region_code, actor_id, key_sha256)
);
SELECT core.apply_project_rls('episode_store.delivery_requests'::regclass);

CREATE FUNCTION episode_store.protect_delivery_identity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE member jsonb;
DECLARE parent episode_store.versions%ROWTYPE;
BEGIN
    IF NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.organization_id',true),'')
       OR NEW.project_id IS DISTINCT FROM NULLIF(current_setting('app.project_id',true),'')
       OR NEW.region_code IS DISTINCT FROM NULLIF(current_setting('app.region_code',true),'') THEN
        RAISE EXCEPTION 'package delivery scope mismatch' USING ERRCODE = '42501';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'PENDING' OR NEW.generation <> 1 OR NEW.attempts <> 0
           OR NEW.artifact_key IS NOT NULL OR NEW.processed_bytes <> 0 OR NEW.processed_files <> 0
           OR NEW.plan->>'format' IS DISTINCT FROM 'hc-episode-delivery-plan/1'
           OR NEW.plan->>'modalities' IS DISTINCT FROM 'all_collected'
           OR NEW.plan->>'annotation_selection' IS DISTINCT FROM 'package_contents_only'
           OR jsonb_typeof(NEW.plan->'members') IS DISTINCT FROM 'array'
           OR jsonb_array_length(NEW.plan->'members') NOT BETWEEN 1 AND 100 THEN
            RAISE EXCEPTION 'invalid initial package delivery' USING ERRCODE = '23514';
        END IF;
        FOR member IN SELECT value FROM jsonb_array_elements(NEW.plan->'members') LOOP
            SELECT * INTO parent FROM episode_store.versions
                WHERE organization_id=NEW.organization_id AND project_id=NEW.project_id
                  AND region_code=NEW.region_code AND status='COMMITTED'
                  AND data_version_id=member->'data'->>'data_version_id';
            IF NOT FOUND OR parent.receipt_document->'data' IS DISTINCT FROM member->'data'
               OR parent.receipt_document->>'manifest_sha256' IS DISTINCT FROM member->>'manifest_sha256' THEN
                RAISE EXCEPTION 'delivery requires exact committed package receipts' USING ERRCODE = '23514';
            END IF;
        END LOOP;
    ELSE
        IF ROW(NEW.organization_id,NEW.project_id,NEW.region_code,NEW.job_id,NEW.plan,NEW.actor_id,
               NEW.total_files,NEW.total_bytes,NEW.created_at)
           IS DISTINCT FROM ROW(OLD.organization_id,OLD.project_id,OLD.region_code,OLD.job_id,
               OLD.plan,OLD.actor_id,OLD.total_files,OLD.total_bytes,OLD.created_at)
           OR (OLD.artifact_sha256 IS NOT NULL AND ROW(NEW.artifact_key,NEW.artifact_sha256,NEW.size_bytes)
               IS DISTINCT FROM ROW(OLD.artifact_key,OLD.artifact_sha256,OLD.size_bytes)) THEN
            RAISE EXCEPTION 'package delivery identity/content is immutable' USING ERRCODE = '23514';
        END IF;
        IF NEW.generation <> OLD.generation AND (NEW.generation <> OLD.generation+1
           OR NEW.status <> 'PENDING' OR OLD.status NOT IN ('FAILED','CANCELLED')) THEN
            RAISE EXCEPTION 'invalid package delivery retry' USING ERRCODE = '23514';
        END IF;
        IF OLD.status='READY' AND NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'ready package delivery is immutable' USING ERRCODE = '23514';
        END IF;
        IF NEW.status='READY' AND OLD.status NOT IN ('RUNNING','READY') THEN
            RAISE EXCEPTION 'ready package delivery requires a worker lease' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER package_delivery_identity BEFORE INSERT OR UPDATE ON episode_store.delivery_jobs
    FOR EACH ROW EXECUTE FUNCTION episode_store.protect_delivery_identity();
CREATE FUNCTION episode_store.reject_delivery_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'package delivery record cannot be rewritten or deleted' USING ERRCODE = '23514'; END $$;
CREATE TRIGGER package_delivery_no_delete BEFORE DELETE ON episode_store.delivery_jobs
    FOR EACH ROW EXECUTE FUNCTION episode_store.reject_delivery_change();
CREATE TRIGGER package_delivery_request_immutable BEFORE UPDATE OR DELETE ON episode_store.delivery_requests
    FOR EACH ROW EXECUTE FUNCTION episode_store.reject_delivery_change();
CREATE FUNCTION episode_store.protect_delivery_request_scope() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM episode_store.delivery_jobs j WHERE j.job_id=NEW.job_id
        AND j.organization_id=NEW.organization_id AND j.project_id=NEW.project_id AND j.region_code=NEW.region_code
        AND j.organization_id=NULLIF(current_setting('app.organization_id',true),'')
        AND j.project_id=NULLIF(current_setting('app.project_id',true),'')
        AND j.region_code=NULLIF(current_setting('app.region_code',true),'')) THEN
        RAISE EXCEPTION 'delivery request scope mismatch' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER package_delivery_request_scope BEFORE INSERT ON episode_store.delivery_requests
    FOR EACH ROW EXECUTE FUNCTION episode_store.protect_delivery_request_scope();
CREATE FUNCTION episode_store.enqueue_delivery_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE event_id uuid;
DECLARE occurred timestamptz := clock_timestamp();
BEGIN
    IF TG_OP='UPDATE' AND NEW.generation=OLD.generation THEN RETURN NEW; END IF;
    event_id := md5('hc-episode-delivery/1:' || jsonb_build_array(
        NEW.organization_id,NEW.project_id,NEW.region_code,NEW.job_id,NEW.generation)::text)::uuid;
    INSERT INTO core.outbox_events
        (event_id,organization_id,project_id,region_code,event_type,envelope,occurred_at)
    VALUES (event_id,NEW.organization_id,NEW.project_id,NEW.region_code,'episode.delivery.requested.v1',
        jsonb_build_object('event_id',event_id::text,'event_type','episode.delivery.requested.v1',
            'schema_version',1,'aggregate_type','episode_delivery','aggregate_id',NEW.job_id,
            'organization_id',NEW.organization_id,'project_id',NEW.project_id,'region_code',NEW.region_code,
            'occurred_at',occurred,'payload',jsonb_build_object('job_id',NEW.job_id,'generation',NEW.generation)),occurred);
    RETURN NEW;
END $$;
CREATE TRIGGER package_delivery_event AFTER INSERT OR UPDATE ON episode_store.delivery_jobs
    FOR EACH ROW EXECUTE FUNCTION episode_store.enqueue_delivery_event();
