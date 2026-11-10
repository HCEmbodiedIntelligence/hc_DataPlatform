-- Physical cache retirement is separate from immutable recording asset facts.
CREATE TABLE ingest.recording_cache_retirements (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    object_key text NOT NULL,
    receipt jsonb NOT NULL,
    retired_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, project_id, region_code, object_key),
    CHECK (receipt->>'policy' = 'verified-rebuildable-cache/1'),
    CHECK (receipt->>'key' = object_key),
    CHECK (receipt->>'rebuild_verified' = 'true'),
    CHECK (starts_with(object_key, 'derived-mcap/' || organization_id || '/' || project_id || '/' || region_code || '/'))
);
SELECT core.apply_project_rls('ingest.recording_cache_retirements'::regclass);
REVOKE DELETE, TRUNCATE ON ingest.recording_cache_retirements FROM PUBLIC;
