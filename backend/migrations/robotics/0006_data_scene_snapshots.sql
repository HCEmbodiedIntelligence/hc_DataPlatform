-- Frozen processing-time model provenance. No updates; foreign key retains model assets
-- through the owning model version's existing transactional delete protocol.
CREATE TABLE robotics.data_scene_snapshots (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    source_key text NOT NULL CHECK (source_key ~ '^[0-9a-f]{64}$'),
    model_version_id text,
    document jsonb NOT NULL,
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, source_key),
    FOREIGN KEY (organization_id, model_version_id)
        REFERENCES registry.robot_model_versions (organization_id, version_id)
);
SELECT core.apply_project_rls('robotics.data_scene_snapshots'::regclass);
CREATE FUNCTION robotics.reject_scene_snapshot_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'data scene snapshots are immutable';
END;
$$;
CREATE TRIGGER data_scene_snapshot_immutable BEFORE UPDATE OR DELETE
ON robotics.data_scene_snapshots FOR EACH ROW EXECUTE FUNCTION robotics.reject_scene_snapshot_change();
