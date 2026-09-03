-- Canonical organization-wide robot assets.
--
-- A physical robot and its model configuration are tenant master data.  Projects
-- may be assigned access to the robot, but project/region are deliberately not
-- part of the robot identity.  Legacy project-owned robot tables are migrated and
-- then removed; there is one authoritative representation after this migration.

CREATE OR REPLACE FUNCTION core.organization_only_scope_matches(
    row_organization_id text
) RETURNS boolean
LANGUAGE sql
STABLE
PARALLEL SAFE
AS $function$
    SELECT
        current_setting('app.platform_admin', true) = 'true'
        OR row_organization_id = NULLIF(current_setting('app.organization_id', true), '')
$function$;

CREATE OR REPLACE FUNCTION core.apply_organization_rls(target_table regclass)
RETURNS void
LANGUAGE plpgsql
AS $function$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_attribute
         WHERE attrelid = target_table
           AND attname = 'organization_id'
           AND attnum > 0
           AND NOT attisdropped
    ) THEN
        RAISE EXCEPTION 'table % has no organization_id column', target_table;
    END IF;

    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', target_table);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', target_table);
    EXECUTE format('DROP POLICY IF EXISTS hc_scope_isolation ON %s', target_table);
    EXECUTE format('DROP POLICY IF EXISTS hc_organization_isolation ON %s', target_table);
    EXECUTE format(
        'CREATE POLICY hc_organization_isolation ON %s '
        'USING (core.organization_only_scope_matches(organization_id)) '
        'WITH CHECK (core.organization_only_scope_matches(organization_id))',
        target_table
    );
END
$function$;

CREATE TABLE IF NOT EXISTS robotics.robot_assets (
    organization_id text NOT NULL,
    robot_id text NOT NULL,
    display_name text NOT NULL,
    serial_no text NOT NULL,
    lifecycle_status text NOT NULL,
    connectivity_state text NOT NULL,
    connectivity_observed_at timestamptz,
    connectivity_source text,
    connectivity_reason_code text,
    etag text NOT NULL,
    topology_revision text NOT NULL,
    binding_id uuid,
    robot_model_version_id text,
    binding_valid_from timestamptz,
    binding_valid_to timestamptz,
    binding_etag text,
    allowed_actions jsonb NOT NULL DEFAULT '[]'::jsonb,
    revision bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, robot_id),
    UNIQUE (organization_id, serial_no),
    FOREIGN KEY (organization_id, robot_model_version_id)
        REFERENCES registry.robot_model_versions (organization_id, version_id),
    CHECK (organization_id <> ''),
    CHECK (robot_id <> ''),
    CHECK (display_name <> ''),
    CHECK (serial_no <> ''),
    CHECK (lifecycle_status IN ('DRAFT', 'ACTIVE', 'MAINTENANCE', 'DISABLED', 'RETIRED')),
    CHECK (connectivity_state IN ('ONLINE', 'OFFLINE', 'DEGRADED')),
    CHECK (etag <> ''),
    CHECK (topology_revision <> ''),
    CHECK (revision > 0),
    CHECK (jsonb_typeof(allowed_actions) = 'array'),
    CHECK (
        (binding_id IS NULL AND robot_model_version_id IS NULL
            AND binding_valid_from IS NULL AND binding_valid_to IS NULL
            AND binding_etag IS NULL)
        OR
        (binding_id IS NOT NULL AND robot_model_version_id IS NOT NULL
            AND binding_valid_from IS NOT NULL AND binding_etag IS NOT NULL)
    ),
    CHECK (binding_valid_to IS NULL OR binding_valid_to > binding_valid_from)
);

CREATE INDEX IF NOT EXISTS robotics_robot_assets_name_idx
ON robotics.robot_assets (organization_id, lower(display_name), robot_id);

CREATE TABLE IF NOT EXISTS robotics.robot_asset_components (
    organization_id text NOT NULL,
    component_id text NOT NULL,
    robot_id text NOT NULL,
    parent_component_id text,
    component_model_id text NOT NULL,
    component_type text NOT NULL,
    display_name text NOT NULL,
    serial_no text NOT NULL,
    lifecycle_status text NOT NULL,
    sort_order bigint NOT NULL,
    revision bigint NOT NULL DEFAULT 1,
    PRIMARY KEY (organization_id, component_id),
    UNIQUE (organization_id, component_id, robot_id),
    FOREIGN KEY (organization_id, robot_id)
        REFERENCES robotics.robot_assets (organization_id, robot_id),
    FOREIGN KEY (organization_id, parent_component_id, robot_id)
        REFERENCES robotics.robot_asset_components (organization_id, component_id, robot_id),
    CHECK (component_id <> ''),
    CHECK (component_model_id <> ''),
    CHECK (component_type <> ''),
    CHECK (display_name <> ''),
    CHECK (serial_no <> ''),
    CHECK (lifecycle_status IN ('DRAFT', 'ACTIVE', 'MAINTENANCE', 'DISABLED', 'RETIRED')),
    CHECK (sort_order >= 0),
    CHECK (revision > 0)
);

CREATE INDEX IF NOT EXISTS robotics_robot_asset_components_robot_idx
ON robotics.robot_asset_components (organization_id, robot_id, sort_order, component_id);

CREATE TABLE IF NOT EXISTS robotics.robot_asset_component_frames (
    organization_id text NOT NULL,
    frame_id text NOT NULL,
    component_id text NOT NULL,
    name text NOT NULL,
    parent_frame text,
    source text NOT NULL,
    calibration_set_id text NOT NULL,
    status text NOT NULL,
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    PRIMARY KEY (organization_id, frame_id),
    FOREIGN KEY (organization_id, component_id)
        REFERENCES robotics.robot_asset_components (organization_id, component_id),
    CHECK (frame_id <> ''),
    CHECK (name <> ''),
    CHECK (source <> ''),
    CHECK (calibration_set_id <> ''),
    CHECK (status <> ''),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE TABLE IF NOT EXISTS robotics.robot_asset_component_channels (
    organization_id text NOT NULL,
    channel_id text NOT NULL,
    component_id text NOT NULL,
    canonical_path text NOT NULL,
    display_name text NOT NULL,
    modality text NOT NULL,
    schema_id text NOT NULL,
    schema_version bigint NOT NULL,
    role text NOT NULL,
    unit text,
    frequency_hz numeric,
    frame_id text,
    clock_id text,
    status text NOT NULL,
    PRIMARY KEY (organization_id, channel_id),
    FOREIGN KEY (organization_id, component_id)
        REFERENCES robotics.robot_asset_components (organization_id, component_id),
    CHECK (channel_id <> ''),
    CHECK (canonical_path <> ''),
    CHECK (display_name <> ''),
    CHECK (modality <> ''),
    CHECK (schema_id <> ''),
    CHECK (schema_version > 0),
    CHECK (role <> ''),
    CHECK (status <> ''),
    CHECK (frequency_hz IS NULL OR frequency_hz >= 0)
);

CREATE TABLE IF NOT EXISTS robotics.project_robot_assignments (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    robot_id text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    assigned_at timestamptz NOT NULL DEFAULT now(),
    assigned_by text NOT NULL,
    revoked_at timestamptz,
    revoked_by text,
    PRIMARY KEY (organization_id, project_id, region_code, robot_id),
    FOREIGN KEY (organization_id, project_id)
        REFERENCES registry.organization_projects (organization_id, project_id),
    FOREIGN KEY (organization_id, robot_id)
        REFERENCES robotics.robot_assets (organization_id, robot_id),
    CHECK (project_id <> ''),
    CHECK (region_code <> ''),
    CHECK (assigned_by <> ''),
    CHECK (
        (active AND revoked_at IS NULL AND revoked_by IS NULL)
        OR (NOT active AND revoked_at IS NOT NULL AND revoked_by IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS robotics.robot_asset_command_receipts (
    organization_id text NOT NULL,
    resource_id text NOT NULL,
    operation text NOT NULL,
    idempotency_key text NOT NULL,
    request_fingerprint char(64) NOT NULL,
    response jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, resource_id, operation, idempotency_key),
    CHECK (resource_id <> ''),
    CHECK (operation IN ('CREATE', 'BIND_MODEL')),
    CHECK (idempotency_key <> ''),
    CHECK (jsonb_typeof(response) = 'object')
);

CREATE TABLE IF NOT EXISTS robotics.robot_asset_audit_events (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id text NOT NULL,
    actor_id text NOT NULL,
    action text NOT NULL,
    resource_id text NOT NULL,
    request_id text NOT NULL,
    occurred_at timestamptz NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (actor_id <> ''),
    CHECK (action <> ''),
    CHECK (resource_id <> ''),
    CHECK (request_id <> ''),
    CHECK (jsonb_typeof(details) = 'object')
);

CREATE INDEX IF NOT EXISTS robotics_robot_asset_audit_time_idx
ON robotics.robot_asset_audit_events (organization_id, occurred_at DESC, event_id DESC);

CREATE TABLE IF NOT EXISTS registry.organization_robot_model_bindings (
    organization_id text NOT NULL,
    binding_id uuid NOT NULL,
    robot_id text NOT NULL,
    version_id text NOT NULL,
    idempotency_key text NOT NULL,
    request_fingerprint char(64) NOT NULL,
    expected_robot_etag text NOT NULL,
    status text NOT NULL,
    bound_at timestamptz NOT NULL,
    unbound_at timestamptz,
    PRIMARY KEY (organization_id, binding_id),
    UNIQUE (organization_id, version_id, idempotency_key),
    FOREIGN KEY (organization_id, robot_id)
        REFERENCES robotics.robot_assets (organization_id, robot_id),
    FOREIGN KEY (organization_id, version_id)
        REFERENCES registry.robot_model_versions (organization_id, version_id),
    CHECK (robot_id <> ''),
    CHECK (idempotency_key <> ''),
    CHECK (expected_robot_etag <> ''),
    CHECK (status IN ('ACTIVE', 'SUPERSEDED', 'REVOKED')),
    CHECK (
        (status = 'ACTIVE' AND unbound_at IS NULL)
        OR (status <> 'ACTIVE' AND unbound_at IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS registry_organization_robot_binding_active_idx
ON registry.organization_robot_model_bindings (organization_id, robot_id)
WHERE status = 'ACTIVE';

-- Robot model payloads were also accidentally keyed by project.  Keep the
-- existing column only as creation provenance while rebuilding every identity,
-- idempotency key and RLS policy at organization scope.
ALTER TABLE registry.robot_model_asset_upload_files
    DROP CONSTRAINT robot_model_asset_upload_file_organization_id_project_id_u_fkey,
    DROP CONSTRAINT robot_model_asset_upload_files_pkey;
ALTER TABLE registry.robot_model_asset_uploads
    DROP CONSTRAINT robot_model_asset_uploads_pkey,
    DROP CONSTRAINT robot_model_asset_uploads_organization_id_project_id_versio_key,
    ADD CONSTRAINT robot_model_asset_uploads_pkey
        PRIMARY KEY (organization_id, upload_id),
    ADD CONSTRAINT robot_model_asset_uploads_organization_version_key
        UNIQUE (organization_id, version_id, idempotency_key);
ALTER TABLE registry.robot_model_asset_upload_files
    ADD CONSTRAINT robot_model_asset_upload_files_pkey
        PRIMARY KEY (organization_id, upload_id, relative_path),
    ADD CONSTRAINT robot_model_asset_upload_files_upload_fk
        FOREIGN KEY (organization_id, upload_id)
        REFERENCES registry.robot_model_asset_uploads (organization_id, upload_id)
        ON DELETE CASCADE;

ALTER TABLE registry.robot_model_assets
    DROP CONSTRAINT robot_model_assets_pkey,
    DROP CONSTRAINT robot_model_assets_organization_id_project_id_version_id_re_key,
    ADD CONSTRAINT robot_model_assets_pkey PRIMARY KEY (organization_id, asset_id),
    ADD CONSTRAINT robot_model_assets_organization_version_path_key
        UNIQUE (organization_id, version_id, relative_path);

ALTER TABLE registry.robot_model_joint_mappings
    DROP CONSTRAINT robot_model_joint_mappings_pkey,
    DROP CONSTRAINT robot_model_joint_mappings_organization_id_project_id_versi_key,
    ADD CONSTRAINT robot_model_joint_mappings_pkey
        PRIMARY KEY (organization_id, version_id, source_joint_name),
    ADD CONSTRAINT robot_model_joint_mappings_organization_target_key
        UNIQUE (organization_id, version_id, target_joint_name);

ALTER TABLE registry.robot_model_publish_preflights
    DROP CONSTRAINT robot_model_publish_preflights_pkey,
    DROP CONSTRAINT robot_model_publish_preflight_organization_id_project_id_ve_key,
    ADD CONSTRAINT robot_model_publish_preflights_pkey
        PRIMARY KEY (organization_id, preflight_id),
    ADD CONSTRAINT robot_model_publish_preflights_organization_version_key
        UNIQUE (organization_id, version_id, idempotency_key);

ALTER TABLE registry.robot_model_command_receipts
    DROP CONSTRAINT robot_model_command_receipts_pkey,
    ADD CONSTRAINT robot_model_command_receipts_pkey
        PRIMARY KEY (organization_id, version_id, operation, idempotency_key);

-- Migrate each legacy robot once, then retain every project/region occurrence as
-- an assignment.  Conflicting serial numbers within an organization fail the
-- migration instead of silently merging distinct physical devices.
INSERT INTO robotics.robot_assets (
    organization_id, robot_id, display_name, serial_no, lifecycle_status,
    connectivity_state, connectivity_observed_at, connectivity_source,
    connectivity_reason_code, etag, topology_revision, binding_id,
    robot_model_version_id, binding_valid_from, binding_valid_to, binding_etag,
    allowed_actions, revision, created_at, updated_at
)
SELECT DISTINCT ON (organization_id, robot_id)
       organization_id, robot_id, display_name, serial_no, lifecycle_status,
       connectivity_state, connectivity_observed_at, connectivity_source,
       connectivity_reason_code, etag, topology_revision,
       CASE WHEN binding_id IS NULL THEN NULL ELSE binding_id::uuid END,
       robot_model_version_id, binding_valid_from, binding_valid_to, binding_etag,
       allowed_actions, revision, created_at, updated_at
  FROM robotics.robot_instances
 ORDER BY organization_id, robot_id, updated_at DESC, project_id, region_code
ON CONFLICT (organization_id, robot_id) DO NOTHING;

INSERT INTO robotics.project_robot_assignments (
    organization_id, project_id, region_code, robot_id, active, assigned_at, assigned_by
)
SELECT organization_id, project_id, region_code, robot_id, true, created_at,
       'migration:robotics/0005'
  FROM robotics.robot_instances
ON CONFLICT (organization_id, project_id, region_code, robot_id) DO NOTHING;

INSERT INTO robotics.robot_asset_components (
    organization_id, component_id, robot_id, parent_component_id,
    component_model_id, component_type, display_name, serial_no,
    lifecycle_status, sort_order, revision
)
SELECT DISTINCT ON (organization_id, component_id)
       organization_id, component_id, robot_id, parent_component_id,
       component_model_id, component_type, display_name, serial_no,
       lifecycle_status, sort_order, revision
  FROM robotics.robot_components
 ORDER BY organization_id, component_id, project_id, region_code
ON CONFLICT (organization_id, component_id) DO NOTHING;

INSERT INTO robotics.robot_asset_component_frames (
    organization_id, frame_id, component_id, name, parent_frame, source,
    calibration_set_id, status, valid_from, valid_to
)
SELECT DISTINCT ON (organization_id, frame_id)
       organization_id, frame_id, component_id, name, parent_frame, source,
       calibration_set_id, status, valid_from, valid_to
  FROM robotics.component_frames
 ORDER BY organization_id, frame_id, valid_from DESC, project_id, region_code
ON CONFLICT (organization_id, frame_id) DO NOTHING;

INSERT INTO robotics.robot_asset_component_channels (
    organization_id, channel_id, component_id, canonical_path, display_name,
    modality, schema_id, schema_version, role, unit, frequency_hz,
    frame_id, clock_id, status
)
SELECT DISTINCT ON (organization_id, channel_id)
       organization_id, channel_id, component_id, canonical_path, display_name,
       modality, schema_id, schema_version, role, unit, frequency_hz,
       frame_id, clock_id, status
  FROM robotics.component_channels
 ORDER BY organization_id, channel_id, project_id, region_code
ON CONFLICT (organization_id, channel_id) DO NOTHING;

INSERT INTO registry.organization_robot_model_bindings (
    organization_id, binding_id, robot_id, version_id, idempotency_key,
    request_fingerprint, expected_robot_etag, status, bound_at, unbound_at
)
SELECT DISTINCT ON (legacy.organization_id, legacy.robot_id)
       legacy.organization_id, legacy.binding_id, legacy.robot_id, legacy.version_id,
       legacy.idempotency_key, legacy.request_fingerprint,
       legacy.expected_robot_etag, legacy.status, legacy.bound_at, legacy.unbound_at
  FROM registry.robot_model_bindings legacy
  JOIN robotics.robot_assets robot
    ON robot.organization_id = legacy.organization_id
   AND robot.robot_id = legacy.robot_id
 ORDER BY legacy.organization_id, legacy.robot_id,
          (legacy.status = 'ACTIVE') DESC, legacy.bound_at DESC
ON CONFLICT DO NOTHING;

-- Project-owned robot topology is not a second source of truth.  Calibrations
-- keep their project identity but reference the organization robot directly.
ALTER TABLE calibrations.calibration_sets
    DROP CONSTRAINT IF EXISTS calibration_sets_robot_component_organization_fk,
    DROP CONSTRAINT IF EXISTS calibration_sets_project_id_region_code_component_id_robot_fkey;

DROP TABLE IF EXISTS robotics.component_frames;
DROP TABLE IF EXISTS robotics.component_channels;
DROP TABLE IF EXISTS robotics.robot_maintenance_records;
DROP TABLE IF EXISTS robotics.robot_components;
DROP TABLE IF EXISTS robotics.robot_command_receipts;
DROP TABLE IF EXISTS registry.robot_model_bindings;
DROP TABLE IF EXISTS robotics.robot_instances;

ALTER TABLE calibrations.calibration_sets
    ADD CONSTRAINT calibration_sets_organization_robot_asset_fk
    FOREIGN KEY (organization_id, robot_instance_id)
    REFERENCES robotics.robot_assets (organization_id, robot_id),
    ADD CONSTRAINT calibration_sets_organization_robot_component_fk
    FOREIGN KEY (organization_id, component_id, robot_instance_id)
    REFERENCES robotics.robot_asset_components (organization_id, component_id, robot_id);

SELECT core.apply_organization_rls('robotics.robot_assets'::regclass);
SELECT core.apply_organization_rls('robotics.robot_asset_components'::regclass);
SELECT core.apply_organization_rls('robotics.robot_asset_component_frames'::regclass);
SELECT core.apply_organization_rls('robotics.robot_asset_component_channels'::regclass);
SELECT core.apply_project_rls('robotics.project_robot_assignments'::regclass);
SELECT core.apply_organization_rls('robotics.robot_asset_command_receipts'::regclass);
SELECT core.apply_organization_rls('robotics.robot_asset_audit_events'::regclass);
SELECT core.apply_organization_rls('registry.organization_robot_model_bindings'::regclass);
SELECT core.apply_organization_rls('registry.robot_model_asset_uploads'::regclass);
SELECT core.apply_organization_rls('registry.robot_model_asset_upload_files'::regclass);
SELECT core.apply_organization_rls('registry.robot_model_assets'::regclass);
SELECT core.apply_organization_rls('registry.robot_model_joint_mappings'::regclass);
SELECT core.apply_organization_rls('registry.robot_model_publish_preflights'::regclass);
SELECT core.apply_organization_rls('registry.robot_model_command_receipts'::regclass);
