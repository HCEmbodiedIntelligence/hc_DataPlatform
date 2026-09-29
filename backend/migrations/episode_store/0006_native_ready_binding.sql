-- Native READY is backed by an exact committed package and published collection.
-- Existing Lance and completed-QC-rejection records retain their original contract.
ALTER TABLE ingest.raw_source_episodes ADD COLUMN data_ref jsonb;
ALTER TABLE ingest.raw_source_episodes DROP CONSTRAINT raw_source_episodes_ready_receipt;
ALTER TABLE ingest.raw_source_episodes ADD CONSTRAINT raw_source_episodes_ready_receipt CHECK (
    (data_ref IS NULL OR (jsonb_typeof(data_ref)='object' AND lance_version IS NULL))
    AND (status <> 'READY' OR (dataset_version IS NOT NULL AND lance_version IS NOT NULL)
         OR quality_status IN ('RISK','REJECT') OR data_ref IS NOT NULL)
);
CREATE FUNCTION episode_store.require_native_ready_binding() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.data_ref IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM episode_store.versions v
        JOIN episode_store.collection_publications p USING
            (organization_id,project_id,region_code,dataset_id,data_version_id)
        JOIN episode_store.collections c USING
            (organization_id,project_id,region_code,dataset_id,collection_version_id)
        JOIN ingest.raw_sources r ON r.organization_id=NEW.organization_id
            AND r.project_id=NEW.project_id AND r.region_code=NEW.region_code
            AND r.raw_source_id=NEW.raw_source_id AND r.dataset_id=v.dataset_id
        WHERE v.organization_id=NEW.organization_id AND v.project_id=NEW.project_id
          AND v.region_code=NEW.region_code AND v.status='COMMITTED'
          AND v.receipt_document->'data'=NEW.data_ref
          AND (NEW.data_ref->>'frame_count')::bigint=NEW.frame_count
          AND c.version=NEW.dataset_version
          AND v.input_document->'identity'->>'rollout_id'=NEW.episode_id
          AND v.input_document->'identity'=NEW.data_ref->'identity'
    ) THEN
        RAISE EXCEPTION 'native data binding requires exact committed package/collection/source';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER protect_native_ready_binding BEFORE INSERT OR UPDATE ON ingest.raw_source_episodes
    FOR EACH ROW EXECUTE FUNCTION episode_store.require_native_ready_binding();
