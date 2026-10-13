CREATE TABLE robotics.robot_camera_calibrations (
    organization_id text NOT NULL,
    robot_id text NOT NULL,
    configuration jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id,robot_id),
    FOREIGN KEY (organization_id,robot_id)
        REFERENCES robotics.robot_assets (organization_id,robot_id) ON DELETE CASCADE
);
SELECT core.apply_organization_rls('robotics.robot_camera_calibrations'::regclass);
