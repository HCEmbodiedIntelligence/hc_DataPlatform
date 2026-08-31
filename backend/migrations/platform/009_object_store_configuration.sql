-- Page-managed object-store configuration. Credential material is encrypted at
-- rest and is never returned by the HTTP read model or copied into generic runtime
-- configuration revisions.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS platform.platform_object_store_configurations (
    environment_id text PRIMARY KEY,
    revision bigint NOT NULL,
    provider text NOT NULL,
    endpoint text NOT NULL,
    public_endpoint text NOT NULL,
    bucket text NOT NULL,
    region text NOT NULL,
    access_key_ciphertext bytea NOT NULL,
    secret_key_ciphertext bytea NOT NULL,
    access_key_hint text NOT NULL,
    updated_by text NOT NULL,
    request_id text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    CHECK (revision > 0),
    CHECK (provider = 'oss'),
    CHECK (endpoint ~ '^https://[^/?#]+$'),
    CHECK (public_endpoint ~ '^https://[^/?#]+$'),
    CHECK (bucket ~ '^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$'),
    CHECK (region ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
    CHECK (length(access_key_hint) BETWEEN 5 AND 64),
    CHECK (updated_by <> '' AND length(updated_by) <= 255),
    CHECK (request_id <> '' AND length(request_id) <= 255)
);

REVOKE ALL ON platform.platform_object_store_configurations FROM PUBLIC;

COMMENT ON TABLE platform.platform_object_store_configurations IS
'Active page-managed object-store configuration; credentials are pgcrypto ciphertext.';
