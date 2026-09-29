-- Additive G1 delivery jobs, using the existing scoped outbox dispatcher.
CREATE TABLE annotation.delivery_jobs (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    task_id text NOT NULL REFERENCES annotation.annotation_tasks(task_id),
    job_id text PRIMARY KEY CHECK (job_id ~ '^ad_[0-9a-f]{64}$'),
    plan jsonb NOT NULL,
    annotation_key text NOT NULL,
    actor_id text NOT NULL,
    status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','RUNNING','READY','FAILED','CANCELLED')),
    phase text NOT NULL DEFAULT 'queued' CHECK (phase IN ('queued','verifying','projecting','archiving','uploading','completed','failed','cancelled')),
    generation bigint NOT NULL DEFAULT 1 CHECK (generation > 0),
    attempts bigint NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    lease_token uuid,
    lease_expires_at timestamptz,
    artifact_key text,
    artifact_sha256 text CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
    size_bytes bigint CHECK (size_bytes > 0),
    manifest_sha256 text CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    error_code text CHECK (error_code ~ '^[A-Z0-9_]{1,128}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (organization_id <> '' AND project_id <> '' AND region_code <> ''),
    CHECK ((status = 'RUNNING') = (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CHECK ((lease_token IS NULL) = (lease_expires_at IS NULL)),
    CHECK ((status = 'FAILED') = (error_code IS NOT NULL)),
    CHECK ((artifact_key IS NULL) = (artifact_sha256 IS NULL)),
    CHECK ((artifact_sha256 IS NULL) = (size_bytes IS NULL)),
    CHECK ((artifact_sha256 IS NULL) = (manifest_sha256 IS NULL)),
    CHECK (status <> 'READY' OR artifact_sha256 IS NOT NULL),
    CHECK ((status = 'PENDING' AND phase = 'queued') OR
           (status = 'RUNNING' AND phase IN ('verifying','projecting','archiving','uploading')) OR
           (status = 'READY' AND phase = 'completed') OR
           (status = 'FAILED' AND phase = 'failed') OR
           (status = 'CANCELLED' AND phase = 'cancelled'))
);
SELECT core.apply_project_rls('annotation.delivery_jobs'::regclass);
CREATE INDEX delivery_task_page ON annotation.delivery_jobs
    (organization_id, project_id, region_code, task_id, created_at DESC, job_id DESC);

CREATE TABLE annotation.delivery_requests (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    task_id text NOT NULL REFERENCES annotation.annotation_tasks(task_id),
    actor_id text NOT NULL,
    key_sha256 text NOT NULL CHECK (key_sha256 ~ '^[0-9a-f]{64}$'),
    request_sha256 text NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    job_id text NOT NULL REFERENCES annotation.delivery_jobs(job_id),
    PRIMARY KEY (organization_id, project_id, region_code, task_id, actor_id, key_sha256)
);
SELECT core.apply_project_rls('annotation.delivery_requests'::regclass);

CREATE FUNCTION annotation.protect_delivery_identity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner annotation.annotation_tasks%ROWTYPE;
DECLARE snapshot annotation.revision_files%ROWTYPE;
BEGIN
    SELECT * INTO STRICT owner FROM annotation.annotation_tasks WHERE task_id = NEW.task_id;
    IF ROW(NEW.organization_id, NEW.project_id, NEW.region_code)
       IS DISTINCT FROM ROW(owner.organization_id, owner.project_id, owner.region_code)
       OR NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.organization_id', true), '')
       OR NEW.project_id IS DISTINCT FROM NULLIF(current_setting('app.project_id', true), '')
       OR NEW.region_code IS DISTINCT FROM NULLIF(current_setting('app.region_code', true), '')
       OR NEW.plan->'binding'->>'task_id' IS DISTINCT FROM NEW.task_id
       OR owner.data_ref IS NULL OR NEW.plan->'binding'->'data' IS DISTINCT FROM owner.data_ref THEN
        RAISE EXCEPTION 'delivery task/data/scope mismatch' USING ERRCODE = '42501';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'PENDING' OR NEW.generation <> 1 OR NEW.attempts <> 0 THEN
            RAISE EXCEPTION 'new delivery must be pending' USING ERRCODE = '23514';
        END IF;
        SELECT * INTO STRICT snapshot FROM annotation.revision_files
            WHERE task_id = NEW.task_id AND revision = (NEW.plan->'binding'->>'revision')::bigint
            AND format_version = 'hc-annotation/1';
        IF snapshot.status <> 'READY' OR snapshot.object_key IS DISTINCT FROM NEW.annotation_key
           OR snapshot.content_sha256 IS DISTINCT FROM NEW.plan->'binding'->>'annotation_file_sha256'
           OR snapshot.revision_content_hash IS DISTINCT FROM NEW.plan->'binding'->>'revision_content_hash'
           OR snapshot.size_bytes IS DISTINCT FROM (NEW.plan->>'annotation_size')::bigint THEN
            RAISE EXCEPTION 'delivery snapshot mismatch' USING ERRCODE = '23514';
        END IF;
    ELSE
        IF ROW(NEW.organization_id, NEW.project_id, NEW.region_code, NEW.task_id, NEW.job_id,
               NEW.plan, NEW.annotation_key, NEW.actor_id, NEW.created_at)
           IS DISTINCT FROM ROW(OLD.organization_id, OLD.project_id, OLD.region_code, OLD.task_id,
               OLD.job_id, OLD.plan, OLD.annotation_key, OLD.actor_id, OLD.created_at)
           OR (OLD.artifact_sha256 IS NOT NULL AND ROW(NEW.artifact_key, NEW.artifact_sha256,
               NEW.size_bytes, NEW.manifest_sha256) IS DISTINCT FROM ROW(OLD.artifact_key,
               OLD.artifact_sha256, OLD.size_bytes, OLD.manifest_sha256)) THEN
            RAISE EXCEPTION 'delivery identity/content is immutable' USING ERRCODE = '23514';
        END IF;
        IF NEW.generation IS DISTINCT FROM OLD.generation AND (
            NEW.generation <> OLD.generation + 1 OR NEW.status <> 'PENDING'
            OR OLD.status NOT IN ('FAILED','CANCELLED')
        ) THEN
            RAISE EXCEPTION 'delivery retry generation conflict' USING ERRCODE = '23514';
        END IF;
        IF OLD.status = 'READY' AND NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'ready delivery is immutable' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER delivery_identity BEFORE INSERT OR UPDATE ON annotation.delivery_jobs
    FOR EACH ROW EXECUTE FUNCTION annotation.protect_delivery_identity();
CREATE TRIGGER delivery_no_delete BEFORE DELETE ON annotation.delivery_jobs
    FOR EACH ROW EXECUTE FUNCTION annotation.reject_immutable_change();
CREATE TRIGGER delivery_request_immutable BEFORE UPDATE OR DELETE ON annotation.delivery_requests
    FOR EACH ROW EXECUTE FUNCTION annotation.reject_immutable_change();

CREATE FUNCTION annotation.protect_delivery_request_scope() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM annotation.delivery_jobs j WHERE j.job_id = NEW.job_id
        AND j.task_id = NEW.task_id AND j.organization_id = NEW.organization_id
        AND j.project_id = NEW.project_id AND j.region_code = NEW.region_code
        AND j.organization_id = NULLIF(current_setting('app.organization_id', true), '')
        AND j.project_id = NULLIF(current_setting('app.project_id', true), '')
        AND j.region_code = NULLIF(current_setting('app.region_code', true), '')
    ) THEN
        RAISE EXCEPTION 'delivery request scope mismatch' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER delivery_request_scope BEFORE INSERT ON annotation.delivery_requests
    FOR EACH ROW EXECUTE FUNCTION annotation.protect_delivery_request_scope();

CREATE FUNCTION annotation.enqueue_delivery_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE event_id uuid;
DECLARE occurred timestamptz := clock_timestamp();
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.generation = OLD.generation THEN RETURN NEW; END IF;
    event_id := md5('hc-annotation-delivery/1:' || jsonb_build_array(
        NEW.organization_id, NEW.project_id, NEW.region_code, NEW.job_id, NEW.generation)::text)::uuid;
    INSERT INTO core.outbox_events
        (event_id, organization_id, project_id, region_code, event_type, envelope, occurred_at)
    VALUES (event_id, NEW.organization_id, NEW.project_id, NEW.region_code,
        'annotation.delivery.requested.v1', jsonb_build_object(
            'event_id', event_id::text, 'event_type', 'annotation.delivery.requested.v1',
            'schema_version', 1, 'aggregate_type', 'annotation_delivery',
            'aggregate_id', NEW.job_id, 'organization_id', NEW.organization_id,
            'project_id', NEW.project_id, 'region_code', NEW.region_code, 'occurred_at', occurred,
            'payload', jsonb_build_object('job_id', NEW.job_id, 'task_id', NEW.task_id,
                'generation', NEW.generation)
        ), occurred);
    RETURN NEW;
END $$;
CREATE TRIGGER delivery_event AFTER INSERT OR UPDATE ON annotation.delivery_jobs
    FOR EACH ROW EXECUTE FUNCTION annotation.enqueue_delivery_event();
