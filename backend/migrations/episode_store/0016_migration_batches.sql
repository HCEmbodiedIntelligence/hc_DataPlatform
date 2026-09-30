-- Additive operator checkpoints. Rollback never removes immutable primary data.
CREATE TABLE episode_store.migration_batches (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    batch_id text NOT NULL CHECK (batch_id ~ '^mb_[0-9a-f]{64}$'),
    plan jsonb NOT NULL,
    status text NOT NULL DEFAULT 'PREPARED'
        CHECK (status IN ('PREPARED','PUBLISHED','ROLLED_BACK')),
    publication jsonb,
    rollback jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (organization_id,project_id,region_code,dataset_id,batch_id),
    CHECK ((status='PREPARED')=(publication IS NULL)),
    CHECK ((status='ROLLED_BACK')=(rollback IS NOT NULL))
);
SELECT core.apply_project_rls('episode_store.migration_batches'::regclass);

CREATE FUNCTION episode_store.guard_migration_batch() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE member jsonb;
DECLARE active_head text;
BEGIN
    IF current_setting('app.service_identity',true) IS DISTINCT FROM 'true'
       OR NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.organization_id',true),'')
       OR NEW.project_id IS DISTINCT FROM NULLIF(current_setting('app.project_id',true),'')
       OR NEW.region_code IS DISTINCT FROM NULLIF(current_setting('app.region_code',true),'') THEN
        RAISE EXCEPTION 'migration batch requires exact operator scope' USING ERRCODE='42501';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.status<>'PREPARED' OR NEW.plan->>'format' IS DISTINCT FROM 'hc-episode-migration-batch/1'
           OR NEW.plan->>'batch_id' IS DISTINCT FROM NEW.batch_id
           OR NEW.plan->'scope' IS DISTINCT FROM jsonb_build_array(
               NEW.organization_id,NEW.project_id,NEW.region_code,NEW.dataset_id)
           OR jsonb_array_length(NEW.plan->'members') NOT BETWEEN 1 AND 1000 THEN
            RAISE EXCEPTION 'invalid initial migration batch' USING ERRCODE='23514';
        END IF;
    ELSE
        IF ROW(NEW.organization_id,NEW.project_id,NEW.region_code,NEW.dataset_id,NEW.batch_id,
               NEW.plan,NEW.created_at) IS DISTINCT FROM
           ROW(OLD.organization_id,OLD.project_id,OLD.region_code,OLD.dataset_id,OLD.batch_id,
               OLD.plan,OLD.created_at)
           OR (OLD.publication IS NOT NULL AND NEW.publication IS DISTINCT FROM OLD.publication)
           OR NOT ((OLD.status='PREPARED' AND NEW.status='PUBLISHED')
                OR (OLD.status='PUBLISHED' AND NEW.status='ROLLED_BACK')) THEN
            RAISE EXCEPTION 'migration plan/receipt immutable or invalid transition' USING ERRCODE='23514';
        END IF;
        SELECT collection_version_id INTO active_head FROM episode_store.collection_heads
          WHERE organization_id=NEW.organization_id AND project_id=NEW.project_id
            AND region_code=NEW.region_code AND dataset_id=NEW.dataset_id;
        IF NEW.status='PUBLISHED' THEN
            IF active_head IS DISTINCT FROM NEW.plan->'collection'->>'collection_version_id'
               OR NEW.publication->'collection' IS DISTINCT FROM NEW.plan->'collection'
               OR NOT EXISTS (SELECT 1 FROM dataset_registry.dataset_versions v
                   WHERE v.organization_id=NEW.organization_id AND v.project_id=NEW.project_id
                     AND v.region_code=NEW.region_code AND v.dataset_id=NEW.dataset_id
                     AND v.version_id='version_'||active_head AND v.version_scope='INTERNAL'
                     AND v.version_status='READY') THEN
                RAISE EXCEPTION 'migration publication lacks atomic head/product projection' USING ERRCODE='23514';
            END IF;
        ELSE
            IF active_head IS DISTINCT FROM NEW.plan->'previous_collection'->>'collection_version_id' THEN
                RAISE EXCEPTION 'migration rollback head differs' USING ERRCODE='23514';
            END IF;
        END IF;
    END IF;
    FOR member IN SELECT value FROM jsonb_array_elements(NEW.plan->'members') LOOP
        -- Conversion service checks full plan hash and all bytes. The database
        -- also requires the exact existing committed receipt in this scope.
        IF NOT EXISTS (SELECT 1 FROM episode_store.versions v
            WHERE v.organization_id=NEW.organization_id AND v.project_id=NEW.project_id
              AND v.region_code=NEW.region_code AND v.dataset_id=NEW.dataset_id
              AND v.status='COMMITTED' AND v.receipt_document=member->'receipt') THEN
            RAISE EXCEPTION 'migration member lacks exact committed receipt' USING ERRCODE='23514';
        END IF;
        IF NEW.status<>'PREPARED' AND NOT EXISTS (SELECT 1 FROM episode_store.bindings b
            WHERE b.organization_id=NEW.organization_id AND b.project_id=NEW.project_id
              AND b.region_code=NEW.region_code AND b.dataset_id=NEW.dataset_id
              AND b.episode_id=member->'receipt'->'data'->'identity'->>'episode_id'
              AND b.current_data_version_id IS NOT DISTINCT FROM CASE WHEN NEW.status='PUBLISHED'
                THEN member->'receipt'->'data'->>'data_version_id'
                ELSE NEW.plan->'previous_pointers'->>(b.episode_id) END) THEN
            RAISE EXCEPTION 'migration member pointer differs' USING ERRCODE='23514';
        END IF;
    END LOOP;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END $$;
CREATE TRIGGER migration_batch_guard BEFORE INSERT OR UPDATE ON episode_store.migration_batches
    FOR EACH ROW EXECUTE FUNCTION episode_store.guard_migration_batch();
CREATE TRIGGER migration_batch_no_delete BEFORE DELETE ON episode_store.migration_batches
    FOR EACH ROW EXECUTE FUNCTION episode_store.reject_delivery_change();
