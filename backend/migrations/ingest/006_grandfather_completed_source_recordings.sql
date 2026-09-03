-- Preserve completed historical QC outcomes when source_recording identity is
-- introduced after the fact. These rows remain immutable historical results,
-- while the canonical fingerprint still prevents every future upload of the
-- same task/repository/episode source.

UPDATE ingest.rollouts rollout
SET source_fingerprint = NULL,
    duplicate_of_rollout_id = NULL
WHERE rollout.duplicate_of_rollout_id IS NOT NULL
  AND EXISTS (
      SELECT 1
      FROM quality_rollout_summaries summary
      WHERE summary.organization_id = rollout.organization_id
        AND summary.project_id = rollout.project_id
        AND summary.region_code = rollout.region_code
        AND summary.rollout_id = rollout.rollout_id
  );
