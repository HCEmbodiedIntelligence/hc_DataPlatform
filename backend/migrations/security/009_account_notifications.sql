-- Account-scoped inbox facts.  Content is intentionally derived from a small
-- event vocabulary in the UI; decision reasons and capability details stay in
-- the authorized request/audit domains and are never copied into this inbox.

CREATE TABLE IF NOT EXISTS access_control.account_notifications (
    notification_id uuid PRIMARY KEY,
    recipient_id uuid NOT NULL REFERENCES access_control.accounts(principal_id),
    kind text NOT NULL,
    project_id text NOT NULL,
    access_request_id uuid NOT NULL,
    state text NOT NULL DEFAULT 'UNREAD',
    created_at timestamptz NOT NULL DEFAULT now(),
    read_at timestamptz,
    UNIQUE (recipient_id, kind, access_request_id),
    CHECK (kind IN (
        'MEMBERSHIP_APPROVED', 'MEMBERSHIP_REJECTED', 'MEMBERSHIP_REVOKED',
        'CAPABILITY_APPROVED', 'CAPABILITY_REJECTED', 'CAPABILITY_REVOKED'
    )),
    CHECK (project_id <> ''),
    CHECK (state IN ('UNREAD', 'READ')),
    CHECK (
        (state = 'UNREAD' AND read_at IS NULL)
        OR (state = 'READ' AND read_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS account_notifications_recipient_keyset_idx
ON access_control.account_notifications (recipient_id, created_at DESC, notification_id DESC);

CREATE INDEX IF NOT EXISTS account_notifications_unread_idx
ON access_control.account_notifications (recipient_id, created_at DESC)
WHERE state = 'UNREAD';

ALTER TABLE access_control.account_notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE access_control.account_notifications FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS account_notification_recipient_scope
ON access_control.account_notifications;
CREATE POLICY account_notification_recipient_scope
ON access_control.account_notifications
USING (
    recipient_id::text = NULLIF(current_setting('app.subject_id', true), '')
)
WITH CHECK (
    recipient_id::text = NULLIF(current_setting('app.subject_id', true), '')
);
