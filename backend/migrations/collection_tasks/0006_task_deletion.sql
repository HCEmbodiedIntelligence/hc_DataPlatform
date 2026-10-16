-- A task purge is an explicit, audited exception to data immutability.
-- Selectors remain as tombstones to fence stale workers after data removal.
CREATE TABLE collection_tasks.deletions (
    organization_id text NOT NULL, project_id text NOT NULL, region_code text NOT NULL,
    collection_task_id text NOT NULL, idempotency_key text NOT NULL,
    state text NOT NULL CHECK (state IN ('DELETING','FAILED','DELETED')),
    manifest jsonb NOT NULL, result jsonb NOT NULL, requested_by text,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id,project_id,collection_task_id),
    UNIQUE (organization_id,project_id,idempotency_key)
);
CREATE TABLE collection_tasks.deletion_targets (
    organization_id text NOT NULL, project_id text NOT NULL, collection_task_id text NOT NULL,
    relation text NOT NULL, selector jsonb NOT NULL CHECK (selector <> '{}'::jsonb),
    PRIMARY KEY (organization_id,project_id,collection_task_id,relation,selector),
    FOREIGN KEY (organization_id,project_id,collection_task_id)
        REFERENCES collection_tasks.deletions
);
CREATE INDEX deletion_target_relation ON collection_tasks.deletion_targets
    (organization_id,project_id,relation);
SELECT core.apply_project_rls('collection_tasks.deletions'::regclass);
SELECT core.apply_project_rls('collection_tasks.deletion_targets'::regclass);

CREATE FUNCTION collection_tasks.purge_permitted(rel text, row_data jsonb)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM collection_tasks.deletion_targets t
        JOIN collection_tasks.deletions d USING (organization_id,project_id,collection_task_id)
        WHERE t.organization_id=current_setting('app.organization_id',true)
          AND t.project_id=current_setting('app.project_id',true)
          AND t.collection_task_id=current_setting('app.collection_task_purge_id',true)
          AND d.state IN ('DELETING','FAILED') AND t.relation=rel
          AND row_data @> t.selector
    )
$$;
CREATE FUNCTION collection_tasks.reject_deleted_task_write()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NOT collection_tasks.purge_permitted(TG_TABLE_SCHEMA||'.'||TG_TABLE_NAME,to_jsonb(NEW))
       AND EXISTS (
        SELECT 1 FROM collection_tasks.deletion_targets t
        WHERE t.organization_id=current_setting('app.organization_id',true)
          AND t.project_id=current_setting('app.project_id',true)
          AND t.relation=TG_TABLE_SCHEMA||'.'||TG_TABLE_NAME
          AND to_jsonb(NEW) @> t.selector
    ) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='COLLECTION_TASK_DELETED';
    END IF;
    RETURN NEW;
END $$;

-- Install the writer fence only on the reviewed task-data ownership graph.
DO $migration$
DECLARE relation_name text;
BEGIN
    FOREACH relation_name IN ARRAY ARRAY[
        'collection_tasks.collection_tasks',
        'ingest.collection_jobs',
        'ingest.rollouts',
        'ingest.upload_sessions',
        'ingest.upload_objects',
        'ingest.upload_parts',
        'ingest.rollout_objects',
        'ingest.manifest_discoveries',
        'ingest.workflow_triggers',
        'ingest.device_capture_facts',
        'ingest.robot_ingest_uploads',
        'ingest.robot_ingest_assets',
        'ingest.robot_ingest_camera_metrics',
        'ingest.robot_ingest_attempts',
        'ingest.raw_sources',
        'ingest.raw_ingest_jobs',
        'ingest.raw_source_episodes',
        'ingest.episode_resolutions',
        'ingest.robot_processing',
        'ingest.robot_processing_requests',
        'ingest.mcap_processing_files',
        'ingest.continuous_recordings',
        'ingest.recording_uploads',
        'ingest.recording_upload_assets',
        'ingest.recording_slice_revisions',
        'ingest.recording_episode_slices',
        'ingest.recording_episode_asset_windows',
        'ingest.recording_episode_processing',
        'ingest.recording_episode_qc_reports',
        'ingest.recording_episode_dataset_version_reservations',
        'dataset_registry.datasets',
        'dataset_registry.dataset_detail_facts',
        'dataset_registry.dataset_versions',
        'dataset_registry.dataset_version_async_jobs',
        'dataset_registry.dataset_version_capacity_facts',
        'dataset_registry.dataset_version_content_projections',
        'dataset_registry.dataset_version_episode_revisions',
        'dataset_registry.dataset_version_episodes',
        'dataset_registry.dataset_version_manifest_entries',
        'dataset_registry.dataset_version_operational_inventory',
        'dataset_registry.dataset_version_required_storage',
        'dataset_registry.dataset_version_review_decisions',
        'dataset_registry.dataset_version_review_findings',
        'dataset_registry.dataset_version_schema_details',
        'dataset_registry.dataset_version_schema_summaries',
        'dataset_registry.dataset_version_source_provenance',
        'dataset_registry.dataset_version_successor_drafts',
        'episode_store.bindings',
        'episode_store.versions',
        'episode_store.attempts',
        'episode_store.events',
        'episode_store.collections',
        'episode_store.collection_events',
        'episode_store.collection_heads',
        'episode_store.collection_publications',
        'episode_store.dataset_schemas',
        'episode_store.media_previews',
        'episode_store.data_reviews',
        'episode_store.qc_evidence',
        'episode_store.delivery_jobs',
        'episode_store.delivery_requests',
        'episode_store.tag_derivation_jobs',
        'episode_store.tag_derivation_requests',
        'episode_store.migration_batches',
        'episode_store.annotation_migrations',
        'episode_store.collection_releases',
        'annotation.annotation_tasks',
        'annotation.annotation_revisions',
        'annotation.annotation_operations',
        'annotation.annotation_reviews',
        'annotation.annotation_mutations',
        'annotation.annotation_submissions',
        'annotation.annotation_submission_mutations',
        'annotation.annotation_task_triggers',
        'annotation.auto_annotation_jobs',
        'annotation.revision_files',
        'annotation.delivery_jobs',
        'annotation.delivery_requests',
        'annotation.frame_selection_manifests',
        'annotation.legacy_cleaning_migrations',
        'annotation.tag_schema_bindings',
        'manual_cleaning.manual_issues',
        'manual_cleaning.manual_issue_draft_links',
        'manual_cleaning.cleaning_workbench_drafts',
        'manual_cleaning.cleaning_draft_ancestry',
        'manual_cleaning.cleaning_draft_edl_revisions',
        'manual_cleaning.cleaning_draft_previews',
        'manual_cleaning.cleaning_workbench_commits',
        'manual_cleaning.cleaning_workbench_jobs',
        'manual_cleaning.cleaning_commit_output_revisions',
        'publishing.dataset_versions',
        'publishing.publication_assets',
        'publishing.export_attempts',
        'publishing.published_exports',
        'publishing.rollout_publication_lineage',
        'publishing.episode_version_finalizations',
        'public.aligned_fragment_attempts',
        'public.qc_reports',
        'public.quality_rollout_summaries',
        'public.raw_verification_reports',
        'public.lance_datasets',
        'public.lance_dataset_versions',
        'public.lance_rollout_lineage',
        'public.lance_pending_reconciliation',
        'public.lance_schema_snapshots',
        'preview.artifacts',
        'preview.jobs',
        'preview.sessions',
        'aligned_media.artifacts',
        'aligned_media.jobs',
        'aligned_media.dataset_version_retirement_requests',
        'workflow.jobs',
        'workflow.reconciliation_items',
        'core.outbox_events',
        'robotics.data_scene_snapshots',
        'storage.managed_objects',
        'storage.managed_multipart_uploads',
        'storage.object_operations'
    ] LOOP
        EXECUTE format('CREATE TRIGGER collection_task_deletion_fence BEFORE INSERT OR UPDATE ON %s FOR EACH ROW EXECUTE FUNCTION collection_tasks.reject_deleted_task_write()',relation_name::regclass);
    END LOOP;
END $migration$;

-- Preserve each existing immutable guard in full. Only DELETE of a row in the
-- current transaction's durable, tenant-scoped purge manifest can pass it.
DO $migration$
DECLARE guard regprocedure; definition text;
BEGIN
    FOR guard IN
        SELECT DISTINCT tgfoid::regprocedure FROM pg_trigger
        WHERE NOT tgisinternal AND (tgtype & 8) <> 0
          AND ((tgtype & 2) <> 0 OR tgname='dataset_version_aligned_media_retirement')
          AND tgrelid IN (
              SELECT tgrelid FROM pg_trigger WHERE tgname='collection_task_deletion_fence'
          )
    LOOP
        definition := pg_get_functiondef(guard);
        definition := regexp_replace(definition, E'\\mBEGIN\\M',
            E'BEGIN\n    IF TG_OP = ''DELETE'' AND collection_tasks.purge_permitted(TG_TABLE_SCHEMA||''.''||TG_TABLE_NAME,to_jsonb(OLD)) THEN RETURN OLD; END IF;', 'i');
        EXECUTE definition;
    END LOOP;
END $migration$;
