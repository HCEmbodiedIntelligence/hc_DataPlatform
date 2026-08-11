"""SQLAlchemy 2 persistence model for access control and append-only audit."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    desc,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class CapabilityCatalogModel(Base):
    __tablename__ = "access_capability_catalogs"

    catalog_version: Mapped[str] = mapped_column(String(128), primary_key=True)
    contract_status: Mapped[str] = mapped_column(String(32), nullable=False)
    capability_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    reserved_denylist: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    catalog_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RoleVersionModel(Base):
    __tablename__ = "access_role_versions"
    __table_args__ = (
        UniqueConstraint("role_id", "role_version", name="uq_access_role_version"),
        Index("ix_access_role_catalog_status", "catalog_version", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    role_id: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    role_version: Mapped[str] = mapped_column(String(128), nullable=False)
    catalog_version: Mapped[str] = mapped_column(String(128), nullable=False)
    base_capability_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    capability_ceiling_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    set_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MemberModel(Base):
    __tablename__ = "access_members"
    __table_args__ = (
        Index("ix_access_members_project_status_role", "project_id", "status", "role_id"),
        Index(
            "uq_access_members_project_principal_current",
            "project_id",
            "principal_id",
            unique=True,
            postgresql_where=text("status <> 'REMOVED'"),
            sqlite_where=text("status <> 'REMOVED'"),
        ),
    )

    member_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(256), nullable=False)
    project_id: Mapped[str] = mapped_column(String(256), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(256), nullable=False)
    principal_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    role_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role_version: Mapped[str] = mapped_column(String(128), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resource_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)


class RoleAssignmentModel(Base):
    __tablename__ = "access_role_assignments"
    __table_args__ = (
        Index("ix_access_role_assignment_subject_scope_status", "member_id", "scope_key", "status"),
        Index(
            "uq_access_role_assignment_member_active",
            "member_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
            sqlite_where=text("status = 'ACTIVE'"),
        ),
    )

    role_assignment_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(256), nullable=False)
    subject: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    role_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role_version: Mapped[str] = mapped_column(String(128), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    inherit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)


class ScopeGrantModel(Base):
    __tablename__ = "access_scope_grants"
    __table_args__ = (
        Index(
            "ix_access_scope_grant_subject_scope_effect_status_ceiling",
            "subject_key",
            "scope_key",
            "effect",
            "status",
            "ceiling_version",
        ),
        Index(
            "uq_access_scope_grant_active_identity",
            "subject_key",
            "scope_key",
            "effect",
            "capability_digest",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
            sqlite_where=text("status = 'ACTIVE'"),
        ),
    )

    scope_grant_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    subject_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    subject: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    scope_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    effect: Mapped[str] = mapped_column(String(16), nullable=False)
    capability_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    capability_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    ceiling_version: Mapped[str] = mapped_column(String(128), nullable=False)
    inherit: Mapped[bool] = mapped_column(Boolean, nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)


class AuthorizationSnapshotModel(Base):
    __tablename__ = "access_authorization_snapshots"
    __table_args__ = (
        Index(
            "ix_access_authorization_policy_role_scope",
            "policy_version",
            "role_version",
            "scope_key",
        ),
    )

    snapshot_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    principal_id: Mapped[str] = mapped_column(String(256), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    capability_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    denied_capability_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    catalog_version: Mapped[str] = mapped_column(String(128), nullable=False)
    role_version: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    refresh_state: Mapped[str] = mapped_column(String(32), nullable=False)


class InvitationModel(Base):
    __tablename__ = "access_invitations"
    __table_args__ = (
        Index("ix_access_invitation_project_status_created", "project_id", "status", "created_at"),
    )

    invitation_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(256), nullable=False)
    project_id: Mapped[str] = mapped_column(String(256), nullable=False)
    target_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    target_display: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    intended_role_id: Mapped[str] = mapped_column(String(64), nullable=False)
    intended_role_version: Mapped[str] = mapped_column(String(128), nullable=False)
    intended_scope: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    policy_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    superseded_by_invitation_id: Mapped[str | None] = mapped_column(String(256))
    etag: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AccessPreflightModel(Base):
    __tablename__ = "access_preflights"
    __table_args__ = (Index("ix_access_preflight_project_expiry", "project_id", "expires_at"),)

    preflight_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(256), nullable=False)
    actor_principal_id: Mapped[str] = mapped_column(String(256), nullable=False)
    operation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    policy_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_etag: Mapped[str] = mapped_column(String(256), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditOutboxModel(Base):
    __tablename__ = "audit_outbox"
    __table_args__ = (
        UniqueConstraint("outbox_id", name="uq_audit_outbox_id"),
        UniqueConstraint(
            "producer_service", "producer_event_id", name="uq_audit_outbox_producer_event"
        ),
        Index("ix_audit_outbox_relay", "relay_status", "next_attempt_at"),
    )

    outbox_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(256), nullable=False)
    producer_service: Mapped[str] = mapped_column(String(128), nullable=False)
    producer_event_id: Mapped[str] = mapped_column(String(256), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    relay_status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(128))


class AuditEventModel(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_audit_event_id"),
        UniqueConstraint(
            "producer_service", "producer_event_id", name="uq_audit_event_producer_event"
        ),
        Index(
            "ix_audit_event_scope_occurred_event",
            "scope_key",
            desc("occurred_at"),
            desc("event_id"),
        ),
        Index("ix_audit_event_scope_name_occurred", "scope_key", "event_name", desc("occurred_at")),
        Index(
            "ix_audit_event_scope_actor_occurred",
            "scope_key",
            "actor_principal_id",
            desc("occurred_at"),
        ),
    )

    event_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    organization_id: Mapped[str] = mapped_column(String(256), nullable=False)
    project_id: Mapped[str] = mapped_column(String(256), nullable=False)
    region_code: Mapped[str | None] = mapped_column(String(64))
    event_name: Mapped[str] = mapped_column(String(256), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingest_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    actor_principal_id: Mapped[str] = mapped_column(String(256), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(256), nullable=False)
    request_id: Mapped[str] = mapped_column(String(256), nullable=False)
    producer_service: Mapped[str] = mapped_column(String(128), nullable=False)
    producer_event_id: Mapped[str] = mapped_column(String(256), nullable=False)
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    catalog_version: Mapped[str] = mapped_column(String(128), nullable=False)
    outcome_status: Mapped[str] = mapped_column(String(32), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    risk_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    retention_class: Mapped[str] = mapped_column(String(32), nullable=False)
    retention_policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    retain_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    legal_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    integrity_version: Mapped[str] = mapped_column(String(128), nullable=False)
    record_digest: Mapped[str] = mapped_column(String(512), nullable=False)
    checkpoint_id: Mapped[str | None] = mapped_column(String(256))


class AuditReadProjectionModel(Base):
    __tablename__ = "audit_read_projections"
    __table_args__ = (
        Index(
            "ix_audit_projection_scope_occurred_event",
            "scope_key",
            desc("occurred_at"),
            desc("event_id"),
        ),
    )

    event_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    projection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    projection_version: Mapped[str] = mapped_column(String(128), nullable=False)
    rebuilt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditExportJobModel(Base):
    __tablename__ = "audit_export_jobs"
    __table_args__ = (
        UniqueConstraint("export_id", name="uq_audit_export_id"),
        Index("ix_audit_export_scope_status_snapshot", "scope_key", "status", "snapshot_at"),
        Index("ix_audit_export_artifact_expiry", "artifact_expires_at"),
    )

    export_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    requester_principal_id: Mapped[str] = mapped_column(String(256), nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    normalized_filters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    normalized_filter_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    field_profile: Mapped[str] = mapped_column(String(32), nullable=False)
    job_id: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    succeeded_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    omitted_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    artifact_ref: Mapped[str | None] = mapped_column(Text)
    artifact_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    create_authorization_policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditRetentionPolicyModel(Base):
    __tablename__ = "audit_retention_policies"

    policy_version: Mapped[str] = mapped_column(String(128), primary_key=True)
    classes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    writes_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditLegalHoldModel(Base):
    __tablename__ = "audit_legal_holds"
    __table_args__ = (Index("ix_audit_legal_hold_scope_status", "scope_key", "status"),)

    legal_hold_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    selector: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditIntegrityCheckpointModel(Base):
    __tablename__ = "audit_integrity_checkpoints"
    __table_args__ = (Index("ix_audit_integrity_scope_through", "scope_key", "through_sequence"),)

    checkpoint_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    through_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    root_digest: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
