-- HA4-05 stores only low-risk, allowlisted runtime configuration snapshots.
-- Secret material and release-owned configuration are rejected by the application
-- contract before this table is reached. Revisions and events are immutable facts;
-- rollback creates a new monotonic revision instead of moving the head backwards.

ALTER TABLE platform.platform_instances
ADD COLUMN IF NOT EXISTS applied_config_revision bigint NOT NULL DEFAULT 0
CHECK (applied_config_revision >= 0);

CREATE TABLE IF NOT EXISTS platform.platform_runtime_config_heads (
    environment_id text PRIMARY KEY,
    current_revision bigint NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    CHECK (
        environment_id ~ '^[A-Za-z0-9](?:[A-Za-z0-9._:/-]{0,126}[A-Za-z0-9])?$'
    ),
    CHECK (current_revision >= 0)
);

CREATE TABLE IF NOT EXISTS platform.platform_runtime_config_revisions (
    environment_id text NOT NULL,
    revision bigint NOT NULL,
    schema_version text NOT NULL,
    values_json jsonb NOT NULL,
    content_sha256 char(64) NOT NULL,
    actor_id text NOT NULL,
    reason text NOT NULL,
    request_id text NOT NULL,
    rollback_of_revision bigint,
    created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    PRIMARY KEY (environment_id, revision),
    CHECK (revision > 0),
    CHECK (schema_version = 'hc-runtime-config/v1'),
    CHECK (jsonb_typeof(values_json) = 'object'),
    CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (actor_id <> '' AND length(actor_id) <= 255),
    CHECK (reason <> '' AND length(reason) <= 500),
    CHECK (request_id <> '' AND length(request_id) <= 255),
    CHECK (rollback_of_revision IS NULL OR rollback_of_revision >= 0),
    FOREIGN KEY (environment_id)
        REFERENCES platform.platform_runtime_config_heads(environment_id)
);

CREATE TABLE IF NOT EXISTS platform.platform_runtime_config_events (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    environment_id text NOT NULL,
    event_kind text NOT NULL,
    previous_revision bigint NOT NULL,
    revision bigint NOT NULL,
    target_revision bigint,
    actor_id text NOT NULL,
    reason text NOT NULL,
    request_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    CHECK (event_kind IN ('APPLY', 'ROLLBACK')),
    CHECK (previous_revision >= 0),
    CHECK (revision > previous_revision),
    CHECK (
        (event_kind = 'APPLY' AND target_revision IS NULL)
        OR (event_kind = 'ROLLBACK' AND target_revision >= 0)
    ),
    CHECK (actor_id <> '' AND length(actor_id) <= 255),
    CHECK (reason <> '' AND length(reason) <= 500),
    CHECK (request_id <> '' AND length(request_id) <= 255),
    UNIQUE (environment_id, revision),
    FOREIGN KEY (environment_id, revision)
        REFERENCES platform.platform_runtime_config_revisions(environment_id, revision)
);

CREATE INDEX IF NOT EXISTS platform_runtime_config_revisions_created_idx
ON platform.platform_runtime_config_revisions (environment_id, created_at DESC, revision DESC);

CREATE INDEX IF NOT EXISTS platform_runtime_config_events_created_idx
ON platform.platform_runtime_config_events (environment_id, created_at DESC, event_id DESC);

CREATE OR REPLACE FUNCTION platform.reject_runtime_config_fact_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'PLATFORM_RUNTIME_CONFIG_FACT_IMMUTABLE' USING ERRCODE = 'P0001';
END;
$function$;

DROP TRIGGER IF EXISTS platform_runtime_config_revisions_immutable
ON platform.platform_runtime_config_revisions;
CREATE TRIGGER platform_runtime_config_revisions_immutable
BEFORE UPDATE OR DELETE ON platform.platform_runtime_config_revisions
FOR EACH ROW EXECUTE FUNCTION platform.reject_runtime_config_fact_mutation();

DROP TRIGGER IF EXISTS platform_runtime_config_events_immutable
ON platform.platform_runtime_config_events;
CREATE TRIGGER platform_runtime_config_events_immutable
BEFORE UPDATE OR DELETE ON platform.platform_runtime_config_events
FOR EACH ROW EXECUTE FUNCTION platform.reject_runtime_config_fact_mutation();
