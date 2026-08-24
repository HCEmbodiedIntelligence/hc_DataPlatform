-- Generalize the recipient-only inbox from access-request decisions to a
-- small, allowlisted business-event vocabulary. The event key is an opaque,
-- deterministic producer identity: it is never rendered to recipients and it
-- makes a command retry incapable of creating a second notification.

ALTER TABLE access_control.account_notifications
    ADD COLUMN IF NOT EXISTS resource_type text,
    ADD COLUMN IF NOT EXISTS resource_id text,
    ADD COLUMN IF NOT EXISTS event_key text;

UPDATE access_control.account_notifications
   SET resource_type = 'ACCESS_REQUEST',
       resource_id = access_request_id::text,
       event_key = 'access-request:' || kind || ':' || access_request_id::text
 WHERE resource_type IS NULL
    OR resource_id IS NULL
    OR event_key IS NULL;

ALTER TABLE access_control.account_notifications
    ALTER COLUMN resource_type SET NOT NULL,
    ALTER COLUMN resource_id SET NOT NULL,
    ALTER COLUMN event_key SET NOT NULL,
    ALTER COLUMN access_request_id DROP NOT NULL;

ALTER TABLE access_control.account_notifications
    DROP CONSTRAINT IF EXISTS account_notifications_recipient_id_kind_access_request_id_key,
    DROP CONSTRAINT IF EXISTS account_notifications_kind_check;

ALTER TABLE access_control.account_notifications
    ADD CONSTRAINT account_notifications_kind_check
        CHECK (kind IN (
            'MEMBERSHIP_APPROVED', 'MEMBERSHIP_REJECTED', 'MEMBERSHIP_REVOKED',
            'CAPABILITY_APPROVED', 'CAPABILITY_REJECTED', 'CAPABILITY_REVOKED',
            'COLLECTION_TASK_CLOSED', 'COLLECTION_TASK_CANCELLED',
            'COLLECTION_TASK_REOPENED', 'DATASET_VERSION_PUBLISHED'
        )),
    ADD CONSTRAINT account_notifications_resource_type_check
        CHECK (resource_type IN ('ACCESS_REQUEST', 'COLLECTION_TASK', 'DATASET_VERSION')),
    ADD CONSTRAINT account_notifications_resource_id_nonempty
        CHECK (resource_id <> ''),
    ADD CONSTRAINT account_notifications_event_key_nonempty
        CHECK (event_key <> '' AND length(event_key) <= 512),
    ADD CONSTRAINT account_notifications_access_request_resource_check
        CHECK (
            (resource_type = 'ACCESS_REQUEST' AND access_request_id IS NOT NULL)
            OR (resource_type <> 'ACCESS_REQUEST' AND access_request_id IS NULL)
        ),
    ADD CONSTRAINT account_notifications_recipient_event_key_key
        UNIQUE (recipient_id, event_key);

CREATE INDEX IF NOT EXISTS account_notifications_recipient_resource_idx
ON access_control.account_notifications (recipient_id, resource_type, resource_id, created_at DESC);
