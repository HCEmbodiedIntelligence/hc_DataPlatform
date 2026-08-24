-- P07 fixed-version delivery and review facts.  The product page reads these
-- scoped projections rather than inferring state from a raw object store or
-- from the lower-level Lance catalog.  All persisted documents are explicitly
-- safe page facts: credentials, signed URLs, object paths, and raw locators
-- are rejected at the database boundary.

CREATE TABLE IF NOT EXISTS dataset_registry.dataset_version_content_projections (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    version_id text NOT NULL,
    content_snapshot_id text NOT NULL,
    content_snapshot_hash text NOT NULL,
    manifest_id text NOT NULL,
    manifest_sha256 text NOT NULL,
    operational_revision text NOT NULL,
    content_document jsonb NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, dataset_id, version_id),
    FOREIGN KEY (organization_id, project_id, region_code, dataset_id, version_id)
        REFERENCES dataset_registry.dataset_versions (
            organization_id, project_id, region_code, dataset_id, version_id
        ),
    CHECK (content_snapshot_id <> ''),
    CHECK (content_snapshot_hash ~ '^[a-fA-F0-9]{64}$'),
    CHECK (manifest_id <> ''),
    CHECK (manifest_sha256 ~ '^[a-fA-F0-9]{64}$'),
    CHECK (operational_revision <> ''),
    CHECK (jsonb_typeof(content_document) = 'object'),
    CHECK (content_document ->> 'dataset_id' = dataset_id),
    CHECK (content_document ->> 'version_id' = version_id),
    CHECK (
        content_document -> 'content_snapshot' ->> 'content_snapshot_id'
        = content_snapshot_id
    ),
    CHECK (content_document -> 'manifest' ->> 'manifest_id' = manifest_id),
    CHECK (content_document -> 'scope' ->> 'organization_id' = organization_id),
    CHECK (content_document -> 'scope' ->> 'project_id' = project_id),
    CHECK (content_document -> 'scope' ->> 'region_code' = region_code),
    CHECK (NOT (content_document ? 'credential')),
    CHECK (NOT (content_document ? 'secret')),
    CHECK (NOT (content_document ? 'token')),
    CHECK (NOT (content_document ? 'object_locator')),
    CHECK (NOT (content_document ? 'object_path'))
);

CREATE TABLE IF NOT EXISTS dataset_registry.dataset_version_episode_revisions (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    version_id text NOT NULL,
    episode_id text NOT NULL,
    revision_id text NOT NULL,
    ordinal bigint NOT NULL,
    revision_document jsonb NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, dataset_id, version_id, revision_id),
    FOREIGN KEY (organization_id, project_id, region_code, dataset_id, version_id, episode_id)
        REFERENCES dataset_registry.dataset_version_episodes (
            organization_id, project_id, region_code, dataset_id, version_id, episode_id
        ),
    CHECK (episode_id ~ '^episode_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (revision_id ~ '^revision_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (ordinal >= 0),
    CHECK (jsonb_typeof(revision_document) = 'object'),
    CHECK (revision_document ->> 'dataset_id' = dataset_id),
    CHECK (revision_document ->> 'version_id' = version_id),
    CHECK (revision_document ->> 'episode_id' = episode_id),
    CHECK (revision_document ->> 'revision_id' = revision_id),
    CHECK ((revision_document ->> 'ordinal')::bigint = ordinal),
    CHECK (revision_document -> 'scope' ->> 'organization_id' = organization_id),
    CHECK (revision_document -> 'scope' ->> 'project_id' = project_id),
    CHECK (revision_document -> 'scope' ->> 'region_code' = region_code),
    CHECK (NOT (revision_document ? 'credential')),
    CHECK (NOT (revision_document ? 'secret')),
    CHECK (NOT (revision_document ? 'token')),
    CHECK (NOT (revision_document ? 'object_locator')),
    CHECK (NOT (revision_document ? 'object_path'))
);

CREATE UNIQUE INDEX IF NOT EXISTS dataset_version_episode_revision_episode_uq
ON dataset_registry.dataset_version_episode_revisions (
    organization_id, project_id, region_code, dataset_id, version_id, episode_id
);

CREATE TABLE IF NOT EXISTS dataset_registry.dataset_version_schema_details (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    version_id text NOT NULL,
    schema_snapshot_id text NOT NULL,
    channel_count bigint NOT NULL,
    schema_document jsonb NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, dataset_id, version_id),
    FOREIGN KEY (organization_id, project_id, region_code, dataset_id, version_id)
        REFERENCES dataset_registry.dataset_versions (
            organization_id, project_id, region_code, dataset_id, version_id
        ),
    CHECK (schema_snapshot_id <> ''),
    CHECK (channel_count >= 0),
    CHECK (jsonb_typeof(schema_document) = 'object'),
    CHECK (schema_document ->> 'dataset_id' = dataset_id),
    CHECK (schema_document ->> 'version_id' = version_id),
    CHECK (schema_document -> 'schema_snapshot' ->> 'reference_id' = schema_snapshot_id),
    CHECK (schema_document -> 'scope' ->> 'organization_id' = organization_id),
    CHECK (schema_document -> 'scope' ->> 'project_id' = project_id),
    CHECK (schema_document -> 'scope' ->> 'region_code' = region_code),
    CHECK (NOT (schema_document ? 'credential')),
    CHECK (NOT (schema_document ? 'secret')),
    CHECK (NOT (schema_document ? 'token'))
);

CREATE TABLE IF NOT EXISTS dataset_registry.dataset_version_manifest_entries (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    version_id text NOT NULL,
    entry_id text NOT NULL,
    episode_id text NOT NULL,
    revision_id text NOT NULL,
    entry_role text NOT NULL,
    size_bytes numeric(30, 0) NOT NULL,
    entry_document jsonb NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, dataset_id, version_id, entry_id),
    FOREIGN KEY (organization_id, project_id, region_code, dataset_id, version_id)
        REFERENCES dataset_registry.dataset_versions (
            organization_id, project_id, region_code, dataset_id, version_id
        ),
    CHECK (entry_id <> ''),
    CHECK (episode_id ~ '^episode_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (revision_id ~ '^revision_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (entry_role IN ('SOURCE', 'REVISION', 'INDEX', 'METADATA')),
    CHECK (size_bytes >= 0),
    CHECK (jsonb_typeof(entry_document) = 'object'),
    CHECK (entry_document ->> 'entry_id' = entry_id),
    CHECK (entry_document ->> 'episode_id' = episode_id),
    CHECK (entry_document ->> 'revision_id' = revision_id),
    CHECK (entry_document ->> 'role' = entry_role),
    CHECK (NOT (entry_document ? 'credential')),
    CHECK (NOT (entry_document ? 'secret')),
    CHECK (NOT (entry_document ? 'token')),
    CHECK (NOT (entry_document ? 'object_locator')),
    CHECK (NOT (entry_document ? 'object_path'))
);

CREATE INDEX IF NOT EXISTS dataset_version_manifest_entries_page_idx
ON dataset_registry.dataset_version_manifest_entries (
    organization_id, project_id, region_code, dataset_id, version_id, entry_role, entry_id
);

CREATE TABLE IF NOT EXISTS dataset_registry.dataset_version_required_storage (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    version_id text NOT NULL,
    object_id text NOT NULL,
    object_role text NOT NULL,
    size_bytes numeric(30, 0) NOT NULL,
    safe_locator text,
    storage_document jsonb NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, dataset_id, version_id, object_id),
    FOREIGN KEY (organization_id, project_id, region_code, dataset_id, version_id)
        REFERENCES dataset_registry.dataset_versions (
            organization_id, project_id, region_code, dataset_id, version_id
        ),
    CHECK (object_id <> ''),
    CHECK (object_role IN ('SOURCE', 'REVISION', 'INDEX', 'METADATA', 'PREVIEW', 'EXPORT')),
    CHECK (size_bytes >= 0),
    CHECK (safe_locator IS NULL OR (length(safe_locator) <= 256 AND position('/' IN safe_locator) = 0)),
    CHECK (jsonb_typeof(storage_document) = 'object'),
    CHECK (storage_document ->> 'dataset_id' = dataset_id),
    CHECK (storage_document ->> 'version_id' = version_id),
    CHECK (storage_document ->> 'object_id' = object_id),
    CHECK (storage_document ->> 'role' = object_role),
    CHECK (storage_document -> 'scope' ->> 'organization_id' = organization_id),
    CHECK (storage_document -> 'scope' ->> 'project_id' = project_id),
    CHECK (storage_document -> 'scope' ->> 'region_code' = region_code),
    CHECK (NOT (storage_document ? 'credential')),
    CHECK (NOT (storage_document ? 'secret')),
    CHECK (NOT (storage_document ? 'token')),
    CHECK (NOT (storage_document ? 'object_locator')),
    CHECK (NOT (storage_document ? 'object_path'))
);

CREATE INDEX IF NOT EXISTS dataset_version_required_storage_page_idx
ON dataset_registry.dataset_version_required_storage (
    organization_id, project_id, region_code, dataset_id, version_id, object_role, object_id
);

CREATE TABLE IF NOT EXISTS dataset_registry.dataset_version_operational_inventory (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    version_id text NOT NULL,
    inventory_id text NOT NULL,
    operational_revision text NOT NULL,
    inventory_kind text NOT NULL,
    inventory_status text NOT NULL,
    created_at timestamptz NOT NULL,
    inventory_document jsonb NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, dataset_id, version_id, inventory_id),
    FOREIGN KEY (organization_id, project_id, region_code, dataset_id, version_id)
        REFERENCES dataset_registry.dataset_versions (
            organization_id, project_id, region_code, dataset_id, version_id
        ),
    CHECK (inventory_id <> ''),
    CHECK (operational_revision <> ''),
    CHECK (inventory_kind IN ('PREVIEW', 'EXPORT', 'MATERIALIZATION')),
    CHECK (inventory_status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'EXPIRED', 'STALE')),
    CHECK (jsonb_typeof(inventory_document) = 'object'),
    CHECK (inventory_document ->> 'dataset_id' = dataset_id),
    CHECK (inventory_document ->> 'version_id' = version_id),
    CHECK (inventory_document ->> 'inventory_id' = inventory_id),
    CHECK (inventory_document ->> 'operational_revision' = operational_revision),
    CHECK (inventory_document -> 'scope' ->> 'organization_id' = organization_id),
    CHECK (inventory_document -> 'scope' ->> 'project_id' = project_id),
    CHECK (inventory_document -> 'scope' ->> 'region_code' = region_code),
    CHECK (NOT (inventory_document ? 'credential')),
    CHECK (NOT (inventory_document ? 'secret')),
    CHECK (NOT (inventory_document ? 'token')),
    CHECK (NOT (inventory_document ? 'object_locator')),
    CHECK (NOT (inventory_document ? 'object_path'))
);

CREATE INDEX IF NOT EXISTS dataset_version_operational_inventory_page_idx
ON dataset_registry.dataset_version_operational_inventory (
    organization_id, project_id, region_code, dataset_id, version_id,
    operational_revision, created_at DESC, inventory_id DESC
);

CREATE TABLE IF NOT EXISTS dataset_registry.dataset_version_review_decisions (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    version_id text NOT NULL,
    review_decision_id text NOT NULL,
    decision text NOT NULL,
    created_at timestamptz NOT NULL,
    decision_document jsonb NOT NULL,
    PRIMARY KEY (
        organization_id, project_id, region_code, dataset_id, version_id, review_decision_id
    ),
    FOREIGN KEY (organization_id, project_id, region_code, dataset_id, version_id)
        REFERENCES dataset_registry.dataset_versions (
            organization_id, project_id, region_code, dataset_id, version_id
        ),
    CHECK (review_decision_id ~ '^review_decision_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (decision IN ('APPROVED', 'RETURNED')),
    CHECK (jsonb_typeof(decision_document) = 'object'),
    CHECK (decision_document ->> 'id' = review_decision_id),
    CHECK (decision_document ->> 'output_version_id' = version_id),
    CHECK (decision_document ->> 'decision' = decision),
    CHECK (NOT (decision_document ? 'credential')),
    CHECK (NOT (decision_document ? 'secret')),
    CHECK (NOT (decision_document ? 'token'))
);

CREATE TABLE IF NOT EXISTS dataset_registry.dataset_version_review_findings (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    version_id text NOT NULL,
    review_finding_id text NOT NULL,
    review_decision_id text NOT NULL,
    output_revision_id text NOT NULL,
    episode_stream_id text NOT NULL,
    severity text NOT NULL,
    created_at timestamptz NOT NULL,
    finding_document jsonb NOT NULL,
    PRIMARY KEY (
        organization_id, project_id, region_code, dataset_id, version_id, review_finding_id
    ),
    FOREIGN KEY (
        organization_id, project_id, region_code, dataset_id, version_id, review_decision_id
    ) REFERENCES dataset_registry.dataset_version_review_decisions (
        organization_id, project_id, region_code, dataset_id, version_id, review_decision_id
    ),
    CHECK (review_finding_id ~ '^review_finding_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (output_revision_id ~ '^revision_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (episode_stream_id ~ '^stream_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    CHECK (jsonb_typeof(finding_document) = 'object'),
    CHECK (finding_document ->> 'id' = review_finding_id),
    CHECK (finding_document ->> 'output_revision_id' = output_revision_id),
    CHECK (finding_document ->> 'episode_stream_id' = episode_stream_id),
    CHECK (NOT (finding_document ? 'credential')),
    CHECK (NOT (finding_document ? 'secret')),
    CHECK (NOT (finding_document ? 'token'))
);

CREATE TABLE IF NOT EXISTS dataset_registry.dataset_version_successor_drafts (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    version_id text NOT NULL,
    successor_draft_id text NOT NULL,
    supersedes_draft_id text NOT NULL,
    review_decision_id text NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (
        organization_id, project_id, region_code, dataset_id, version_id, successor_draft_id
    ),
    FOREIGN KEY (
        organization_id, project_id, region_code, dataset_id, version_id, review_decision_id
    ) REFERENCES dataset_registry.dataset_version_review_decisions (
        organization_id, project_id, region_code, dataset_id, version_id, review_decision_id
    ),
    CHECK (successor_draft_id ~ '^draft_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (supersedes_draft_id ~ '^draft_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (successor_draft_id <> supersedes_draft_id)
);

CREATE TABLE IF NOT EXISTS dataset_registry.dataset_version_async_jobs (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    version_id text NOT NULL,
    job_id text NOT NULL,
    job_type text NOT NULL,
    job_status text NOT NULL,
    resource_version bigint NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    job_document jsonb NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, job_id),
    FOREIGN KEY (organization_id, project_id, region_code, dataset_id, version_id)
        REFERENCES dataset_registry.dataset_versions (
            organization_id, project_id, region_code, dataset_id, version_id
        ),
    CHECK (job_id <> ''),
    CHECK (job_type <> ''),
    CHECK (job_status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')),
    CHECK (resource_version > 0),
    CHECK (updated_at >= created_at),
    CHECK (jsonb_typeof(job_document) = 'object'),
    CHECK (job_document ->> 'job_id' = job_id),
    CHECK (job_document ->> 'job_type' = job_type),
    CHECK (job_document ->> 'status' = job_status),
    CHECK (NOT (job_document ? 'credential')),
    CHECK (NOT (job_document ? 'secret')),
    CHECK (NOT (job_document ? 'token')),
    CHECK (NOT (job_document ? 'object_locator')),
    CHECK (NOT (job_document ? 'object_path'))
);

CREATE INDEX IF NOT EXISTS dataset_version_async_jobs_version_idx
ON dataset_registry.dataset_version_async_jobs (
    organization_id, project_id, region_code, dataset_id, version_id, created_at DESC
);

SELECT core.apply_project_rls('dataset_registry.dataset_version_content_projections'::regclass);
SELECT core.apply_project_rls('dataset_registry.dataset_version_episode_revisions'::regclass);
SELECT core.apply_project_rls('dataset_registry.dataset_version_schema_details'::regclass);
SELECT core.apply_project_rls('dataset_registry.dataset_version_manifest_entries'::regclass);
SELECT core.apply_project_rls('dataset_registry.dataset_version_required_storage'::regclass);
SELECT core.apply_project_rls('dataset_registry.dataset_version_operational_inventory'::regclass);
SELECT core.apply_project_rls('dataset_registry.dataset_version_review_decisions'::regclass);
SELECT core.apply_project_rls('dataset_registry.dataset_version_review_findings'::regclass);
SELECT core.apply_project_rls('dataset_registry.dataset_version_successor_drafts'::regclass);
SELECT core.apply_project_rls('dataset_registry.dataset_version_async_jobs'::regclass);
