-- Additive G1 schema. Legacy Lance/annotation identities remain untouched.
CREATE SCHEMA IF NOT EXISTS episode_store;
CREATE TABLE episode_store.bindings (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    episode_id text NOT NULL,
    rollout_id text NOT NULL,
    current_data_version_id text,
    PRIMARY KEY (organization_id, project_id, region_code, dataset_id, episode_id),
    UNIQUE (organization_id, project_id, region_code, dataset_id, rollout_id)
);
CREATE TABLE episode_store.versions (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    episode_id text NOT NULL,
    data_version_id text NOT NULL,
    version bigint NOT NULL CHECK (version > 0),
    idempotency_key text NOT NULL CHECK (idempotency_key ~ '^[0-9a-f]{64}$'),
    input_document jsonb NOT NULL,
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    status text NOT NULL CHECK (status IN ('PREPARING', 'COMMITTED')),
    receipt_document jsonb,
    committed_at timestamptz,
    PRIMARY KEY (organization_id, project_id, region_code, data_version_id),
    UNIQUE (organization_id, project_id, region_code, dataset_id, episode_id, version),
    UNIQUE (organization_id, project_id, region_code, idempotency_key),
    UNIQUE (organization_id, project_id, region_code, dataset_id, episode_id, data_version_id),
    FOREIGN KEY (organization_id, project_id, region_code, dataset_id, episode_id)
        REFERENCES episode_store.bindings,
    CHECK ((status = 'COMMITTED') = (receipt_document IS NOT NULL AND committed_at IS NOT NULL))
);
ALTER TABLE episode_store.bindings ADD CONSTRAINT current_episode_data_version
    FOREIGN KEY (organization_id, project_id, region_code, dataset_id, episode_id, current_data_version_id)
        REFERENCES episode_store.versions
            (organization_id, project_id, region_code, dataset_id, episode_id, data_version_id);
CREATE TABLE episode_store.attempts (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    episode_id text NOT NULL,
    data_version_id text NOT NULL,
    attempt_id uuid NOT NULL,
    status text NOT NULL CHECK (status IN ('PREPARING', 'OBJECTS_VERIFIED', 'COMMITTED', 'FAILED')),
    error_code text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (organization_id, project_id, region_code, attempt_id),
    FOREIGN KEY (organization_id, project_id, region_code, dataset_id, episode_id, data_version_id)
        REFERENCES episode_store.versions
            (organization_id, project_id, region_code, dataset_id, episode_id, data_version_id)
);
-- A dedicated transactional event waits for W2 projections. Existing Temporal
-- histories are not renamed or routed to a new handler by this schema alone.
CREATE TABLE episode_store.events (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    episode_id text NOT NULL,
    data_version_id text NOT NULL,
    event_type text NOT NULL DEFAULT 'EPISODE_PACKAGE_COMMITTED',
    payload jsonb NOT NULL,
    delivered_at timestamptz,
    PRIMARY KEY (organization_id, project_id, region_code, data_version_id, event_type),
    FOREIGN KEY (organization_id, project_id, region_code, dataset_id, episode_id, data_version_id)
        REFERENCES episode_store.versions
            (organization_id, project_id, region_code, dataset_id, episode_id, data_version_id)
);
CREATE TABLE episode_store.collections (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    collection_version_id text NOT NULL,
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    document jsonb NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, dataset_id, collection_version_id)
);
SELECT core.apply_project_rls('episode_store.bindings'::regclass);
SELECT core.apply_project_rls('episode_store.versions'::regclass);
SELECT core.apply_project_rls('episode_store.attempts'::regclass);
SELECT core.apply_project_rls('episode_store.events'::regclass);
SELECT core.apply_project_rls('episode_store.collections'::regclass);
CREATE FUNCTION episode_store.immutable_committed_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'COMMITTED' AND (TG_OP = 'DELETE' OR NEW IS DISTINCT FROM OLD) THEN
        RAISE EXCEPTION 'committed Episode version is immutable';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER protect_episode_versions BEFORE UPDATE OR DELETE ON episode_store.versions
    FOR EACH ROW EXECUTE FUNCTION episode_store.immutable_committed_version();
