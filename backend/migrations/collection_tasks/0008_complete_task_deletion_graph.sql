-- Extend the reviewed task-data graph as capture formats and plans evolve.
-- Shared schemes, robot mappings, calibration definitions and schema versions
-- remain immutable. Only their links to this task's Dataset are removed.
DO $migration$
DECLARE relation_name text; guard regprocedure; definition text;
BEGIN
    FOREACH relation_name IN ARRAY ARRAY[
        'collection_tasks.capture_plans',
        'collection_tasks.capture_plan_heads',
        'ingest.raw_capture_snapshots',
        'ingest.capture_recording_snapshots',
        'ingest.capture_reprocess_requests',
        'ingest.recording_source_links',
        'ingest.robot_task_data_requirements',
        'episode_store.dataset_capture_contracts',
        'episode_store.dataset_capture_formats',
        'episode_store.dataset_schema_versions',
        'calibrations.calibration_dataset_version_associations',
        'data_schemas.stream_schema_dataset_version_references',
        'annotation.collection_task_tag_schema_bindings'
    ] LOOP
        EXECUTE format(
            'CREATE TRIGGER collection_task_deletion_fence BEFORE INSERT OR UPDATE ON %s '
            'FOR EACH ROW EXECUTE FUNCTION collection_tasks.reject_deleted_task_write()',
            relation_name::regclass
        );
    END LOOP;

    -- Preserve immutable history guards, permitting only DELETE authorized by
    -- the durable task purge manifest in the current organization/project.
    -- Shared tables using the same guard have no purge targets and stay protected.
    FOR guard IN
        SELECT DISTINCT tgfoid::regprocedure FROM pg_trigger
        WHERE NOT tgisinternal AND (tgtype & 8) <> 0 AND (tgtype & 2) <> 0
          AND tgrelid IN (
              SELECT tgrelid FROM pg_trigger WHERE tgname='collection_task_deletion_fence'
          )
    LOOP
        definition := pg_get_functiondef(guard);
        IF position('collection_tasks.purge_permitted' IN definition) = 0 THEN
            definition := regexp_replace(definition, E'\\mBEGIN\\M',
                E'BEGIN\n    IF TG_OP = ''DELETE'' AND collection_tasks.purge_permitted(TG_TABLE_SCHEMA||''.''||TG_TABLE_NAME,to_jsonb(OLD)) THEN RETURN OLD; END IF;', 'i');
            EXECUTE definition;
        END IF;
    END LOOP;
END $migration$;
