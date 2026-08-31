-- Register every immutable Raw upload as a format-neutral business source.
-- PostgreSQL stores only identity, object locations, lineage, and processing
-- state; Raw MCAP/capture bundles/LeRobot objects remain in object storage.

CREATE TABLE IF NOT EXISTS ingest.raw_sources (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    raw_source_id text NOT NULL,
    upload_id text NOT NULL,
    dataset_id text,
    collection_task_id text,
    robot_id text,
    source_format text NOT NULL
        CHECK (source_format IN ('MCAP', 'CAPTURE_BUNDLE', 'LEROBOT_V3')),
    source_format_version text NOT NULL CHECK (source_format_version <> ''),
    manifest_key text NOT NULL,
    storage_prefix text NOT NULL,
    content_hash char(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    file_count integer NOT NULL CHECK (file_count > 0),
    total_bytes bigint NOT NULL CHECK (total_bytes > 0),
    raw_status text NOT NULL
        CHECK (raw_status IN ('UPLOADING', 'COMMITTED', 'INVALID', 'DELETED')),
    processing_status text NOT NULL CHECK (processing_status IN (
        'PENDING', 'DISCOVERING_EPISODES', 'PROCESSING', 'READY',
        'PARTIALLY_FAILED', 'FAILED'
    )),
    created_at timestamptz NOT NULL,
    committed_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, raw_source_id),
    UNIQUE (organization_id, project_id, region_code, source_format, upload_id),
    UNIQUE (manifest_key),
    FOREIGN KEY (organization_id, project_id)
        REFERENCES registry.organization_projects (organization_id, project_id),
    CHECK (raw_source_id ~ '^[A-Za-z0-9._-]{1,128}$'),
    CHECK (dataset_id IS NULL OR dataset_id ~ '^[A-Za-z0-9._-]{1,128}$'),
    CHECK (collection_task_id IS NULL OR collection_task_id ~ '^[A-Za-z0-9._-]{1,128}$'),
    CHECK (robot_id IS NULL OR robot_id ~ '^[A-Za-z0-9._-]{1,128}$'),
    CHECK (manifest_key <> '' AND storage_prefix <> '')
);

CREATE INDEX IF NOT EXISTS raw_sources_pending_idx
ON ingest.raw_sources (
    organization_id, project_id, region_code, processing_status, committed_at, raw_source_id
)
WHERE raw_status = 'COMMITTED'
  AND processing_status IN ('PENDING', 'DISCOVERING_EPISODES', 'PROCESSING');

CREATE INDEX IF NOT EXISTS raw_sources_content_lookup_idx
ON ingest.raw_sources (
    organization_id, project_id, source_format, content_hash, committed_at DESC
);

CREATE TABLE IF NOT EXISTS ingest.raw_source_episodes (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    raw_source_id text NOT NULL,
    episode_id text NOT NULL,
    source_episode_index integer NOT NULL CHECK (source_episode_index >= 0),
    status text NOT NULL CHECK (status IN ('PENDING', 'PROCESSING', 'READY', 'FAILED')),
    frame_count bigint CHECK (frame_count > 0),
    dataset_version integer CHECK (dataset_version > 0),
    lance_version integer CHECK (lance_version > 0),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (
        organization_id, project_id, region_code, raw_source_id, episode_id
    ),
    UNIQUE (
        organization_id, project_id, region_code, raw_source_id, source_episode_index
    ),
    FOREIGN KEY (organization_id, project_id, region_code, raw_source_id)
        REFERENCES ingest.raw_sources (
            organization_id, project_id, region_code, raw_source_id
        ),
    CHECK (episode_id ~ '^[A-Za-z0-9._-]{1,128}$'),
    CHECK (
        (status = 'READY' AND dataset_version IS NOT NULL AND lance_version IS NOT NULL)
        OR status <> 'READY'
    )
);

CREATE INDEX IF NOT EXISTS raw_source_episodes_status_idx
ON ingest.raw_source_episodes (
    organization_id, project_id, region_code, raw_source_id, status, source_episode_index
);

CREATE TABLE IF NOT EXISTS ingest.raw_ingest_jobs (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    job_id text NOT NULL,
    raw_source_id text NOT NULL,
    workflow_id text,
    job_type text NOT NULL CHECK (job_type IN (
        'DIRECT_EPISODE_INGEST', 'CONTINUOUS_RECORDING_DISCOVERY', 'LEROBOT_IMPORT'
    )),
    adapter_name text NOT NULL CHECK (adapter_name IN ('mcap', 'capture_bundle', 'lerobot_v3')),
    status text NOT NULL CHECK (status IN (
        'PENDING', 'RUNNING', 'SUCCEEDED', 'PARTIALLY_FAILED', 'FAILED', 'CANCELLED'
    )),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error_code text CHECK (
        last_error_code IS NULL OR last_error_code ~ '^[A-Z0-9_]+$'
    ),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, job_id),
    UNIQUE (organization_id, project_id, region_code, raw_source_id),
    UNIQUE (workflow_id),
    FOREIGN KEY (organization_id, project_id, region_code, raw_source_id)
        REFERENCES ingest.raw_sources (
            organization_id, project_id, region_code, raw_source_id
        ),
    CHECK (job_id <> '')
);

CREATE INDEX IF NOT EXISTS raw_ingest_jobs_pending_idx
ON ingest.raw_ingest_jobs (
    organization_id, project_id, region_code, status, updated_at, job_id
)
WHERE status IN ('PENDING', 'RUNNING');

SELECT core.apply_project_rls('ingest.raw_sources'::regclass);
SELECT core.apply_project_rls('ingest.raw_source_episodes'::regclass);
SELECT core.apply_project_rls('ingest.raw_ingest_jobs'::regclass);

-- Existing MCAP/capture-bundle commits already have authoritative object and
-- organization identity. Backfill their registry records without inventing a
-- Dataset assignment that is only known later in the workflow.
INSERT INTO ingest.raw_sources (
    organization_id, project_id, region_code, raw_source_id, upload_id,
    dataset_id, collection_task_id, robot_id, source_format,
    source_format_version, manifest_key, storage_prefix, content_hash,
    file_count, total_bytes, raw_status, processing_status,
    created_at, committed_at, updated_at
)
SELECT
    object.organization_id,
    object.project_id,
    object.region_code,
    'upload-' || replace(session.session_id::text, '-', ''),
    session.session_id::text,
    platform_task.dataset_id,
    collection.task_id,
    rollout.robot_id,
    CASE
        WHEN discovery.preflight_json #>> '{manifest,processing_mode}' = 'CONTINUOUS_RECORDING'
            THEN 'CAPTURE_BUNDLE'
        ELSE 'MCAP'
    END,
    CASE
        WHEN discovery.preflight_json #>> '{manifest,processing_mode}' = 'CONTINUOUS_RECORDING'
            THEN 'v1'
        ELSE '1.0'
    END,
    object.manifest_key,
    COALESCE(
        NULLIF(regexp_replace(object.object_key, '/[^/]+$', ''), object.object_key),
        object.object_key
    ),
    object.source_sha256,
    1,
    object.file_size,
    'COMMITTED',
    CASE
        WHEN workflow_job.status = 'SUCCEEDED' THEN 'READY'
        WHEN workflow_job.status IN ('QUALITY_RISK', 'QUALITY_REJECTED')
            THEN 'PARTIALLY_FAILED'
        WHEN workflow_job.status IN ('TECHNICAL_FAILED', 'CANCELLED') THEN 'FAILED'
        WHEN workflow_job.status = 'RUNNING' THEN 'PROCESSING'
        ELSE 'PENDING'
    END,
    session.created_at,
    object.committed_at,
    GREATEST(session.updated_at, object.committed_at)
FROM ingest.rollout_objects object
JOIN ingest.upload_sessions session
  ON session.organization_id = object.organization_id
 AND session.project_id = object.project_id
 AND session.region_code = object.region_code
 AND session.rollout_id = object.rollout_id
JOIN ingest.rollouts rollout
  ON rollout.organization_id = object.organization_id
 AND rollout.project_id = object.project_id
 AND rollout.region_code = object.region_code
 AND rollout.rollout_id = object.rollout_id
JOIN ingest.collection_jobs collection
  ON collection.organization_id = rollout.organization_id
 AND collection.project_id = rollout.project_id
 AND collection.region_code = rollout.region_code
 AND collection.collection_job_id = rollout.collection_job_id
LEFT JOIN collection_tasks.collection_tasks platform_task
  ON platform_task.organization_id = collection.organization_id
 AND platform_task.project_id = collection.project_id
 AND platform_task.collection_task_id = collection.task_id
LEFT JOIN ingest.manifest_discoveries discovery
  ON discovery.organization_id = session.organization_id
 AND discovery.project_id = session.project_id
 AND discovery.region_code = session.region_code
 AND discovery.session_id = session.session_id
LEFT JOIN ingest.workflow_triggers trigger
  ON trigger.organization_id = session.organization_id
 AND trigger.project_id = session.project_id
 AND trigger.region_code = session.region_code
 AND trigger.session_id = session.session_id
LEFT JOIN workflow.jobs workflow_job
  ON workflow_job.organization_id = session.organization_id
 AND workflow_job.project_id = session.project_id
 AND workflow_job.workflow_id = trigger.workflow_id
ON CONFLICT (organization_id, project_id, region_code, raw_source_id) DO NOTHING;

INSERT INTO ingest.raw_source_episodes (
    organization_id, project_id, region_code, raw_source_id, episode_id,
    source_episode_index, status, frame_count, dataset_version, lance_version,
    created_at, updated_at
)
SELECT
    source.organization_id,
    source.project_id,
    source.region_code,
    source.raw_source_id,
    object.rollout_id,
    0,
    CASE
        WHEN source.processing_status = 'READY' THEN 'READY'
        WHEN source.processing_status = 'PROCESSING' THEN 'PROCESSING'
        WHEN source.processing_status IN ('PARTIALLY_FAILED', 'FAILED') THEN 'FAILED'
        ELSE 'PENDING'
    END,
    NULL,
    CASE
        WHEN source.processing_status = 'READY'
         AND (workflow_job.result #>> '{dataset_version,version}') ~ '^[1-9][0-9]*$'
            THEN (workflow_job.result #>> '{dataset_version,version}')::integer
        ELSE NULL
    END,
    CASE
        WHEN source.processing_status = 'READY'
         AND (workflow_job.result #>> '{derived,lance_version}') ~ '^[1-9][0-9]*$'
            THEN (workflow_job.result #>> '{derived,lance_version}')::integer
        ELSE NULL
    END,
    source.created_at,
    source.updated_at
FROM ingest.raw_sources source
JOIN ingest.upload_sessions session
  ON session.organization_id = source.organization_id
 AND session.project_id = source.project_id
 AND session.region_code = source.region_code
 AND session.session_id::text = source.upload_id
JOIN ingest.rollout_objects object
  ON object.organization_id = session.organization_id
 AND object.project_id = session.project_id
 AND object.region_code = session.region_code
 AND object.rollout_id = session.rollout_id
LEFT JOIN ingest.workflow_triggers trigger
  ON trigger.organization_id = session.organization_id
 AND trigger.project_id = session.project_id
 AND trigger.region_code = session.region_code
 AND trigger.session_id = session.session_id
LEFT JOIN workflow.jobs workflow_job
  ON workflow_job.organization_id = session.organization_id
 AND workflow_job.project_id = session.project_id
 AND workflow_job.workflow_id = trigger.workflow_id
WHERE source.source_format = 'MCAP'
  AND (
      source.processing_status <> 'READY'
      OR (
          (workflow_job.result #>> '{dataset_version,version}') ~ '^[1-9][0-9]*$'
          AND (workflow_job.result #>> '{derived,lance_version}') ~ '^[1-9][0-9]*$'
      )
  )
ON CONFLICT (
    organization_id, project_id, region_code, raw_source_id, episode_id
) DO NOTHING;

INSERT INTO ingest.raw_ingest_jobs (
    organization_id, project_id, region_code, job_id, raw_source_id,
    workflow_id, job_type, adapter_name, status, attempts, last_error_code,
    created_at, updated_at
)
SELECT
    source.organization_id,
    source.project_id,
    source.region_code,
    'raw-job-' || replace(session.session_id::text, '-', ''),
    source.raw_source_id,
    trigger.workflow_id,
    CASE
        WHEN source.source_format = 'CAPTURE_BUNDLE'
            THEN 'CONTINUOUS_RECORDING_DISCOVERY'
        ELSE 'DIRECT_EPISODE_INGEST'
    END,
    CASE
        WHEN source.source_format = 'CAPTURE_BUNDLE' THEN 'capture_bundle'
        ELSE 'mcap'
    END,
    CASE
        WHEN source.processing_status = 'READY' THEN 'SUCCEEDED'
        WHEN source.processing_status = 'PARTIALLY_FAILED' THEN 'PARTIALLY_FAILED'
        WHEN source.processing_status = 'FAILED' THEN 'FAILED'
        WHEN source.processing_status = 'PROCESSING' THEN 'RUNNING'
        ELSE 'PENDING'
    END,
    COALESCE(trigger.attempts, 0),
    trigger.last_error_code,
    source.created_at,
    source.updated_at
FROM ingest.raw_sources source
JOIN ingest.upload_sessions session
  ON session.organization_id = source.organization_id
 AND session.project_id = source.project_id
 AND session.region_code = source.region_code
 AND session.session_id::text = source.upload_id
LEFT JOIN ingest.workflow_triggers trigger
  ON trigger.organization_id = session.organization_id
 AND trigger.project_id = session.project_id
 AND trigger.region_code = session.region_code
 AND trigger.session_id = session.session_id
WHERE source.source_format IN ('MCAP', 'CAPTURE_BUNDLE')
ON CONFLICT (organization_id, project_id, region_code, raw_source_id) DO NOTHING;

-- MCAP already runs through workflow.jobs. Mirror that authoritative workflow
-- state into the format-neutral Raw registry so status reads never need OSS
-- scans and do not diverge from the common processing chain.
CREATE OR REPLACE FUNCTION ingest.sync_raw_source_workflow_job()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    mapped_job_status text;
    mapped_processing_status text;
    mapped_episode_status text;
    receipt_dataset_version integer;
    receipt_lance_version integer;
BEGIN
    IF NEW.status = 'SUCCEEDED' THEN
        IF (NEW.result #>> '{dataset_version,version}') ~ '^[1-9][0-9]*$'
           AND (NEW.result #>> '{derived,lance_version}') ~ '^[1-9][0-9]*$'
        THEN
            mapped_job_status := 'SUCCEEDED';
            mapped_processing_status := 'READY';
            mapped_episode_status := 'READY';
            receipt_dataset_version := (NEW.result #>> '{dataset_version,version}')::integer;
            receipt_lance_version := (NEW.result #>> '{derived,lance_version}')::integer;
        ELSE
            mapped_job_status := 'PARTIALLY_FAILED';
            mapped_processing_status := 'PARTIALLY_FAILED';
            mapped_episode_status := 'FAILED';
        END IF;
    ELSIF NEW.status IN ('QUALITY_RISK', 'QUALITY_REJECTED') THEN
        mapped_job_status := 'PARTIALLY_FAILED';
        mapped_processing_status := 'PARTIALLY_FAILED';
        mapped_episode_status := 'FAILED';
    ELSIF NEW.status IN ('TECHNICAL_FAILED', 'CANCELLED') THEN
        mapped_job_status := CASE WHEN NEW.status = 'CANCELLED' THEN 'CANCELLED' ELSE 'FAILED' END;
        mapped_processing_status := 'FAILED';
        mapped_episode_status := 'FAILED';
    ELSIF NEW.status = 'RUNNING' THEN
        mapped_job_status := 'RUNNING';
        mapped_processing_status := 'PROCESSING';
        mapped_episode_status := 'PROCESSING';
    ELSE
        mapped_job_status := 'PENDING';
        mapped_processing_status := 'PENDING';
        mapped_episode_status := 'PENDING';
    END IF;

    UPDATE ingest.raw_ingest_jobs job
       SET status = mapped_job_status,
           attempts = NEW.attempt,
           last_error_code = COALESCE(
               NEW.error_code,
               CASE
                   WHEN NEW.status = 'SUCCEEDED' AND mapped_job_status <> 'SUCCEEDED'
                       THEN 'RAW_SOURCE_RECEIPT_MISSING'
                   ELSE NULL
               END
           ),
           updated_at = NEW.updated_at
     WHERE job.organization_id = NEW.organization_id
       AND job.project_id = NEW.project_id
       AND job.workflow_id = NEW.workflow_id;

    IF FOUND THEN
        UPDATE ingest.raw_sources source
           SET processing_status = mapped_processing_status,
               updated_at = NEW.updated_at
         WHERE source.organization_id = NEW.organization_id
           AND source.project_id = NEW.project_id
           AND EXISTS (
               SELECT 1
                 FROM ingest.raw_ingest_jobs job
                WHERE job.organization_id = source.organization_id
                  AND job.project_id = source.project_id
                  AND job.region_code = source.region_code
                  AND job.raw_source_id = source.raw_source_id
                  AND job.workflow_id = NEW.workflow_id
           );

        UPDATE ingest.raw_source_episodes episode
           SET status = mapped_episode_status,
               dataset_version = CASE
                   WHEN mapped_episode_status = 'READY' THEN receipt_dataset_version
                   ELSE NULL
               END,
               lance_version = CASE
                   WHEN mapped_episode_status = 'READY' THEN receipt_lance_version
                   ELSE NULL
               END,
               updated_at = NEW.updated_at
         WHERE episode.organization_id = NEW.organization_id
           AND episode.project_id = NEW.project_id
           AND EXISTS (
               SELECT 1
                 FROM ingest.raw_ingest_jobs job
                WHERE job.organization_id = episode.organization_id
                  AND job.project_id = episode.project_id
                  AND job.region_code = episode.region_code
                  AND job.raw_source_id = episode.raw_source_id
                  AND job.workflow_id = NEW.workflow_id
           );
    END IF;
    RETURN NEW;
END
$function$;

DROP TRIGGER IF EXISTS workflow_jobs_sync_raw_source ON workflow.jobs;
CREATE TRIGGER workflow_jobs_sync_raw_source
AFTER INSERT OR UPDATE OF status, stage, attempt, result, error_code, updated_at
ON workflow.jobs
FOR EACH ROW EXECUTE FUNCTION ingest.sync_raw_source_workflow_job();

-- Location, format, hash, and ownership are immutable lineage. Workers may only
-- advance status/receipts and timestamps after registration.
CREATE OR REPLACE FUNCTION ingest.protect_raw_source_identity()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.organization_id IS DISTINCT FROM OLD.organization_id
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.region_code IS DISTINCT FROM OLD.region_code
       OR NEW.raw_source_id IS DISTINCT FROM OLD.raw_source_id
       OR NEW.upload_id IS DISTINCT FROM OLD.upload_id
       OR NEW.dataset_id IS DISTINCT FROM OLD.dataset_id
       OR NEW.collection_task_id IS DISTINCT FROM OLD.collection_task_id
       OR NEW.robot_id IS DISTINCT FROM OLD.robot_id
       OR NEW.source_format IS DISTINCT FROM OLD.source_format
       OR NEW.source_format_version IS DISTINCT FROM OLD.source_format_version
       OR NEW.manifest_key IS DISTINCT FROM OLD.manifest_key
       OR NEW.storage_prefix IS DISTINCT FROM OLD.storage_prefix
       OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
       OR NEW.file_count IS DISTINCT FROM OLD.file_count
       OR NEW.total_bytes IS DISTINCT FROM OLD.total_bytes
       OR NEW.raw_status IS DISTINCT FROM OLD.raw_status
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.committed_at IS DISTINCT FROM OLD.committed_at
    THEN
        RAISE EXCEPTION 'Raw source immutable identity cannot be changed';
    END IF;
    RETURN NEW;
END
$function$;

DROP TRIGGER IF EXISTS raw_sources_immutable_identity ON ingest.raw_sources;
CREATE TRIGGER raw_sources_immutable_identity
BEFORE UPDATE ON ingest.raw_sources
FOR EACH ROW EXECUTE FUNCTION ingest.protect_raw_source_identity();
