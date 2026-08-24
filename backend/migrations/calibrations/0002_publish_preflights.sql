-- Durable publish proofs for the pre-existing calibration-set projection.  A
-- proof binds the exact draft ETag, content hash and validation report, then is
-- consumed in the same transaction as the READY/ACTIVE transition.

ALTER TABLE calibrations.calibration_sets
    ADD COLUMN IF NOT EXISTS revision bigint NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE calibrations.calibration_sets
    DROP CONSTRAINT IF EXISTS calibration_sets_revision_check;
ALTER TABLE calibrations.calibration_sets
    ADD CONSTRAINT calibration_sets_revision_check CHECK (revision >= 1) NOT VALID;
ALTER TABLE calibrations.calibration_sets
    VALIDATE CONSTRAINT calibration_sets_revision_check;

CREATE TABLE IF NOT EXISTS calibrations.calibration_publish_preflights (
    project_id text NOT NULL,
    region_code text NOT NULL,
    preflight_id uuid NOT NULL,
    set_id text NOT NULL,
    version bigint NOT NULL,
    idempotency_key text NOT NULL,
    request_fingerprint char(64) NOT NULL,
    expected_etag text NOT NULL,
    expected_hash text NOT NULL,
    validation_context_hash text NOT NULL,
    validation_report_id text NOT NULL,
    allowed boolean NOT NULL,
    status text NOT NULL,
    token_hash char(64) NOT NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL,
    consumed_at timestamptz,
    PRIMARY KEY (project_id, region_code, preflight_id),
    UNIQUE (project_id, region_code, set_id, version, idempotency_key),
    FOREIGN KEY (project_id, region_code, set_id)
        REFERENCES calibrations.calibration_sets (project_id, region_code, set_id),
    CHECK (set_id <> ''), CHECK (version >= 0), CHECK (idempotency_key <> ''),
    CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    CHECK (status IN ('ISSUED', 'CONSUMED', 'EXPIRED')),
    CHECK ((status = 'CONSUMED') = (consumed_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS calibration_publish_preflights_expiry_idx
ON calibrations.calibration_publish_preflights (expires_at)
WHERE status = 'ISSUED';

SELECT core.apply_project_rls('calibrations.calibration_publish_preflights'::regclass);
