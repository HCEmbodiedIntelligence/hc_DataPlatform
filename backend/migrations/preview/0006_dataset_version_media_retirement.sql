-- Bind canonical MP4 retirement to an actual Dataset-version deletion. The
-- receipt is inserted in the same transaction as the DELETE and is consumed by
-- scoped media maintenance with a retryable lease.

CREATE TABLE aligned_media.dataset_version_retirement_requests (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    version_id text NOT NULL,
    dataset_version bigint NOT NULL,
    status text NOT NULL DEFAULT 'PENDING',
    attempt integer NOT NULL DEFAULT 0,
    lease_token uuid,
    lease_expires_at timestamptz,
    last_error text,
    requested_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    PRIMARY KEY (organization_id, project_id, region_code, dataset_id, version_id),
    CHECK (version_id = 'version_lance_' || dataset_version::text),
    CHECK (dataset_version >= 1),
    CHECK (status IN ('PENDING', 'RUNNING', 'COMPLETED')),
    CHECK (attempt >= 0),
    CHECK ((status = 'RUNNING') =
           (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CHECK ((status = 'COMPLETED') = (completed_at IS NOT NULL))
);

CREATE INDEX aligned_media_version_retirement_pending_idx
ON aligned_media.dataset_version_retirement_requests (
    organization_id, project_id, region_code, status, requested_at,
    dataset_id, dataset_version
)
WHERE status <> 'COMPLETED';

SELECT core.apply_project_rls(
    'aligned_media.dataset_version_retirement_requests'::regclass
);

CREATE OR REPLACE FUNCTION aligned_media.enqueue_dataset_version_retirement()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, aligned_media
AS $$
DECLARE
    lance_version bigint;
BEGIN
    IF OLD.version_id !~ '^version_lance_[1-9][0-9]*$' THEN
        RETURN OLD;
    END IF;
    lance_version := substring(OLD.version_id FROM '^version_lance_([1-9][0-9]*)$')::bigint;
    IF EXISTS (
        SELECT 1
          FROM aligned_media.artifacts AS artifact
         WHERE artifact.organization_id = OLD.organization_id
           AND artifact.project_id = OLD.project_id
           AND artifact.region_code = OLD.region_code
           AND artifact.dataset_id = OLD.dataset_id
           AND artifact.dataset_version = lance_version
           AND artifact.dataset_committed_at IS NOT NULL
           AND artifact.deleted_at IS NULL
    ) THEN
        INSERT INTO aligned_media.dataset_version_retirement_requests (
            organization_id, project_id, region_code, dataset_id, version_id,
            dataset_version, status, requested_at, updated_at
        ) VALUES (
            OLD.organization_id, OLD.project_id, OLD.region_code, OLD.dataset_id,
            OLD.version_id, lance_version, 'PENDING', now(), now()
        )
        ON CONFLICT (organization_id, project_id, region_code, dataset_id, version_id)
        DO NOTHING;
    END IF;
    RETURN OLD;
END;
$$;

REVOKE ALL ON FUNCTION aligned_media.enqueue_dataset_version_retirement() FROM PUBLIC;

CREATE TRIGGER dataset_version_aligned_media_retirement
AFTER DELETE ON dataset_registry.dataset_versions
FOR EACH ROW
EXECUTE FUNCTION aligned_media.enqueue_dataset_version_retirement();

REVOKE ALL ON aligned_media.dataset_version_retirement_requests FROM PUBLIC;

COMMENT ON TABLE aligned_media.dataset_version_retirement_requests IS
    'Exact, leased canonical-MP4 cleanup receipts created atomically by Dataset version deletion.';
