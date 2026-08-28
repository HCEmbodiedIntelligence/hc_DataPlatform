-- Robot-model updates are performed on new drafts so published versions and
-- active robot bindings stay immutable until the replacement passes preflight.

ALTER TABLE registry.robot_model_command_receipts
    DROP CONSTRAINT IF EXISTS robot_model_command_receipts_operation_check;

ALTER TABLE registry.robot_model_command_receipts
    DROP CONSTRAINT IF EXISTS registry_robot_model_command_receipts_operation_check;

ALTER TABLE registry.robot_model_command_receipts
    ADD CONSTRAINT registry_robot_model_command_receipts_operation_check
    CHECK (operation IN ('REPLACE_JOINT_MAPPINGS', 'CREATE_DRAFT'));

CREATE UNIQUE INDEX IF NOT EXISTS registry_robot_model_versions_label_idx
ON registry.robot_model_versions (organization_id, robot_model_id, version_label);
