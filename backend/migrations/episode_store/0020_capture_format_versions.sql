-- Append-only v2 formats coexist with the original single-format tables.
CREATE TABLE episode_store.dataset_capture_formats (
 organization_id text NOT NULL, project_id text NOT NULL, region_code text NOT NULL,
 dataset_id text NOT NULL, format_version text NOT NULL, document jsonb NOT NULL,
 content_hash text NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY (organization_id,project_id,region_code,dataset_id,format_version)
);
CREATE TABLE episode_store.dataset_schema_versions (
 organization_id text NOT NULL, project_id text NOT NULL, region_code text NOT NULL,
 dataset_id text NOT NULL, schema_snapshot_id text NOT NULL, document jsonb NOT NULL,
 content_hash text NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY (organization_id,project_id,region_code,dataset_id,schema_snapshot_id)
);
CREATE TABLE ingest.capture_reprocess_requests (
 organization_id text NOT NULL, project_id text NOT NULL, region_code text NOT NULL,
 raw_source_id text NOT NULL, request_id text NOT NULL, plan_revision integer NOT NULL,
 document jsonb NOT NULL, content_hash text NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY (organization_id,project_id,region_code,raw_source_id,request_id)
);
DO $$ DECLARE relation text; BEGIN
 FOREACH relation IN ARRAY ARRAY[
  'episode_store.dataset_capture_formats','episode_store.dataset_schema_versions',
  'ingest.capture_reprocess_requests'
 ] LOOP
  PERFORM core.apply_project_rls(relation::regclass);
  EXECUTE format('CREATE TRIGGER immutable_capture_history BEFORE UPDATE OR DELETE ON %s '
   'FOR EACH ROW EXECUTE FUNCTION ingest.protect_processing_history()', relation);
 END LOOP;
END $$;
