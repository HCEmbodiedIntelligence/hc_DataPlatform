-- P15 component management keeps topology and downstream calibration/channel facts durable.
-- A component is never hard-deleted: RETIRED preserves all historical references.

ALTER TABLE robotics.robot_components
    ADD COLUMN IF NOT EXISTS revision bigint NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS etag text NOT NULL DEFAULT '"robotics-component:legacy:1"',
    ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

UPDATE robotics.robot_components
   SET etag = '"robotics-component:' || component_id || ':1"'
 WHERE etag = '"robotics-component:legacy:1"';

ALTER TABLE robotics.robot_components
    ADD CONSTRAINT robotics_robot_components_revision_positive
    CHECK (revision > 0),
    ADD CONSTRAINT robotics_robot_components_lifecycle_known
    CHECK (lifecycle_status IN ('DRAFT', 'ACTIVE', 'MAINTENANCE', 'DISABLED', 'RETIRED')) NOT VALID;

ALTER TABLE robotics.robot_components
    ADD CONSTRAINT robotics_robot_components_parent_same_robot_fk
    FOREIGN KEY (project_id, region_code, parent_component_id, robot_id)
    REFERENCES robotics.robot_components (project_id, region_code, component_id, robot_id)
    NOT VALID;

CREATE INDEX IF NOT EXISTS robotics_component_parent_idx
ON robotics.robot_components (project_id, region_code, robot_id, parent_component_id, component_id);

ALTER TABLE robotics.robot_command_receipts
    DROP CONSTRAINT IF EXISTS robot_command_receipts_resource_type_check,
    DROP CONSTRAINT IF EXISTS robot_command_receipts_operation_check,
    ADD CONSTRAINT robotics_robot_command_receipts_resource_type_check
    CHECK (resource_type IN ('ROBOT', 'COMPONENT')),
    ADD CONSTRAINT robotics_robot_command_receipts_operation_check
    CHECK (operation IN ('CREATE', 'UPDATE', 'TRANSITION', 'MAINTENANCE', 'RETIRE'));

ALTER TABLE robotics.robot_maintenance_records
    DROP CONSTRAINT IF EXISTS robot_maintenance_records_event_type_check,
    DROP CONSTRAINT IF EXISTS robot_maintenance_records_check,
    ADD CONSTRAINT robotics_robot_maintenance_records_event_type_check
    CHECK (event_type IN ('MAINTENANCE', 'LIFECYCLE_TRANSITION', 'COMPONENT_LIFECYCLE_TRANSITION')),
    ADD CONSTRAINT robotics_robot_maintenance_records_check
    CHECK (
        (event_type = 'MAINTENANCE' AND previous_lifecycle_status IS NULL AND lifecycle_status IS NULL)
        OR (
            event_type IN ('LIFECYCLE_TRANSITION', 'COMPONENT_LIFECYCLE_TRANSITION')
            AND previous_lifecycle_status IS NOT NULL
            AND lifecycle_status IS NOT NULL
        )
    );
