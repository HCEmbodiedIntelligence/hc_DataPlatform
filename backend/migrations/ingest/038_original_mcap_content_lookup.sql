CREATE INDEX recording_original_mcap_content_idx
    ON ingest.recording_upload_assets (organization_id, project_id, region_code, expected_sha256)
    WHERE asset_path='source/original/source.mcap' AND status='COMMITTED';
