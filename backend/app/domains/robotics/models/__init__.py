"""SQLAlchemy persistence for robotics, calibration and schema aggregates."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Index, String, UniqueConstraint, desc
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RobotModel(Base):
    __tablename__ = "robot_models"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "normalized_manufacturer",
            "normalized_model_code",
            name="uq_robot_model_org_manufacturer_code",
        ),
        Index(
            "ix_robot_model_org_created", "organization_id", desc("created_at"), desc("model_id")
        ),
    )

    model_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    manufacturer: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_manufacturer: Mapped[str] = mapped_column(String(120), nullable=False)
    model_code: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_model_code: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    current_published_version_id: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_version: Mapped[str] = mapped_column(String(80), nullable=False)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RobotModelVersion(Base):
    __tablename__ = "robot_model_versions"
    __table_args__ = (
        UniqueConstraint("model_id", "version_label", name="uq_robot_model_version_label"),
        Index("ix_robot_model_version_model_created", "model_id", desc("created_at")),
    )

    version_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    model_id: Mapped[str] = mapped_column(String(160), nullable=False)
    version_label: Mapped[str] = mapped_column(String(80), nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(32), nullable=False)
    upload_status: Mapped[str] = mapped_column(String(32), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_availability: Mapped[str] = mapped_column(String(32), nullable=False)
    publish_readiness: Mapped[str] = mapped_column(String(48), nullable=False)
    manifest_hash: Mapped[str | None] = mapped_column(String(80))
    validation_input_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    joint_mappings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    sample_candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    resource_version: Mapped[str] = mapped_column(String(80), nullable=False)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RobotAssetUploadSession(Base):
    __tablename__ = "robot_asset_upload_sessions"
    __table_args__ = (Index("ix_robot_upload_version_created", "version_id", desc("created_at")),)

    upload_session_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    version_id: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    objects: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RobotValidationReport(Base):
    __tablename__ = "robot_model_validation_reports"
    __table_args__ = (
        Index("ix_robot_validation_version_created", "version_id", desc("created_at")),
    )

    report_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    version_id: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    job_id: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RobotModelBinding(Base):
    __tablename__ = "robot_model_bindings"
    __table_args__ = (
        Index(
            "ix_robot_binding_scope_current",
            "project_id",
            "region_code",
            "scope_type",
            "scope_id",
            "status",
            desc("valid_from"),
        ),
    )

    binding_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    project_id: Mapped[str] = mapped_column(String(160), nullable=False)
    region_code: Mapped[str | None] = mapped_column(String(64))
    scope_type: Mapped[str] = mapped_column(String(40), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(160), nullable=False)
    robot_model_version_id: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)


class Robot(Base):
    __tablename__ = "robots"
    __table_args__ = (
        UniqueConstraint("project_id", "region_code", "serial_no", name="uq_robot_scope_serial"),
        Index(
            "ix_robot_scope_created",
            "project_id",
            "region_code",
            desc("created_at"),
            desc("robot_id"),
        ),
    )

    robot_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    project_id: Mapped[str] = mapped_column(String(160), nullable=False)
    region_code: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    serial_no: Mapped[str] = mapped_column(String(200), nullable=False)
    robot_model_id: Mapped[str] = mapped_column(String(160), nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(32), nullable=False)
    connectivity: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    topology_revision: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_version: Mapped[str] = mapped_column(String(80), nullable=False)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Component(Base):
    __tablename__ = "robot_components"
    __table_args__ = (
        Index("ix_component_robot_sort", "robot_id", "sort_key", "component_id"),
        Index("ix_component_scope_id", "project_id", "region_code", "component_id"),
    )

    component_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    project_id: Mapped[str] = mapped_column(String(160), nullable=False)
    region_code: Mapped[str] = mapped_column(String(64), nullable=False)
    robot_id: Mapped[str] = mapped_column(String(160), nullable=False)
    parent_component_id: Mapped[str | None] = mapped_column(String(160))
    component_type: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    serial_no: Mapped[str | None] = mapped_column(String(200))
    lifecycle: Mapped[str] = mapped_column(String(32), nullable=False)
    connectivity: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    sort_key: Mapped[str] = mapped_column(String(80), nullable=False)
    bindings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    frames: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    channels: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    resource_version: Mapped[str] = mapped_column(String(80), nullable=False)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RoboticsPreflight(Base):
    __tablename__ = "robotics_preflights"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_robotics_preflight_token"),
        Index("ix_robotics_preflight_expiry", "expires_at", "used_at"),
    )

    preflight_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(512), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(160), nullable=False)
    target_id: Mapped[str] = mapped_column(String(160), nullable=False)
    expected_etag: Mapped[str] = mapped_column(String(256), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RoboticsJob(Base):
    __tablename__ = "robotics_jobs"
    __table_args__ = (Index("ix_robotics_job_scope_created", "scope_key", desc("created_at")),)

    job_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(512), nullable=False)
    job_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(160), nullable=False)
    result_ref: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CalibrationSet(Base):
    __tablename__ = "calibration_sets"
    __table_args__ = (
        Index("ix_calibration_set_scope_created", "project_id", "region_code", desc("created_at")),
    )

    set_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    project_id: Mapped[str] = mapped_column(String(160), nullable=False)
    region_code: Mapped[str] = mapped_column(String(64), nullable=False)
    robot_id: Mapped[str] = mapped_column(String(160), nullable=False)
    component_id: Mapped[str] = mapped_column(String(160), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lifecycle: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_version: Mapped[str] = mapped_column(String(80), nullable=False)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CalibrationVersion(Base):
    __tablename__ = "calibration_versions"
    __table_args__ = (
        UniqueConstraint("set_id", "version", name="uq_calibration_set_version"),
        Index("ix_calibration_version_set_created", "set_id", desc("created_at")),
    )

    row_id: Mapped[str] = mapped_column(String(220), primary_key=True)
    set_id: Mapped[str] = mapped_column(String(160), nullable=False)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    project_id: Mapped[str] = mapped_column(String(160), nullable=False)
    region_code: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    validation_context_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_artifacts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    availability: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    resource_version: Mapped[str] = mapped_column(String(80), nullable=False)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CalibrationReport(Base):
    __tablename__ = "calibration_reports"
    __table_args__ = (
        Index("ix_calibration_report_version_created", "version_row_id", desc("created_at")),
    )

    report_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    project_id: Mapped[str] = mapped_column(String(160), nullable=False)
    region_code: Mapped[str] = mapped_column(String(64), nullable=False)
    version_row_id: Mapped[str] = mapped_column(String(220), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    job_id: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DataSchema(Base):
    __tablename__ = "data_schemas"
    __table_args__ = (
        UniqueConstraint("organization_id", "normalized_name", name="uq_data_schema_org_name"),
        Index("ix_data_schema_org_created", "organization_id", desc("created_at")),
    )

    schema_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False)
    logical_type: Mapped[str] = mapped_column(String(80), nullable=False)
    family_id: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000))
    compatibility_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    current_published_version: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_version: Mapped[str] = mapped_column(String(80), nullable=False)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DataSchemaVersion(Base):
    __tablename__ = "data_schema_versions"
    __table_args__ = (
        UniqueConstraint("schema_id", "version", name="uq_data_schema_version"),
        Index("ix_data_schema_version_schema_created", "schema_id", desc("created_at")),
    )

    row_id: Mapped[str] = mapped_column(String(220), primary_key=True)
    schema_id: Mapped[str] = mapped_column(String(160), nullable=False)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    parent_version_id: Mapped[str | None] = mapped_column(String(220))
    lifecycle: Mapped[str] = mapped_column(String(32), nullable=False)
    definition_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    compatibility: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    references: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    resource_version: Mapped[str] = mapped_column(String(80), nullable=False)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SchemaCompatibilityCheck(Base):
    __tablename__ = "schema_compatibility_checks"

    check_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    job_id: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SchemaImportValidation(Base):
    __tablename__ = "schema_import_validations"

    import_validation_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(160), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    normalized_definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    committed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class DatasetSchemaSnapshot(Base):
    __tablename__ = "dataset_schema_snapshots"
    __table_args__ = (
        Index("ix_schema_snapshot_org_created", "organization_id", desc("created_at")),
    )

    snapshot_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(160), nullable=False)
    dataset_id: Mapped[str] = mapped_column(String(160), nullable=False)
    dataset_version_id: Mapped[str | None] = mapped_column(String(160))
    content_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    channels: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    diff: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
