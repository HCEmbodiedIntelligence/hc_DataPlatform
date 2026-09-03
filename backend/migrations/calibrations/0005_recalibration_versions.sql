-- A calibration document is immutable once written.  Editing/recalibrating a
-- current set therefore creates a new version document and advances only the
-- current projection, preserving every predecessor for audit and inspection.
ALTER TABLE calibrations.calibration_command_receipts
    DROP CONSTRAINT IF EXISTS calibration_command_receipts_operation_check;
ALTER TABLE calibrations.calibration_command_receipts
    ADD CONSTRAINT calibration_command_receipts_operation_check
    CHECK (operation IN ('CREATE', 'VALIDATE', 'RECALIBRATE')) NOT VALID;
ALTER TABLE calibrations.calibration_command_receipts
    VALIDATE CONSTRAINT calibration_command_receipts_operation_check;
