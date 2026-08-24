-- P05 user-visible dataset registry.  This is intentionally separate from the
-- lower-level Lance catalog: an empty Dataset is a valid product resource.
CREATE SCHEMA IF NOT EXISTS dataset_registry;

CREATE TABLE IF NOT EXISTS dataset_registry.datasets (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    name text NOT NULL,
    description text NOT NULL,
    labels jsonb NOT NULL DEFAULT '[]'::jsonb,
    availability text NOT NULL,
    owner_id text NOT NULL,
    owner_display_name text NOT NULL,
    robot_model_id text,
    robot_id text,
    task text,
    scene text,
    asset_state text NOT NULL,
    storage_class text NOT NULL,
    channels jsonb NOT NULL DEFAULT '[]'::jsonb,
    episode_count bigint NOT NULL DEFAULT 0,
    pending_review_version_count bigint NOT NULL DEFAULT 0,
    returned_version_count bigint NOT NULL DEFAULT 0,
    actionable_draft_count bigint NOT NULL DEFAULT 0,
    version bigint NOT NULL,
    dataset_document jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    activity_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, dataset_id),
    FOREIGN KEY (organization_id, project_id)
        REFERENCES registry.organization_projects (organization_id, project_id),
    CHECK (organization_id <> ''),
    CHECK (project_id <> ''),
    CHECK (region_code <> ''),
    CHECK (dataset_id ~ '^dataset_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (name <> ''),
    CHECK (length(name) <= 256),
    CHECK (length(description) <= 4096),
    CHECK (availability IN ('ACTIVE', 'FROZEN')),
    CHECK (owner_id <> ''),
    CHECK (owner_display_name <> ''),
    CHECK (asset_state <> ''),
    CHECK (storage_class <> ''),
    CHECK (episode_count >= 0),
    CHECK (pending_review_version_count >= 0),
    CHECK (returned_version_count >= 0),
    CHECK (actionable_draft_count >= 0),
    CHECK (version > 0),
    CHECK (jsonb_typeof(labels) = 'array'),
    CHECK (jsonb_typeof(channels) = 'array'),
    CHECK (jsonb_typeof(dataset_document) = 'object'),
    CHECK (dataset_document ->> 'dataset_id' = dataset_id),
    CHECK (dataset_document -> 'scope' ->> 'organization_id' = organization_id),
    CHECK (dataset_document -> 'scope' ->> 'project_id' = project_id),
    CHECK (dataset_document -> 'scope' ->> 'region_code' = region_code),
    CHECK (NOT (dataset_document ? 'credential_input')),
    CHECK (NOT (dataset_document ? 'token')),
    CHECK (NOT (dataset_document ? 'secret')),
    CHECK (updated_at >= created_at),
    CHECK (activity_at >= created_at)
);

CREATE UNIQUE INDEX IF NOT EXISTS dataset_page_scope_name_uq
ON dataset_registry.datasets (organization_id, project_id, region_code, lower(name));

CREATE INDEX IF NOT EXISTS dataset_page_scope_activity_idx
ON dataset_registry.datasets (
    organization_id, project_id, region_code, activity_at DESC, dataset_id DESC
);

CREATE INDEX IF NOT EXISTS dataset_page_scope_created_idx
ON dataset_registry.datasets (
    organization_id, project_id, region_code, created_at DESC, dataset_id DESC
);

CREATE INDEX IF NOT EXISTS dataset_page_scope_filters_idx
ON dataset_registry.datasets (
    organization_id, project_id, region_code, robot_model_id, robot_id, task, scene,
    asset_state, storage_class
);

CREATE INDEX IF NOT EXISTS dataset_page_channels_gin_idx
ON dataset_registry.datasets USING gin (channels);

SELECT core.apply_project_rls('dataset_registry.datasets'::regclass);
