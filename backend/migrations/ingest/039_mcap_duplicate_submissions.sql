ALTER TABLE ingest.mcap_processing_files ADD COLUMN duplicate_of text;
CREATE INDEX mcap_failed_original_sha_idx ON ingest.mcap_processing_files
    (organization_id, project_id, region_code, (source_document->>'sha256'))
    WHERE status='FAILED' AND duplicate_of IS NULL;
