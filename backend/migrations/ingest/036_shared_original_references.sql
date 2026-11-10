-- Multiple processing generations reference one immutable Raw object.
ALTER TABLE ingest.recording_upload_assets
    DROP CONSTRAINT recording_upload_assets_object_key_key;
ALTER TABLE ingest.recording_upload_assets
    ADD CONSTRAINT recording_upload_asset_object_key UNIQUE
    (organization_id, project_id, region_code, upload_id, object_key);
