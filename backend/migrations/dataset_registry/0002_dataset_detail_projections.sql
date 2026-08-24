-- P06 immutable dataset-detail projections.  These tables are intentionally
-- separate from the lower-level Lance catalog: the page has stable product IDs
-- and only persists safe, user-visible facts.

CREATE TABLE IF NOT EXISTS dataset_registry.dataset_versions (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    version_id text NOT NULL,
    display_version text NOT NULL,
    version_kind text NOT NULL,
    version_status text NOT NULL,
    created_at timestamptz NOT NULL,
    published_at timestamptz,
    version_document jsonb NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, dataset_id, version_id),
    FOREIGN KEY (organization_id, project_id, region_code, dataset_id)
        REFERENCES dataset_registry.datasets (organization_id, project_id, region_code, dataset_id),
    CHECK (version_id ~ '^version_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (display_version <> ''),
    CHECK (length(display_version) <= 64),
    CHECK (version_kind IN ('RAW', 'CLEANED')),
    CHECK (version_status IN ('REVIEWING', 'RETURNED', 'READY')),
    CHECK (version_status <> 'READY' OR published_at IS NOT NULL),
    CHECK (jsonb_typeof(version_document) = 'object'),
    CHECK (version_document ->> 'dataset_id' = dataset_id),
    CHECK (version_document ->> 'version_id' = version_id),
    CHECK (version_document ->> 'kind' = version_kind),
    CHECK (version_document ->> 'status' = version_status),
    CHECK (version_document -> 'scope' ->> 'organization_id' = organization_id),
    CHECK (version_document -> 'scope' ->> 'project_id' = project_id),
    CHECK (version_document -> 'scope' ->> 'region_code' = region_code),
    CHECK (NOT (version_document ? 'credential_input')),
    CHECK (NOT (version_document ? 'credential')),
    CHECK (NOT (version_document ? 'secret')),
    CHECK (NOT (version_document ? 'access_token')),
    CHECK (NOT (version_document ? 'authorization'))
);

CREATE INDEX IF NOT EXISTS dataset_detail_versions_created_idx
ON dataset_registry.dataset_versions (
    organization_id, project_id, region_code, dataset_id, created_at DESC, version_id DESC
);

CREATE INDEX IF NOT EXISTS dataset_detail_versions_status_idx
ON dataset_registry.dataset_versions (
    organization_id, project_id, region_code, dataset_id, version_kind, version_status
);

CREATE TABLE IF NOT EXISTS dataset_registry.dataset_detail_facts (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    episode_count bigint NOT NULL,
    effective_duration_ns numeric(30, 0) NOT NULL,
    source_bytes numeric(30, 0) NOT NULL,
    required_physical_bytes numeric(30, 0) NOT NULL,
    actual_oss_bytes numeric(30, 0),
    pending_review_version_count bigint NOT NULL,
    returned_version_count bigint NOT NULL,
    actionable_draft_count bigint NOT NULL,
    calculation_state text NOT NULL,
    calculated_at timestamptz NOT NULL,
    fact_document jsonb NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, dataset_id),
    FOREIGN KEY (organization_id, project_id, region_code, dataset_id)
        REFERENCES dataset_registry.datasets (organization_id, project_id, region_code, dataset_id),
    CHECK (episode_count >= 0),
    CHECK (effective_duration_ns >= 0),
    CHECK (source_bytes >= 0),
    CHECK (required_physical_bytes >= 0),
    CHECK (actual_oss_bytes IS NULL OR actual_oss_bytes >= 0),
    CHECK (pending_review_version_count >= 0),
    CHECK (returned_version_count >= 0),
    CHECK (actionable_draft_count >= 0),
    CHECK (calculation_state IN ('CALCULATING', 'PARTIAL', 'SETTLED', 'FAILED')),
    CHECK (jsonb_typeof(fact_document) = 'object'),
    CHECK (fact_document ->> 'dataset_id' = dataset_id),
    CHECK (fact_document -> 'scope' ->> 'organization_id' = organization_id),
    CHECK (fact_document -> 'scope' ->> 'project_id' = project_id),
    CHECK (fact_document -> 'scope' ->> 'region_code' = region_code),
    CHECK (NOT (fact_document ? 'credential')),
    CHECK (NOT (fact_document ? 'secret'))
);

CREATE TABLE IF NOT EXISTS dataset_registry.dataset_version_schema_summaries (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    version_id text NOT NULL,
    schema_snapshot_id text NOT NULL,
    channel_count bigint,
    schema_document jsonb NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, dataset_id, version_id),
    FOREIGN KEY (organization_id, project_id, region_code, dataset_id, version_id)
        REFERENCES dataset_registry.dataset_versions (
            organization_id, project_id, region_code, dataset_id, version_id
        ),
    CHECK (schema_snapshot_id <> ''),
    CHECK (channel_count IS NULL OR channel_count >= 0),
    CHECK (jsonb_typeof(schema_document) = 'object'),
    CHECK (schema_document ->> 'dataset_id' = dataset_id),
    CHECK (schema_document ->> 'version_id' = version_id),
    CHECK (schema_document -> 'scope' ->> 'organization_id' = organization_id),
    CHECK (schema_document -> 'scope' ->> 'project_id' = project_id),
    CHECK (schema_document -> 'scope' ->> 'region_code' = region_code),
    CHECK (NOT (schema_document ? 'credential')),
    CHECK (NOT (schema_document ? 'secret'))
);

CREATE TABLE IF NOT EXISTS dataset_registry.dataset_version_source_provenance (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    version_id text NOT NULL,
    provenance_id text NOT NULL,
    upload_id text NOT NULL,
    source_id text,
    source_display_name text,
    source_manifest_id text NOT NULL,
    registered_at timestamptz NOT NULL,
    provenance_document jsonb NOT NULL,
    PRIMARY KEY (
        organization_id, project_id, region_code, dataset_id, version_id, provenance_id
    ),
    FOREIGN KEY (organization_id, project_id, region_code, dataset_id, version_id)
        REFERENCES dataset_registry.dataset_versions (
            organization_id, project_id, region_code, dataset_id, version_id
        ),
    CHECK (provenance_id <> ''),
    CHECK (upload_id <> ''),
    CHECK (source_manifest_id <> ''),
    CHECK (jsonb_typeof(provenance_document) = 'object'),
    CHECK (provenance_document ->> 'dataset_id' = dataset_id),
    CHECK (provenance_document ->> 'version_id' = version_id),
    CHECK (provenance_document ->> 'provenance_id' = provenance_id),
    CHECK (provenance_document -> 'scope' ->> 'organization_id' = organization_id),
    CHECK (provenance_document -> 'scope' ->> 'project_id' = project_id),
    CHECK (provenance_document -> 'scope' ->> 'region_code' = region_code),
    CHECK (NOT (provenance_document ? 'credential')),
    CHECK (NOT (provenance_document ? 'secret')),
    CHECK (NOT (provenance_document ? 'object_locator')),
    CHECK (NOT (provenance_document ? 'object_path'))
);

CREATE INDEX IF NOT EXISTS dataset_detail_source_provenance_registered_idx
ON dataset_registry.dataset_version_source_provenance (
    organization_id, project_id, region_code, dataset_id, version_id,
    registered_at DESC, provenance_id DESC
);

CREATE INDEX IF NOT EXISTS dataset_detail_source_provenance_source_idx
ON dataset_registry.dataset_version_source_provenance (
    organization_id, project_id, region_code, dataset_id, version_id, source_id
);

CREATE TABLE IF NOT EXISTS dataset_registry.dataset_version_capacity_facts (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    version_id text NOT NULL,
    capacity_state text NOT NULL,
    calculated_at timestamptz NOT NULL,
    capacity_document jsonb NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, dataset_id, version_id),
    FOREIGN KEY (organization_id, project_id, region_code, dataset_id, version_id)
        REFERENCES dataset_registry.dataset_versions (
            organization_id, project_id, region_code, dataset_id, version_id
        ),
    CHECK (capacity_state IN ('CALCULATING', 'PARTIAL', 'SETTLED', 'FAILED')),
    CHECK (jsonb_typeof(capacity_document) = 'object'),
    CHECK (capacity_document ->> 'dataset_id' = dataset_id),
    CHECK (capacity_document ->> 'version_id' = version_id),
    CHECK (capacity_document ->> 'state' = capacity_state),
    CHECK (capacity_document -> 'scope' ->> 'organization_id' = organization_id),
    CHECK (capacity_document -> 'scope' ->> 'project_id' = project_id),
    CHECK (capacity_document -> 'scope' ->> 'region_code' = region_code),
    CHECK (NOT (capacity_document ? 'credential')),
    CHECK (NOT (capacity_document ? 'secret'))
);

CREATE TABLE IF NOT EXISTS dataset_registry.dataset_version_episodes (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    version_id text NOT NULL,
    episode_id text NOT NULL,
    revision_id text NOT NULL,
    ordinal bigint NOT NULL,
    started_at timestamptz,
    started_at_ns numeric(30, 0) NOT NULL,
    included boolean NOT NULL,
    success_state text NOT NULL,
    task text,
    robot_id text,
    review_status text,
    review_finding_count bigint NOT NULL DEFAULT 0,
    has_finding boolean NOT NULL DEFAULT false,
    change_type text,
    episode_document jsonb NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, dataset_id, version_id, episode_id),
    FOREIGN KEY (organization_id, project_id, region_code, dataset_id, version_id)
        REFERENCES dataset_registry.dataset_versions (
            organization_id, project_id, region_code, dataset_id, version_id
        ),
    CHECK (episode_id ~ '^episode_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (revision_id ~ '^revision_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (ordinal >= 0),
    CHECK (started_at_ns >= 0),
    CHECK (success_state <> ''),
    CHECK (review_finding_count >= 0),
    CHECK ((review_finding_count > 0) = has_finding),
    CHECK (jsonb_typeof(episode_document) = 'object'),
    CHECK (episode_document ->> 'dataset_id' = dataset_id),
    CHECK (episode_document ->> 'version_id' = version_id),
    CHECK (episode_document ->> 'episode_id' = episode_id),
    CHECK (episode_document -> 'selected_revision' ->> 'revision_id' = revision_id),
    CHECK ((episode_document -> 'selected_revision' ->> 'ordinal')::bigint = ordinal),
    CHECK (episode_document -> 'scope' ->> 'organization_id' = organization_id),
    CHECK (episode_document -> 'scope' ->> 'project_id' = project_id),
    CHECK (episode_document -> 'scope' ->> 'region_code' = region_code),
    CHECK (NOT (episode_document ? 'credential')),
    CHECK (NOT (episode_document ? 'secret')),
    CHECK (NOT (episode_document ? 'object_locator')),
    CHECK (NOT (episode_document ? 'object_path'))
);

CREATE INDEX IF NOT EXISTS dataset_detail_episodes_ordinal_idx
ON dataset_registry.dataset_version_episodes (
    organization_id, project_id, region_code, dataset_id, version_id, ordinal, episode_id
);

CREATE INDEX IF NOT EXISTS dataset_detail_episodes_started_idx
ON dataset_registry.dataset_version_episodes (
    organization_id, project_id, region_code, dataset_id, version_id,
    started_at_ns DESC, episode_id DESC
);

CREATE INDEX IF NOT EXISTS dataset_detail_episodes_filters_idx
ON dataset_registry.dataset_version_episodes (
    organization_id, project_id, region_code, dataset_id, version_id,
    task, robot_id, success_state, included, review_status, has_finding, change_type
);

SELECT core.apply_project_rls('dataset_registry.dataset_versions'::regclass);
SELECT core.apply_project_rls('dataset_registry.dataset_detail_facts'::regclass);
SELECT core.apply_project_rls('dataset_registry.dataset_version_schema_summaries'::regclass);
SELECT core.apply_project_rls('dataset_registry.dataset_version_source_provenance'::regclass);
SELECT core.apply_project_rls('dataset_registry.dataset_version_capacity_facts'::regclass);
SELECT core.apply_project_rls('dataset_registry.dataset_version_episodes'::regclass);
