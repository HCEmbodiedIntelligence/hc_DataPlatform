-- BE-05 owns only immutable verification report metadata.
CREATE TABLE IF NOT EXISTS raw_verification_reports (
    report_sha256 char(64) PRIMARY KEY CHECK (report_sha256 ~ '^[0-9a-f]{64}$'),
    project_id text NOT NULL,
    rollout_id text NOT NULL,
    source_sha256 char(64) NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    object_key text NOT NULL,
    status text NOT NULL CHECK (status IN ('RAW_VERIFIED', 'REJECTED')),
    report_json jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, rollout_id, source_sha256),
    CHECK (report_json ->> 'schema_version' = 'raw-verification-report/v1'),
    CHECK (report_json ->> 'content_sha256' = report_sha256),
    CHECK (report_json ->> 'source_sha256' = source_sha256),
    CHECK (report_json ->> 'rollout_id' = rollout_id),
    CHECK (report_json ->> 'object_key' = object_key),
    CHECK (report_json ->> 'status' = status),
    CHECK (jsonb_typeof(report_json -> 'schema_inventory') = 'array'),
    CHECK (jsonb_typeof(report_json -> 'channel_inventory') = 'array'),
    CHECK (jsonb_typeof(report_json -> 'topics') = 'array'),
    CHECK (jsonb_typeof(report_json -> 'findings') = 'array')
);

ALTER TABLE raw_verification_reports ENABLE ROW LEVEL SECURITY;

CREATE INDEX IF NOT EXISTS raw_verification_reports_rollout_created_idx
    ON raw_verification_reports (project_id, rollout_id, created_at DESC);

CREATE OR REPLACE FUNCTION reject_raw_verification_report_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'raw verification reports are immutable' USING ERRCODE = '55000';
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'raw_verification_reports_immutable'
    ) THEN
        CREATE TRIGGER raw_verification_reports_immutable
        BEFORE UPDATE OR DELETE ON raw_verification_reports
        FOR EACH ROW EXECUTE FUNCTION reject_raw_verification_report_mutation();
    END IF;
END;
$$;
