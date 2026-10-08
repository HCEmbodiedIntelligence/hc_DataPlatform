-- Explicit business publication, distinct from collection ingestion and Lance.
CREATE TABLE episode_store.collection_releases (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    dataset_version text NOT NULL CHECK (dataset_version ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'),
    version_id text NOT NULL CHECK (version_id ~ '^version_release_[0-9a-f]{32}$'),
    collection_version_id text NOT NULL,
    request_document jsonb NOT NULL,
    document jsonb NOT NULL,
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    document_sha256 text NOT NULL CHECK (document_sha256 ~ '^[0-9a-f]{64}$'),
    document_size bigint NOT NULL CHECK (document_size BETWEEN 1 AND 67108864),
    object_key text NOT NULL,
    actor_id text NOT NULL CHECK (length(actor_id)>0),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id,project_id,region_code,dataset_id,dataset_version),
    UNIQUE (organization_id,project_id,region_code,dataset_id,version_id),
    FOREIGN KEY (organization_id,project_id,region_code,dataset_id,collection_version_id)
        REFERENCES episode_store.collections,
    FOREIGN KEY (organization_id,project_id,region_code,dataset_id,version_id)
        REFERENCES dataset_registry.dataset_versions
);
SELECT core.apply_project_rls('episode_store.collection_releases'::regclass);
CREATE FUNCTION episode_store.guard_collection_release() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.organization_id',true),'')
       OR NEW.project_id IS DISTINCT FROM NULLIF(current_setting('app.project_id',true),'')
       OR NEW.region_code IS DISTINCT FROM NULLIF(current_setting('app.region_code',true),'')
       OR NEW.document->>'format' IS DISTINCT FROM 'hc-collection-release/1'
       OR NEW.document->'content'->>'dataset_version' IS DISTINCT FROM NEW.dataset_version
       OR NEW.document->'content'->'source_collection'->>'collection_version_id'
           IS DISTINCT FROM NEW.collection_version_id
       OR NEW.document->'content'->'source_collection'->>'dataset_id' IS DISTINCT FROM NEW.dataset_id
       OR NEW.document->'content'->'source_collection'->>'organization_id'
           IS DISTINCT FROM NEW.organization_id
       OR NEW.document->'content'->'source_collection'->>'project_id' IS DISTINCT FROM NEW.project_id
       OR NEW.document->'content'->'source_collection'->>'region_code' IS DISTINCT FROM NEW.region_code
       OR NEW.document->>'content_hash' IS DISTINCT FROM NEW.content_hash
       OR NEW.request_document->>'collection_version_id' IS DISTINCT FROM NEW.collection_version_id
       OR NOT EXISTS (
           SELECT 1 FROM dataset_registry.dataset_versions v
           WHERE v.organization_id=NEW.organization_id AND v.project_id=NEW.project_id
             AND v.region_code=NEW.region_code AND v.dataset_id=NEW.dataset_id
             AND v.version_id=NEW.version_id AND v.version_scope='DATASET_RELEASE'
             AND v.version_document->'manifest'->>'sha256'=NEW.content_hash
       ) THEN
        RAISE EXCEPTION 'collection release requires exact scope and atomic business projection'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER collection_release_guard BEFORE INSERT ON episode_store.collection_releases
    FOR EACH ROW EXECUTE FUNCTION episode_store.guard_collection_release();
CREATE TRIGGER collection_release_immutable BEFORE UPDATE OR DELETE
    ON episode_store.collection_releases
    FOR EACH ROW EXECUTE FUNCTION publishing.reject_immutable_change();
