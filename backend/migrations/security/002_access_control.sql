-- Direct accounts and reviewed project access.  Registration creates only an ACTIVE account;
-- it never creates a project, membership, capability grant, or account approval request.

CREATE SCHEMA IF NOT EXISTS access_control;

CREATE TABLE IF NOT EXISTS access_control.accounts (
    principal_id uuid PRIMARY KEY,
    canonical_username text NOT NULL UNIQUE,
    display_username text NOT NULL,
    status text NOT NULL DEFAULT 'ACTIVE',
    password_hash text NOT NULL,
    capability_revision bigint NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    disabled_at timestamptz,
    CHECK (canonical_username <> ''),
    CHECK (display_username <> ''),
    CHECK (status IN ('ACTIVE', 'DISABLED')),
    CHECK (capability_revision >= 0)
);

CREATE TABLE IF NOT EXISTS access_control.sessions (
    session_id uuid PRIMARY KEY,
    principal_id uuid NOT NULL REFERENCES access_control.accounts (principal_id),
    token_hash char(64) NOT NULL UNIQUE,
    issued_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz,
    CHECK (token_hash ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS access_sessions_principal_active_idx
ON access_control.sessions (principal_id, issued_at DESC)
WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS access_control.membership_requests (
    request_id uuid PRIMARY KEY,
    project_id text NOT NULL,
    requester_id uuid NOT NULL REFERENCES access_control.accounts (principal_id),
    status text NOT NULL,
    reason text,
    decided_by text,
    decision_reason text,
    revision bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (project_id <> ''),
    CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'WITHDRAWN', 'REVOKED')),
    CHECK (revision >= 1)
);

CREATE UNIQUE INDEX IF NOT EXISTS membership_requests_one_pending_idx
ON access_control.membership_requests (requester_id, project_id)
WHERE status = 'PENDING';

CREATE INDEX IF NOT EXISTS membership_requests_project_queue_idx
ON access_control.membership_requests (project_id, status, created_at DESC, request_id);

CREATE TABLE IF NOT EXISTS access_control.memberships (
    principal_id uuid NOT NULL REFERENCES access_control.accounts (principal_id),
    project_id text NOT NULL,
    source_request_id uuid NOT NULL UNIQUE
        REFERENCES access_control.membership_requests (request_id),
    active boolean NOT NULL DEFAULT true,
    activated_at timestamptz NOT NULL DEFAULT now(),
    activated_by text NOT NULL,
    revoked_at timestamptz,
    revoked_by text,
    PRIMARY KEY (principal_id, project_id),
    CHECK (project_id <> ''),
    CHECK (activated_by <> '')
);

CREATE INDEX IF NOT EXISTS memberships_active_principal_idx
ON access_control.memberships (principal_id, project_id)
WHERE active;

CREATE TABLE IF NOT EXISTS access_control.capability_requests (
    request_id uuid PRIMARY KEY,
    project_id text NOT NULL,
    requester_id uuid NOT NULL REFERENCES access_control.accounts (principal_id),
    capability_keys text[] NOT NULL,
    status text NOT NULL,
    reason text,
    decided_by text,
    decision_reason text,
    revision bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (project_id <> ''),
    CHECK (cardinality(capability_keys) > 0),
    CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'WITHDRAWN', 'REVOKED')),
    CHECK (revision >= 1)
);

CREATE INDEX IF NOT EXISTS capability_requests_project_queue_idx
ON access_control.capability_requests (project_id, status, created_at DESC, request_id);

CREATE TABLE IF NOT EXISTS access_control.capability_grants (
    grant_id uuid PRIMARY KEY,
    source_request_id uuid NOT NULL REFERENCES access_control.capability_requests (request_id),
    principal_id uuid NOT NULL REFERENCES access_control.accounts (principal_id),
    project_id text NOT NULL,
    capability_key text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    activated_at timestamptz NOT NULL DEFAULT now(),
    activated_by text NOT NULL,
    revoked_at timestamptz,
    revoked_by text,
    UNIQUE (source_request_id, capability_key),
    CHECK (project_id <> ''),
    CHECK (capability_key <> ''),
    CHECK (activated_by <> '')
);

CREATE INDEX IF NOT EXISTS capability_grants_effective_idx
ON access_control.capability_grants (principal_id, project_id, capability_key)
WHERE active;

CREATE TABLE IF NOT EXISTS access_control.command_idempotency (
    actor_id text NOT NULL,
    operation text NOT NULL,
    idempotency_key text NOT NULL,
    request_fingerprint char(64) NOT NULL,
    resource_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_id, operation, idempotency_key),
    CHECK (actor_id <> ''),
    CHECK (operation <> ''),
    CHECK (idempotency_key <> ''),
    CHECK (request_fingerprint ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS access_control.audit_events (
    event_id uuid PRIMARY KEY,
    scope_kind text NOT NULL,
    project_id text,
    actor_id text,
    action text NOT NULL,
    resource_type text NOT NULL,
    resource_id text NOT NULL,
    request_id text NOT NULL,
    outcome text NOT NULL,
    safe_details jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    CHECK (scope_kind IN ('PLATFORM', 'PROJECT')),
    CHECK ((scope_kind = 'PLATFORM' AND project_id IS NULL)
        OR (scope_kind = 'PROJECT' AND project_id IS NOT NULL AND project_id <> '')),
    CHECK (action <> ''),
    CHECK (resource_type <> ''),
    CHECK (resource_id <> ''),
    CHECK (request_id <> ''),
    CHECK (outcome IN ('SUCCEEDED', 'DENIED', 'FAILED'))
);

CREATE INDEX IF NOT EXISTS access_audit_project_occurred_idx
ON access_control.audit_events (project_id, occurred_at DESC, event_id)
WHERE scope_kind = 'PROJECT';

-- Audit records are append-only for the application role. Corrections are new events.
CREATE OR REPLACE FUNCTION access_control.reject_audit_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'access audit events are append-only';
END
$function$;

DROP TRIGGER IF EXISTS access_audit_no_update_delete ON access_control.audit_events;
CREATE TRIGGER access_audit_no_update_delete
BEFORE UPDATE OR DELETE ON access_control.audit_events
FOR EACH ROW EXECUTE FUNCTION access_control.reject_audit_mutation();

-- core.reconcile_project_rls() deliberately scans all project_id columns. Access discovery
-- is the exception: it must inspect a principal's memberships across projects before a
-- project is selected, and its repository methods enforce exact project visibility in SQL.
-- This migration is therefore rerun after the core reconciliation pass.
DO $block$
DECLARE
    access_table regclass;
BEGIN
    FOREACH access_table IN ARRAY ARRAY[
        'access_control.membership_requests'::regclass,
        'access_control.memberships'::regclass,
        'access_control.capability_requests'::regclass,
        'access_control.capability_grants'::regclass,
        'access_control.audit_events'::regclass
    ]
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS hc_scope_isolation ON %s', access_table);
        EXECUTE format('ALTER TABLE %s NO FORCE ROW LEVEL SECURITY', access_table);
        EXECUTE format('ALTER TABLE %s DISABLE ROW LEVEL SECURITY', access_table);
    END LOOP;
END
$block$;
