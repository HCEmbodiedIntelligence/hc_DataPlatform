"""foundation idempotency, outbox and audit tables

Revision ID: foundation_0001
Revises:
Create Date: 2026-08-11
"""

import sqlalchemy as sa

from alembic import op

revision = "foundation_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "idempotency_records",
        sa.Column("record_id", sa.String(128), primary_key=True),
        sa.Column("scope", sa.String(1024), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64)),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("response_schema_version", sa.String(64)),
        sa.Column("response_body_or_resource_ref", sa.JSON()),
        sa.Column("async_job_id", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scope", "idempotency_key", name="uq_idempotency_scope_key"),
    )
    op.create_index("ix_idempotency_state_expires", "idempotency_records", ["state", "expires_at"])
    op.create_table(
        "outbox_records",
        sa.Column("outbox_id", sa.String(128), primary_key=True),
        sa.Column("producer_context", sa.String(100), nullable=False),
        sa.Column("message_contract", sa.String(200), nullable=False),
        sa.Column("contract_version", sa.String(64), nullable=False),
        sa.Column("aggregate_ref", sa.JSON(), nullable=False),
        sa.Column("effective_scope", sa.JSON(), nullable=False),
        sa.Column("payload_or_payload_ref", sa.JSON(), nullable=False),
        sa.Column("traceparent", sa.String(512)),
        sa.Column("correlation_id", sa.String(128)),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("broker_message_id", sa.String(256)),
        sa.Column("last_error_code", sa.Text()),
    )
    op.create_index(
        "ix_outbox_available_unpublished", "outbox_records", ["published_at", "available_at"]
    )
    op.create_index("ix_outbox_producer", "outbox_records", ["producer_context"])
    op.create_table(
        "audit_records",
        sa.Column("audit_id", sa.String(128), primary_key=True),
        sa.Column("event_name", sa.String(200), nullable=False),
        sa.Column("target_type", sa.String(100), nullable=False),
        sa.Column("target_id", sa.String(128), nullable=False),
        sa.Column("outcome", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("region_code", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("detail", sa.JSON()),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_audit_scope_occurred",
        "audit_records",
        ["organization_id", "project_id", "region_code", "occurred_at"],
    )
    op.create_index("ix_audit_target", "audit_records", ["target_type", "target_id"])


def downgrade() -> None:
    op.drop_table("audit_records")
    op.drop_table("outbox_records")
    op.drop_table("idempotency_records")
