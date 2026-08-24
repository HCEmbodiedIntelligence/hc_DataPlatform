-- P14 mutable joint-mapping commands retain their exact response under a
-- project-scoped idempotency key.  This is separate from publish preflights:
-- a mapping edit can be safely retried without reapplying an ETag transition.

CREATE TABLE IF NOT EXISTS registry.robot_model_command_receipts (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    version_id text NOT NULL,
    operation text NOT NULL,
    idempotency_key text NOT NULL,
    request_fingerprint char(64) NOT NULL,
    response jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, project_id, version_id, operation, idempotency_key),
    FOREIGN KEY (organization_id, version_id)
        REFERENCES registry.robot_model_versions (organization_id, version_id),
    CHECK (operation IN ('REPLACE_JOINT_MAPPINGS')),
    CHECK (idempotency_key <> ''),
    CHECK (jsonb_typeof(response) = 'object')
);

CREATE INDEX IF NOT EXISTS registry_robot_model_command_receipts_created_idx
ON registry.robot_model_command_receipts (organization_id, project_id, version_id, created_at DESC);

SELECT core.apply_project_rls('registry.robot_model_command_receipts'::regclass);
