-- Preserve precise server-derived blockers across idempotent preflight replay.
-- A generic denied result is not actionable enough for calibration operators.
ALTER TABLE calibrations.calibration_publish_preflights
    ADD COLUMN IF NOT EXISTS blockers jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE calibrations.calibration_publish_preflights
    DROP CONSTRAINT IF EXISTS calibration_publish_preflights_blockers_array;
ALTER TABLE calibrations.calibration_publish_preflights
    ADD CONSTRAINT calibration_publish_preflights_blockers_array
    CHECK (jsonb_typeof(blockers) = 'array') NOT VALID;
ALTER TABLE calibrations.calibration_publish_preflights
    VALIDATE CONSTRAINT calibration_publish_preflights_blockers_array;
