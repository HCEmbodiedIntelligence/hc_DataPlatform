-- Scheme mappings and recording snapshots outlive the managed robot.
-- An active binding must still point at a live robot, including during concurrent writes.
ALTER TABLE ingest.capture_scheme_links
    ADD COLUMN active_robot_id text GENERATED ALWAYS AS
        (CASE WHEN active THEN robot_id END) STORED,
    DROP CONSTRAINT capture_scheme_links_organization_id_robot_id_fkey,
    ADD CONSTRAINT capture_scheme_links_robot_history_fk
        FOREIGN KEY (organization_id, robot_id)
        REFERENCES robotics.robot_history_anchors (organization_id, robot_id),
    ADD CONSTRAINT capture_scheme_links_active_robot_fk
        FOREIGN KEY (organization_id, active_robot_id)
        REFERENCES robotics.robot_assets (organization_id, robot_id);

ALTER TABLE ingest.capture_recording_snapshots
    DROP CONSTRAINT capture_recording_snapshots_organization_id_robot_id_fkey,
    ADD CONSTRAINT capture_recording_snapshots_robot_history_fk
        FOREIGN KEY (organization_id, robot_id)
        REFERENCES robotics.robot_history_anchors (organization_id, robot_id);
