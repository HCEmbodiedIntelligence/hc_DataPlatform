-- Robot frame_count describes received source frames; sample_count describes
-- the committed output grid. Web native entries historically use frame_count
-- for that output and leave sample_count NULL. Preserve both real meanings.
CREATE OR REPLACE FUNCTION episode_store.require_native_ready_binding()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='UPDATE' AND OLD.data_ref IS NOT NULL AND NEW.data_ref IS NULL THEN
        RAISE EXCEPTION 'committed native binding cannot be cleared';
    END IF;
    IF NEW.data_ref IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM episode_store.versions v
        JOIN episode_store.collection_publications p USING
            (organization_id,project_id,region_code,dataset_id,data_version_id)
        JOIN episode_store.collections c USING
            (organization_id,project_id,region_code,dataset_id,collection_version_id)
        JOIN ingest.raw_sources r ON r.organization_id=NEW.organization_id
            AND r.project_id=NEW.project_id AND r.region_code=NEW.region_code
            AND r.raw_source_id=NEW.raw_source_id AND r.dataset_id=v.dataset_id
        JOIN ingest.rollouts rollout ON rollout.organization_id=NEW.organization_id
            AND rollout.project_id=NEW.project_id AND rollout.region_code=NEW.region_code
            AND rollout.rollout_id=NEW.episode_id
        WHERE v.organization_id=NEW.organization_id AND v.project_id=NEW.project_id
          AND v.region_code=NEW.region_code AND v.status='COMMITTED'
          AND v.receipt_document->'data'=NEW.data_ref
          AND (NEW.data_ref->>'frame_count')::bigint=COALESCE(NEW.sample_count,NEW.frame_count)
          AND c.version=NEW.dataset_version
          AND v.input_document->'identity'->>'rollout_id'=NEW.episode_id
          AND v.input_document->'identity'=NEW.data_ref->'identity'
          AND v.input_document->>'source_sha256'=rollout.source_sha256
          AND rollout.collection_session_id=NEW.raw_source_id
    ) THEN
        RAISE EXCEPTION 'native binding requires exact committed package/collection/Raw lineage and output samples';
    END IF;
    RETURN NEW;
END $$;
