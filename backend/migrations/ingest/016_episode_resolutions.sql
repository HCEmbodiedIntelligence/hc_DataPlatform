-- An explicit user decision affects one Episode, never its shared Raw objects.
ALTER TABLE ingest.raw_source_episodes DROP CONSTRAINT raw_source_episodes_status_check;
ALTER TABLE ingest.raw_source_episodes ADD CONSTRAINT raw_source_episodes_status_check
    CHECK (status IN ('PENDING', 'PROCESSING', 'READY', 'FAILED', 'DISCARDED'));

CREATE TABLE ingest.episode_resolutions (
    resolution_id text PRIMARY KEY,
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    raw_source_id text NOT NULL,
    episode_id text NOT NULL,
    action text NOT NULL CHECK (action IN ('REPROCESS', 'DISCARD')),
    status text NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'DISCARDED')),
    previous_attempt_id text NOT NULL,
    workflow_id text,
    actor_id text NOT NULL,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (organization_id, project_id, region_code, raw_source_id, episode_id)
        REFERENCES ingest.raw_source_episodes
            (organization_id, project_id, region_code, raw_source_id, episode_id)
);
CREATE INDEX episode_resolutions_latest ON ingest.episode_resolutions
    (organization_id, project_id, region_code, raw_source_id, episode_id, created_at DESC);
SELECT core.apply_project_rls('ingest.episode_resolutions'::regclass);
