-- A visibility pointer must never reference an uncommitted package, including
-- direct SQL mistakes outside the normal committer. Null is allowed on creation.
CREATE FUNCTION episode_store.require_committed_pointer() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.current_data_version_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM episode_store.versions v
        WHERE v.organization_id=NEW.organization_id AND v.project_id=NEW.project_id
          AND v.region_code=NEW.region_code AND v.dataset_id=NEW.dataset_id
          AND v.episode_id=NEW.episode_id AND v.data_version_id=NEW.current_data_version_id
          AND v.status='COMMITTED'
    ) THEN
        RAISE EXCEPTION 'Episode visibility pointer requires a committed package';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER protect_episode_pointer BEFORE INSERT OR UPDATE ON episode_store.bindings
    FOR EACH ROW EXECUTE FUNCTION episode_store.require_committed_pointer();
