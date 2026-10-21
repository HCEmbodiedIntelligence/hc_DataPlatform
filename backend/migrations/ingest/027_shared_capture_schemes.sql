-- Organization assets are separate from instance sources and project task ownership.
-- Additive migration: legacy versions, uploads and successful Episodes remain untouched.
CREATE TABLE ingest.capture_schemes (
 organization_id text NOT NULL,
 scheme_id text NOT NULL,
 revision integer NOT NULL CHECK (revision > 0),
 draft jsonb NOT NULL,
 latest_version integer,
 archived boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (organization_id, scheme_id)
);
CREATE TABLE ingest.capture_scheme_validations (
 organization_id text NOT NULL, scheme_id text NOT NULL,
 revision integer NOT NULL CHECK (revision > 0),
 source_format text NOT NULL CHECK (source_format IN ('MCAP','LEROBOT_V3')),
 validation_id text NOT NULL,
 document jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (organization_id, scheme_id, validation_id),
 FOREIGN KEY (organization_id, scheme_id)
 REFERENCES ingest.capture_schemes (organization_id, scheme_id)
);
CREATE TABLE ingest.capture_scheme_versions (
 organization_id text NOT NULL, scheme_id text NOT NULL,
 version integer NOT NULL CHECK (version > 0), draft_revision integer NOT NULL,
 content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
 document jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (organization_id, scheme_id, version),
 UNIQUE (organization_id, scheme_id, draft_revision),
 FOREIGN KEY (organization_id, scheme_id)
 REFERENCES ingest.capture_schemes (organization_id, scheme_id)
);
CREATE TABLE ingest.capture_scheme_links (
 organization_id text NOT NULL, robot_id text NOT NULL, scheme_id text NOT NULL,
 revision integer NOT NULL CHECK (revision > 0),
 scheme_version integer NOT NULL, mapping_version integer NOT NULL,
 is_default boolean NOT NULL DEFAULT false, active boolean NOT NULL DEFAULT true,
 updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (organization_id, robot_id, scheme_id),
 FOREIGN KEY (organization_id, robot_id)
 REFERENCES robotics.robot_assets (organization_id, robot_id),
 FOREIGN KEY (organization_id, scheme_id, scheme_version)
 REFERENCES ingest.capture_scheme_versions (organization_id, scheme_id, version)
);
CREATE UNIQUE INDEX capture_scheme_instance_default ON ingest.capture_scheme_links
 (organization_id, robot_id) WHERE active AND is_default;
CREATE TABLE ingest.capture_mapping_versions (
 organization_id text NOT NULL, robot_id text NOT NULL, scheme_id text NOT NULL,
 mapping_version integer NOT NULL CHECK (mapping_version > 0), scheme_version integer NOT NULL,
 content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
 document jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (organization_id, robot_id, scheme_id, mapping_version),
 FOREIGN KEY (organization_id, robot_id, scheme_id)
 REFERENCES ingest.capture_scheme_links (organization_id, robot_id, scheme_id),
 FOREIGN KEY (organization_id, scheme_id, scheme_version)
 REFERENCES ingest.capture_scheme_versions (organization_id, scheme_id, version)
);
CREATE TABLE ingest.capture_mapping_validations (
 organization_id text NOT NULL, robot_id text NOT NULL, scheme_id text NOT NULL,
 mapping_version integer NOT NULL, source_format text NOT NULL,
 validation_id text NOT NULL, document jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (organization_id, robot_id, scheme_id, mapping_version, validation_id),
 FOREIGN KEY (organization_id, robot_id, scheme_id, mapping_version)
 REFERENCES ingest.capture_mapping_versions
 (organization_id, robot_id, scheme_id, mapping_version)
);
CREATE TABLE collection_tasks.capture_plans (
 organization_id text NOT NULL, project_id text NOT NULL, collection_task_id text NOT NULL,
 region_code text NOT NULL, revision integer NOT NULL CHECK (revision > 0),
 scheme_id text NOT NULL, scheme_version integer NOT NULL,
 document jsonb NOT NULL, content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (organization_id, project_id, collection_task_id, revision),
 FOREIGN KEY (organization_id, project_id, collection_task_id)
 REFERENCES collection_tasks.collection_tasks (organization_id, project_id, collection_task_id),
 FOREIGN KEY (organization_id, scheme_id, scheme_version)
 REFERENCES ingest.capture_scheme_versions (organization_id, scheme_id, version)
);
CREATE TABLE collection_tasks.capture_plan_heads (
 organization_id text NOT NULL, project_id text NOT NULL, collection_task_id text NOT NULL,
 revision integer NOT NULL, locked_at timestamptz,
 PRIMARY KEY (organization_id, project_id, collection_task_id),
 FOREIGN KEY (organization_id, project_id, collection_task_id, revision)
 REFERENCES collection_tasks.capture_plans
 (organization_id, project_id, collection_task_id, revision)
);
CREATE TABLE episode_store.dataset_capture_contracts (
 organization_id text NOT NULL, project_id text NOT NULL, region_code text NOT NULL,
 dataset_id text NOT NULL, contract_sha256 text NOT NULL,
 document jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (organization_id, project_id, region_code, dataset_id)
);
CREATE TABLE ingest.capture_recording_snapshots (
 organization_id text NOT NULL, robot_id text NOT NULL, recording_id text NOT NULL,
 project_id text NOT NULL, region_code text NOT NULL, collection_task_id text NOT NULL,
 plan_revision integer NOT NULL, document jsonb NOT NULL,
 content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (organization_id, robot_id, recording_id),
 FOREIGN KEY (organization_id, project_id, collection_task_id, plan_revision)
 REFERENCES collection_tasks.capture_plans
 (organization_id, project_id, collection_task_id, revision),
 FOREIGN KEY (organization_id, robot_id)
 REFERENCES robotics.robot_assets (organization_id, robot_id)
);
CREATE TABLE ingest.capture_scheme_audit (
 audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 organization_id text NOT NULL, actor text NOT NULL, action text NOT NULL,
 scheme_id text NOT NULL, document jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE ingest.capture_legacy_references (
 organization_id text NOT NULL, legacy_kind text NOT NULL, legacy_key text NOT NULL,
 source_sha256 text NOT NULL, scheme_id text NOT NULL, scheme_version integer,
 document jsonb NOT NULL, migrated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (organization_id, legacy_kind, legacy_key, source_sha256),
 FOREIGN KEY (organization_id, scheme_id)
 REFERENCES ingest.capture_schemes (organization_id, scheme_id)
);
-- An explicit cutoff distinguishes legacy tasks from newly created tasks needing a plan.
ALTER TABLE collection_tasks.collection_tasks
 ADD COLUMN capture_plan_required boolean NOT NULL DEFAULT false;
ALTER TABLE collection_tasks.collection_tasks ALTER COLUMN capture_plan_required SET DEFAULT true;

DO $body$
DECLARE relation text;
BEGIN
 FOREACH relation IN ARRAY ARRAY[
  'ingest.capture_schemes', 'ingest.capture_scheme_validations',
  'ingest.capture_scheme_versions', 'ingest.capture_scheme_links',
  'ingest.capture_mapping_versions', 'ingest.capture_mapping_validations',
  'ingest.capture_scheme_audit', 'ingest.capture_legacy_references'
 ] LOOP
  PERFORM core.apply_organization_rls(relation::regclass);
 END LOOP;
 FOREACH relation IN ARRAY ARRAY[
  'collection_tasks.capture_plans', 'collection_tasks.capture_plan_heads',
  'ingest.capture_recording_snapshots', 'episode_store.dataset_capture_contracts'
 ] LOOP
  PERFORM core.apply_project_rls(relation::regclass);
 END LOOP;
 FOREACH relation IN ARRAY ARRAY[
  'ingest.capture_scheme_validations', 'ingest.capture_scheme_versions',
  'ingest.capture_mapping_versions', 'ingest.capture_mapping_validations',
  'collection_tasks.capture_plans', 'ingest.capture_recording_snapshots',
  'ingest.capture_scheme_audit', 'ingest.capture_legacy_references',
  'episode_store.dataset_capture_contracts'
 ] LOOP
  EXECUTE format('CREATE TRIGGER immutable_capture_history BEFORE UPDATE OR DELETE ON %s '
   'FOR EACH ROW EXECUTE FUNCTION ingest.protect_processing_history()', relation);
 END LOOP;
END $body$;
