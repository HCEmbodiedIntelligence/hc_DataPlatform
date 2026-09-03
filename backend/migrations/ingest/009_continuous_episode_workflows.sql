-- Turn finalized continuous-recording Episodes into durable workflow-owned work.
-- Existing PENDING_QC rows are backfilled and enqueued so deployment of this
-- migration also repairs Episodes finalized before the worker path existed.

ALTER TABLE ingest.recording_episode_processing
    ADD COLUMN IF NOT EXISTS workflow_id text,
    ADD COLUMN IF NOT EXISTS event_id uuid,
    ADD COLUMN IF NOT EXISTS dataset_id text,
    ADD COLUMN IF NOT EXISTS dataset_version integer,
    ADD COLUMN IF NOT EXISTS lance_version integer,
    ADD COLUMN IF NOT EXISTS annotation_task_id text,
    ADD COLUMN IF NOT EXISTS aligned_media_camera_count integer,
    ADD COLUMN IF NOT EXISTS failure_code text,
    ADD COLUMN IF NOT EXISTS failure_stage text;

-- The retired public transition endpoint could mark an Episode READY without
-- Dataset/media/task receipts. Do not grandfather that fabricated readiness.
UPDATE ingest.recording_episode_processing
   SET status = 'FAILED',
       failure_code = 'LEGACY_READY_UNVERIFIED',
       failure_stage = 'legacy_transition'
 WHERE status = 'READY'
   AND (
       dataset_id IS NULL OR dataset_version IS NULL OR lance_version IS NULL
       OR annotation_task_id IS NULL OR aligned_media_camera_count IS NULL
   );

WITH identities AS (
    SELECT
        organization_id,
        project_id,
        region_code,
        recording_id,
        episode_id,
        'continuous-episode:v1:' || project_id || ':' || region_code || '%2F'
            || recording_id || '%2F' || episode_id AS workflow_id,
        md5(
            'continuous-recording-episode-workflow/v1:' || organization_id || ':'
            || project_id || ':' || region_code || ':' || recording_id || ':'
            || episode_id || ':' || finalized_revision::text
        ) AS event_hash
    FROM ingest.recording_episode_processing
)
UPDATE ingest.recording_episode_processing processing
   SET workflow_id = identities.workflow_id,
       event_id = (
           substr(identities.event_hash, 1, 8) || '-'
           || substr(identities.event_hash, 9, 4) || '-'
           || substr(identities.event_hash, 13, 4) || '-'
           || substr(identities.event_hash, 17, 4) || '-'
           || substr(identities.event_hash, 21, 12)
       )::uuid,
       failure_code = CASE
           WHEN processing.status IN ('QC_FAILED', 'FAILED')
               THEN COALESCE(processing.failure_code, 'LEGACY_PROCESSING_FAILURE')
           ELSE processing.failure_code
       END,
       failure_stage = CASE
           WHEN processing.status = 'QC_FAILED'
               THEN COALESCE(processing.failure_stage, 'qc')
           WHEN processing.status = 'FAILED'
               THEN COALESCE(processing.failure_stage, 'alignment')
           ELSE processing.failure_stage
       END
  FROM identities
 WHERE processing.organization_id = identities.organization_id
   AND processing.project_id = identities.project_id
   AND processing.region_code = identities.region_code
   AND processing.recording_id = identities.recording_id
   AND processing.episode_id = identities.episode_id;

UPDATE ingest.recording_episode_processing
   SET episode_document = episode_document
       || jsonb_build_object(
           'workflow_id', workflow_id,
           'event_id', event_id::text,
           'status', status,
           'failure_code', failure_code,
           'failure_stage', failure_stage
       );

ALTER TABLE ingest.recording_episode_processing
    ALTER COLUMN workflow_id SET NOT NULL,
    ALTER COLUMN event_id SET NOT NULL,
    ADD CONSTRAINT recording_episode_processing_workflow_uidx UNIQUE (workflow_id),
    ADD CONSTRAINT recording_episode_processing_event_uidx UNIQUE (event_id),
    ADD CONSTRAINT recording_episode_processing_ready_receipt CHECK (
        (status = 'READY'
            AND dataset_id IS NOT NULL
            AND dataset_version > 0
            AND lance_version > 0
            AND annotation_task_id IS NOT NULL
            AND aligned_media_camera_count > 0)
        OR
        (status <> 'READY'
            AND dataset_id IS NULL
            AND dataset_version IS NULL
            AND lance_version IS NULL
            AND annotation_task_id IS NULL
            AND aligned_media_camera_count IS NULL)
    ),
    ADD CONSTRAINT recording_episode_processing_failure_receipt CHECK (
        (status IN ('QC_FAILED', 'FAILED'))
            = (failure_code IS NOT NULL AND failure_stage IS NOT NULL)
    ),
    ADD CONSTRAINT recording_episode_processing_failure_code CHECK (
        failure_code IS NULL OR failure_code ~ '^[A-Z0-9_]+$'
    );

CREATE TABLE IF NOT EXISTS ingest.recording_episode_qc_reports (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    recording_id text NOT NULL,
    episode_id text NOT NULL,
    report_id text NOT NULL,
    report_sha256 char(64) NOT NULL CHECK (report_sha256 ~ '^[0-9a-f]{64}$'),
    status text NOT NULL CHECK (status IN ('PASS', 'REJECT')),
    report_document jsonb NOT NULL CHECK (jsonb_typeof(report_document) = 'object'),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (
        organization_id, project_id, region_code, recording_id, episode_id, report_id
    ),
    UNIQUE (report_id),
    FOREIGN KEY (organization_id, project_id, region_code, recording_id, episode_id)
        REFERENCES ingest.recording_episode_processing (
            organization_id, project_id, region_code, recording_id, episode_id
        )
);

SELECT core.apply_project_rls('ingest.recording_episode_qc_reports'::regclass);

-- Only one continuous Episode may own the next logical version of a Dataset at
-- a time.  Outbox delivery for sibling Episodes retries while a reservation is
-- active, which preserves commit order without holding a database transaction
-- across QC, FFmpeg, or Lance work.
CREATE TABLE IF NOT EXISTS ingest.recording_episode_dataset_version_reservations (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    recording_id text NOT NULL,
    episode_id text NOT NULL,
    workflow_id text NOT NULL,
    dataset_id text NOT NULL,
    dataset_version integer NOT NULL CHECK (dataset_version > 0),
    reservation_status text NOT NULL
        CHECK (reservation_status IN ('RESERVED', 'COMMITTED', 'RELEASED')),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, recording_id, episode_id),
    FOREIGN KEY (organization_id, project_id, region_code, recording_id, episode_id)
        REFERENCES ingest.recording_episode_processing (
            organization_id, project_id, region_code, recording_id, episode_id
        ),
    UNIQUE (workflow_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS recording_episode_active_dataset_version_uidx
ON ingest.recording_episode_dataset_version_reservations (
    organization_id, project_id, region_code, dataset_id, dataset_version
)
WHERE reservation_status <> 'RELEASED';

CREATE UNIQUE INDEX IF NOT EXISTS recording_episode_dataset_reservation_uidx
ON ingest.recording_episode_dataset_version_reservations (
    organization_id, project_id, region_code, dataset_id
)
WHERE reservation_status = 'RESERVED';

SELECT core.apply_project_rls(
    'ingest.recording_episode_dataset_version_reservations'::regclass
);

INSERT INTO core.outbox_events (
    event_id, organization_id, project_id, region_code, event_type,
    envelope, occurred_at, published_at, publish_attempts, available_at
)
SELECT
    processing.event_id,
    processing.organization_id,
    processing.project_id,
    processing.region_code,
    'continuous-recording.episode.workflow.requested.v1',
    jsonb_build_object(
        'event_id', processing.event_id::text,
        'event_type', 'continuous-recording.episode.workflow.requested.v1',
        'schema_version', 1,
        'aggregate_type', 'continuous_recording_episode',
        'aggregate_id', processing.recording_id || '/' || processing.episode_id,
        'organization_id', processing.organization_id,
        'project_id', processing.project_id,
        'region_code', processing.region_code,
        'occurred_at', processing.created_at,
        'payload', jsonb_build_object(
            'workflow_id', processing.workflow_id,
            'recording_id', processing.recording_id,
            'episode_id', processing.episode_id,
            'finalized_revision', processing.finalized_revision
        )
    ),
    processing.created_at,
    NULL,
    0,
    clock_timestamp()
FROM ingest.recording_episode_processing processing
WHERE processing.status = 'PENDING_QC'
ON CONFLICT (event_id) DO NOTHING;

CREATE INDEX IF NOT EXISTS recording_episode_processing_workflow_status_idx
ON ingest.recording_episode_processing (
    organization_id, project_id, region_code, workflow_id, status
);

REVOKE INSERT, UPDATE, DELETE, TRUNCATE
ON ingest.recording_episode_qc_reports FROM PUBLIC;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE
ON ingest.recording_episode_dataset_version_reservations FROM PUBLIC;
