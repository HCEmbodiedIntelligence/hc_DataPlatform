-- P19 reads the durable cross-domain project audit stream with stable keyset
-- pagination.  This is an additive index only: existing producer payloads are
-- intentionally not rewritten, and no generic JSON field becomes queryable.

CREATE INDEX IF NOT EXISTS audit_events_p19_scope_keyset_idx
ON core.audit_events (
    project_id,
    region_code,
    occurred_at DESC,
    audit_id DESC
)
INCLUDE (
    actor_id,
    action,
    resource_type,
    resource_id,
    request_id,
    before_hash,
    after_hash
);
