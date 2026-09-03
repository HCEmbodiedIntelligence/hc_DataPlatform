-- Global application-process liveness. These rows are operational control-plane
-- state, not tenant data, so they deliberately have no project RLS policy.

CREATE SCHEMA IF NOT EXISTS platform;

CREATE TABLE IF NOT EXISTS platform.platform_instances (
    instance_id uuid PRIMARY KEY,
    node_name text NOT NULL,
    role text NOT NULL,
    release_id text NOT NULL,
    release_manifest_digest text NOT NULL,
    component_image_digest text NOT NULL,
    runtime_version text NOT NULL,
    pod_name text,
    kubernetes_node_name text,
    kubernetes_zone text,
    started_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    last_heartbeat_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    readiness_summary jsonb NOT NULL,
    CHECK (node_name <> '' AND length(node_name) <= 253),
    CHECK (role IN ('frontend', 'api', 'worker', 'media-worker', 'maintenance-controller')),
    CHECK (
        release_id = 'unreleased'
        OR release_id ~ '^platform-v[A-Za-z0-9][A-Za-z0-9._-]{0,119}$'
    ),
    CHECK (
        release_manifest_digest = 'unreleased'
        OR release_manifest_digest ~ '^sha256:[0-9a-f]{64}$'
    ),
    CHECK (
        component_image_digest = 'unreleased'
        OR component_image_digest ~ '^sha256:[0-9a-f]{64}$'
    ),
    CHECK (runtime_version <> '' AND length(runtime_version) <= 128),
    CHECK (pod_name IS NULL OR (pod_name <> '' AND length(pod_name) <= 253)),
    CHECK (
        kubernetes_node_name IS NULL
        OR (kubernetes_node_name <> '' AND length(kubernetes_node_name) <= 253)
    ),
    CHECK (
        kubernetes_zone IS NULL
        OR (kubernetes_zone <> '' AND length(kubernetes_zone) <= 253)
    ),
    CHECK (last_heartbeat_at >= started_at),
    CHECK (jsonb_typeof(readiness_summary) = 'object'),
    CHECK (
        readiness_summary->>'status'
        IN ('starting', 'ready', 'not_ready', 'draining')
    ),
    CHECK (jsonb_typeof(readiness_summary->'failed_checks') = 'array')
);

CREATE INDEX IF NOT EXISTS platform_instances_heartbeat_idx
ON platform.platform_instances (last_heartbeat_at, instance_id);

CREATE INDEX IF NOT EXISTS platform_instances_role_heartbeat_idx
ON platform.platform_instances (role, last_heartbeat_at DESC, instance_id);
