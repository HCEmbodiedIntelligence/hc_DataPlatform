-- Separate the publication grace lease from an in-flight Lance commit and add
-- a two-phase, exact-receipt retirement marker for committed canonical MP4s.

ALTER TABLE aligned_media.artifacts
    ADD COLUMN commit_started_at timestamptz,
    ADD COLUMN retired_at timestamptz,
    ADD COLUMN deleted_at timestamptz;

ALTER TABLE aligned_media.artifacts
    ADD CONSTRAINT aligned_media_commit_attempt_requires_lease
        CHECK (commit_started_at IS NULL OR commit_lease_expires_at IS NOT NULL),
    ADD CONSTRAINT aligned_media_retirement_requires_commit
        CHECK (retired_at IS NULL OR dataset_committed_at IS NOT NULL),
    ADD CONSTRAINT aligned_media_deletion_requires_retirement
        CHECK (deleted_at IS NULL OR retired_at IS NOT NULL);

CREATE INDEX aligned_media_artifacts_retirement_idx
ON aligned_media.artifacts (
    organization_id, project_id, region_code, dataset_id, dataset_version, retired_at
)
WHERE retired_at IS NOT NULL;

COMMENT ON COLUMN aligned_media.artifacts.commit_started_at IS
    'Non-null only while a fenced Lance Dataset commit may still be running.';
COMMENT ON COLUMN aligned_media.artifacts.retired_at IS
    'Authorization tombstone written before exact object deletion.';
COMMENT ON COLUMN aligned_media.artifacts.deleted_at IS
    'Exact object_manifest deletion completed; receipt metadata remains for audit.';
