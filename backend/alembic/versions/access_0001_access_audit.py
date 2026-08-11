"""Access policy and append-only audit facts.

Revision ID: access_0001
Revises: None
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "access_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = ("access",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "access_capability_catalogs",
        sa.Column("catalog_version", sa.String(128), primary_key=True),
        sa.Column("contract_status", sa.String(32), nullable=False),
        sa.Column("capability_keys", sa.JSON(), nullable=False),
        sa.Column("reserved_denylist", sa.JSON(), nullable=False),
        sa.Column("catalog_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "access_role_versions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("role_id", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("role_version", sa.String(128), nullable=False),
        sa.Column("catalog_version", sa.String(128), nullable=False),
        sa.Column("base_capability_keys", sa.JSON(), nullable=False),
        sa.Column("capability_ceiling_keys", sa.JSON(), nullable=False),
        sa.Column("set_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("role_id", "role_version", name="uq_access_role_version"),
    )
    op.create_index(
        "ix_access_role_catalog_status", "access_role_versions", ["catalog_version", "status"]
    )
    op.create_table(
        "access_members",
        sa.Column("member_id", sa.String(256), primary_key=True),
        sa.Column("organization_id", sa.String(256), nullable=False),
        sa.Column("project_id", sa.String(256), nullable=False),
        sa.Column("principal_id", sa.String(256), nullable=False),
        sa.Column("principal_snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("role_id", sa.String(64), nullable=False),
        sa.Column("role_version", sa.String(128), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_active_at", sa.DateTime(timezone=True)),
        sa.Column("resource_version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("etag", sa.String(256), nullable=False),
    )
    op.create_index(
        "ix_access_members_project_status_role",
        "access_members",
        ["project_id", "status", "role_id"],
    )
    op.create_index(
        "uq_access_members_project_principal_current",
        "access_members",
        ["project_id", "principal_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'REMOVED'"),
    )
    op.create_table(
        "access_role_assignments",
        sa.Column("role_assignment_id", sa.String(256), primary_key=True),
        sa.Column("member_id", sa.String(256), nullable=False),
        sa.Column("subject", sa.JSON(), nullable=False),
        sa.Column("role_id", sa.String(64), nullable=False),
        sa.Column("role_version", sa.String(128), nullable=False),
        sa.Column("scope_key", sa.String(1024), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("inherit", sa.Boolean(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("etag", sa.String(256), nullable=False),
    )
    op.create_index(
        "ix_access_role_assignment_subject_scope_status",
        "access_role_assignments",
        ["member_id", "scope_key", "status"],
    )
    op.create_index(
        "uq_access_role_assignment_member_active",
        "access_role_assignments",
        ["member_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_table(
        "access_scope_grants",
        sa.Column("scope_grant_id", sa.String(256), primary_key=True),
        sa.Column("subject_key", sa.String(1024), nullable=False),
        sa.Column("subject", sa.JSON(), nullable=False),
        sa.Column("scope_key", sa.String(1024), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("effect", sa.String(16), nullable=False),
        sa.Column("capability_keys", sa.JSON(), nullable=False),
        sa.Column("capability_digest", sa.String(64), nullable=False),
        sa.Column("ceiling_version", sa.String(128), nullable=False),
        sa.Column("inherit", sa.Boolean(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("etag", sa.String(256), nullable=False),
    )
    op.create_index(
        "ix_access_scope_grant_subject_scope_effect_status_ceiling",
        "access_scope_grants",
        ["subject_key", "scope_key", "effect", "status", "ceiling_version"],
    )
    op.create_index(
        "uq_access_scope_grant_active_identity",
        "access_scope_grants",
        ["subject_key", "scope_key", "effect", "capability_digest"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_table(
        "access_authorization_snapshots",
        sa.Column("snapshot_id", sa.String(256), primary_key=True),
        sa.Column("principal_id", sa.String(256), nullable=False),
        sa.Column("scope_key", sa.String(1024), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("capability_keys", sa.JSON(), nullable=False),
        sa.Column("denied_capability_keys", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("catalog_version", sa.String(128), nullable=False),
        sa.Column("role_version", sa.String(128), nullable=False),
        sa.Column("policy_version", sa.String(128), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_state", sa.String(32), nullable=False),
    )
    op.create_index(
        "ix_access_authorization_policy_role_scope",
        "access_authorization_snapshots",
        ["policy_version", "role_version", "scope_key"],
    )
    op.create_table(
        "access_invitations",
        sa.Column("invitation_id", sa.String(256), primary_key=True),
        sa.Column("organization_id", sa.String(256), nullable=False),
        sa.Column("project_id", sa.String(256), nullable=False),
        sa.Column("target_hash", sa.String(64), nullable=False),
        sa.Column("target_display", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("intended_role_id", sa.String(64), nullable=False),
        sa.Column("intended_role_version", sa.String(128), nullable=False),
        sa.Column("intended_scope", sa.JSON(), nullable=False),
        sa.Column("policy_revision", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_by_invitation_id", sa.String(256)),
        sa.Column("etag", sa.String(256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_access_invitation_project_status_created",
        "access_invitations",
        ["project_id", "status", "created_at"],
    )
    op.create_table(
        "access_preflights",
        sa.Column("preflight_id", sa.String(256), primary_key=True),
        sa.Column("project_id", sa.String(256), nullable=False),
        sa.Column("actor_principal_id", sa.String(256), nullable=False),
        sa.Column("operation_hash", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("policy_revision", sa.String(128), nullable=False),
        sa.Column("policy_etag", sa.String(256), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_access_preflight_project_expiry", "access_preflights", ["project_id", "expires_at"]
    )
    op.create_table(
        "audit_outbox",
        sa.Column("outbox_id", sa.String(256), primary_key=True),
        sa.Column("aggregate_type", sa.String(128), nullable=False),
        sa.Column("aggregate_id", sa.String(256), nullable=False),
        sa.Column("producer_service", sa.String(128), nullable=False),
        sa.Column("producer_event_id", sa.String(256), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("relay_status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(128)),
        sa.UniqueConstraint("outbox_id", name="uq_audit_outbox_id"),
        sa.UniqueConstraint(
            "producer_service", "producer_event_id", name="uq_audit_outbox_producer_event"
        ),
    )
    op.create_index("ix_audit_outbox_relay", "audit_outbox", ["relay_status", "next_attempt_at"])
    op.create_table(
        "audit_events",
        sa.Column("event_id", sa.String(256), primary_key=True),
        sa.Column("scope_key", sa.String(1024), nullable=False),
        sa.Column("organization_id", sa.String(256), nullable=False),
        sa.Column("project_id", sa.String(256), nullable=False),
        sa.Column("region_code", sa.String(64)),
        sa.Column("event_name", sa.String(256), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingest_sequence", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("actor_principal_id", sa.String(256), nullable=False),
        sa.Column("resource_type", sa.String(128), nullable=False),
        sa.Column("resource_id", sa.String(256), nullable=False),
        sa.Column("request_id", sa.String(256), nullable=False),
        sa.Column("producer_service", sa.String(128), nullable=False),
        sa.Column("producer_event_id", sa.String(256), nullable=False),
        sa.Column("input_payload", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        sa.Column("catalog_version", sa.String(128), nullable=False),
        sa.Column("outcome_status", sa.String(32), nullable=False),
        sa.Column("risk_level", sa.String(32), nullable=False),
        sa.Column("risk_payload", sa.JSON(), nullable=False),
        sa.Column("retention_class", sa.String(32), nullable=False),
        sa.Column("retention_policy_version", sa.String(128), nullable=False),
        sa.Column("retain_until", sa.DateTime(timezone=True)),
        sa.Column("legal_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("integrity_version", sa.String(128), nullable=False),
        sa.Column("record_digest", sa.String(512), nullable=False),
        sa.Column("checkpoint_id", sa.String(256)),
        sa.UniqueConstraint("event_id", name="uq_audit_event_id"),
        sa.UniqueConstraint(
            "producer_service", "producer_event_id", name="uq_audit_event_producer_event"
        ),
    )
    op.create_index(
        "ix_audit_event_scope_occurred_event",
        "audit_events",
        ["scope_key", sa.text("occurred_at DESC"), sa.text("event_id DESC")],
    )
    op.create_index(
        "ix_audit_event_scope_name_occurred",
        "audit_events",
        ["scope_key", "event_name", sa.text("occurred_at DESC")],
    )
    op.create_index(
        "ix_audit_event_scope_actor_occurred",
        "audit_events",
        ["scope_key", "actor_principal_id", sa.text("occurred_at DESC")],
    )
    op.create_table(
        "audit_read_projections",
        sa.Column("event_id", sa.String(256), primary_key=True),
        sa.Column("scope_key", sa.String(1024), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("projection", sa.JSON(), nullable=False),
        sa.Column("projection_version", sa.String(128), nullable=False),
        sa.Column("rebuilt_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_audit_projection_scope_occurred_event",
        "audit_read_projections",
        ["scope_key", sa.text("occurred_at DESC"), sa.text("event_id DESC")],
    )
    op.create_table(
        "audit_export_jobs",
        sa.Column("export_id", sa.String(256), primary_key=True),
        sa.Column("scope_key", sa.String(1024), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("requester_principal_id", sa.String(256), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("normalized_filters", sa.JSON(), nullable=False),
        sa.Column("normalized_filter_hash", sa.String(64), nullable=False),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("field_profile", sa.String(32), nullable=False),
        sa.Column("job_id", sa.String(256), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("succeeded_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("omitted_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("artifact_ref", sa.Text()),
        sa.Column("artifact_expires_at", sa.DateTime(timezone=True)),
        sa.Column("artifact_sha256", sa.String(64)),
        sa.Column("create_authorization_policy_version", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("export_id", name="uq_audit_export_id"),
    )
    op.create_index(
        "ix_audit_export_scope_status_snapshot",
        "audit_export_jobs",
        ["scope_key", "status", "snapshot_at"],
    )
    op.create_index("ix_audit_export_artifact_expiry", "audit_export_jobs", ["artifact_expires_at"])
    op.create_table(
        "audit_retention_policies",
        sa.Column("policy_version", sa.String(128), primary_key=True),
        sa.Column("classes", sa.JSON(), nullable=False),
        sa.Column("writes_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "audit_legal_holds",
        sa.Column("legal_hold_id", sa.String(256), primary_key=True),
        sa.Column("scope_key", sa.String(1024), nullable=False),
        sa.Column("selector", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_audit_legal_hold_scope_status", "audit_legal_holds", ["scope_key", "status"]
    )
    op.create_table(
        "audit_integrity_checkpoints",
        sa.Column("checkpoint_id", sa.String(256), primary_key=True),
        sa.Column("scope_key", sa.String(1024), nullable=False),
        sa.Column("through_sequence", sa.BigInteger(), nullable=False),
        sa.Column("strategy_version", sa.String(128), nullable=False),
        sa.Column("root_digest", sa.String(512), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_audit_integrity_scope_through",
        "audit_integrity_checkpoints",
        ["scope_key", "through_sequence"],
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX ix_audit_event_relationships_gin "
            "ON audit_events USING gin (input_payload jsonb_path_ops)"
        )
        op.execute(
            """
            CREATE FUNCTION reject_audit_event_mutation() RETURNS trigger AS $$
            BEGIN
              RAISE EXCEPTION 'AUDIT_APPEND_ONLY_VIOLATION' USING ERRCODE = '55000';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        op.execute(
            "CREATE TRIGGER audit_events_append_only BEFORE UPDATE OR DELETE ON audit_events "
            "FOR EACH ROW EXECUTE FUNCTION reject_audit_event_mutation()"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS audit_events_append_only ON audit_events")
        op.execute("DROP FUNCTION IF EXISTS reject_audit_event_mutation()")
    for table in (
        "audit_integrity_checkpoints",
        "audit_legal_holds",
        "audit_retention_policies",
        "audit_export_jobs",
        "audit_read_projections",
        "audit_events",
        "audit_outbox",
        "access_preflights",
        "access_invitations",
        "access_authorization_snapshots",
        "access_scope_grants",
        "access_role_assignments",
        "access_members",
        "access_role_versions",
        "access_capability_catalogs",
    ):
        op.drop_table(table)
