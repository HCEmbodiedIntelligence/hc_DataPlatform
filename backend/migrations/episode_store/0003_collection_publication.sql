-- Dataset collection visibility is distinct from an individual package commit.
ALTER TABLE episode_store.collections ADD COLUMN version bigint CHECK (version > 0);
ALTER TABLE episode_store.collections ADD COLUMN created_at timestamptz NOT NULL
    DEFAULT clock_timestamp();
ALTER TABLE episode_store.collections ADD CONSTRAINT collection_number
    UNIQUE (organization_id, project_id, region_code, dataset_id, version);
CREATE TABLE episode_store.collection_heads (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    collection_version_id text NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, dataset_id),
    FOREIGN KEY (organization_id, project_id, region_code, dataset_id, collection_version_id)
        REFERENCES episode_store.collections
);
CREATE TABLE episode_store.collection_publications (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    data_version_id text NOT NULL,
    collection_version_id text NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, data_version_id),
    FOREIGN KEY (organization_id, project_id, region_code, data_version_id)
        REFERENCES episode_store.versions,
    FOREIGN KEY (organization_id, project_id, region_code, dataset_id, collection_version_id)
        REFERENCES episode_store.collections
);
CREATE TABLE episode_store.collection_events (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    collection_version_id text NOT NULL,
    payload jsonb NOT NULL,
    delivered_at timestamptz,
    PRIMARY KEY (organization_id, project_id, region_code, dataset_id, collection_version_id),
    FOREIGN KEY (organization_id, project_id, region_code, dataset_id, collection_version_id)
        REFERENCES episode_store.collections
);
CREATE FUNCTION episode_store.immutable_collection() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'published collection and publication receipts are immutable';
END $$;
CREATE TRIGGER protect_collections BEFORE UPDATE OR DELETE ON episode_store.collections
    FOR EACH ROW EXECUTE FUNCTION episode_store.immutable_collection();
CREATE TRIGGER protect_publications BEFORE UPDATE OR DELETE ON episode_store.collection_publications
    FOR EACH ROW EXECUTE FUNCTION episode_store.immutable_collection();
SELECT core.apply_project_rls('episode_store.collection_heads'::regclass);
SELECT core.apply_project_rls('episode_store.collection_publications'::regclass);
SELECT core.apply_project_rls('episode_store.collection_events'::regclass);
