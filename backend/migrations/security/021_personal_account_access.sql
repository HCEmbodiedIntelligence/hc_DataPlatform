-- Personal accounts remain valid without tenant scope. Organization membership is a
-- separate reviewed relationship and never implies project membership or capabilities.

CREATE TABLE IF NOT EXISTS access_control.organization_join_codes (
    organization_id text PRIMARY KEY,
    display_name text NOT NULL,
    join_code text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (organization_id <> ''),
    CHECK (display_name <> ''),
    CHECK (join_code <> '')
);

INSERT INTO access_control.organization_join_codes (organization_id, display_name, join_code)
SELECT DISTINCT organization_id, organization_id, organization_id
FROM registry.organization_projects
ON CONFLICT (organization_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS access_control.organization_membership_requests (
    request_id uuid PRIMARY KEY,
    organization_id text NOT NULL,
    requester_id uuid NOT NULL REFERENCES access_control.accounts (principal_id),
    status text NOT NULL,
    reason text NOT NULL,
    decided_by text,
    decision_reason text,
    revision bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (organization_id <> ''),
    CHECK (reason <> ''),
    CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'WITHDRAWN', 'REVOKED')),
    CHECK (revision >= 1)
);

CREATE UNIQUE INDEX IF NOT EXISTS organization_membership_requests_one_pending_idx
ON access_control.organization_membership_requests (requester_id, organization_id)
WHERE status = 'PENDING';

CREATE INDEX IF NOT EXISTS organization_membership_requests_queue_idx
ON access_control.organization_membership_requests (
    organization_id, status, created_at DESC, request_id
);

CREATE TABLE IF NOT EXISTS access_control.organization_memberships (
    principal_id uuid NOT NULL REFERENCES access_control.accounts (principal_id),
    organization_id text NOT NULL,
    source_request_id uuid UNIQUE
        REFERENCES access_control.organization_membership_requests (request_id),
    active boolean NOT NULL DEFAULT true,
    activated_at timestamptz NOT NULL DEFAULT now(),
    activated_by text NOT NULL,
    revoked_at timestamptz,
    revoked_by text,
    PRIMARY KEY (principal_id, organization_id),
    CHECK (organization_id <> ''),
    CHECK (activated_by <> '')
);

CREATE INDEX IF NOT EXISTS organization_memberships_active_principal_idx
ON access_control.organization_memberships (principal_id, organization_id)
WHERE active;

-- Preserve the organization relationship implied by legacy active project memberships.
INSERT INTO access_control.organization_memberships (
    principal_id, organization_id, source_request_id, active, activated_at, activated_by
)
SELECT membership.principal_id,
       membership.organization_id,
       NULL,
       true,
       min(membership.activated_at),
       'security/021 legacy project membership backfill'
FROM access_control.memberships membership
WHERE membership.active
GROUP BY membership.principal_id, membership.organization_id
ON CONFLICT (principal_id, organization_id) DO UPDATE SET
    active = true,
    revoked_at = NULL,
    revoked_by = NULL;

COMMENT ON TABLE access_control.organization_memberships IS
    'Reviewed organization relationships; they grant no project data or business capability.';

COMMENT ON TABLE access_control.organization_membership_requests IS
    'Account-owned requests to join an organization, independent of authentication.';
