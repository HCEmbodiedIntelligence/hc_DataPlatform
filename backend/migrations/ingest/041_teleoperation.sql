CREATE TABLE ingest.teleoperation_endpoints (
    organization_id text NOT NULL,
    robot_id text NOT NULL,
    document jsonb NOT NULL CHECK (jsonb_typeof(document) = 'object'),
    PRIMARY KEY (organization_id, robot_id)
);
SELECT core.apply_organization_rls('ingest.teleoperation_endpoints'::regclass);
