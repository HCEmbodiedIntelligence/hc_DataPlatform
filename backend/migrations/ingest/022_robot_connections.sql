-- Additive receipts: no identity, credential, queue or historical ownership rewrite.
CREATE TABLE ingest.robot_connection_tickets (
    ticket_digest text PRIMARY KEY CHECK (ticket_digest ~ '^[a-f0-9]{64}$'),
    organization_id text NOT NULL,
    document jsonb NOT NULL CHECK (jsonb_typeof(document)='object')
);
CREATE TABLE ingest.robot_connection_locals (
    organization_id text NOT NULL,
    ingest_identity_id text NOT NULL,
    local_robot jsonb NOT NULL CHECK (jsonb_typeof(local_robot)='object'),
    PRIMARY KEY (organization_id, ingest_identity_id),
    FOREIGN KEY (organization_id, ingest_identity_id)
        REFERENCES ingest.robot_ingest_identities(organization_id, ingest_identity_id)
);
SELECT core.apply_organization_rls('ingest.robot_connection_tickets'::regclass);
SELECT core.apply_organization_rls('ingest.robot_connection_locals'::regclass);
