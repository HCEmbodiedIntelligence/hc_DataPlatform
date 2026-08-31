-- REL6 stores the durable release state separately from the append-only P19
-- audit chain. Browser requests can approve state, but never carry deployment
-- credentials or execute Kubernetes mutations.

CREATE TABLE IF NOT EXISTS platform.platform_release_runs (
    environment_id text NOT NULL,
    release_id text NOT NULL,
    source_version text NOT NULL,
    target_version text NOT NULL,
    manifest_sha256 char(64) NOT NULL,
    source_images_json jsonb NOT NULL,
    target_images_json jsonb NOT NULL,
    state text NOT NULL,
    state_version bigint NOT NULL DEFAULT 1,
    requested_by text NOT NULL,
    approved_by text,
    approval_reason text,
    created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    PRIMARY KEY (environment_id, release_id),
    CHECK (environment_id ~ '^[A-Za-z0-9](?:[A-Za-z0-9._:/-]{0,126}[A-Za-z0-9])?$'),
    CHECK (release_id ~ '^platform-v[A-Za-z0-9][A-Za-z0-9._-]{0,119}$'),
    CHECK (source_version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'),
    CHECK (target_version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'),
    CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (jsonb_typeof(source_images_json) = 'object'),
    CHECK (jsonb_typeof(target_images_json) = 'object'),
    CHECK (state IN (
        'PREFLIGHT_BLOCKED', 'AWAITING_APPROVAL', 'APPROVED', 'EXPAND',
        'CANARY', 'ROLLOUT', 'CONTRACT_PENDING', 'COMPLETED',
        'ROLLED_BACK', 'FAILED'
    )),
    CHECK (state_version > 0),
    CHECK (requested_by <> '' AND length(requested_by) <= 255),
    CHECK (approved_by IS NULL OR (approved_by <> '' AND length(approved_by) <= 255)),
    CHECK (approved_by IS NULL OR approved_by <> requested_by),
    CHECK (approval_reason IS NULL OR length(approval_reason) BETWEEN 8 AND 500)
);

CREATE TABLE IF NOT EXISTS platform.platform_release_events (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    environment_id text NOT NULL,
    release_id text NOT NULL,
    state_version bigint NOT NULL,
    event_kind text NOT NULL,
    state text NOT NULL,
    actor_id text NOT NULL,
    reason_code text NOT NULL,
    request_id text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    UNIQUE (environment_id, release_id, state_version),
    FOREIGN KEY (environment_id, release_id)
        REFERENCES platform.platform_release_runs(environment_id, release_id) ON DELETE RESTRICT,
    CHECK (state_version > 0),
    CHECK (event_kind ~ '^[A-Z][A-Z0-9_]{0,127}$'),
    CHECK (reason_code ~ '^[A-Z][A-Z0-9_]{0,127}$'),
    CHECK (actor_id <> '' AND length(actor_id) <= 255),
    CHECK (request_id <> '' AND length(request_id) <= 255)
);

CREATE INDEX IF NOT EXISTS platform_release_runs_updated_idx
ON platform.platform_release_runs (environment_id, updated_at DESC, release_id DESC);

CREATE INDEX IF NOT EXISTS platform_release_events_occurred_idx
ON platform.platform_release_events (environment_id, release_id, occurred_at, event_id);

CREATE OR REPLACE FUNCTION platform.reject_release_event_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'PLATFORM_RELEASE_EVENT_IMMUTABLE' USING ERRCODE = 'P0001';
END;
$function$;

DROP TRIGGER IF EXISTS platform_release_events_immutable
ON platform.platform_release_events;
CREATE TRIGGER platform_release_events_immutable
BEFORE UPDATE OR DELETE ON platform.platform_release_events
FOR EACH ROW EXECUTE FUNCTION platform.reject_release_event_mutation();
