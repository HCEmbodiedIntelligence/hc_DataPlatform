-- Keep the registry projection aligned with the public robot Bootstrap contract.
-- The frontend and generated API accept the stable scope kinds below; the earlier
-- internal value ROBOT made otherwise valid bindings fail wire validation.

UPDATE robotics.robot_instances
SET binding_scope_type = 'ROBOT_INSTANCE'
WHERE binding_scope_type = 'ROBOT';

ALTER TABLE robotics.robot_instances
    DROP CONSTRAINT IF EXISTS robot_instances_binding_scope_type_check;

ALTER TABLE robotics.robot_instances
    ADD CONSTRAINT robot_instances_binding_scope_type_check
    CHECK (
        binding_scope_type IS NULL
        OR binding_scope_type IN ('ROBOT_MODEL_DEFAULT', 'ROBOT_INSTANCE')
    );
