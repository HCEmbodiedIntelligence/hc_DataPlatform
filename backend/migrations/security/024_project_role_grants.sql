ALTER TABLE access_control.capability_requests
    ADD COLUMN expires_at timestamptz,
    ADD COLUMN resource_id text,
    ADD COLUMN replaces_request_id uuid REFERENCES access_control.capability_requests (request_id);

ALTER TABLE access_control.capability_grants ADD COLUMN expires_at timestamptz;
