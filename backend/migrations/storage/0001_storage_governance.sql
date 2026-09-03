CREATE SCHEMA IF NOT EXISTS storage;

CREATE TABLE IF NOT EXISTS storage.inventory_snapshots (
    project_id text NOT NULL,
    snapshot_id text NOT NULL,
    observed_at timestamptz NOT NULL,
    physical_total_bytes numeric(39, 0) NOT NULL,
    physical_instance_count bigint NOT NULL,
    candidate_business_total_bytes numeric(39, 0) NOT NULL,
    candidate_logical_object_count bigint NOT NULL,
    replica_overhead_bytes numeric(39, 0) NOT NULL,
    temporary_bytes numeric(39, 0) NOT NULL,
    duplicate_inventory_rows_ignored bigint NOT NULL DEFAULT 0,
    content_digest char(64) NOT NULL,
    published_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, snapshot_id),
    CHECK (project_id <> ''),
    CHECK (snapshot_id <> ''),
    CHECK (physical_total_bytes >= 0),
    CHECK (physical_instance_count >= 0),
    CHECK (candidate_business_total_bytes >= 0),
    CHECK (candidate_logical_object_count >= 0),
    CHECK (replica_overhead_bytes >= 0),
    CHECK (temporary_bytes >= 0),
    CHECK (duplicate_inventory_rows_ignored >= 0),
    CHECK (content_digest ~ '^[0-9a-f]{64}$'),
    CHECK (
        physical_total_bytes
        = candidate_business_total_bytes + replica_overhead_bytes + temporary_bytes
    )
);

CREATE INDEX IF NOT EXISTS storage_inventory_snapshots_latest_idx
ON storage.inventory_snapshots (project_id, observed_at DESC, snapshot_id DESC);

CREATE TABLE IF NOT EXISTS storage.inventory_facts (
    project_id text NOT NULL,
    snapshot_id text NOT NULL,
    physical_instance_id text NOT NULL,
    logical_object_id text,
    physical_bytes numeric(39, 0) NOT NULL,
    disposition text NOT NULL,
    business_category text,
    object_role text NOT NULL,
    observed_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, snapshot_id, physical_instance_id),
    FOREIGN KEY (project_id, snapshot_id)
        REFERENCES storage.inventory_snapshots (project_id, snapshot_id)
        ON DELETE RESTRICT,
    CHECK (project_id <> ''),
    CHECK (snapshot_id <> ''),
    CHECK (physical_instance_id <> ''),
    CHECK (logical_object_id IS NULL OR logical_object_id <> ''),
    CHECK (physical_bytes >= 0),
    CHECK (disposition IN ('PRIMARY', 'REPLICA', 'TEMPORARY')),
    CHECK (
        business_category IS NULL
        OR business_category IN (
            'RAW',
            'ANNOTATION_COMPLETE',
            'PENDING_ANNOTATION',
            'ISSUE_DATA'
        )
    ),
    CHECK (
        object_role IN (
            'RAW',
            'MANIFEST',
            'PUBLISHED_MANIFEST',
            'REBUILDABLE_DERIVATIVE',
            'OTHER'
        )
    ),
    CHECK (
        (disposition = 'TEMPORARY' AND business_category IS NULL)
        OR (
            disposition IN ('PRIMARY', 'REPLICA')
            AND logical_object_id IS NOT NULL
            AND business_category IS NOT NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS storage_inventory_facts_business_idx
ON storage.inventory_facts (
    project_id,
    snapshot_id,
    business_category,
    logical_object_id,
    physical_instance_id
);

CREATE TABLE IF NOT EXISTS storage.lifecycle_policies (
    project_id text NOT NULL,
    policy_id text NOT NULL,
    name text NOT NULL,
    business_category text NOT NULL,
    object_role text NOT NULL,
    action text NOT NULL,
    minimum_age_days integer NOT NULL,
    priority integer NOT NULL,
    state text NOT NULL DEFAULT 'DRAFT',
    version bigint NOT NULL DEFAULT 1,
    etag text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, policy_id),
    CHECK (project_id <> ''),
    CHECK (policy_id <> ''),
    CHECK (name <> ''),
    CHECK (
        business_category IN (
            'RAW',
            'ANNOTATION_COMPLETE',
            'PENDING_ANNOTATION',
            'ISSUE_DATA'
        )
    ),
    CHECK (
        object_role IN (
            'RAW',
            'MANIFEST',
            'PUBLISHED_MANIFEST',
            'REBUILDABLE_DERIVATIVE',
            'OTHER'
        )
    ),
    CHECK (action IN ('RETAIN', 'REVIEW_EXPIRATION', 'CLEAN_REBUILDABLE_CACHE')),
    CHECK (
        action <> 'CLEAN_REBUILDABLE_CACHE'
        OR object_role = 'REBUILDABLE_DERIVATIVE'
    ),
    CHECK (minimum_age_days BETWEEN 0 AND 36500),
    CHECK (priority BETWEEN 0 AND 10000),
    CHECK (state IN ('DRAFT', 'ENABLED', 'PAUSED')),
    CHECK (version > 0),
    CHECK (etag <> '')
);

CREATE UNIQUE INDEX IF NOT EXISTS storage_lifecycle_policy_name_unique_idx
ON storage.lifecycle_policies (project_id, lower(name));

CREATE UNIQUE INDEX IF NOT EXISTS storage_lifecycle_enabled_target_unique_idx
ON storage.lifecycle_policies (
    project_id,
    business_category,
    object_role,
    priority
)
WHERE state = 'ENABLED';

CREATE TABLE IF NOT EXISTS storage.lifecycle_audit_events (
    project_id text NOT NULL,
    audit_id text NOT NULL,
    policy_id text NOT NULL,
    actor_id text NOT NULL,
    action text NOT NULL,
    before_digest char(64),
    after_digest char(64),
    request_id text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, audit_id),
    CHECK (project_id <> ''),
    CHECK (audit_id <> ''),
    CHECK (policy_id <> ''),
    CHECK (actor_id <> ''),
    CHECK (action <> ''),
    CHECK (before_digest IS NULL OR before_digest ~ '^[0-9a-f]{64}$'),
    CHECK (after_digest IS NULL OR after_digest ~ '^[0-9a-f]{64}$'),
    CHECK (request_id <> ''),
    CHECK (jsonb_typeof(details) = 'object')
);

CREATE INDEX IF NOT EXISTS storage_lifecycle_audit_page_idx
ON storage.lifecycle_audit_events (project_id, audit_id);

CREATE OR REPLACE FUNCTION storage.reject_audit_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'storage lifecycle audit events are append-only';
END
$function$;

DROP TRIGGER IF EXISTS storage_lifecycle_audit_immutable
ON storage.lifecycle_audit_events;
CREATE TRIGGER storage_lifecycle_audit_immutable
BEFORE UPDATE OR DELETE ON storage.lifecycle_audit_events
FOR EACH ROW EXECUTE FUNCTION storage.reject_audit_mutation();

CREATE TABLE IF NOT EXISTS storage.lifecycle_executions (
    project_id text NOT NULL,
    execution_id text NOT NULL,
    policy_id text NOT NULL,
    policy_version bigint NOT NULL,
    action text NOT NULL,
    production boolean NOT NULL DEFAULT true,
    production_execution_approved boolean NOT NULL DEFAULT false,
    request_fingerprint char(64) NOT NULL,
    status text NOT NULL DEFAULT 'PENDING',
    next_batch integer NOT NULL DEFAULT 0,
    last_error text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, execution_id),
    FOREIGN KEY (project_id, policy_id)
        REFERENCES storage.lifecycle_policies (project_id, policy_id)
        ON DELETE RESTRICT,
    CHECK (project_id <> ''),
    CHECK (execution_id <> ''),
    CHECK (policy_id <> ''),
    CHECK (policy_version > 0),
    CHECK (action = 'CLEAN_REBUILDABLE_CACHE'),
    -- OPEN-10 is unconfirmed: production execution is impossible at the database layer.
    CHECK (production = false),
    CHECK (production_execution_approved = false),
    CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    CHECK (status IN ('PENDING', 'RUNNING', 'BLOCKED', 'COMPLETED', 'FAILED')),
    CHECK (next_batch >= 0)
);

CREATE TABLE IF NOT EXISTS storage.lifecycle_execution_items (
    project_id text NOT NULL,
    execution_id text NOT NULL,
    physical_instance_id text NOT NULL,
    logical_object_id text NOT NULL,
    object_role text NOT NULL,
    rebuild_source_id text,
    active_reference_count bigint NOT NULL DEFAULT 0,
    protection_verified boolean NOT NULL DEFAULT false,
    status text NOT NULL DEFAULT 'PENDING',
    attempt integer NOT NULL DEFAULT 0,
    last_error text,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, execution_id, physical_instance_id),
    FOREIGN KEY (project_id, execution_id)
        REFERENCES storage.lifecycle_executions (project_id, execution_id)
        ON DELETE RESTRICT,
    CHECK (project_id <> ''),
    CHECK (execution_id <> ''),
    CHECK (physical_instance_id <> ''),
    CHECK (logical_object_id <> ''),
    CHECK (
        object_role IN (
            'RAW',
            'MANIFEST',
            'PUBLISHED_MANIFEST',
            'REBUILDABLE_DERIVATIVE',
            'OTHER'
        )
    ),
    CHECK (rebuild_source_id IS NULL OR rebuild_source_id <> ''),
    CHECK (active_reference_count >= 0),
    CHECK (status IN ('PENDING', 'PROCESSED', 'BLOCKED', 'FAILED')),
    CHECK (attempt >= 0),
    CHECK (
        status <> 'PROCESSED'
        OR (
            object_role = 'REBUILDABLE_DERIVATIVE'
            AND rebuild_source_id IS NOT NULL
            AND active_reference_count = 0
            AND protection_verified
        )
    )
);

SELECT core.apply_project_rls('storage.inventory_snapshots'::regclass);
SELECT core.apply_project_rls('storage.inventory_facts'::regclass);
SELECT core.apply_project_rls('storage.lifecycle_policies'::regclass);
SELECT core.apply_project_rls('storage.lifecycle_audit_events'::regclass);
SELECT core.apply_project_rls('storage.lifecycle_executions'::regclass);
SELECT core.apply_project_rls('storage.lifecycle_execution_items'::regclass);
