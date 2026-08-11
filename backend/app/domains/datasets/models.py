"""SQLAlchemy 2 persistence model for the dataset/version/review context."""

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
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (
        UniqueConstraint("scope_key", "dataset_id", name="uq_datasets_scope_dataset"),
        Index("ix_datasets_scope_updated_id", "scope_key", "updated_at", "dataset_id"),
    )

    dataset_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(512), nullable=False)
    organization_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    region_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    labels: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    availability: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    current_ready_version_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    row_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint(
            "scope_key",
            "dataset_id",
            "version_id",
            name="uq_dataset_versions_scope_dataset_version",
        ),
        CheckConstraint(
            "version_id NOT IN ('current', 'latest')", name="ck_dataset_versions_exact_id"
        ),
        CheckConstraint(
            "status IN ('REVIEWING', 'RETURNED', 'READY')", name="ck_dataset_versions_status"
        ),
        CheckConstraint("kind IN ('RAW', 'CLEANED')", name="ck_dataset_versions_kind"),
        CheckConstraint(
            "successor_draft_id IS NULL OR successor_draft_id <> supersedes_draft_id",
            name="ck_dataset_versions_new_successor",
        ),
        Index(
            "ix_dataset_versions_dataset_created_id",
            "scope_key",
            "dataset_id",
            "created_at",
            "version_id",
        ),
    )

    version_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(512), nullable=False)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.dataset_id"), nullable=False)
    display_version: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source_draft_id: Mapped[str | None] = mapped_column(String(128))
    review_decision_id: Mapped[str | None] = mapped_column(String(128))
    successor_draft_id: Mapped[str | None] = mapped_column(String(128))
    supersedes_draft_id: Mapped[str | None] = mapped_column(String(128))
    returned_from_version_id: Mapped[str | None] = mapped_column(String(128))
    returned_from_review_decision_id: Mapped[str | None] = mapped_column(String(128))
    version_token: Mapped[str] = mapped_column(String(256), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    row_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    etag: Mapped[str] = mapped_column(String(256), nullable=False)


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (
        UniqueConstraint("scope_key", "episode_id", name="uq_episodes_scope_episode"),
    )
    episode_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(512), nullable=False)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.dataset_id"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)


class EpisodeRevision(Base):
    __tablename__ = "episode_revisions"
    __table_args__ = (
        UniqueConstraint("scope_key", "revision_id", name="uq_episode_revisions_scope_revision"),
        CheckConstraint(
            "start_ns >= 0 AND end_ns > start_ns", name="ck_episode_revisions_half_open"
        ),
    )
    revision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(512), nullable=False)
    episode_id: Mapped[str] = mapped_column(ForeignKey("episodes.episode_id"), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    start_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    streams: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)


class DatasetVersionRevision(Base):
    __tablename__ = "dataset_version_revisions"
    __table_args__ = (
        UniqueConstraint("version_id", "ordinal", name="uq_dataset_version_revisions_ordinal"),
        UniqueConstraint("version_id", "episode_id", name="uq_dataset_version_revisions_episode"),
    )
    version_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_versions.version_id"), primary_key=True
    )
    episode_id: Mapped[str] = mapped_column(ForeignKey("episodes.episode_id"), primary_key=True)
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("episode_revisions.revision_id"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)


class DatasetVersionReferenceSnapshot(Base):
    __tablename__ = "dataset_version_reference_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "version_id",
            "reference_type",
            "reference_id",
            "reference_version",
            name="uq_dataset_version_reference_snapshot",
        ),
    )
    snapshot_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_versions.version_id"), nullable=False
    )
    reference_type: Mapped[str] = mapped_column(String(64), nullable=False)
    reference_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reference_version: Mapped[str] = mapped_column(String(128), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class DatasetVersionManifest(Base):
    __tablename__ = "dataset_version_manifests"
    manifest_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_versions.version_id"), nullable=False, unique=True
    )
    canonical_bytes: Mapped[bytes] = mapped_column(nullable=False)
    format_version: Mapped[str] = mapped_column(String(64), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ManifestEntry(Base):
    __tablename__ = "manifest_entries"
    __table_args__ = (
        UniqueConstraint("manifest_id", "entry_id", name="uq_manifest_entries_manifest_entry"),
    )
    entry_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    manifest_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_version_manifests.manifest_id"), nullable=False
    )
    episode_id: Mapped[str] = mapped_column(String(128), nullable=False)
    revision_id: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(96), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    safe_locator: Mapped[str | None] = mapped_column(Text)


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (
        UniqueConstraint(
            "scope_key", "output_version_id", name="uq_review_decisions_scope_version"
        ),
        CheckConstraint(
            "decision IN ('APPROVED', 'RETURNED')", name="ck_review_decisions_decision"
        ),
    )
    review_decision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(512), nullable=False)
    output_version_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_versions.version_id"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    audit_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewFinding(Base):
    """P07-owned immutable fact.

    This table, its ID family and its absence of lifecycle columns deliberately keep
    ReviewFinding disjoint from ``manual_cleaning.manual_issues``.  It must never
    gain status, assignee, resolution, reopen or update semantics.
    """

    __tablename__ = "review_findings"
    __table_args__ = (
        CheckConstraint("start_ns >= 0 AND end_ns > start_ns", name="ck_review_findings_half_open"),
        Index("ix_review_findings_decision_id", "review_decision_id", "review_finding_id"),
    )
    review_finding_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    review_decision_id: Mapped[str] = mapped_column(
        ForeignKey("review_decisions.review_decision_id"), nullable=False
    )
    output_revision_id: Mapped[str] = mapped_column(String(128), nullable=False)
    episode_stream_id: Mapped[str] = mapped_column(String(128), nullable=False)
    start_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    finding_type: Mapped[str] = mapped_column(String(96), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewReturnLineage(Base):
    __tablename__ = "review_return_lineage"
    __table_args__ = (
        CheckConstraint(
            "successor_draft_id <> supersedes_draft_id",
            name="ck_review_return_lineage_new_successor",
        ),
    )
    review_decision_id: Mapped[str] = mapped_column(
        ForeignKey("review_decisions.review_decision_id"), primary_key=True
    )
    successor_draft_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    supersedes_draft_id: Mapped[str] = mapped_column(String(128), nullable=False)
    returned_from_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DeletionPreflight(Base):
    __tablename__ = "deletion_preflights"
    preflight_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(512), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    executable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    blocked_reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
