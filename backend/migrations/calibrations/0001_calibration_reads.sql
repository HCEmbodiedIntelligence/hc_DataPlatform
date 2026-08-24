-- P16 formal, read-only calibration set projections.  Validation and publication
-- are explicitly unavailable until their workflow has product approval.
CREATE SCHEMA IF NOT EXISTS calibrations;

CREATE TABLE IF NOT EXISTS calibrations.calibration_sets (
    project_id text NOT NULL,
    region_code text NOT NULL,
    set_id text NOT NULL,
    robot_instance_id text NOT NULL,
    component_id text,
    version bigint NOT NULL,
    snapshot_status text NOT NULL,
    availability text,
    content_hash text,
    validation_context_hash text,
    validation_status text,
    validation_content_hash text,
    validation_report_id text,
    etag text NOT NULL,
    allowed_actions jsonb NOT NULL DEFAULT '[]'::jsonb,
    blocked_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (project_id, region_code, set_id),
    FOREIGN KEY (project_id, region_code, component_id, robot_instance_id)
        REFERENCES robotics.robot_components (project_id, region_code, component_id, robot_id),
    CHECK (project_id <> ''), CHECK (region_code <> ''), CHECK (set_id <> ''),
    CHECK (robot_instance_id <> ''), CHECK (version >= 0), CHECK (snapshot_status <> ''),
    CHECK (etag <> ''), CHECK (jsonb_typeof(allowed_actions) = 'array'),
    CHECK (jsonb_typeof(blocked_reasons) = 'array'),
    CHECK ((validation_status IS NULL AND validation_content_hash IS NULL AND validation_report_id IS NULL)
           OR (validation_status IS NOT NULL AND validation_content_hash IS NOT NULL
               AND validation_report_id IS NOT NULL AND validation_context_hash IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS calibrations_set_scope_robot_idx
ON calibrations.calibration_sets (project_id, region_code, robot_instance_id, set_id);
CREATE INDEX IF NOT EXISTS calibrations_set_scope_component_idx
ON calibrations.calibration_sets (project_id, region_code, component_id, set_id);

SELECT core.apply_project_rls('calibrations.calibration_sets'::regclass);
