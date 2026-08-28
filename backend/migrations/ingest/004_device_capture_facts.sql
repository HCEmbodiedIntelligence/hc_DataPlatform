-- Device-agent facts are the only source for CAPTURED/SAVED semantics. Upload
-- registration and object-store commit events must never synthesize these rows.

CREATE TABLE IF NOT EXISTS ingest.device_capture_facts (
    fact_id uuid PRIMARY KEY,
    organization_id text NOT NULL DEFAULT NULLIF(current_setting('app.organization_id', true), ''),
    project_id text NOT NULL,
    region_code text NOT NULL,
    source_event_id text NOT NULL,
    event_type text NOT NULL CHECK (event_type IN ('CAPTURED', 'SAVED')),
    collection_task_id text NOT NULL,
    collection_job_id text NOT NULL,
    recording_request_id text NOT NULL,
    data_package_id text NOT NULL,
    robot_id text NOT NULL,
    device_id text NOT NULL,
    device_sequence_no bigint NOT NULL CHECK (device_sequence_no >= 0),
    capture_started_at timestamptz NOT NULL,
    capture_ended_at timestamptz NOT NULL,
    saved_at timestamptz,
    local_artifact_size bigint,
    local_artifact_sha256 char(64),
    recorder_version text NOT NULL,
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    producer_subject_id text NOT NULL,
    source_fingerprint char(64) NOT NULL,
    fact_json jsonb NOT NULL,
    UNIQUE (organization_id, project_id, region_code, device_id, source_event_id),
    FOREIGN KEY (organization_id, project_id, collection_task_id)
        REFERENCES collection_tasks.collection_tasks (
            organization_id, project_id, collection_task_id
        ),
    CHECK (organization_id <> ''),
    CHECK (project_id <> '' AND region_code <> ''),
    CHECK (capture_ended_at > capture_started_at),
    CHECK (occurred_at >= capture_ended_at),
    CHECK (source_fingerprint ~ '^[a-f0-9]{64}$'),
    CHECK (
        (event_type = 'CAPTURED'
            AND saved_at IS NULL
            AND local_artifact_size IS NULL
            AND local_artifact_sha256 IS NULL)
        OR
        (event_type = 'SAVED'
            AND saved_at >= capture_ended_at
            AND occurred_at >= saved_at
            AND local_artifact_size > 0
            AND local_artifact_sha256 ~ '^[a-f0-9]{64}$')
    )
);

CREATE INDEX IF NOT EXISTS device_capture_facts_task_event_idx
ON ingest.device_capture_facts (
    organization_id, project_id, region_code, collection_task_id,
    event_type, occurred_at DESC, fact_id DESC
);

CREATE INDEX IF NOT EXISTS device_capture_facts_package_event_idx
ON ingest.device_capture_facts (
    organization_id, project_id, region_code, data_package_id,
    event_type, occurred_at DESC, fact_id DESC
);

SELECT core.apply_project_rls('ingest.device_capture_facts'::regclass);

CREATE OR REPLACE FUNCTION ingest.reject_device_capture_fact_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '23514',
        MESSAGE = 'DEVICE_CAPTURE_FACT_IMMUTABLE',
        DETAIL = 'Device CAPTURED/SAVED facts are append-only.';
END
$function$;

DROP TRIGGER IF EXISTS device_capture_facts_immutable ON ingest.device_capture_facts;
CREATE TRIGGER device_capture_facts_immutable
BEFORE UPDATE OR DELETE ON ingest.device_capture_facts
FOR EACH ROW
EXECUTE FUNCTION ingest.reject_device_capture_fact_mutation();

REVOKE UPDATE, DELETE ON ingest.device_capture_facts FROM PUBLIC;
