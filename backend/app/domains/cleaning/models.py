"""SQLAlchemy 2 models for ManualIssue and CleaningDraft aggregates."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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

SCHEMA = "manual_cleaning"


class ManualIssue(Base):
    """P09-owned lifecycle root; never a ReviewFinding projection."""

    __tablename__ = "manual_issues"
    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN', 'IN_PROGRESS', 'RESOLVED')", name="ck_manual_issues_status"
        ),
        CheckConstraint("start_ns >= 0 AND end_ns > start_ns", name="ck_manual_issues_half_open"),
        CheckConstraint(
            "status <> 'IN_PROGRESS' OR assignee_id IS NOT NULL",
            name="ck_manual_issues_in_progress_assignee",
        ),
        CheckConstraint(
            "(status = 'RESOLVED' AND resolution_version_id IS NOT NULL "
            "AND resolved_at IS NOT NULL AND resolved_by IS NOT NULL) OR "
            "(status <> 'RESOLVED' AND resolution_version_id IS NULL "
            "AND resolved_at IS NULL AND resolved_by IS NULL)",
            name="ck_manual_issues_resolution_complete",
        ),
        Index(
            "ix_manual_issues_scope_updated_id",
            "organization_id",
            "project_id",
            "region_code",
            "updated_at",
            "manual_issue_id",
        ),
        {"schema": SCHEMA},
    )
    manual_issue_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    region_code: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    origin_dataset_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    episode_id: Mapped[str] = mapped_column(String(128), nullable=False)
    episode_revision_id: Mapped[str] = mapped_column(String(128), nullable=False)
    episode_stream_id: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_snapshot_id: Mapped[str] = mapped_column(String(128), nullable=False)
    robot_model_version_id: Mapped[str | None] = mapped_column(String(128))
    calibration_set_id: Mapped[str | None] = mapped_column(String(128))
    start_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    issue_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    assignee_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    note: Mapped[str] = mapped_column(Text, nullable=False)
    resolution_version_id: Mapped[str | None] = mapped_column(String(128))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    producer_draft_id: Mapped[str | None] = mapped_column(String(128))
    root_issue_draft_id: Mapped[str | None] = mapped_column(String(128))
    lineage_depth: Mapped[int | None] = mapped_column(Integer)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str | None] = mapped_column(String(128))
    resource_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)


class ManualIssueHistory(Base):
    __tablename__ = "manual_issue_history"
    __table_args__ = (
        UniqueConstraint(
            "manual_issue_id", "next_resource_version", name="uq_manual_issue_history_version"
        ),
        {"schema": SCHEMA},
    )
    history_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    manual_issue_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.manual_issues.manual_issue_id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    prior_resource_version: Mapped[int | None] = mapped_column(BigInteger)
    next_resource_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    before_facts: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after_facts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CleaningDraft(Base):
    __tablename__ = "cleaning_drafts"
    __table_args__ = (
        CheckConstraint("status IN ('EDITING', 'COMMITTED')", name="ck_cleaning_drafts_status"),
        CheckConstraint(
            "origin_type IN ('ISSUE_DERIVED', 'REVIEW_RETURN')", name="ck_cleaning_drafts_origin"
        ),
        CheckConstraint("current_edl_revision >= 0", name="ck_cleaning_drafts_edl_revision"),
        Index(
            "ix_cleaning_drafts_scope_updated_id",
            "organization_id",
            "project_id",
            "region_code",
            "updated_at",
            "draft_id",
        ),
        {"schema": SCHEMA},
    )
    draft_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    region_code: Mapped[str] = mapped_column(String(64), nullable=False)
    origin_type: Mapped[str] = mapped_column(String(24), nullable=False)
    dataset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    base_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    episode_id: Mapped[str] = mapped_column(String(128), nullable=False)
    base_revision_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="EDITING")
    current_edl_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    current_operation_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    committed_edl_revision: Mapped[int | None] = mapped_column(BigInteger)
    active_preview_id: Mapped[str | None] = mapped_column(String(128))
    active_commit_id: Mapped[str | None] = mapped_column(String(128))
    resource_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)


class ManualIssueDraftLink(Base):
    __tablename__ = "manual_issue_draft_links"
    __table_args__ = ({"schema": SCHEMA},)
    draft_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.cleaning_drafts.draft_id"), primary_key=True
    )
    manual_issue_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.manual_issues.manual_issue_id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IssueDerivedDraftContext(Base):
    __tablename__ = "issue_derived_draft_contexts"
    __table_args__ = (
        CheckConstraint("schema_version = 1", name="ck_issue_derived_contexts_schema_version"),
        CheckConstraint(
            "start_ns >= 0 AND end_ns > start_ns", name="ck_issue_derived_contexts_half_open"
        ),
        {"schema": SCHEMA},
    )
    draft_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.cleaning_drafts.draft_id"), primary_key=True
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    dataset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    base_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    episode_id: Mapped[str] = mapped_column(String(128), nullable=False)
    base_revision_id: Mapped[str] = mapped_column(String(128), nullable=False)
    selected_stream_id: Mapped[str] = mapped_column(String(128), nullable=False)
    selected_channel_path: Mapped[str] = mapped_column(String(512), nullable=False)
    start_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ReviewReturnDraftLineage(Base):
    __tablename__ = "review_return_draft_lineage"
    __table_args__ = (
        UniqueConstraint(
            "returned_from_review_decision_id", name="uq_review_return_draft_lineage_decision"
        ),
        CheckConstraint(
            "draft_id <> supersedes_draft_id", name="ck_review_return_draft_lineage_new_id"
        ),
        {"schema": SCHEMA},
    )
    draft_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.cleaning_drafts.draft_id"), primary_key=True
    )
    supersedes_draft_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.cleaning_drafts.draft_id"), nullable=False
    )
    returned_from_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    returned_from_review_decision_id: Mapped[str] = mapped_column(String(128), nullable=False)


class ReviewSuccessorComposition(Base):
    __tablename__ = "review_successor_compositions"
    __table_args__ = (
        CheckConstraint(
            "schema_version = 1", name="ck_review_successor_compositions_schema_version"
        ),
        {"schema": SCHEMA},
    )
    draft_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.cleaning_drafts.draft_id"), primary_key=True
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    editable_base_revision_id: Mapped[str] = mapped_column(String(128), nullable=False)
    composition_hash: Mapped[str] = mapped_column(String(71), nullable=False)


class ReviewSuccessorCompositionMember(Base):
    __tablename__ = "review_successor_composition_members"
    __table_args__ = (
        UniqueConstraint(
            "draft_id", "source_revision_id", name="uq_review_successor_member_revision"
        ),
        CheckConstraint(
            "handling IN ('EDITABLE_BASE', 'CARRY_FORWARD')",
            name="ck_review_successor_member_handling",
        ),
        {"schema": SCHEMA},
    )
    draft_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.review_successor_compositions.draft_id"), primary_key=True
    )
    source_ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_revision_id: Mapped[str] = mapped_column(String(128), nullable=False)
    handling: Mapped[str] = mapped_column(String(24), nullable=False)


class CleaningDraftLineageClosure(Base):
    __tablename__ = "cleaning_draft_lineage_closure"
    __table_args__ = (
        CheckConstraint("depth >= 0", name="ck_cleaning_draft_lineage_depth"),
        CheckConstraint(
            "(depth = 0) = (ancestor_draft_id = descendant_draft_id)",
            name="ck_cleaning_draft_lineage_self_depth",
        ),
        {"schema": SCHEMA},
    )
    ancestor_draft_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.cleaning_drafts.draft_id"), primary_key=True
    )
    descendant_draft_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.cleaning_drafts.draft_id"), primary_key=True
    )
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CleaningEdlRevision(Base):
    __tablename__ = "cleaning_edl_revisions"
    __table_args__ = (
        UniqueConstraint(
            "draft_id", "edl_revision", name="uq_cleaning_edl_revisions_draft_revision"
        ),
        {"schema": SCHEMA},
    )
    draft_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.cleaning_drafts.draft_id"), primary_key=True
    )
    edl_revision: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    operation_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    canonical_document: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    validation_result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    calculated_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    author_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CleaningOperation(Base):
    __tablename__ = "cleaning_operations"
    __table_args__ = (
        UniqueConstraint(
            "draft_id", "edl_revision", "sequence_no", name="uq_cleaning_operations_sequence"
        ),
        CheckConstraint(
            "operation_type IN "
            "('TRIM','EXCLUDE_RANGE','SPLIT','TIME_OFFSET','DISABLE_CHANNEL',"
            "'SET_METADATA','INVALIDATE_EPISODE','INVALID_MASK')",
            name="ck_cleaning_operations_type",
        ),
        {"schema": SCHEMA},
    )
    draft_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    edl_revision: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    operation: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class CleaningPreview(Base):
    __tablename__ = "cleaning_previews"
    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED','RUNNING','READY','FAILED')", name="ck_cleaning_previews_status"
        ),
        UniqueConstraint(
            "preview_id",
            "draft_id",
            "base_revision_id",
            "edl_revision",
            "operation_hash",
            name="uq_cleaning_previews_identity",
        ),
        {"schema": SCHEMA},
    )
    preview_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    draft_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.cleaning_drafts.draft_id"), nullable=False
    )
    base_revision_id: Mapped[str] = mapped_column(String(128), nullable=False)
    edl_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    operation_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    job_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    viewer_manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    source_to_output_map: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    validation_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_ref: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CleaningCommit(Base):
    __tablename__ = "cleaning_commits"
    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED','SUCCEEDED','FAILED')", name="ck_cleaning_commits_status"
        ),
        Index(
            "uq_cleaning_commits_one_success",
            "draft_id",
            unique=True,
            postgresql_where=text("status = 'SUCCEEDED'"),
        ),
        {"schema": SCHEMA},
    )
    commit_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    draft_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.cleaning_drafts.draft_id"), nullable=False
    )
    preview_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.cleaning_previews.preview_id"), nullable=False
    )
    base_revision_id: Mapped[str] = mapped_column(String(128), nullable=False)
    edl_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    operation_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    successor_composition_hash: Mapped[str | None] = mapped_column(String(71))
    acknowledgement: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    job_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    output_version_id: Mapped[str | None] = mapped_column(String(128))
    error_ref: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CleaningCommitOutputRevision(Base):
    __tablename__ = "cleaning_commit_output_revisions"
    __table_args__ = (
        UniqueConstraint("commit_id", "revision_id", name="uq_cleaning_commit_output_revision"),
        CheckConstraint(
            "member_mode IN ('EDIT_RESULT','CARRY_FORWARD')",
            name="ck_cleaning_commit_output_member_mode",
        ),
        CheckConstraint(
            "member_mode <> 'CARRY_FORWARD' OR revision_id = source_revision_id",
            name="ck_cleaning_commit_output_carry_forward",
        ),
        {"schema": SCHEMA},
    )
    commit_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.cleaning_commits.commit_id"), primary_key=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_revision_id: Mapped[str] = mapped_column(String(128), nullable=False)
    member_mode: Mapped[str] = mapped_column(String(24), nullable=False)


class CleaningEditSession(Base):
    __tablename__ = "cleaning_edit_sessions"
    __table_args__ = ({"schema": SCHEMA},)
    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    draft_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.cleaning_drafts.draft_id"), nullable=False
    )
    principal_id: Mapped[str] = mapped_column(String(128), nullable=False)
    client_instance_id: Mapped[str] = mapped_column(String(128), nullable=False)
    lease_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ManualCleaningIdempotency(Base):
    __tablename__ = "manual_cleaning_idempotency"
    __table_args__ = (
        UniqueConstraint(
            "scope_key",
            "principal_id",
            "operation_id",
            "idempotency_key",
            name="uq_manual_cleaning_idempotency_identity",
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


class ManualCleaningOutbox(Base):
    __tablename__ = "manual_cleaning_outbox"
    __table_args__ = (
        UniqueConstraint(
            "aggregate_type",
            "aggregate_id",
            "aggregate_version",
            "event_name",
            name="uq_manual_cleaning_outbox_aggregate_event",
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
