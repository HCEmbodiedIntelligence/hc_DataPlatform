CREATE SCHEMA IF NOT EXISTS ingest;

CREATE TABLE IF NOT EXISTS ingest.collection_jobs (
    project_id text NOT NULL,
    region_code text NOT NULL,
    task_id text NOT NULL,
    collection_job_id text NOT NULL,
    robot_id text NOT NULL,
    status text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, collection_job_id),
    CHECK (status IN ('REGISTERED', 'COLLECTING', 'PAUSED', 'COMPLETED', 'FAILED', 'CANCELLED'))
);

CREATE TABLE IF NOT EXISTS ingest.rollouts (
    project_id text NOT NULL,
    region_code text NOT NULL,
    collection_job_id text NOT NULL,
    rollout_id text NOT NULL,
    sequence_no integer NOT NULL CHECK (sequence_no > 0),
    robot_id text NOT NULL,
    source_sha256 char(64) NOT NULL,
    status text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, rollout_id),
    UNIQUE (project_id, collection_job_id, sequence_no),
    FOREIGN KEY (project_id, collection_job_id)
        REFERENCES ingest.collection_jobs (project_id, collection_job_id),
    CHECK (status IN (
        'REGISTERED', 'UPLOADING', 'RAW_COMMITTED', 'VERIFYING',
        'RAW_VERIFIED', 'FAILED', 'CANCELLED'
    )),
    CHECK (source_sha256 ~ '^[a-f0-9]{64}$')
);

CREATE TABLE IF NOT EXISTS ingest.upload_sessions (
    session_id uuid PRIMARY KEY,
    project_id text NOT NULL,
    region_code text NOT NULL,
    rollout_id text NOT NULL,
    object_key text NOT NULL UNIQUE,
    multipart_upload_id text NOT NULL,
    expected_sha256 char(64) NOT NULL,
    expected_size bigint NOT NULL CHECK (expected_size > 0),
    expected_crc64 numeric(20,0) NOT NULL
        CHECK (expected_crc64 BETWEEN 0 AND 18446744073709551615),
    manifest_fingerprint char(64) NOT NULL,
    status text NOT NULL,
    etag text,
    failure_code text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    completed_at timestamptz,
    UNIQUE (project_id, rollout_id),
    FOREIGN KEY (project_id, rollout_id)
        REFERENCES ingest.rollouts (project_id, rollout_id),
    CHECK (status IN (
        'REGISTERED', 'UPLOADING', 'PAUSED', 'MULTIPART_COMPLETED',
        'RAW_COMMITTED', 'FAILED', 'CANCELLED'
    )),
    CHECK (expected_sha256 ~ '^[a-f0-9]{64}$'),
    CHECK (manifest_fingerprint ~ '^[a-f0-9]{64}$')
);

CREATE TABLE IF NOT EXISTS ingest.upload_objects (
    object_id uuid PRIMARY KEY,
    session_id uuid NOT NULL UNIQUE REFERENCES ingest.upload_sessions (session_id),
    project_id text NOT NULL,
    region_code text NOT NULL,
    rollout_id text NOT NULL,
    object_key text NOT NULL UNIQUE,
    expected_size bigint NOT NULL CHECK (expected_size > 0),
    expected_sha256 char(64) NOT NULL,
    expected_crc64 numeric(20,0) NOT NULL
        CHECK (expected_crc64 BETWEEN 0 AND 18446744073709551615),
    actual_size bigint CHECK (actual_size >= 0),
    actual_sha256 char(64),
    actual_crc64 numeric(20,0)
        CHECK (actual_crc64 BETWEEN 0 AND 18446744073709551615),
    etag text,
    status text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CHECK (status IN (
        'PENDING', 'MULTIPART_COMPLETED', 'VERIFIED', 'COMMITTED', 'FAILED', 'CANCELLED'
    ))
);

CREATE TABLE IF NOT EXISTS ingest.upload_parts (
    session_id uuid NOT NULL REFERENCES ingest.upload_sessions (session_id),
    project_id text NOT NULL,
    region_code text NOT NULL,
    part_number integer NOT NULL CHECK (part_number BETWEEN 1 AND 10000),
    status text NOT NULL CHECK (status IN ('AUTHORIZED', 'UPLOADED')),
    etag text,
    size bigint CHECK (size >= 0),
    crc64 numeric(20,0) CHECK (crc64 BETWEEN 0 AND 18446744073709551615),
    authorization_expires_at timestamptz,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (session_id, part_number)
);

CREATE TABLE IF NOT EXISTS ingest.rollout_objects (
    project_id text NOT NULL,
    region_code text NOT NULL,
    rollout_id text NOT NULL,
    object_key text NOT NULL UNIQUE,
    manifest_key text NOT NULL UNIQUE,
    source_sha256 char(64) NOT NULL,
    crc64 numeric(20,0) NOT NULL CHECK (crc64 BETWEEN 0 AND 18446744073709551615),
    file_size bigint NOT NULL CHECK (file_size > 0),
    status text NOT NULL CHECK (status IN ('COMMITTED')),
    committed_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, rollout_id),
    FOREIGN KEY (project_id, rollout_id)
        REFERENCES ingest.rollouts (project_id, rollout_id),
    CHECK (source_sha256 ~ '^[a-f0-9]{64}$')
);

CREATE INDEX IF NOT EXISTS upload_sessions_active_idx
ON ingest.upload_sessions (project_id, region_code, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS upload_parts_session_status_idx
ON ingest.upload_parts (session_id, status, part_number);

-- BE-02 owns core.apply_project_rls. In the supported merge order it is present before
-- this migration and installs project/region USING and WITH CHECK policies.
DO $block$
DECLARE
    table_name regclass;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'ingest.collection_jobs'::regclass,
        'ingest.rollouts'::regclass,
        'ingest.upload_sessions'::regclass,
        'ingest.upload_objects'::regclass,
        'ingest.upload_parts'::regclass,
        'ingest.rollout_objects'::regclass
    ]
    LOOP
        IF to_regprocedure('core.apply_project_rls(regclass)') IS NOT NULL THEN
            PERFORM core.apply_project_rls(table_name);
        ELSE
            EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', table_name);
            EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', table_name);
        END IF;
    END LOOP;
END
$block$;
