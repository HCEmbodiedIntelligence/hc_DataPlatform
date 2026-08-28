-- A new robot can start with a new model identity and an editable first
-- version.  The idempotency receipt is stored only after both rows exist.

ALTER TABLE registry.robot_model_command_receipts
    DROP CONSTRAINT IF EXISTS registry_robot_model_command_receipts_operation_check;

ALTER TABLE registry.robot_model_command_receipts
    ADD CONSTRAINT registry_robot_model_command_receipts_operation_check
    CHECK (operation IN (
        'REPLACE_JOINT_MAPPINGS',
        'CREATE_DRAFT',
        'CREATE_MODEL_DRAFT'
    ));

CREATE UNIQUE INDEX IF NOT EXISTS registry_robot_model_create_receipt_idx
ON registry.robot_model_command_receipts (
    organization_id,
    project_id,
    operation,
    idempotency_key
)
WHERE operation = 'CREATE_MODEL_DRAFT';
