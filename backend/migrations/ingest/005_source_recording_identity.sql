-- Keep logical source recordings unique even when a converter revision produces
-- different package, rollout, object, and content identities.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE ingest.rollouts
    ADD COLUMN IF NOT EXISTS source_fingerprint char(64),
    ADD COLUMN IF NOT EXISTS duplicate_of_rollout_id text;

ALTER TABLE ingest.rollouts
    DROP CONSTRAINT IF EXISTS rollouts_source_fingerprint_check;
ALTER TABLE ingest.rollouts
    ADD CONSTRAINT rollouts_source_fingerprint_check
    CHECK (
        source_fingerprint IS NULL
        OR source_fingerprint ~ '^[a-f0-9]{64}$'
    );

ALTER TABLE ingest.rollouts
    DROP CONSTRAINT IF EXISTS rollouts_duplicate_not_self_check;
ALTER TABLE ingest.rollouts
    ADD CONSTRAINT rollouts_duplicate_not_self_check
    CHECK (
        duplicate_of_rollout_id IS NULL
        OR duplicate_of_rollout_id <> rollout_id
    );

ALTER TABLE ingest.rollouts
    DROP CONSTRAINT IF EXISTS rollouts_duplicate_of_fkey;
ALTER TABLE ingest.rollouts
    ADD CONSTRAINT rollouts_duplicate_of_fkey
    FOREIGN KEY (project_id, duplicate_of_rollout_id)
    REFERENCES ingest.rollouts (project_id, rollout_id)
    DEFERRABLE INITIALLY IMMEDIATE;

-- Historical Hugging Face manifests predate source_recording. Their producer-owned
-- package names retain the importer family and episode index, which is enough to
-- recover the same task-scoped logical identity emitted by the current converters.
WITH recognized AS (
    SELECT rollout.project_id,
           rollout.rollout_id,
           job.task_id,
           CASE
               WHEN rollout.data_package_id ~
                    '^hf-droid-package-[0-9]{6}-[a-f0-9]{16}$'
               THEN 'aractingi/droid_100'
               WHEN rollout.data_package_id ~
                    '^hf-g1-package-[0-9]{6}-[a-f0-9]{16}$'
               THEN 'unitreerobotics/G1_WBT_Dex1_Put_Clothes_into_Washing_Machine'
           END AS repository,
           COALESCE(
               substring(
                   rollout.data_package_id
                   FROM '^hf-droid-package-([0-9]{6})-[a-f0-9]{16}$'
               ),
               substring(
                   rollout.data_package_id
                   FROM '^hf-g1-package-([0-9]{6})-[a-f0-9]{16}$'
               )
           )::integer AS episode_index
    FROM ingest.rollouts rollout
    JOIN ingest.collection_jobs job
      ON job.project_id = rollout.project_id
     AND job.collection_job_id = rollout.collection_job_id
    WHERE rollout.source_fingerprint IS NULL
      AND (
          rollout.data_package_id ~
              '^hf-droid-package-[0-9]{6}-[a-f0-9]{16}$'
          OR rollout.data_package_id ~
              '^hf-g1-package-[0-9]{6}-[a-f0-9]{16}$'
      )
), fingerprints AS (
    SELECT project_id,
           rollout_id,
           encode(
               digest(
                   concat_ws(
                       E'\n',
                       'source-recording/v1',
                       task_id,
                       'HUGGING_FACE_EPISODE',
                       lower(repository),
                       episode_index::text
                   ),
                   'sha256'
               ),
               'hex'
           ) AS source_fingerprint
    FROM recognized
)
UPDATE ingest.rollouts rollout
SET source_fingerprint = fingerprints.source_fingerprint
FROM fingerprints
WHERE rollout.project_id = fingerprints.project_id
  AND rollout.rollout_id = fingerprints.rollout_id;

-- Existing duplicates remain immutable facts. The newest conversion is the canonical
-- owner and older variants are explicitly linked to it so the dashboard can surface
-- them instead of silently counting them as successful independent episodes.
WITH ranked AS (
    SELECT project_id,
           rollout_id,
           first_value(rollout_id) OVER (
               PARTITION BY project_id, source_fingerprint
               ORDER BY created_at DESC, rollout_id DESC
           ) AS canonical_rollout_id
    FROM ingest.rollouts
    WHERE source_fingerprint IS NOT NULL
)
UPDATE ingest.rollouts rollout
SET duplicate_of_rollout_id = ranked.canonical_rollout_id
FROM ranked
WHERE rollout.project_id = ranked.project_id
  AND rollout.rollout_id = ranked.rollout_id
  AND ranked.rollout_id <> ranked.canonical_rollout_id
  AND rollout.duplicate_of_rollout_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS rollouts_project_source_fingerprint_uidx
ON ingest.rollouts (project_id, source_fingerprint)
WHERE source_fingerprint IS NOT NULL
  AND duplicate_of_rollout_id IS NULL;

CREATE INDEX IF NOT EXISTS rollouts_project_duplicate_of_idx
ON ingest.rollouts (project_id, duplicate_of_rollout_id)
WHERE duplicate_of_rollout_id IS NOT NULL;

