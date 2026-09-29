-- The public document must describe the same READY row and finalized window.
CREATE FUNCTION episode_store.require_continuous_ready_document()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.data_ref IS NOT NULL AND (
        NOT (NEW.episode_document @> jsonb_build_object(
            'scope', jsonb_build_object('organization_id',NEW.organization_id,
                'project_id',NEW.project_id,'region_code',NEW.region_code),
            'recording_id',NEW.recording_id,'episode_id',NEW.episode_id,
            'finalized_revision',NEW.finalized_revision,
            'start_offset_ns',NEW.start_offset_ns::text,'end_offset_ns',NEW.end_offset_ns::text,
            'dataset_id',NEW.dataset_id,'dataset_version',NEW.dataset_version,
            'lance_version',NULL,'qc_report_id',NEW.qc_report_id,
            'alignment_attempt_id',NEW.alignment_attempt_id,
            'annotation_task_id',NEW.annotation_task_id,
            'aligned_media_camera_count',NEW.aligned_media_camera_count
        )) OR NOT EXISTS (
            SELECT 1 FROM ingest.recording_episode_slices s
            WHERE s.organization_id=NEW.organization_id AND s.project_id=NEW.project_id
              AND s.region_code=NEW.region_code AND s.recording_id=NEW.recording_id
              AND s.episode_id=NEW.episode_id AND s.revision=NEW.finalized_revision
              AND s.start_offset_ns=NEW.start_offset_ns AND s.end_offset_ns=NEW.end_offset_ns
        )
    ) THEN
        RAISE EXCEPTION 'continuous READY document/window differs from persisted evidence'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER protect_continuous_ready_document
    BEFORE INSERT OR UPDATE ON ingest.recording_episode_processing
    FOR EACH ROW EXECUTE FUNCTION episode_store.require_continuous_ready_document();
