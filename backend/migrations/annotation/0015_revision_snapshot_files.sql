-- New revision + file job + outbox notification share the revision transaction.
-- Historical revisions are not rewritten/backfilled by this schema migration.
CREATE TABLE annotation.revision_files (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    task_id text NOT NULL,
    revision bigint NOT NULL CHECK (revision >= 0),
    revision_content_hash text NOT NULL CHECK (revision_content_hash ~ '^[0-9a-f]{64}$'),
    format_version text NOT NULL DEFAULT 'hc-annotation/1',
    status text NOT NULL CHECK (status IN ('PENDING','GENERATING','READY','FAILED')),
    generation bigint NOT NULL DEFAULT 1 CHECK (generation > 0),
    attempts bigint NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    lease_token uuid,
    lease_expires_at timestamptz,
    object_key text,
    content_sha256 text CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    size_bytes bigint CHECK (size_bytes > 0),
    error_code text CHECK (error_code ~ '^[A-Z0-9_]{1,128}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (task_id, revision, format_version),
    FOREIGN KEY (task_id, revision) REFERENCES annotation.annotation_revisions(task_id, revision),
    CHECK ((status = 'GENERATING') = (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CHECK ((lease_token IS NULL) = (lease_expires_at IS NULL)),
    CHECK (status <> 'READY' OR (object_key IS NOT NULL AND content_sha256 IS NOT NULL AND size_bytes IS NOT NULL)),
    CHECK ((status = 'FAILED') = (error_code IS NOT NULL)),
    CHECK (organization_id <> '' AND project_id <> '' AND region_code <> '')
);
SELECT core.apply_project_rls('annotation.revision_files'::regclass);

CREATE FUNCTION annotation.protect_revision_file_identity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner annotation.annotation_tasks%ROWTYPE;
DECLARE content text;
BEGIN
    SELECT * INTO STRICT owner FROM annotation.annotation_tasks WHERE task_id = NEW.task_id;
    SELECT content_hash INTO STRICT content FROM annotation.annotation_revisions
        WHERE task_id = NEW.task_id AND revision = NEW.revision;
    IF ROW(NEW.organization_id, NEW.project_id, NEW.region_code)
       IS DISTINCT FROM ROW(owner.organization_id, owner.project_id, owner.region_code)
       OR NEW.revision_content_hash IS DISTINCT FROM content
       OR NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.organization_id', true), '')
       OR NEW.project_id IS DISTINCT FROM NULLIF(current_setting('app.project_id', true), '')
       OR NEW.region_code IS DISTINCT FROM NULLIF(current_setting('app.region_code', true), '') THEN
        RAISE EXCEPTION 'annotation file scope or revision mismatch' USING ERRCODE = '42501';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF ROW(NEW.organization_id, NEW.project_id, NEW.region_code, NEW.task_id,
               NEW.revision, NEW.revision_content_hash, NEW.format_version, NEW.created_at)
           IS DISTINCT FROM ROW(OLD.organization_id, OLD.project_id, OLD.region_code, OLD.task_id,
               OLD.revision, OLD.revision_content_hash, OLD.format_version, OLD.created_at)
           OR (OLD.content_sha256 IS NOT NULL AND NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256)
           OR (OLD.size_bytes IS NOT NULL AND NEW.size_bytes IS DISTINCT FROM OLD.size_bytes)
           OR NEW.generation < OLD.generation THEN
            RAISE EXCEPTION 'annotation file content identity is immutable' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER revision_file_identity BEFORE INSERT OR UPDATE ON annotation.revision_files
    FOR EACH ROW EXECUTE FUNCTION annotation.protect_revision_file_identity();

CREATE FUNCTION annotation.enqueue_revision_file_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE event_id uuid;
DECLARE occurred timestamptz := clock_timestamp();
DECLARE aggregate text;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.generation = OLD.generation THEN RETURN NEW; END IF;
    aggregate := NEW.task_id || '/' || NEW.revision || '/' || NEW.format_version;
    event_id := md5('hc-annotation-file/1:' || jsonb_build_array(
        NEW.organization_id, NEW.project_id, NEW.region_code, NEW.task_id,
        NEW.revision, NEW.format_version, NEW.generation)::text)::uuid;
    INSERT INTO core.outbox_events
        (event_id, organization_id, project_id, region_code, event_type, envelope, occurred_at)
    VALUES (event_id, NEW.organization_id, NEW.project_id, NEW.region_code,
        'annotation.revision_file.requested.v1', jsonb_build_object(
            'event_id', event_id::text, 'event_type', 'annotation.revision_file.requested.v1',
            'schema_version', 1, 'aggregate_type', 'annotation_revision_file',
            'aggregate_id', aggregate, 'organization_id', NEW.organization_id,
            'project_id', NEW.project_id, 'region_code', NEW.region_code, 'occurred_at', occurred,
            'payload', jsonb_build_object('task_id', NEW.task_id, 'revision', NEW.revision,
                'format_version', NEW.format_version, 'generation', NEW.generation,
                'revision_content_hash', NEW.revision_content_hash)
        ), occurred);
    RETURN NEW;
END $$;
CREATE TRIGGER revision_file_event AFTER INSERT OR UPDATE ON annotation.revision_files
    FOR EACH ROW EXECUTE FUNCTION annotation.enqueue_revision_file_event();

CREATE FUNCTION annotation.queue_revision_file() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO annotation.revision_files
        (organization_id, project_id, region_code, task_id, revision, revision_content_hash, status)
    SELECT organization_id, project_id, region_code, NEW.task_id, NEW.revision, NEW.content_hash, 'PENDING'
    FROM annotation.annotation_tasks WHERE task_id = NEW.task_id;
    RETURN NEW;
END $$;
CREATE TRIGGER revision_file_pending AFTER INSERT ON annotation.annotation_revisions
    FOR EACH ROW EXECUTE FUNCTION annotation.queue_revision_file();
