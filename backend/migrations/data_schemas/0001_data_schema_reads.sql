-- P17 organization registry reads plus project/region route resolution.
CREATE SCHEMA IF NOT EXISTS data_schemas;

CREATE TABLE IF NOT EXISTS data_schemas.stream_schema_versions (
    organization_id text NOT NULL,
    schema_id text NOT NULL,
    schema_version bigint NOT NULL,
    family_id text NOT NULL,
    display_name text NOT NULL,
    logical_type text NOT NULL,
    status text NOT NULL,
    compatibility_mode text NOT NULL,
    compatibility_result text,
    hash_algorithm text,
    canonicalization_version text,
    hash_value text,
    schema_definition jsonb NOT NULL,
    etag text NOT NULL,
    allowed_actions jsonb NOT NULL DEFAULT '[]'::jsonb,
    blocked_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (organization_id, schema_id, schema_version),
    CHECK (organization_id <> ''), CHECK (schema_id <> ''), CHECK (schema_version > 0),
    CHECK (family_id <> ''), CHECK (display_name <> ''), CHECK (logical_type <> ''),
    CHECK (status <> ''), CHECK (compatibility_mode <> ''), CHECK (etag <> ''),
    CHECK (jsonb_typeof(schema_definition) = 'object'),
    CHECK (jsonb_typeof(allowed_actions) = 'array'), CHECK (jsonb_typeof(blocked_reasons) = 'array'),
    CHECK ((hash_algorithm IS NULL AND canonicalization_version IS NULL AND hash_value IS NULL)
           OR (hash_algorithm IS NOT NULL AND canonicalization_version IS NOT NULL AND hash_value IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS data_schema_versions_organization_name_idx
ON data_schemas.stream_schema_versions (organization_id, lower(display_name), schema_id, schema_version);
