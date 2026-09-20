-- Storage and processing have independent lifecycles. Existing records retain
-- their processing state and historical media references.
ALTER TABLE ingest.raw_sources DROP CONSTRAINT raw_sources_source_format_check;
ALTER TABLE ingest.raw_sources ADD CONSTRAINT raw_sources_source_format_check
    CHECK (source_format IN ('MCAP', 'CAPTURE_BUNDLE', 'LEROBOT_V3', 'ROSBAG'));
ALTER TABLE ingest.raw_sources DROP CONSTRAINT raw_sources_processing_status_check;
ALTER TABLE ingest.raw_sources ADD CONSTRAINT raw_sources_processing_status_check
    CHECK (processing_status IN ('NOT_REQUESTED', 'PENDING', 'DISCOVERING_EPISODES',
        'PROCESSING', 'READY', 'PARTIALLY_FAILED', 'FAILED'));
ALTER TABLE ingest.raw_ingest_jobs DROP CONSTRAINT raw_ingest_jobs_job_type_check;
ALTER TABLE ingest.raw_ingest_jobs ADD CONSTRAINT raw_ingest_jobs_job_type_check
    CHECK (job_type IN ('RAW_STORAGE', 'DIRECT_EPISODE_INGEST',
        'CONTINUOUS_RECORDING_DISCOVERY', 'LEROBOT_IMPORT'));
ALTER TABLE ingest.raw_ingest_jobs DROP CONSTRAINT raw_ingest_jobs_adapter_name_check;
ALTER TABLE ingest.raw_ingest_jobs ADD CONSTRAINT raw_ingest_jobs_adapter_name_check
    CHECK (adapter_name IN ('raw_storage', 'mcap', 'capture_bundle', 'lerobot_v3'));
CREATE INDEX raw_sources_dataset_browse_idx ON ingest.raw_sources
    (organization_id, project_id, region_code, dataset_id, committed_at DESC, raw_source_id);
