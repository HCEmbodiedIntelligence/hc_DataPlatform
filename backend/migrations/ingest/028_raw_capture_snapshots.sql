-- Browser/other Raw intake persists the same server-owned snapshot as robot intake.
-- No historical data or existing schema is rewritten.
CREATE TABLE ingest.raw_capture_snapshots (
 organization_id text NOT NULL, project_id text NOT NULL, region_code text NOT NULL,
 raw_source_id text NOT NULL, document jsonb NOT NULL,
 content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (organization_id,project_id,region_code,raw_source_id),
 FOREIGN KEY (organization_id,project_id,region_code,raw_source_id)
 REFERENCES ingest.raw_sources (organization_id,project_id,region_code,raw_source_id)
);
SELECT core.apply_project_rls('ingest.raw_capture_snapshots'::regclass);
CREATE TRIGGER immutable_raw_capture_snapshot BEFORE UPDATE OR DELETE ON ingest.raw_capture_snapshots
 FOR EACH ROW EXECUTE FUNCTION ingest.protect_processing_history();
