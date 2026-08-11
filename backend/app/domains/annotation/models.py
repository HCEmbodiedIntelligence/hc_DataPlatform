"""SQLAlchemy 2 persistence for annotation tasks and immutable outputs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

SCHEMA = "annotation"
ACTIVE_WORKFLOW_SQL = "workflow_status NOT IN ('APPROVED', 'CANCELLED')"


class AnnotationTask(Base):
    __tablename__ = "annotation_tasks"
    __table_args__ = (
        CheckConstraint(
            "workflow_status IN "
            "('QUEUED','ASSIGNED','IN_PROGRESS','BLOCKED','SUBMITTED','RETURNED',"
            "'APPROVED','CANCELLED')",
            name="ck_annotation_tasks_workflow_status",
        ),
        CheckConstraint(
            "source_status IN ('CURRENT','STALE')", name="ck_annotation_tasks_source_status"
        ),
        CheckConstraint(
            "start_ns >= 0 AND end_ns > start_ns", name="ck_annotation_tasks_half_open"
        ),
        Index(
            "uq_annotation_tasks_active_coverage",
            "organization_id",
            "project_id",
            "region_code",
            "coverage_key_hash",
            unique=True,
            postgresql_where=text(ACTIVE_WORKFLOW_SQL),
        ),
        Index(
            "ix_annotation_tasks_scope_updated_id",
            "organization_id",
            "project_id",
            "region_code",
            "updated_at",
            "task_id",
        ),
        {"schema": SCHEMA},
    )
    task_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    region_code: Mapped[str] = mapped_column(String(64), nullable=False)
    coverage_key_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    task_source: Mapped[str] = mapped_column(String(32), nullable=False)
    workflow_status: Mapped[str] = mapped_column(String(24), nullable=False)
    source_status: Mapped[str] = mapped_column(String(16), nullable=False, default="CURRENT")
    block_source: Mapped[str | None] = mapped_column(String(24))
    block_reason: Mapped[str | None] = mapped_column(Text)
    dataset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    episode_id: Mapped[str] = mapped_column(String(128), nullable=False)
    base_revision_id: Mapped[str] = mapped_column(String(128), nullable=False)
    stream_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    start_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ontology_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ontology_version: Mapped[str] = mapped_column(String(128), nullable=False)
    ontology_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    assignment_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    assignee_id: Mapped[str | None] = mapped_column(String(128))
    assigned_by: Mapped[str | None] = mapped_column(String(128))
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_draft_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    current_draft_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    current_submission_id: Mapped[str | None] = mapped_column(String(128))
    submitted_annotation_set_id: Mapped[str | None] = mapped_column(String(128))
    correction_of_annotation_set_id: Mapped[str | None] = mapped_column(String(128))
    predecessor_task_id: Mapped[str | None] = mapped_column(String(128))
    successor_task_id: Mapped[str | None] = mapped_column(String(128))
    latest_review_id: Mapped[str | None] = mapped_column(String(128))
    resource_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AnnotationDraft(Base):
    __tablename__ = "annotation_drafts"
    __table_args__ = (
        CheckConstraint(
            "state IN ('ACTIVE','FROZEN_SUBMITTED','STALE_READ_ONLY')",
            name="ck_annotation_drafts_state",
        ),
        {"schema": SCHEMA},
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.annotation_tasks.task_id"), primary_key=True
    )
    current_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    current_content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="ACTIVE")
    resource_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)


class AnnotationDraftRevision(Base):
    __tablename__ = "annotation_draft_revisions"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "draft_revision", name="uq_annotation_draft_revisions_task_revision"
        ),
        {"schema": SCHEMA},
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.annotation_drafts.task_id"), primary_key=True
    )
    draft_revision: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entries: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    saved_by: Mapped[str] = mapped_column(String(128), nullable=False)
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    client_mutation_id: Mapped[str] = mapped_column(String(128), nullable=False)


class AnnotationEntryResolution(Base):
    __tablename__ = "annotation_entry_resolutions"
    __table_args__ = (
        CheckConstraint(
            "state IN ('OPEN_EXISTING','CLAIMABLE','CAN_CREATE','ASSIGNED_TO_OTHER','FORBIDDEN')",
            name="ck_annotation_entry_resolutions_state",
        ),
        {"schema": SCHEMA},
    )
    resolution_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    region_code: Mapped[str] = mapped_column(String(64), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(128), nullable=False)
    coverage_key_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    resolved_context: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    task_id: Mapped[str | None] = mapped_column(String(128))
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AnnotationSubmission(Base):
    __tablename__ = "annotation_submissions"
    __table_args__ = (
        CheckConstraint(
            "submission_state IN ('SUBMITTED','APPROVED','RETURNED')",
            name="ck_annotation_submissions_state",
        ),
        UniqueConstraint(
            "task_id", "draft_revision", name="uq_annotation_submissions_task_draft_revision"
        ),
        {"schema": SCHEMA},
    )
    submission_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.annotation_tasks.task_id"), nullable=False
    )
    annotation_set_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    draft_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    submission_state: Mapped[str] = mapped_column(String(16), nullable=False)
    submitted_by: Mapped[str] = mapped_column(String(128), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    latest_review_id: Mapped[str | None] = mapped_column(String(128))
    resource_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)


class AnnotationSubmitPreflight(Base):
    __tablename__ = "annotation_submit_preflights"
    __table_args__ = ({"schema": SCHEMA},)
    preflight_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.annotation_tasks.task_id"), nullable=False
    )
    draft_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    issue_watermark: Mapped[str] = mapped_column(String(128), nullable=False)
    valid: Mapped[bool] = mapped_column(nullable=False)
    validation_errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AnnotationSet(Base):
    __tablename__ = "annotation_sets"
    __table_args__ = (
        CheckConstraint(
            "effective_state IN ('ACTIVE','SUPERSEDED')", name="ck_annotation_sets_effective_state"
        ),
        UniqueConstraint("submission_id", name="uq_annotation_sets_submission"),
        Index(
            "ix_annotation_sets_revision_submitted_id",
            "base_revision_id",
            "submitted_at",
            "annotation_set_id",
        ),
        {"schema": SCHEMA},
    )
    annotation_set_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.annotation_tasks.task_id"), nullable=False
    )
    submission_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.annotation_submissions.submission_id"), nullable=False
    )
    organization_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    region_code: Mapped[str] = mapped_column(String(64), nullable=False)
    base_revision_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ontology_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ontology_version: Mapped[str] = mapped_column(String(128), nullable=False)
    ontology_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    entries: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    submitted_by: Mapped[str] = mapped_column(String(128), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_state: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    supersedes_annotation_set_id: Mapped[str | None] = mapped_column(String(128))
    superseded_by_annotation_set_id: Mapped[str | None] = mapped_column(String(128))


class AnnotationReview(Base):
    __tablename__ = "annotation_reviews"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('APPROVED','RETURNED')", name="ck_annotation_reviews_decision"
        ),
        UniqueConstraint("submission_id", name="uq_annotation_reviews_submission"),
        {"schema": SCHEMA},
    )
    review_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.annotation_tasks.task_id"), nullable=False
    )
    submission_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.annotation_submissions.submission_id"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AnnotationIdempotency(Base):
    __tablename__ = "annotation_idempotency"
    __table_args__ = (
        UniqueConstraint(
            "scope_key",
            "principal_id",
            "operation_id",
            "idempotency_key",
            name="uq_annotation_idempotency_identity",
        ),
        {"schema": SCHEMA},
    )
    record_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(512), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(128), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AnnotationOutbox(Base):
    __tablename__ = "annotation_outbox"
    __table_args__ = (
        UniqueConstraint(
            "aggregate_type",
            "aggregate_id",
            "aggregate_version",
            "event_name",
            name="uq_annotation_outbox_aggregate_event",
        ),
        {"schema": SCHEMA},
    )
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_name: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
