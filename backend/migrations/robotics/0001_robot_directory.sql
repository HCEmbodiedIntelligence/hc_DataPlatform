-- P15 formal robot, component, Frame, and Channel read facts.
CREATE SCHEMA IF NOT EXISTS robotics;

CREATE TABLE IF NOT EXISTS robotics.robot_instances (
    project_id text NOT NULL,
    region_code text NOT NULL,
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
    binding_id text,
    binding_scope_type text,
    binding_scope_id text,
    robot_model_version_id text,
    binding_valid_from timestamptz,
    binding_valid_to timestamptz,
    binding_etag text,
    allowed_actions jsonb NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (project_id, region_code, robot_id),
    UNIQUE (project_id, region_code, serial_no),
    CHECK (project_id <> ''), CHECK (region_code <> ''), CHECK (robot_id <> ''),
    CHECK (display_name <> ''), CHECK (serial_no <> ''), CHECK (lifecycle_status <> ''),
    CHECK (connectivity_state <> ''), CHECK (etag <> ''), CHECK (topology_revision <> ''),
    CHECK (jsonb_typeof(allowed_actions) = 'array'),
    CHECK ((binding_id IS NULL AND binding_scope_type IS NULL AND binding_scope_id IS NULL
            AND robot_model_version_id IS NULL AND binding_valid_from IS NULL
            AND binding_valid_to IS NULL AND binding_etag IS NULL)
           OR (binding_id IS NOT NULL AND binding_scope_type IS NOT NULL AND binding_scope_id IS NOT NULL
               AND robot_model_version_id IS NOT NULL AND binding_valid_from IS NOT NULL
               AND binding_etag IS NOT NULL)),
    CHECK (binding_valid_to IS NULL OR binding_valid_to > binding_valid_from)
);

CREATE TABLE IF NOT EXISTS robotics.robot_components (
    project_id text NOT NULL,
    region_code text NOT NULL,
    component_id text NOT NULL,
    robot_id text NOT NULL,
    parent_component_id text,
    component_model_id text NOT NULL,
    component_type text NOT NULL,
    display_name text NOT NULL,
    serial_no text NOT NULL,
    lifecycle_status text NOT NULL,
    sort_order bigint NOT NULL,
    PRIMARY KEY (project_id, region_code, component_id),
    UNIQUE (project_id, region_code, component_id, robot_id),
    FOREIGN KEY (project_id, region_code, robot_id)
        REFERENCES robotics.robot_instances (project_id, region_code, robot_id),
    CHECK (project_id <> ''), CHECK (region_code <> ''), CHECK (component_id <> ''),
    CHECK (component_model_id <> ''), CHECK (component_type <> ''), CHECK (display_name <> ''),
    CHECK (serial_no <> ''), CHECK (lifecycle_status <> ''), CHECK (sort_order >= 0)
);

CREATE TABLE IF NOT EXISTS robotics.component_frames (
    project_id text NOT NULL,
    region_code text NOT NULL,
    frame_id text NOT NULL,
    component_id text NOT NULL,
    name text NOT NULL,
    parent_frame text,
    source text NOT NULL,
    calibration_set_id text NOT NULL,
    status text NOT NULL,
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    PRIMARY KEY (project_id, region_code, frame_id),
    FOREIGN KEY (project_id, region_code, component_id)
        REFERENCES robotics.robot_components (project_id, region_code, component_id),
    CHECK (project_id <> ''), CHECK (region_code <> ''), CHECK (frame_id <> ''),
    CHECK (name <> ''), CHECK (source <> ''), CHECK (calibration_set_id <> ''), CHECK (status <> ''),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE TABLE IF NOT EXISTS robotics.component_channels (
    project_id text NOT NULL,
    region_code text NOT NULL,
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
    PRIMARY KEY (project_id, region_code, channel_id),
    FOREIGN KEY (project_id, region_code, component_id)
        REFERENCES robotics.robot_components (project_id, region_code, component_id),
    CHECK (project_id <> ''), CHECK (region_code <> ''), CHECK (channel_id <> ''),
    CHECK (canonical_path <> ''), CHECK (display_name <> ''), CHECK (modality <> ''),
    CHECK (schema_id <> ''), CHECK (schema_version > 0), CHECK (role <> ''), CHECK (status <> ''),
    CHECK (frequency_hz IS NULL OR frequency_hz >= 0)
);

CREATE INDEX IF NOT EXISTS robotics_robot_scope_name_idx
ON robotics.robot_instances (project_id, region_code, lower(display_name), robot_id);
CREATE INDEX IF NOT EXISTS robotics_component_robot_idx
ON robotics.robot_components (project_id, region_code, robot_id, sort_order, component_id);
CREATE INDEX IF NOT EXISTS robotics_frame_component_idx
ON robotics.component_frames (project_id, region_code, component_id, valid_from DESC, frame_id);
CREATE INDEX IF NOT EXISTS robotics_channel_component_idx
ON robotics.component_channels (project_id, region_code, component_id, canonical_path, channel_id);

SELECT core.apply_project_rls('robotics.robot_instances'::regclass);
SELECT core.apply_project_rls('robotics.robot_components'::regclass);
SELECT core.apply_project_rls('robotics.component_frames'::regclass);
SELECT core.apply_project_rls('robotics.component_channels'::regclass);
