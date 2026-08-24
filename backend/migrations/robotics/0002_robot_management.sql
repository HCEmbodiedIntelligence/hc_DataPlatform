-- P15 mutable robot facts, lifecycle/maintenance history, and durable command receipts.
-- This is forward-only: existing read projections keep their stable identifiers.

ALTER TABLE robotics.robot_instances
    ADD COLUMN IF NOT EXISTS revision bigint NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE robotics.robot_instances
    ADD CONSTRAINT robotics_robot_instances_revision_positive
    CHECK (revision > 0);

CREATE TABLE IF NOT EXISTS robotics.robot_maintenance_records (
    project_id text NOT NULL,
    region_code text NOT NULL,
    record_id text NOT NULL,
    robot_id text NOT NULL,
    component_id text,
    event_type text NOT NULL,
    previous_lifecycle_status text,
    lifecycle_status text,
    summary text NOT NULL,
    details text,
    actor_id text NOT NULL,
    occurred_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, region_code, record_id),
    FOREIGN KEY (project_id, region_code, robot_id)
        REFERENCES robotics.robot_instances (project_id, region_code, robot_id),
    FOREIGN KEY (project_id, region_code, component_id)
        REFERENCES robotics.robot_components (project_id, region_code, component_id),
    CHECK (record_id <> ''), CHECK (robot_id <> ''),
    CHECK (event_type IN ('MAINTENANCE', 'LIFECYCLE_TRANSITION')),
    CHECK (summary <> ''), CHECK (actor_id <> ''),
    CHECK (
        (event_type = 'MAINTENANCE' AND previous_lifecycle_status IS NULL AND lifecycle_status IS NULL)
        OR (
            event_type = 'LIFECYCLE_TRANSITION'
            AND previous_lifecycle_status IS NOT NULL
            AND lifecycle_status IS NOT NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS robotics_maintenance_robot_occurred_idx
ON robotics.robot_maintenance_records (project_id, region_code, robot_id, occurred_at DESC, record_id);

CREATE TABLE IF NOT EXISTS robotics.robot_command_receipts (
    project_id text NOT NULL,
    region_code text NOT NULL,
    resource_type text NOT NULL,
    resource_id text NOT NULL,
    operation text NOT NULL,
    idempotency_key text NOT NULL,
    request_fingerprint text NOT NULL,
    response jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, region_code, resource_type, resource_id, operation, idempotency_key),
    CHECK (resource_type = 'ROBOT'),
    CHECK (resource_id <> ''), CHECK (operation IN ('CREATE', 'UPDATE', 'TRANSITION', 'MAINTENANCE')),
    CHECK (idempotency_key <> ''), CHECK (request_fingerprint <> ''),
    CHECK (jsonb_typeof(response) = 'object')
);

SELECT core.apply_project_rls('robotics.robot_maintenance_records'::regclass);
SELECT core.apply_project_rls('robotics.robot_command_receipts'::regclass);
