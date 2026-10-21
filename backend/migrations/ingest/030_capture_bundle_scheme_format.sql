-- Allow the existing raw MCAP + video transport to validate shared capture schemes.
-- Existing versions, task plans and uploaded bytes remain immutable.
ALTER TABLE ingest.capture_scheme_validations
  DROP CONSTRAINT capture_scheme_validations_source_format_check;
ALTER TABLE ingest.capture_scheme_validations
  ADD CONSTRAINT capture_scheme_validations_source_format_check
  CHECK (source_format IN ('MCAP','LEROBOT_V3','CAPTURE_BUNDLE')) NOT VALID;
ALTER TABLE ingest.capture_scheme_validations
  VALIDATE CONSTRAINT capture_scheme_validations_source_format_check;
