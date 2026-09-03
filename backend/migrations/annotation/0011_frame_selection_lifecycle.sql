-- Give immutable frame-selection content an explicit, quota-bound lifecycle.
-- Object deletion is always exact-key and follows an ACTIVE -> DELETING database
-- transition. Identity fields remain immutable for the lifetime of the row.

CREATE TABLE IF NOT EXISTS annotation.frame_selection_project_quotas (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    quota_bytes bigint NOT NULL DEFAULT 10737418240 CHECK (quota_bytes > 0),
    used_bytes bigint NOT NULL DEFAULT 0 CHECK (used_bytes >= 0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, project_id, region_code),
    CHECK (used_bytes <= quota_bytes)
);

SELECT core.apply_project_rls('annotation.frame_selection_project_quotas'::regclass);

ALTER TABLE annotation.frame_selection_manifests
    ADD COLUMN IF NOT EXISTS dataset_id text,
    ADD COLUMN IF NOT EXISTS rollout_id text,
    ADD COLUMN IF NOT EXISTS retention_policy text NOT NULL DEFAULT 'TASK_DATASET_90D'
        CHECK (retention_policy IN ('TASK_DATASET_90D', 'PROJECT_OVERRIDE')),
    ADD COLUMN IF NOT EXISTS retention_until timestamptz,
    ADD COLUMN IF NOT EXISTS expires_at timestamptz,
    ADD COLUMN IF NOT EXISTS lifecycle_status text NOT NULL DEFAULT 'ACTIVE'
        CHECK (lifecycle_status IN ('ACTIVE', 'DELETING')),
    ADD COLUMN IF NOT EXISTS legal_hold boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS governance_hold boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS deletion_token uuid,
    ADD COLUMN IF NOT EXISTS deletion_started_at timestamptz,
    ADD COLUMN IF NOT EXISTS deletion_error_code text,
    ADD COLUMN IF NOT EXISTS last_accessed_at timestamptz,
    ADD COLUMN IF NOT EXISTS lifecycle_version bigint NOT NULL DEFAULT 1
        CHECK (lifecycle_version > 0);

UPDATE annotation.frame_selection_manifests AS manifest
SET dataset_id = task.dataset_id,
    rollout_id = task.rollout_id,
    retention_until = COALESCE(manifest.retention_until, manifest.created_at + interval '90 days'),
    expires_at = COALESCE(manifest.expires_at, manifest.created_at + interval '90 days'),
    last_accessed_at = COALESCE(manifest.last_accessed_at, manifest.created_at)
FROM annotation.annotation_tasks AS task
WHERE task.task_id = manifest.task_id
  AND (
      manifest.dataset_id IS NULL OR manifest.rollout_id IS NULL
      OR manifest.retention_until IS NULL OR manifest.expires_at IS NULL
      OR manifest.last_accessed_at IS NULL
  );

ALTER TABLE annotation.frame_selection_manifests
    ALTER COLUMN dataset_id SET NOT NULL,
    ALTER COLUMN rollout_id SET NOT NULL,
    ALTER COLUMN retention_until SET NOT NULL,
    ALTER COLUMN expires_at SET NOT NULL,
    ALTER COLUMN last_accessed_at SET NOT NULL;

ALTER TABLE annotation.frame_selection_manifests
    DROP CONSTRAINT IF EXISTS frame_selection_deletion_state_shape;
ALTER TABLE annotation.frame_selection_manifests
    ADD CONSTRAINT frame_selection_deletion_state_shape CHECK (
        (lifecycle_status = 'ACTIVE'
            AND deletion_token IS NULL AND deletion_started_at IS NULL)
        OR (lifecycle_status = 'DELETING'
            AND deletion_token IS NOT NULL AND deletion_started_at IS NOT NULL)
    );

ALTER TABLE annotation.frame_selection_manifests
    DROP CONSTRAINT IF EXISTS frame_selection_retention_shape;
ALTER TABLE annotation.frame_selection_manifests
    ADD CONSTRAINT frame_selection_retention_shape CHECK (
        expires_at >= created_at AND retention_until >= created_at
    );

CREATE INDEX IF NOT EXISTS frame_selection_reclaimable_idx
ON annotation.frame_selection_manifests (
    organization_id, project_id, region_code, lifecycle_status,
    retention_until, expires_at, task_id
) WHERE NOT legal_hold AND NOT governance_hold;

INSERT INTO annotation.frame_selection_project_quotas (
    organization_id, project_id, region_code, quota_bytes, used_bytes
)
SELECT
    organization_id,
    project_id,
    region_code,
    GREATEST(10737418240, sum(size_bytes)),
    sum(size_bytes)
FROM annotation.frame_selection_manifests
GROUP BY organization_id, project_id, region_code
ON CONFLICT (organization_id, project_id, region_code) DO UPDATE
SET used_bytes = EXCLUDED.used_bytes,
    quota_bytes = GREATEST(
        annotation.frame_selection_project_quotas.quota_bytes,
        EXCLUDED.used_bytes
    ),
    updated_at = now();

CREATE OR REPLACE FUNCTION annotation.reserve_frame_selection_quota()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO annotation.frame_selection_project_quotas (
        organization_id, project_id, region_code
    ) VALUES (NEW.organization_id, NEW.project_id, NEW.region_code)
    ON CONFLICT DO NOTHING;

    UPDATE annotation.frame_selection_project_quotas
    SET used_bytes = used_bytes + NEW.size_bytes,
        updated_at = now()
    WHERE organization_id = NEW.organization_id
      AND project_id = NEW.project_id
      AND region_code = NEW.region_code
      AND used_bytes + NEW.size_bytes <= quota_bytes;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'frame-selection project quota exceeded'
            USING ERRCODE = '53100';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION annotation.release_frame_selection_quota()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE annotation.frame_selection_project_quotas
    SET used_bytes = GREATEST(0, used_bytes - OLD.size_bytes),
        updated_at = now()
    WHERE organization_id = OLD.organization_id
      AND project_id = OLD.project_id
      AND region_code = OLD.region_code;
    RETURN OLD;
END
$$;

CREATE OR REPLACE FUNCTION annotation.guard_frame_selection_lifecycle()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.lifecycle_status <> 'DELETING' OR OLD.deletion_token IS NULL THEN
            RAISE EXCEPTION 'frame-selection deletion requires a fenced DELETING receipt'
                USING ERRCODE = '55000';
        END IF;
        IF EXISTS (
            SELECT 1
            FROM annotation.auto_annotation_jobs AS job
            WHERE job.task_id = OLD.task_id
              AND job.status IN ('QUEUED', 'RUNNING')
              AND job.sampling_reference->>'object_key' = OLD.object_key
        ) THEN
            RAISE EXCEPTION 'frame-selection object is in active use'
                USING ERRCODE = '55000';
        END IF;
        RETURN OLD;
    END IF;

    IF ROW(
        NEW.organization_id, NEW.project_id, NEW.region_code, NEW.task_id,
        NEW.dataset_id, NEW.rollout_id, NEW.source_sha256, NEW.object_key,
        NEW.content_sha256, NEW.size_bytes, NEW.sampling_version,
        NEW.camera_set, NEW.source_frame_count, NEW.selected_group_count,
        NEW.created_at
    ) IS DISTINCT FROM ROW(
        OLD.organization_id, OLD.project_id, OLD.region_code, OLD.task_id,
        OLD.dataset_id, OLD.rollout_id, OLD.source_sha256, OLD.object_key,
        OLD.content_sha256, OLD.size_bytes, OLD.sampling_version,
        OLD.camera_set, OLD.source_frame_count, OLD.selected_group_count,
        OLD.created_at
    ) THEN
        RAISE EXCEPTION 'frame-selection identity is immutable'
            USING ERRCODE = '55000';
    END IF;
    IF OLD.lifecycle_status = 'DELETING' AND NEW.lifecycle_status <> 'DELETING' THEN
        RAISE EXCEPTION 'frame-selection deletion cannot be unfenced'
            USING ERRCODE = '55000';
    END IF;
    NEW.lifecycle_version := OLD.lifecycle_version + 1;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS frame_selection_manifests_immutable
ON annotation.frame_selection_manifests;
DROP TRIGGER IF EXISTS frame_selection_manifest_lifecycle_guard
ON annotation.frame_selection_manifests;
CREATE TRIGGER frame_selection_manifest_lifecycle_guard
BEFORE UPDATE OR DELETE ON annotation.frame_selection_manifests
FOR EACH ROW EXECUTE FUNCTION annotation.guard_frame_selection_lifecycle();

DROP TRIGGER IF EXISTS frame_selection_manifest_reserve_quota
ON annotation.frame_selection_manifests;
CREATE TRIGGER frame_selection_manifest_reserve_quota
BEFORE INSERT ON annotation.frame_selection_manifests
FOR EACH ROW EXECUTE FUNCTION annotation.reserve_frame_selection_quota();

DROP TRIGGER IF EXISTS frame_selection_manifest_release_quota
ON annotation.frame_selection_manifests;
CREATE TRIGGER frame_selection_manifest_release_quota
AFTER DELETE ON annotation.frame_selection_manifests
FOR EACH ROW EXECUTE FUNCTION annotation.release_frame_selection_quota();

REVOKE DELETE ON annotation.frame_selection_manifests FROM PUBLIC;
