from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ScopeMixin:
    organization_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    region_code: Mapped[str] = mapped_column(String(64), nullable=False)


class DataSource(ScopeMixin, Base):
    __tablename__ = "data_sources"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "project_id",
            "region_code",
            "normalized_name",
            name="uq_ingest_source_scope_name",
        ),
        Index(
            "ix_ingest_source_scope_updated",
            "organization_id",
            "project_id",
            "region_code",
            "updated_at",
            "source_id",
        ),
    )

    source_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    normalized_name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_format: Mapped[str] = mapped_column(String(128), nullable=False)
    source_format_version: Mapped[str | None] = mapped_column(String(128))
    adapter_version: Mapped[str] = mapped_column(String(64), nullable=False)
    administrative_state: Mapped[str] = mapped_column(String(32), nullable=False)
    config_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    credential_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    upload_policy_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    resource_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)
    projection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CredentialRef(Base):
    __tablename__ = "credential_refs"
    __table_args__ = (
        UniqueConstraint("source_id", "version", name="uq_ingest_credential_source_version"),
    )

    credential_ref_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("data_sources.source_id"), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    credential_type: Mapped[str] = mapped_column(String(64), nullable=False)
    managed_secret_ref: Mapped[str | None] = mapped_column(String(256))
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    safe_hint: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConnectionTest(ScopeMixin, Base):
    __tablename__ = "connection_tests"
    __table_args__ = (Index("ix_ingest_connection_source_created", "source_id", "created_at"),)

    connection_test_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("data_sources.source_id"), nullable=False)
    job_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_config_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    observed_credential_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    projection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UploadSession(ScopeMixin, Base):
    __tablename__ = "upload_sessions"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "project_id",
            "region_code",
            "supersedes_upload_id",
            name="uq_ingest_upload_scope_supersedes",
        ),
        Index(
            "ix_ingest_upload_scope_updated",
            "organization_id",
            "project_id",
            "region_code",
            "updated_at",
            "upload_id",
        ),
        Index("ix_ingest_upload_source_state", "source_id", "lifecycle_status"),
    )

    upload_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("data_sources.source_id"), nullable=False)
    target_dataset_id: Mapped[str | None] = mapped_column(String(128))
    supersedes_upload_id: Mapped[str | None] = mapped_column(String(128))
    lifecycle_status: Mapped[str] = mapped_column(String(32), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_config_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_credential_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    upload_policy_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    resource_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)
    current_manifest_id: Mapped[str | None] = mapped_column(String(128))
    current_verification_run_id: Mapped[str | None] = mapped_column(String(128))
    current_quarantine_id: Mapped[str | None] = mapped_column(String(128))
    object_set_hash: Mapped[str | None] = mapped_column(String(64))
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    projection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UploadObject(Base):
    __tablename__ = "upload_objects"
    __table_args__ = (
        UniqueConstraint(
            "upload_id", "normalized_relative_path", name="uq_ingest_object_upload_path"
        ),
        Index("ix_ingest_object_upload_updated", "upload_id", "updated_at", "upload_object_id"),
    )

    upload_object_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    upload_id: Mapped[str] = mapped_column(ForeignKey("upload_sessions.upload_id"), nullable=False)
    client_object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    declared_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    declared_sha256: Mapped[str | None] = mapped_column(String(64))
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    provider_etag: Mapped[str | None] = mapped_column(String(512))
    multipart_status: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    projection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UploadPartAttempt(Base):
    __tablename__ = "upload_part_attempts"
    __table_args__ = (
        UniqueConstraint(
            "upload_object_id", "part_number", "attempt", name="uq_ingest_part_attempt"
        ),
        Index("ix_ingest_part_object_number", "upload_object_id", "part_number", "attempt"),
    )

    part_attempt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    upload_object_id: Mapped[str] = mapped_column(
        ForeignKey("upload_objects.upload_object_id"), nullable=False
    )
    part_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    attempt: Mapped[int] = mapped_column(BigInteger, nullable=False)
    offset_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_etag: Mapped[str | None] = mapped_column(String(512))
    checksum_algorithm: Mapped[str | None] = mapped_column(String(64))
    checksum_value: Mapped[str | None] = mapped_column(String(512))
    safe_error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    projection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceManifest(Base):
    __tablename__ = "source_manifests"
    __table_args__ = (
        UniqueConstraint("upload_id", "revision", name="uq_ingest_manifest_revision"),
    )

    manifest_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    upload_id: Mapped[str] = mapped_column(ForeignKey("upload_sessions.upload_id"), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    canonical_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    object_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    projection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ManifestNode(Base):
    __tablename__ = "manifest_nodes"
    __table_args__ = (
        Index("ix_ingest_manifest_node_parent", "manifest_id", "parent_node_id", "node_id"),
    )

    node_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    manifest_id: Mapped[str] = mapped_column(
        ForeignKey("source_manifests.manifest_id"), nullable=False
    )
    parent_node_id: Mapped[str | None] = mapped_column(String(128))
    json_pointer: Mapped[str] = mapped_column(Text, nullable=False)
    projection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class VerificationRun(Base):
    __tablename__ = "verification_runs"
    __table_args__ = (
        Index("ix_ingest_run_upload_created", "upload_id", "created_at", "verification_run_id"),
    )

    verification_run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    upload_id: Mapped[str] = mapped_column(ForeignKey("upload_sessions.upload_id"), nullable=False)
    supersedes_run_id: Mapped[str | None] = mapped_column(String(128))
    job_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    object_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    projection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VerificationStage(Base):
    __tablename__ = "verification_stages"
    __table_args__ = (
        UniqueConstraint(
            "verification_run_id", "stage_code", "transition_no", name="uq_ingest_stage_transition"
        ),
    )

    stage_transition_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    verification_run_id: Mapped[str] = mapped_column(
        ForeignKey("verification_runs.verification_run_id"), nullable=False
    )
    stage_code: Mapped[str] = mapped_column(String(64), nullable=False)
    transition_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    projection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VerificationFinding(Base):
    __tablename__ = "verification_findings"
    __table_args__ = (
        Index("ix_ingest_finding_run_created", "verification_run_id", "created_at", "finding_id"),
    )

    finding_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    verification_run_id: Mapped[str] = mapped_column(
        ForeignKey("verification_runs.verification_run_id"), nullable=False
    )
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str] = mapped_column(String(128), nullable=False)
    safe_relative_path: Mapped[str | None] = mapped_column(Text)
    projection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Quarantine(Base):
    __tablename__ = "quarantines"
    __table_args__ = (UniqueConstraint("verification_run_id", name="uq_ingest_quarantine_run"),)

    quarantine_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    upload_id: Mapped[str] = mapped_column(ForeignKey("upload_sessions.upload_id"), nullable=False)
    verification_run_id: Mapped[str] = mapped_column(
        ForeignKey("verification_runs.verification_run_id"), nullable=False
    )
    object_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    projection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UploadJob(ScopeMixin, Base):
    __tablename__ = "upload_jobs"
    __table_args__ = (Index("ix_ingest_job_resource", "resource_type", "resource_id"),)

    job_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    projection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DatasetRegistrationReceipt(ScopeMixin, Base):
    __tablename__ = "dataset_registration_receipts"

    registration_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    registration_receipt_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    upload_id: Mapped[str] = mapped_column(String(128), nullable=False)
    verification_run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_manifest_id: Mapped[str] = mapped_column(String(128), nullable=False)
    registration_intent_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_object_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    projection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UploadEvent(ScopeMixin, Base):
    __tablename__ = "upload_events"
    __table_args__ = (
        Index("ix_ingest_event_upload_occurred", "upload_id", "occurred_at", "event_id"),
    )

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    upload_id: Mapped[str] = mapped_column(ForeignKey("upload_sessions.upload_id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    event_level: Mapped[str] = mapped_column(String(16), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    projection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
