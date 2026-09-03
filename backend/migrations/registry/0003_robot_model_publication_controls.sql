-- P14 publication controls: project-scoped joint mappings, one-time publish
-- preflights, and a durable robot-binding history mirrored into P15's current
-- robot projection by the application transaction.

CREATE TABLE IF NOT EXISTS registry.robot_model_joint_mappings (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    version_id text NOT NULL,
    source_joint_name text NOT NULL,
    target_joint_name text NOT NULL,
    direction text NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, project_id, version_id, source_joint_name),
    UNIQUE (organization_id, project_id, version_id, target_joint_name),
    FOREIGN KEY (organization_id, version_id)
        REFERENCES registry.robot_model_versions (organization_id, version_id),
    CHECK (project_id <> ''),
    CHECK (source_joint_name <> ''),
    CHECK (target_joint_name <> ''),
    CHECK (direction IN ('SAME', 'INVERTED'))
);

CREATE TABLE IF NOT EXISTS registry.robot_model_publish_preflights (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    preflight_id uuid NOT NULL,
    version_id text NOT NULL,
    idempotency_key text NOT NULL,
    request_fingerprint char(64) NOT NULL,
    expected_etag text NOT NULL,
    asset_manifest_hash char(64),
    mapping_hash char(64),
    allowed boolean NOT NULL,
    status text NOT NULL,
    token_hash char(64) NOT NULL,
    checks jsonb NOT NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL,
    consumed_at timestamptz,
    PRIMARY KEY (organization_id, project_id, preflight_id),
    UNIQUE (organization_id, project_id, version_id, idempotency_key),
    FOREIGN KEY (organization_id, version_id)
        REFERENCES registry.robot_model_versions (organization_id, version_id),
    CHECK (idempotency_key <> ''),
    CHECK (expected_etag <> ''),
    CHECK (status IN ('ISSUED', 'CONSUMED', 'EXPIRED')),
    CHECK (jsonb_typeof(checks) = 'array'),
    CHECK ((status = 'CONSUMED') = (consumed_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS registry.robot_model_bindings (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    binding_id uuid NOT NULL,
    robot_id text NOT NULL,
    version_id text NOT NULL,
    idempotency_key text NOT NULL,
    request_fingerprint char(64) NOT NULL,
    expected_robot_etag text NOT NULL,
    status text NOT NULL,
    bound_at timestamptz NOT NULL,
    unbound_at timestamptz,
    PRIMARY KEY (organization_id, project_id, region_code, binding_id),
    UNIQUE (organization_id, project_id, region_code, version_id, idempotency_key),
    FOREIGN KEY (organization_id, version_id)
        REFERENCES registry.robot_model_versions (organization_id, version_id),
    CHECK (region_code <> ''),
    CHECK (robot_id <> ''),
    CHECK (idempotency_key <> ''),
    CHECK (expected_robot_etag <> ''),
    CHECK (status IN ('ACTIVE', 'SUPERSEDED', 'REVOKED')),
    CHECK ((status = 'ACTIVE' AND unbound_at IS NULL) OR (status <> 'ACTIVE' AND unbound_at IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS registry_robot_model_bindings_one_active_robot_idx
ON registry.robot_model_bindings (organization_id, project_id, region_code, robot_id)
WHERE status = 'ACTIVE';

CREATE INDEX IF NOT EXISTS registry_robot_model_joint_mappings_version_idx
ON registry.robot_model_joint_mappings (organization_id, project_id, version_id, source_joint_name);

CREATE INDEX IF NOT EXISTS registry_robot_model_publish_preflights_expiry_idx
ON registry.robot_model_publish_preflights (organization_id, project_id, version_id, expires_at);

SELECT core.apply_project_rls('registry.robot_model_joint_mappings'::regclass);
SELECT core.apply_project_rls('registry.robot_model_publish_preflights'::regclass);
SELECT core.apply_project_rls('registry.robot_model_bindings'::regclass);
