-- Attempt-fenced media publication and exact persisted playback metadata.

ALTER TABLE preview.artifacts
    ADD COLUMN publication_token uuid,
    ADD COLUMN publication_receipt_at timestamptz,
    ADD COLUMN timeline_json jsonb,
    ADD COLUMN placeholder_manifest jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN placeholder_count bigint NOT NULL DEFAULT 0;

ALTER TABLE preview.artifacts
    ADD CONSTRAINT preview_artifacts_timeline_object_ck
        CHECK (timeline_json IS NULL OR jsonb_typeof(timeline_json) = 'object'),
    ADD CONSTRAINT preview_artifacts_placeholder_manifest_array_ck
        CHECK (jsonb_typeof(placeholder_manifest) = 'array'),
    ADD CONSTRAINT preview_artifacts_placeholder_count_ck
        CHECK (placeholder_count >= 0),
    ADD CONSTRAINT preview_artifacts_publication_receipt_ck
        CHECK (
            (publication_token IS NULL AND publication_receipt_at IS NULL)
            OR (publication_token IS NOT NULL AND publication_receipt_at IS NOT NULL)
        );

ALTER TABLE preview.jobs
    ADD COLUMN owner_id text,
    ADD COLUMN lease_expires_at timestamptz,
    ADD COLUMN attempt_token uuid,
    ADD COLUMN heartbeat_at timestamptz;

ALTER TABLE preview.jobs
    ADD CONSTRAINT preview_jobs_lease_tuple_ck
        CHECK (
            (owner_id IS NULL AND lease_expires_at IS NULL AND attempt_token IS NULL)
            OR (owner_id IS NOT NULL AND owner_id <> ''
                AND lease_expires_at IS NOT NULL AND attempt_token IS NOT NULL)
        );

CREATE INDEX preview_jobs_expired_lease_idx
ON preview.jobs (lease_expires_at, created_at, job_id)
WHERE status = 'RUNNING';

CREATE INDEX preview_artifacts_publication_token_idx
ON preview.artifacts (
    organization_id, project_id, region_code, artifact_key, publication_token
)
WHERE publication_token IS NOT NULL;
