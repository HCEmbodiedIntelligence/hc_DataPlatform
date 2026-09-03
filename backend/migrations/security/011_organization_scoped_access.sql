-- Bind reviewed platform access to the same organization/project identity used by
-- durable product records.  A project ID is not globally unique: guessing an
-- organization during upgrade would silently grant a cross-organization scope.

DO $preflight$
DECLARE
    invalid_projects text;
    invalid_notifications text;
BEGIN
    WITH referenced_projects AS (
        SELECT project_id FROM access_control.membership_requests
        UNION
        SELECT project_id FROM access_control.memberships
        UNION
        SELECT project_id FROM access_control.capability_requests
        UNION
        SELECT project_id FROM access_control.capability_grants
        UNION
        SELECT project_id
        FROM access_control.account_notifications
        UNION
        SELECT project_id
        FROM access_control.audit_events
        WHERE scope_kind = 'PROJECT'
    ), ambiguous AS (
        SELECT referenced.project_id
        FROM referenced_projects referenced
        LEFT JOIN registry.organization_projects project
          ON project.project_id = referenced.project_id
        GROUP BY referenced.project_id
        HAVING count(project.organization_id) <> 1
    )
    SELECT string_agg(project_id, ', ' ORDER BY project_id)
      INTO invalid_projects
      FROM ambiguous;

    IF invalid_projects IS NOT NULL THEN
        RAISE EXCEPTION
            'organization-scoped access upgrade requires exactly one registry organization for every existing project: %',
            invalid_projects;
    END IF;

    WITH request_projects AS (
        SELECT request_id, project_id
        FROM access_control.membership_requests
        UNION ALL
        SELECT request_id, project_id
        FROM access_control.capability_requests
    ), request_organizations AS (
        SELECT request.request_id, project.organization_id
        FROM request_projects request
        JOIN registry.organization_projects project
          ON project.project_id = request.project_id
    ), invalid AS (
        SELECT notification.notification_id::text
        FROM access_control.account_notifications notification
        LEFT JOIN request_organizations request
          ON request.request_id = notification.access_request_id
        GROUP BY notification.notification_id
        HAVING count(request.organization_id) <> 1
    )
    SELECT string_agg(notification_id, ', ' ORDER BY notification_id)
      INTO invalid_notifications
      FROM invalid;

    IF invalid_notifications IS NOT NULL THEN
        RAISE EXCEPTION
            'organization-scoped access upgrade cannot resolve notification request identity: %',
            invalid_notifications;
    END IF;
END
$preflight$;

ALTER TABLE access_control.membership_requests
    ADD COLUMN IF NOT EXISTS organization_id text;
ALTER TABLE access_control.memberships
    ADD COLUMN IF NOT EXISTS organization_id text;
ALTER TABLE access_control.capability_requests
    ADD COLUMN IF NOT EXISTS organization_id text;
ALTER TABLE access_control.capability_grants
    ADD COLUMN IF NOT EXISTS organization_id text;
ALTER TABLE access_control.account_notifications
    ADD COLUMN IF NOT EXISTS organization_id text;
ALTER TABLE access_control.audit_events
    ADD COLUMN IF NOT EXISTS organization_id text;

UPDATE access_control.membership_requests request
   SET organization_id = project.organization_id
  FROM registry.organization_projects project
 WHERE project.project_id = request.project_id
   AND request.organization_id IS NULL;

UPDATE access_control.memberships membership
   SET organization_id = project.organization_id
  FROM registry.organization_projects project
 WHERE project.project_id = membership.project_id
   AND membership.organization_id IS NULL;

UPDATE access_control.capability_requests request
   SET organization_id = project.organization_id
  FROM registry.organization_projects project
 WHERE project.project_id = request.project_id
   AND request.organization_id IS NULL;

UPDATE access_control.capability_grants capability_grant
   SET organization_id = project.organization_id
  FROM registry.organization_projects project
 WHERE project.project_id = capability_grant.project_id
   AND capability_grant.organization_id IS NULL;

WITH request_organizations AS (
    SELECT request_id, organization_id
    FROM access_control.membership_requests
    UNION ALL
    SELECT request_id, organization_id
    FROM access_control.capability_requests
)
UPDATE access_control.account_notifications notification
   SET organization_id = request.organization_id
  FROM request_organizations request
 WHERE request.request_id = notification.access_request_id
   AND notification.organization_id IS NULL;

-- Historical access audit rows predate organization identity.  The table's
-- application invariant is append-only, so keep this one-time backfill inside
-- the migration transaction and restore the exact mutation guard immediately.
-- If any later statement fails, PostgreSQL rolls both the data change and the
-- trigger state back together.
ALTER TABLE access_control.audit_events
    DISABLE TRIGGER access_audit_no_update_delete;

UPDATE access_control.audit_events event
   SET organization_id = project.organization_id
  FROM registry.organization_projects project
 WHERE event.scope_kind = 'PROJECT'
   AND project.project_id = event.project_id
   AND event.organization_id IS NULL;

ALTER TABLE access_control.audit_events
    ENABLE TRIGGER access_audit_no_update_delete;

ALTER TABLE access_control.membership_requests
    ALTER COLUMN organization_id SET NOT NULL,
    ADD CONSTRAINT membership_requests_organization_project_fk
        FOREIGN KEY (organization_id, project_id)
        REFERENCES registry.organization_projects (organization_id, project_id),
    ADD CONSTRAINT membership_requests_organization_nonempty
        CHECK (organization_id <> '');
ALTER TABLE access_control.memberships
    ALTER COLUMN organization_id SET NOT NULL,
    ADD CONSTRAINT memberships_organization_project_fk
        FOREIGN KEY (organization_id, project_id)
        REFERENCES registry.organization_projects (organization_id, project_id),
    ADD CONSTRAINT memberships_organization_nonempty
        CHECK (organization_id <> '');
ALTER TABLE access_control.capability_requests
    ALTER COLUMN organization_id SET NOT NULL,
    ADD CONSTRAINT capability_requests_organization_project_fk
        FOREIGN KEY (organization_id, project_id)
        REFERENCES registry.organization_projects (organization_id, project_id),
    ADD CONSTRAINT capability_requests_organization_nonempty
        CHECK (organization_id <> '');
ALTER TABLE access_control.capability_grants
    ALTER COLUMN organization_id SET NOT NULL,
    ADD CONSTRAINT capability_grants_organization_project_fk
        FOREIGN KEY (organization_id, project_id)
        REFERENCES registry.organization_projects (organization_id, project_id),
    ADD CONSTRAINT capability_grants_organization_nonempty
        CHECK (organization_id <> '');
ALTER TABLE access_control.account_notifications
    ALTER COLUMN organization_id SET NOT NULL,
    ADD CONSTRAINT account_notifications_organization_nonempty
        CHECK (organization_id <> '');
ALTER TABLE access_control.audit_events
    ADD CONSTRAINT access_audit_events_project_organization_required
        CHECK (scope_kind <> 'PROJECT' OR organization_id IS NOT NULL),
    ADD CONSTRAINT access_audit_events_organization_nonempty
        CHECK (organization_id IS NULL OR organization_id <> '');

ALTER TABLE access_control.memberships
    DROP CONSTRAINT memberships_pkey,
    ADD PRIMARY KEY (principal_id, organization_id, project_id);

DROP INDEX IF EXISTS access_control.membership_requests_one_pending_idx;
CREATE UNIQUE INDEX membership_requests_one_pending_idx
ON access_control.membership_requests (requester_id, organization_id, project_id)
WHERE status = 'PENDING';

DROP INDEX IF EXISTS access_control.membership_requests_project_queue_idx;
CREATE INDEX membership_requests_organization_project_queue_idx
ON access_control.membership_requests (
    organization_id, project_id, status, created_at DESC, request_id
);

DROP INDEX IF EXISTS access_control.memberships_active_principal_idx;
CREATE INDEX memberships_active_principal_organization_project_idx
ON access_control.memberships (principal_id, organization_id, project_id)
WHERE active;

DROP INDEX IF EXISTS access_control.capability_requests_project_queue_idx;
CREATE INDEX capability_requests_organization_project_queue_idx
ON access_control.capability_requests (
    organization_id, project_id, status, created_at DESC, request_id
);

DROP INDEX IF EXISTS access_control.capability_grants_effective_idx;
CREATE INDEX capability_grants_effective_organization_project_idx
ON access_control.capability_grants (
    principal_id, organization_id, project_id, capability_key
)
WHERE active;

CREATE INDEX access_audit_events_organization_project_time_idx
ON access_control.audit_events (organization_id, project_id, occurred_at DESC, event_id DESC)
WHERE scope_kind = 'PROJECT';
