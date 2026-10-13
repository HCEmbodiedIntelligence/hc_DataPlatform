-- Deleting one failed Episode preserves its raw recording, siblings and QC history.
ALTER TABLE ingest.recording_episode_processing
    DROP CONSTRAINT recording_episode_processing_status_check;
ALTER TABLE ingest.recording_episode_processing
    ADD CONSTRAINT recording_episode_processing_status_check CHECK (
        status IN ('PENDING_QC', 'QC_RUNNING', 'QC_FAILED', 'PENDING_ALIGNMENT',
                   'ALIGNING', 'READY', 'FAILED', 'DISCARDED')
    );
