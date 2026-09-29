-- New format schema registration must not populate or depend on Lance tables.
CREATE TABLE episode_store.dataset_schemas (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    schema_snapshot_id text NOT NULL,
    document jsonb NOT NULL,
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (organization_id, project_id, region_code, dataset_id)
);
CREATE TRIGGER protect_dataset_schemas BEFORE UPDATE OR DELETE ON episode_store.dataset_schemas
    FOR EACH ROW EXECUTE FUNCTION episode_store.immutable_collection();
SELECT core.apply_project_rls('episode_store.dataset_schemas'::regclass);
