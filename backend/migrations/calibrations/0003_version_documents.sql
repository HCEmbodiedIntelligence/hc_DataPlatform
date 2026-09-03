-- P16 owns immutable calibration-version documents and the validation reports
-- derived from them.  The existing calibration_sets row remains the current
-- projection used by list/detail/publish; a document is never inferred from a
-- hash and remains addressable by its set/version pair.

CREATE TABLE IF NOT EXISTS calibrations.calibration_version_documents (
    project_id text NOT NULL,
    region_code text NOT NULL,
    set_id text NOT NULL,
    version bigint NOT NULL,
    source text NOT NULL,
    document jsonb NOT NULL,
    content_hash char(64) NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, region_code, set_id, version),
    FOREIGN KEY (project_id, region_code, set_id)
        REFERENCES calibrations.calibration_sets (project_id, region_code, set_id),
    CHECK (set_id <> ''),
    CHECK (version >= 0),
    CHECK (source IN ('MANUAL', 'IMPORT', 'RECALIBRATION')),
    CHECK (jsonb_typeof(document) = 'object'),
    CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    CHECK (created_by <> '')
);

CREATE TABLE IF NOT EXISTS calibrations.calibration_validation_reports (
    project_id text NOT NULL,
    region_code text NOT NULL,
    report_id text NOT NULL,
    set_id text NOT NULL,
    version bigint NOT NULL,
    content_hash char(64) NOT NULL,
    validation_context_hash char(64) NOT NULL,
    status text NOT NULL,
    findings jsonb NOT NULL,
    checked_by text NOT NULL,
    checked_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, region_code, report_id),
    UNIQUE (project_id, region_code, set_id, version, content_hash, validation_context_hash),
    FOREIGN KEY (project_id, region_code, set_id, version)
        REFERENCES calibrations.calibration_version_documents
            (project_id, region_code, set_id, version),
    CHECK (report_id <> ''),
    CHECK (version >= 0),
    CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    CHECK (validation_context_hash ~ '^[0-9a-f]{64}$'),
    CHECK (status IN ('PASSED', 'FAILED')),
    CHECK (jsonb_typeof(findings) = 'array'),
    CHECK (checked_by <> '')
);

-- Receipts deliberately retain only deterministic resource references, not a
-- raw command.  A repeated key can therefore replay an exact durable outcome
-- while the command fingerprint detects semantic key reuse.
CREATE TABLE IF NOT EXISTS calibrations.calibration_command_receipts (
    project_id text NOT NULL,
    region_code text NOT NULL,
    resource_id text NOT NULL,
    operation text NOT NULL,
    idempotency_key text NOT NULL,
    request_fingerprint char(64) NOT NULL,
    version bigint NOT NULL,
    report_id text,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, region_code, resource_id, operation, idempotency_key),
    CHECK (resource_id <> ''),
    CHECK (operation IN ('CREATE', 'VALIDATE')),
    CHECK (idempotency_key <> ''),
    CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    CHECK (version >= 0)
);

CREATE INDEX IF NOT EXISTS calibration_version_documents_scope_set_idx
ON calibrations.calibration_version_documents (project_id, region_code, set_id, version DESC);
CREATE INDEX IF NOT EXISTS calibration_validation_reports_scope_set_idx
ON calibrations.calibration_validation_reports
    (project_id, region_code, set_id, version DESC, checked_at DESC);

SELECT core.apply_project_rls('calibrations.calibration_version_documents'::regclass);
SELECT core.apply_project_rls('calibrations.calibration_validation_reports'::regclass);
SELECT core.apply_project_rls('calibrations.calibration_command_receipts'::regclass);
