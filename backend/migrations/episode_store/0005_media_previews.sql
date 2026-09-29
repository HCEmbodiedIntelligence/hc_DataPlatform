-- Display derivatives are separate from immutable RGB-D primary packages.
CREATE TABLE episode_store.media_previews (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    episode_id text NOT NULL,
    data_version_id text NOT NULL,
    feature text NOT NULL,
    profile_id text NOT NULL,
    source_hash text NOT NULL CHECK (source_hash ~ '^[0-9a-f]{64}$'),
    status text NOT NULL CHECK (status IN ('PREPARING', 'READY', 'FAILED')),
    receipt jsonb,
    failure_code text,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (organization_id, project_id, region_code, data_version_id, feature, profile_id),
    FOREIGN KEY (organization_id, project_id, region_code, dataset_id, episode_id, data_version_id)
        REFERENCES episode_store.versions
            (organization_id, project_id, region_code, dataset_id, episode_id, data_version_id),
    CHECK ((status = 'READY') = (receipt IS NOT NULL))
);
CREATE FUNCTION episode_store.immutable_ready_preview() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'READY' AND (TG_OP = 'DELETE' OR NEW IS DISTINCT FROM OLD) THEN
        RAISE EXCEPTION 'ready Episode preview is immutable';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER protect_media_previews BEFORE UPDATE OR DELETE ON episode_store.media_previews
    FOR EACH ROW EXECUTE FUNCTION episode_store.immutable_ready_preview();
SELECT core.apply_project_rls('episode_store.media_previews'::regclass);
