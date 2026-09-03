-- Persist the immutable ingest-produced sampling fact used by auto annotation.
-- Candidate frames remain in object storage; PostgreSQL stores only the bounded
-- checksum-bound reference and lineage required to prevent full-rate re-decoding.

CREATE TABLE IF NOT EXISTS annotation.frame_selection_manifests (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    task_id text PRIMARY KEY REFERENCES annotation.annotation_tasks(task_id),
    source_sha256 char(64) NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    object_key text NOT NULL CHECK (object_key <> '' AND length(object_key) <= 2048),
    content_sha256 char(64) NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    size_bytes bigint NOT NULL CHECK (size_bytes BETWEEN 1 AND 8388608),
    sampling_version text NOT NULL CHECK (
        sampling_version <> '' AND length(sampling_version) <= 128
    ),
    camera_set jsonb NOT NULL CHECK (
        jsonb_typeof(camera_set) = 'array' AND jsonb_array_length(camera_set) BETWEEN 1 AND 16
    ),
    source_frame_count bigint NOT NULL CHECK (source_frame_count > 0),
    selected_group_count bigint NOT NULL CHECK (
        selected_group_count > 0 AND selected_group_count <= source_frame_count
    ),
    created_at timestamptz NOT NULL,
    UNIQUE (
        organization_id, project_id, region_code, source_sha256,
        sampling_version, task_id
    )
);

CREATE INDEX IF NOT EXISTS frame_selection_scope_source_idx
ON annotation.frame_selection_manifests(
    organization_id, project_id, region_code, source_sha256, sampling_version
);

DROP TRIGGER IF EXISTS frame_selection_manifests_immutable
ON annotation.frame_selection_manifests;
CREATE TRIGGER frame_selection_manifests_immutable
BEFORE UPDATE OR DELETE ON annotation.frame_selection_manifests
FOR EACH ROW EXECUTE FUNCTION annotation.reject_immutable_change();

SELECT core.apply_project_rls('annotation.frame_selection_manifests'::regclass);

ALTER TABLE annotation.auto_annotation_jobs
    ADD COLUMN IF NOT EXISTS sampling_reference jsonb;

ALTER TABLE annotation.auto_annotation_jobs
    DROP CONSTRAINT IF EXISTS auto_annotation_sampling_reference_shape;
ALTER TABLE annotation.auto_annotation_jobs
    ADD CONSTRAINT auto_annotation_sampling_reference_shape CHECK (
        sampling_reference IS NULL OR (
            jsonb_typeof(sampling_reference) = 'object'
            AND sampling_reference ? 'object_key'
            AND sampling_reference ? 'content_sha256'
            AND sampling_reference ? 'size_bytes'
            AND sampling_reference ? 'source_sha256'
            AND sampling_reference ? 'sampling_version'
            AND sampling_reference ? 'camera_set'
        )
    );
