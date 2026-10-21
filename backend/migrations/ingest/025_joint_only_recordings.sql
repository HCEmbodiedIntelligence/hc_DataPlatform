-- Preserve all existing readiness evidence; zero media is valid only for the explicit v2 joint contract.
ALTER TABLE ingest.recording_episode_processing
    DROP CONSTRAINT recording_episode_processing_ready_receipt;
ALTER TABLE ingest.recording_episode_processing
    ADD CONSTRAINT recording_episode_processing_ready_receipt CHECK (
        (status = 'READY'
            AND dataset_id IS NOT NULL
            AND dataset_version IS NOT NULL AND dataset_version > 0
            AND annotation_task_id IS NOT NULL
            AND aligned_media_camera_count IS NOT NULL AND aligned_media_camera_count >= 0
            AND (
                (lance_version IS NOT NULL AND lance_version > 0
                 AND data_ref IS NULL AND collection_version_id IS NULL)
                OR (lance_version IS NULL AND data_ref IS NOT NULL
                    AND jsonb_typeof(data_ref) = 'object' AND collection_version_id IS NOT NULL)
            ))
        OR (status <> 'READY'
            AND dataset_id IS NULL AND dataset_version IS NULL AND lance_version IS NULL
            AND data_ref IS NULL AND collection_version_id IS NULL
            AND annotation_task_id IS NULL AND aligned_media_camera_count IS NULL)
    );
CREATE OR REPLACE FUNCTION episode_store.require_continuous_ready_binding()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE input jsonb;
DECLARE cameras integer;
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.data_ref IS NOT NULL AND (
        NEW.data_ref IS DISTINCT FROM OLD.data_ref
        OR NEW.collection_version_id IS DISTINCT FROM OLD.collection_version_id
        OR NEW.annotation_task_id IS DISTINCT FROM OLD.annotation_task_id
        OR NEW.qc_report_id IS DISTINCT FROM OLD.qc_report_id
        OR NEW.alignment_attempt_id IS DISTINCT FROM OLD.alignment_attempt_id
        OR NEW.finalized_revision IS DISTINCT FROM OLD.finalized_revision
        OR NEW.start_offset_ns IS DISTINCT FROM OLD.start_offset_ns
        OR NEW.end_offset_ns IS DISTINCT FROM OLD.end_offset_ns
    ) THEN
        RAISE EXCEPTION 'continuous package binding is immutable' USING ERRCODE='23514';
    END IF;
    IF NEW.data_ref IS NULL THEN RETURN NEW; END IF;
    IF NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.organization_id', true),'')
       OR NEW.project_id IS DISTINCT FROM NULLIF(current_setting('app.project_id', true),'')
       OR NEW.region_code IS DISTINCT FROM NULLIF(current_setting('app.region_code', true),'') THEN
        RAISE EXCEPTION 'continuous package scope mismatch' USING ERRCODE='42501';
    END IF;
    SELECT v.input_document INTO input FROM episode_store.versions v
    JOIN episode_store.collections c ON c.organization_id=v.organization_id
        AND c.project_id=v.project_id AND c.region_code=v.region_code
        AND c.dataset_id=v.dataset_id AND c.collection_version_id=NEW.collection_version_id
        AND c.version=NEW.dataset_version
    WHERE v.organization_id=NEW.organization_id AND v.project_id=NEW.project_id
      AND v.region_code=NEW.region_code AND v.dataset_id=NEW.dataset_id
      AND v.data_version_id=NEW.data_ref->>'data_version_id'
      AND v.status='COMMITTED' AND v.receipt_document->'data'=NEW.data_ref
      AND v.input_document->'identity'->>'rollout_id'=NEW.recording_id||':'||NEW.episode_id
      AND c.document->'episodes' @> jsonb_build_array(NEW.data_ref);
    IF NOT FOUND OR NEW.qc_report_id IS NULL OR NEW.alignment_attempt_id IS NULL
       OR NEW.episode_document->'data_ref' IS DISTINCT FROM NEW.data_ref
       OR NEW.episode_document->>'dataset_episode_id'
            IS DISTINCT FROM NEW.data_ref->'identity'->>'episode_id'
       OR NEW.episode_document->>'collection_version_id' IS DISTINCT FROM NEW.collection_version_id
       OR NEW.episode_document->>'status' IS DISTINCT FROM 'READY'
       OR NOT EXISTS (
           SELECT 1 FROM ingest.recording_episode_qc_reports q
           WHERE q.organization_id=NEW.organization_id AND q.project_id=NEW.project_id
             AND q.region_code=NEW.region_code AND q.recording_id=NEW.recording_id
             AND q.episode_id=NEW.episode_id AND q.report_id=NEW.qc_report_id AND q.status='PASS'
             AND q.report_document->>'source_sha256'=input->>'source_sha256'
       ) OR NOT EXISTS (
           SELECT 1 FROM annotation.annotation_tasks a
           WHERE a.organization_id=NEW.organization_id AND a.project_id=NEW.project_id
             AND a.region_code=NEW.region_code AND a.dataset_id=NEW.dataset_id
             AND a.task_id=NEW.annotation_task_id AND a.data_ref=NEW.data_ref
             AND a.dataset_version=NEW.dataset_version AND a.task_kind='TAGGING'
       ) OR NOT EXISTS (
           SELECT 1 FROM dataset_registry.dataset_version_episode_revisions r
           WHERE r.organization_id=NEW.organization_id AND r.project_id=NEW.project_id
             AND r.region_code=NEW.region_code AND r.dataset_id=NEW.dataset_id
             AND r.version_id='version_'||NEW.collection_version_id
             AND r.episode_id=NEW.data_ref->'identity'->>'episode_id'
             AND jsonb_array_length(r.revision_document->'streams')>0
             AND NOT EXISTS (
                 SELECT 1 FROM jsonb_array_elements(r.revision_document->'streams') s
                 WHERE s->'package_binding'->'data' IS DISTINCT FROM NEW.data_ref
             )
       ) THEN
        RAISE EXCEPTION 'continuous READY requires exact package, collection, QC, task and viewer'
            USING ERRCODE='23514';
    END IF;
    SELECT count(*) INTO cameras FROM jsonb_array_elements_text(input->'required_features') f
        WHERE f LIKE 'observation.images.%';
    IF cameras<>NEW.aligned_media_camera_count OR (cameras=0 AND NOT EXISTS (
        SELECT 1 FROM ingest.continuous_recordings r JOIN ingest.recording_uploads u
        ON (u.organization_id,u.project_id,u.region_code,u.upload_id)=
           (r.organization_id,r.project_id,r.region_code,r.recording_upload_id)
        WHERE (r.organization_id,r.project_id,r.region_code,r.recording_id)=
              (NEW.organization_id,NEW.project_id,NEW.region_code,NEW.recording_id)
          AND u.upload_document->'command'->'recording_config'->>'schema_version'='recording-config/v2'
          AND u.upload_document->'command'->'recording_config'->>'collection_mode'='JOINT_ONLY'
    )) OR EXISTS (
        SELECT 1 FROM jsonb_array_elements_text(input->'required_features') f
        WHERE f LIKE 'observation.images.%' AND NOT EXISTS (
            SELECT 1 FROM episode_store.media_previews m
            WHERE m.organization_id=NEW.organization_id AND m.project_id=NEW.project_id
              AND m.region_code=NEW.region_code AND m.dataset_id=NEW.dataset_id
              AND m.data_version_id=NEW.data_ref->>'data_version_id' AND m.feature=f
              AND m.status='READY' AND m.receipt->'data'=NEW.data_ref
        )
    ) THEN
        RAISE EXCEPTION 'continuous READY requires all committed camera previews'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
