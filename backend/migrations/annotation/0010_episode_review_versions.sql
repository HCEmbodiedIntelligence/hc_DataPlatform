-- One immutable Episode business version is created for every review
-- submission. Draft revisions remain editor history and are not business
-- versions.

ALTER TABLE annotation.annotation_submissions
ADD COLUMN IF NOT EXISTS episode_version bigint;

DROP TRIGGER IF EXISTS annotation_submissions_append_only
ON annotation.annotation_submissions;

WITH ranked AS (
    SELECT submission_id,
           row_number() OVER (
               PARTITION BY task_id ORDER BY created_at, submission_id
           ) AS episode_version
    FROM annotation.annotation_submissions
)
UPDATE annotation.annotation_submissions AS submission
SET episode_version = ranked.episode_version
FROM ranked
WHERE ranked.submission_id = submission.submission_id
  AND submission.episode_version IS NULL;

ALTER TABLE annotation.annotation_submissions
ALTER COLUMN episode_version SET NOT NULL;

ALTER TABLE annotation.annotation_submissions
ADD CONSTRAINT annotation_submission_episode_version_positive
CHECK (episode_version > 0);

CREATE UNIQUE INDEX IF NOT EXISTS annotation_submission_episode_version_uq
ON annotation.annotation_submissions (task_id, episode_version);

CREATE TRIGGER annotation_submissions_append_only
BEFORE UPDATE OR DELETE ON annotation.annotation_submissions
FOR EACH ROW EXECUTE FUNCTION annotation.reject_immutable_change();

COMMENT ON COLUMN annotation.annotation_submissions.episode_version IS
'Stable Episode business-version ordinal. It advances only on submit-for-review, never on draft save.';
