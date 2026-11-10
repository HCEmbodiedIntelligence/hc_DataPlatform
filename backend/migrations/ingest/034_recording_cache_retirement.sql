-- Keep immutable asset receipts while recording verified cache recoverability.
ALTER TABLE ingest.recording_upload_assets
    ADD COLUMN IF NOT EXISTS cache_retirement jsonb
    CHECK (cache_retirement IS NULL OR jsonb_typeof(cache_retirement) = 'object');
