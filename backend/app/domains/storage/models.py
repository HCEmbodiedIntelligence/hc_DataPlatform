"""SQLAlchemy 2 persistence for storage facts and lifecycle evidence."""

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
    UniqueConstraint,
    desc,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class StorageAreaModel(Base):
    __tablename__ = "storage_areas"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "project_id",
            "region_code",
            "area_key",
            name="uq_storage_area_scope_key",
        ),
    )

    area_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    project_id: Mapped[str] = mapped_column(String(160), nullable=False)
    region_code: Mapped[str] = mapped_column(String(32), nullable=False)
    area_key: Mapped[str] = mapped_column(String(160), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)


class StorageObjectModel(Base):
    __tablename__ = "storage_objects"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "project_id",
            "region_code",
            "provider_bucket",
            "provider_object_key",
            "provider_version_id",
            name="uq_storage_provider_object_version",
        ),
        Index(
            "ix_storage_object_scope_role_status_created",
            "organization_id",
            "project_id",
            "region_code",
            "object_role",
            "status",
            desc("created_at"),
            desc("object_id"),
        ),
        Index(
            "ix_storage_object_scope_classification",
            "organization_id",
            "project_id",
            "region_code",
            "classification_status",
        ),
    )

    object_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    project_id: Mapped[str] = mapped_column(String(160), nullable=False)
    region_code: Mapped[str] = mapped_column(String(32), nullable=False)
    area_id: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_bucket: Mapped[str] = mapped_column(String(256), nullable=False)
    provider_object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    provider_version_id: Mapped[str] = mapped_column(String(256), nullable=False)
    object_version: Mapped[str] = mapped_column(String(160), nullable=False)
    object_role: Mapped[str | None] = mapped_column(String(32))
    classification_status: Mapped[str] = mapped_column(String(32), nullable=False)
    display_key: Mapped[str] = mapped_column(String(256), nullable=False)
    physical_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_class: Mapped[str] = mapped_column(String(32), nullable=False)
    media_type: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    content_sha256: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resource_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)


class StorageReferenceModel(Base):
    __tablename__ = "storage_object_references"
    __table_args__ = (
        UniqueConstraint(
            "object_id",
            "resource_type",
            "resource_id",
            "resource_version",
            "relation",
            name="uq_storage_reference_identity",
        ),
        Index("ix_storage_reference_resource", "resource_type", "resource_id"),
    )

    reference_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    object_id: Mapped[str] = mapped_column(String(160), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(160), nullable=False)
    resource_version: Mapped[str] = mapped_column(String(160), nullable=False)
    relation: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ObjectProtectionModel(Base):
    __tablename__ = "storage_object_protections"
    __table_args__ = (
        UniqueConstraint("object_id", "protection_version", name="uq_storage_protection_version"),
        Index("ix_storage_protection_current", "object_id", "current"),
    )

    protection_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    object_id: Mapped[str] = mapped_column(String(160), nullable=False)
    protection_version: Mapped[str] = mapped_column(String(160), nullable=False)
    current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reference_state: Mapped[str] = mapped_column(String(32), nullable=False)
    reference_count: Mapped[int | None] = mapped_column(BigInteger)
    retention_state: Mapped[str] = mapped_column(String(32), nullable=False)
    retain_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    legal_hold_state: Mapped[str] = mapped_column(String(32), nullable=False)
    legal_hold_count: Mapped[int | None] = mapped_column(BigInteger)
    provider_lock_state: Mapped[str] = mapped_column(String(32), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(160), nullable=False)


class InventorySnapshotModel(Base):
    __tablename__ = "storage_inventory_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "project_id",
            "region_code",
            "snapshot_id",
            name="uq_storage_inventory_scope_snapshot",
        ),
        Index(
            "ix_storage_inventory_scope_current_asof",
            "organization_id",
            "project_id",
            "region_code",
            "current",
            desc("as_of"),
        ),
    )

    snapshot_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    project_id: Mapped[str] = mapped_column(String(160), nullable=False)
    region_code: Mapped[str] = mapped_column(String(32), nullable=False)
    current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    freshness: Mapped[str] = mapped_column(String(32), nullable=False)
    process_status: Mapped[str] = mapped_column(String(32), nullable=False)
    formula_version: Mapped[str] = mapped_column(String(160), nullable=False)
    data_completeness: Mapped[str] = mapped_column(String(32), nullable=False)
    watermarks: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    reconciliation: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    overview_projection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    refresh_job_id: Mapped[str | None] = mapped_column(String(160))


class CostSnapshotModel(Base):
    __tablename__ = "storage_cost_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "project_id",
            "region_code",
            "billing_period",
            "source_revision",
            "formula_version",
            name="uq_storage_cost_source_revision",
        ),
        Index(
            "ix_storage_cost_scope_period",
            "organization_id",
            "project_id",
            "region_code",
            desc("billing_period"),
        ),
    )

    cost_snapshot_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    project_id: Mapped[str] = mapped_column(String(160), nullable=False)
    region_code: Mapped[str] = mapped_column(String(32), nullable=False)
    billing_period: Mapped[str] = mapped_column(String(7), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    source_revision: Mapped[str] = mapped_column(String(160), nullable=False)
    formula_version: Mapped[str] = mapped_column(String(160), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    projection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class LifecyclePolicySetModel(Base):
    __tablename__ = "storage_lifecycle_policy_sets"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "project_id", "region_code", name="uq_storage_policy_set_scope"
        ),
    )

    policy_set_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    project_id: Mapped[str] = mapped_column(String(160), nullable=False)
    region_code: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(160), nullable=False)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LifecyclePolicyVersionModel(Base):
    __tablename__ = "storage_lifecycle_policy_versions"
    __table_args__ = (
        UniqueConstraint("policy_id", "version", name="uq_storage_policy_version"),
        Index(
            "ix_storage_policy_scope_current_updated",
            "organization_id",
            "project_id",
            "region_code",
            "current",
            desc("updated_at"),
            desc("policy_id"),
        ),
    )

    row_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    policy_id: Mapped[str] = mapped_column(String(160), nullable=False)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    project_id: Mapped[str] = mapped_column(String(160), nullable=False)
    region_code: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(160), nullable=False)
    current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    simulation_input_hash: Mapped[str] = mapped_column(String(160), nullable=False)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StorageJobModel(Base):
    __tablename__ = "storage_jobs"
    __table_args__ = (Index("ix_storage_job_scope_created", "scope_key", desc("created_at")),)

    job_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(160), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress: Mapped[float | None]
    failure: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LifecycleSimulationModel(Base):
    __tablename__ = "storage_lifecycle_simulations"
    __table_args__ = (
        Index(
            "ix_storage_simulation_scope_created",
            "organization_id",
            "project_id",
            "region_code",
            desc("created_at"),
        ),
        Index("ix_storage_simulation_expiry", "status", "expires_at"),
    )

    simulation_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    project_id: Mapped[str] = mapped_column(String(160), nullable=False)
    region_code: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(160), nullable=False)
    impact_digest: Mapped[str] = mapped_column(String(160), nullable=False)
    policy_set_version: Mapped[str] = mapped_column(String(160), nullable=False)
    policy_versions: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(160), nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    result_projection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    job_id: Mapped[str] = mapped_column(String(160), nullable=False)
    audit_event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LifecycleExecutionModel(Base):
    __tablename__ = "storage_lifecycle_executions"
    __table_args__ = (
        Index(
            "ix_storage_execution_scope_started",
            "organization_id",
            "project_id",
            "region_code",
            desc("started_at"),
            desc("execution_id"),
        ),
    )

    execution_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    project_id: Mapped[str] = mapped_column(String(160), nullable=False)
    region_code: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    projection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class RestorePreflightModel(Base):
    __tablename__ = "storage_restore_preflights"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_storage_restore_token"),
        Index("ix_storage_restore_preflight_expiry", "expires_at", "used_at"),
    )

    quote_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(512), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(160), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(160), nullable=False)
    target_version: Mapped[str] = mapped_column(String(160), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(160), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    projection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class RestoreTaskModel(Base):
    __tablename__ = "storage_restore_tasks"
    __table_args__ = (
        Index(
            "ix_storage_restore_scope_requested",
            "organization_id",
            "project_id",
            "region_code",
            desc("requested_at"),
            desc("restore_task_id"),
        ),
    )

    restore_task_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    project_id: Mapped[str] = mapped_column(String(160), nullable=False)
    region_code: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    projection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    job_id: Mapped[str] = mapped_column(String(160), nullable=False)


class MultipartUploadProjectionModel(Base):
    __tablename__ = "storage_multipart_upload_projections"
    __table_args__ = (
        Index(
            "ix_storage_multipart_scope_activity",
            "organization_id",
            "project_id",
            "region_code",
            desc("last_activity_at"),
            desc("upload_id"),
        ),
    )

    upload_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    project_id: Mapped[str] = mapped_column(String(160), nullable=False)
    region_code: Mapped[str] = mapped_column(String(32), nullable=False)
    upload_version: Mapped[str] = mapped_column(String(160), nullable=False)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)
    provider_bucket: Mapped[str] = mapped_column(String(256), nullable=False)
    provider_object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    provider_etag: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    uploaded_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expected_bytes: Mapped[int | None] = mapped_column(BigInteger)
    uploaded_part_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    protection_state: Mapped[str] = mapped_column(String(32), nullable=False)
    protected_reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    projection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class MultipartAbortPlanModel(Base):
    __tablename__ = "storage_multipart_abort_plans"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_storage_abort_plan_token"),
        Index("ix_storage_abort_plan_expiry", "expires_at", "consumed_at"),
    )

    plan_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    upload_id: Mapped[str] = mapped_column(String(160), nullable=False)
    upload_version: Mapped[str] = mapped_column(String(160), nullable=False)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)
    observed_last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    provider_etag: Mapped[str] = mapped_column(String(256), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(160), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MultipartAbortModel(Base):
    __tablename__ = "storage_multipart_aborts"
    __table_args__ = (
        UniqueConstraint("plan_id", name="uq_storage_abort_plan_once"),
        Index("ix_storage_abort_upload_created", "upload_id", desc("created_at")),
    )

    abort_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(160), nullable=False)
    upload_id: Mapped[str] = mapped_column(String(160), nullable=False)
    job_id: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StorageCommandIntentModel(Base):
    __tablename__ = "storage_command_intents"
    __table_args__ = (
        UniqueConstraint("scope_key", "idempotency_key", name="uq_storage_command_intent"),
    )

    intent_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(512), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
