-- P14 formal robot-model registry.  This slice intentionally persists only
-- approved read metadata; asset upload and publication remain explicit 501
-- feature-unavailable operations until a separately approved workflow exists.
CREATE SCHEMA IF NOT EXISTS registry;

CREATE TABLE IF NOT EXISTS registry.organization_projects (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    PRIMARY KEY (organization_id, project_id),
    CHECK (organization_id <> ''),
    CHECK (project_id <> '')
);

SELECT core.apply_project_rls('registry.organization_projects'::regclass);

CREATE TABLE IF NOT EXISTS registry.robot_models (
    organization_id text NOT NULL,
    model_id text NOT NULL,
    manufacturer text NOT NULL,
    model_code text NOT NULL,
    display_name text NOT NULL,
    current_published_version_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, model_id),
    UNIQUE (organization_id, manufacturer, model_code),
    CHECK (organization_id <> ''),
    CHECK (model_id <> ''),
    CHECK (manufacturer <> ''),
    CHECK (model_code <> ''),
    CHECK (display_name <> '')
);

CREATE TABLE IF NOT EXISTS registry.robot_model_versions (
    organization_id text NOT NULL,
    version_id text NOT NULL,
    robot_model_id text NOT NULL,
    version_label text NOT NULL,
    lifecycle text NOT NULL,
    asset_availability text NOT NULL,
    publish_readiness text NOT NULL,
    asset_manifest_hash text,
    validation_input_hash text,
    etag text NOT NULL,
    allowed_actions jsonb NOT NULL DEFAULT '[]'::jsonb,
    blocked_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, version_id),
    FOREIGN KEY (organization_id, robot_model_id)
        REFERENCES registry.robot_models (organization_id, model_id),
    CHECK (version_id <> ''),
    CHECK (version_label <> ''),
    CHECK (lifecycle IN ('DRAFT', 'PUBLISHED', 'DISABLED')),
    CHECK (asset_availability IN ('UNKNOWN', 'AVAILABLE', 'PARTIAL', 'MISSING')),
    CHECK (publish_readiness IN (
        'CONFIGURATION_REQUIRED', 'MAPPING_REQUIRED', 'SAMPLE_VALIDATION_REQUIRED', 'READY', 'BLOCKED'
    )),
    CHECK (etag <> ''),
    CHECK (jsonb_typeof(allowed_actions) = 'array'),
    CHECK (jsonb_typeof(blocked_reasons) = 'array')
);

ALTER TABLE registry.robot_models
    ADD CONSTRAINT registry_robot_models_current_version_fk
    FOREIGN KEY (organization_id, current_published_version_id)
    REFERENCES registry.robot_model_versions (organization_id, version_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE INDEX IF NOT EXISTS registry_robot_models_organization_name_idx
ON registry.robot_models (organization_id, lower(display_name), model_id);

CREATE INDEX IF NOT EXISTS registry_robot_model_versions_model_idx
ON registry.robot_model_versions (organization_id, robot_model_id, version_id);

CREATE TABLE IF NOT EXISTS registry.audit_events (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id text NOT NULL,
    project_id text NOT NULL,
    actor_id text NOT NULL,
    action text NOT NULL,
    resource_type text NOT NULL,
    resource_id text NOT NULL,
    request_id text NOT NULL,
    outcome text NOT NULL,
    occurred_at timestamptz NOT NULL,
    CHECK (organization_id <> ''),
    CHECK (project_id <> ''),
    CHECK (actor_id <> ''),
    CHECK (action <> ''),
    CHECK (resource_type <> ''),
    CHECK (resource_id <> ''),
    CHECK (request_id <> ''),
    CHECK (outcome IN ('SUCCEEDED', 'FEATURE_UNAVAILABLE'))
);

CREATE INDEX IF NOT EXISTS registry_audit_scope_time_idx
ON registry.audit_events (organization_id, project_id, occurred_at DESC, event_id DESC);

SELECT core.apply_project_rls('registry.audit_events'::regclass);
