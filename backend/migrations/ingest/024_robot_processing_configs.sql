-- Additive only: no historical profile, Raw, QC, Episode or outbox mutation.
CREATE TABLE ingest.robot_processing_schemes (
 organization_id text NOT NULL, robot_id text NOT NULL, scheme_id text NOT NULL,
 name text NOT NULL, software text NOT NULL, source_format text NOT NULL,
 revision integer NOT NULL CHECK(revision>0), draft jsonb NOT NULL,
 validation jsonb, published_version integer, is_default boolean NOT NULL DEFAULT false,
 provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
 updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(organization_id,robot_id,scheme_id),
 FOREIGN KEY(organization_id,robot_id) REFERENCES robotics.robot_assets(organization_id,robot_id)
);
CREATE UNIQUE INDEX robot_processing_default ON ingest.robot_processing_schemes
 (organization_id,robot_id,source_format) WHERE is_default;
CREATE TABLE ingest.robot_processing_versions (
 organization_id text NOT NULL, robot_id text NOT NULL, scheme_id text NOT NULL,
 version integer NOT NULL CHECK(version>0), document jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(organization_id,robot_id,scheme_id,version),
 FOREIGN KEY(organization_id,robot_id,scheme_id)
 REFERENCES ingest.robot_processing_schemes(organization_id,robot_id,scheme_id)
);
CREATE TABLE ingest.robot_processing_audit (
 audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 organization_id text NOT NULL, robot_id text NOT NULL, scheme_id text NOT NULL,
 actor text NOT NULL, action text NOT NULL, document jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE ingest.robot_processing_templates (
 organization_id text NOT NULL, template_id text NOT NULL,
 document jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(organization_id,template_id)
);
CREATE FUNCTION ingest.protect_processing_history() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'published processing history is immutable'; END $$;
CREATE TRIGGER immutable_processing_version BEFORE UPDATE OR DELETE
 ON ingest.robot_processing_versions FOR EACH ROW EXECUTE FUNCTION ingest.protect_processing_history();
CREATE TRIGGER immutable_processing_audit BEFORE UPDATE OR DELETE
 ON ingest.robot_processing_audit FOR EACH ROW EXECUTE FUNCTION ingest.protect_processing_history();
CREATE TRIGGER immutable_processing_template BEFORE UPDATE OR DELETE
 ON ingest.robot_processing_templates FOR EACH ROW EXECUTE FUNCTION ingest.protect_processing_history();
SELECT core.apply_organization_rls('ingest.robot_processing_schemes'::regclass);
SELECT core.apply_organization_rls('ingest.robot_processing_versions'::regclass);
SELECT core.apply_organization_rls('ingest.robot_processing_audit'::regclass);
SELECT core.apply_organization_rls('ingest.robot_processing_templates'::regclass);
ALTER TABLE ingest.mcap_processing_files ADD COLUMN config_reference jsonb,
 ADD COLUMN channel_inventory jsonb;
-- Task requirements are maintained by task managers, independently of robot settings.
CREATE TABLE ingest.robot_task_data_requirements (
 organization_id text NOT NULL, project_id text NOT NULL, collection_task_id text NOT NULL,
 requires_rgb boolean NOT NULL, allows_derived_action boolean NOT NULL DEFAULT false,
 actor text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(organization_id,project_id,collection_task_id)
);
SELECT core.apply_project_rls('ingest.robot_task_data_requirements'::regclass);
-- Explicit same-recording receipts prevent automatic duplicate recovery. No inferred links.
CREATE TABLE ingest.recording_source_links (
 organization_id text NOT NULL, project_id text NOT NULL, region_code text NOT NULL,
 raw_source_id text NOT NULL, canonical_episode_id text NOT NULL,
 evidence jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(organization_id,project_id,region_code,raw_source_id)
);
SELECT core.apply_project_rls('ingest.recording_source_links'::regclass);
