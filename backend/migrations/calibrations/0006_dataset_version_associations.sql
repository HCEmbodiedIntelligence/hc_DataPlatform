-- A calibration must be pinned to the immutable dataset version that consumed
-- it; a later recalibration must never silently rewrite historical provenance.
-- The current P16 API is project/region-scoped.  organization_id is retained
-- here solely to enforce the dataset-registry foreign key; the repository
-- rejects an ambiguous project/region dataset lookup rather than guessing one.
CREATE TABLE IF NOT EXISTS calibrations.calibration_dataset_version_associations (
    project_id text NOT NULL,
    region_code text NOT NULL,
    set_id text NOT NULL,
    calibration_version bigint NOT NULL,
    organization_id text NOT NULL,
    dataset_id text NOT NULL,
    dataset_version_id text NOT NULL,
    associated_by text NOT NULL,
    associated_at timestamptz NOT NULL,
    PRIMARY KEY (
        project_id, region_code, set_id, calibration_version, dataset_id, dataset_version_id
    ),
    FOREIGN KEY (project_id, region_code, set_id, calibration_version)
        REFERENCES calibrations.calibration_version_documents
            (project_id, region_code, set_id, version),
    FOREIGN KEY (
        organization_id, project_id, region_code, dataset_id, dataset_version_id
    ) REFERENCES dataset_registry.dataset_versions (
        organization_id, project_id, region_code, dataset_id, version_id
    ),
    CHECK (set_id <> ''),
    CHECK (calibration_version >= 0),
    CHECK (organization_id <> ''),
    CHECK (dataset_id ~ '^dataset_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (dataset_version_id ~ '^version_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (associated_by <> '')
);

CREATE INDEX IF NOT EXISTS calibration_dataset_associations_dataset_idx
ON calibrations.calibration_dataset_version_associations (
    project_id, region_code, dataset_id, dataset_version_id, associated_at DESC
);

ALTER TABLE calibrations.calibration_command_receipts
    DROP CONSTRAINT IF EXISTS calibration_command_receipts_operation_check;
ALTER TABLE calibrations.calibration_command_receipts
    ADD CONSTRAINT calibration_command_receipts_operation_check
    CHECK (operation IN ('CREATE', 'VALIDATE', 'RECALIBRATE', 'ASSOCIATE_DATASET')) NOT VALID;
ALTER TABLE calibrations.calibration_command_receipts
    VALIDATE CONSTRAINT calibration_command_receipts_operation_check;

SELECT core.apply_project_rls('calibrations.calibration_dataset_version_associations'::regclass);
