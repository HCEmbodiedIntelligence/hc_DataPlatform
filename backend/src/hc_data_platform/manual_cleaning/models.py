"""Strict P09 ManualIssue wire, persistence, and mutation models.

ManualIssue remains independent from P07 ReviewFinding.  The small draft record
defined here is only the durable P09 handoff; P10/P11 own its later read and
editing projections.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

ManualIssueId = Annotated[
    str,
    Field(pattern=r"^issue_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"),
]
DatasetId = Annotated[
    str,
    Field(pattern=r"^dataset_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"),
]
DatasetVersionId = Annotated[
    str,
    Field(pattern=r"^version_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"),
]
EpisodeId = Annotated[
    str,
    Field(pattern=r"^episode_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"),
]
EpisodeRevisionId = Annotated[
    str,
    Field(pattern=r"^revision_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"),
]
EpisodeStreamId = Annotated[
    str,
    Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"),
]
CleaningDraftId = Annotated[
    str,
    Field(pattern=r"^draft_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"),
]
DecimalString = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]*)$")]

ManualIssueStatus: TypeAlias = Literal["OPEN", "IN_PROGRESS", "RESOLVED"]
ManualIssueSeverity: TypeAlias = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
ManualIssueType: TypeAlias = Literal[
    "POSE_JITTER",
    "TIMESTAMP_DRIFT",
    "MISSING_FRAME",
    "STREAM_GAP",
    "CALIBRATION_MISMATCH",
    "INVALID_MASK",
    "OTHER",
]
ManualIssueAction: TypeAlias = Literal[
    "VIEW_EPISODE",
    "TRIAGE",
    "START_WORK",
    "CREATE_DRAFT",
    "CONTINUE_DRAFT",
    "RESOLVE",
    "PREVIEW_RANGE",
]


class _ManualCleaningModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ManualIssueScope(_ManualCleaningModel):
    organization_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    region_code: str = Field(min_length=1, max_length=64)


class ManualIssuePrincipal(_ManualCleaningModel):
    id: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=256)


class ManualIssueBlockedReason(_ManualCleaningModel):
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=1000)


class ManualIssueDraftRef(_ManualCleaningModel):
    draft_id: CleaningDraftId
    status: Literal["EDITING", "COMMITTED"]
    updated_at: datetime


class ManualIssueResolutionVersion(_ManualCleaningModel):
    version_id: DatasetVersionId
    producer_draft_id: CleaningDraftId
    root_issue_draft_id: CleaningDraftId
    lineage_depth: DecimalString
    resolved_at: datetime


class ManualIssueRecord(_ManualCleaningModel):
    """Stored source-bound issue and the P09 detail representation."""

    id: ManualIssueId
    etag: str = Field(min_length=3, max_length=256)
    scope: ManualIssueScope
    dataset_id: DatasetId
    origin_dataset_version_id: DatasetVersionId
    episode_id: EpisodeId
    episode_revision_id: EpisodeRevisionId
    episode_stream_id: EpisodeStreamId
    context_status: Literal["VALID"] = "VALID"
    schema_snapshot_id: str = Field(min_length=1, max_length=256)
    robot_model_version_id: str | None = Field(default=None, min_length=1, max_length=256)
    calibration_set_id: str | None = Field(default=None, min_length=1, max_length=256)
    start_ns: DecimalString
    end_ns: DecimalString
    issue_type: ManualIssueType
    severity: ManualIssueSeverity
    status: ManualIssueStatus
    note: str = Field(max_length=8192)
    assignee: ManualIssuePrincipal | None = None
    related_drafts: tuple[ManualIssueDraftRef, ...] = ()
    resolution_version: ManualIssueResolutionVersion | None = None
    resolution_note: str | None = Field(default=None, min_length=1, max_length=4096)
    resolved_at: datetime | None = None
    resolved_by: ManualIssuePrincipal | None = None
    allowed_actions: tuple[ManualIssueAction, ...] = ()
    blocked_reasons: tuple[ManualIssueBlockedReason, ...] = ()
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _consistent_lifecycle(self) -> ManualIssueRecord:
        if int(self.start_ns) >= int(self.end_ns):
            raise ValueError("ManualIssue range must be half-open and non-empty")
        if self.status == "IN_PROGRESS" and self.assignee is None:
            raise ValueError("IN_PROGRESS ManualIssue requires an assignee")
        resolution_values = (
            self.resolution_version,
            self.resolution_note,
            self.resolved_at,
            self.resolved_by,
        )
        if self.status == "RESOLVED":
            if any(value is None for value in resolution_values):
                raise ValueError("RESOLVED ManualIssue requires complete resolution facts")
        elif any(value is not None for value in resolution_values):
            raise ValueError("only RESOLVED ManualIssue may include resolution facts")
        if self.updated_at < self.created_at:
            raise ValueError("ManualIssue updated_at must not precede created_at")
        return self


class ManualIssueListItem(_ManualCleaningModel):
    id: ManualIssueId
    etag: str = Field(min_length=3, max_length=256)
    scope: ManualIssueScope
    dataset_id: DatasetId
    origin_dataset_version_id: DatasetVersionId
    episode_id: EpisodeId
    episode_revision_id: EpisodeRevisionId
    episode_stream_id: EpisodeStreamId
    start_ns: DecimalString
    end_ns: DecimalString
    issue_type: ManualIssueType
    severity: ManualIssueSeverity
    status: ManualIssueStatus
    assignee: ManualIssuePrincipal | None = None
    related_draft_count: DecimalString
    resolution_version: ManualIssueResolutionVersion | None = None
    resolution_note: str | None = Field(default=None, min_length=1, max_length=4096)
    resolved_at: datetime | None = None
    resolved_by: ManualIssuePrincipal | None = None
    allowed_actions: tuple[ManualIssueAction, ...] = ()
    updated_at: datetime


class ManualIssuePageInfo(_ManualCleaningModel):
    after: str | None = None
    before: str | None = None
    has_next: bool
    has_previous: bool


class ManualIssueCounts(_ManualCleaningModel):
    total: DecimalString
    open: DecimalString
    in_progress: DecimalString
    resolved: DecimalString


class ManualIssueFacets(_ManualCleaningModel):
    issue_types: tuple[ManualIssueType, ...] = ()
    severities: tuple[ManualIssueSeverity, ...] = ()
    statuses: tuple[ManualIssueStatus, ...] = ()
    assignees: tuple[ManualIssuePrincipal, ...] = ()


class ManualIssuePageData(_ManualCleaningModel):
    counts: ManualIssueCounts
    facets: ManualIssueFacets
    allowed_actions: tuple[ManualIssueAction, ...] = ()
    snapshot_at: datetime


class ManualIssueDetailEnvelope(_ManualCleaningModel):
    data: ManualIssueRecord
    scope: ManualIssueScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: Literal["manual-cleaning.v1"] = "manual-cleaning.v1"


class ManualIssueListEnvelope(_ManualCleaningModel):
    items: tuple[ManualIssueListItem, ...]
    page_info: ManualIssuePageInfo
    snapshot_at: datetime
    scope: ManualIssueScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: Literal["manual-cleaning.v1"] = "manual-cleaning.v1"


class ManualIssuePageEnvelope(_ManualCleaningModel):
    data: ManualIssuePageData
    scope: ManualIssueScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: Literal["manual-cleaning.v1"] = "manual-cleaning.v1"


class CreateManualIssueCommand(_ManualCleaningModel):
    origin_dataset_version_id: DatasetVersionId
    episode_id: EpisodeId
    episode_revision_id: EpisodeRevisionId
    episode_stream_id: EpisodeStreamId
    start_ns: DecimalString
    end_ns: DecimalString
    issue_type: ManualIssueType
    severity: ManualIssueSeverity
    note: str = Field(max_length=8192)

    @model_validator(mode="after")
    def _valid_range(self) -> CreateManualIssueCommand:
        if int(self.start_ns) >= int(self.end_ns):
            raise ValueError("ManualIssue range must be half-open and non-empty")
        return self


class TriageManualIssueCommand(_ManualCleaningModel):
    target_status: Literal["OPEN", "IN_PROGRESS"]
    severity: ManualIssueSeverity
    assignee_id: str | None = Field(default=None, min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def _in_progress_has_assignee(self) -> TriageManualIssueCommand:
        if self.target_status == "IN_PROGRESS" and self.assignee_id is None:
            raise ValueError("IN_PROGRESS ManualIssue requires assignee_id")
        return self


class ResolveManualIssueCommand(_ManualCleaningModel):
    resolution_version_id: DatasetVersionId
    resolution_note: str = Field(min_length=1, max_length=4096)


class CreateDraftFromIssueCommand(_ManualCleaningModel):
    """The browser may not submit source or draft context to this P09 command."""


class ManualIssueDraftContext(_ManualCleaningModel):
    schema_version: Literal[1] = 1
    dataset_id: DatasetId
    base_version_id: DatasetVersionId
    episode_id: EpisodeId
    base_revision_id: EpisodeRevisionId
    selected_stream_id: EpisodeStreamId
    selected_channel_path: str | None = Field(default=None, max_length=512)
    start_ns: DecimalString
    end_ns: DecimalString
    manual_issue_ids: tuple[ManualIssueId] = Field(min_length=1, max_length=1)


class ManualIssueDraftCandidate(_ManualCleaningModel):
    draft_id: CleaningDraftId
    status: Literal["EDITING"] = "EDITING"
    base_version_id: DatasetVersionId
    base_revision_id: EpisodeRevisionId
    manual_issue_ids: tuple[ManualIssueId] = Field(min_length=1, max_length=1)
    updated_at: datetime


class ManualIssueDraftCreated(_ManualCleaningModel):
    disposition: Literal["CREATED", "ALREADY_LINKED"]
    draft_id: CleaningDraftId
    context: ManualIssueDraftContext
    selection_token: None = None
    expires_at: None = None
    candidates: tuple[()] = ()


class ManualIssueDraftSelectionRequired(_ManualCleaningModel):
    disposition: Literal["SELECTION_REQUIRED"] = "SELECTION_REQUIRED"
    draft_id: None = None
    context: None = None
    selection_token: str = Field(min_length=16, max_length=2048)
    expires_at: datetime
    candidates: tuple[ManualIssueDraftCandidate, ...] = Field(min_length=2)


ManualIssueDraftResult: TypeAlias = Annotated[
    ManualIssueDraftCreated | ManualIssueDraftSelectionRequired,
    Field(discriminator="disposition"),
]


class ManualIssueCreateDraftEnvelope(_ManualCleaningModel):
    data: ManualIssueDraftResult
    scope: ManualIssueScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: Literal["manual-cleaning.v1"] = "manual-cleaning.v1"


class ManualIssueSourceFacts(_ManualCleaningModel):
    """Authoritative immutable source facts resolved from P06/P07 projections."""

    scope: ManualIssueScope
    dataset_id: DatasetId
    version_id: DatasetVersionId
    episode_id: EpisodeId
    revision_id: EpisodeRevisionId
    stream_id: EpisodeStreamId
    stream_channel_path: str | None = Field(default=None, max_length=512)
    stream_start_ns: DecimalString
    stream_end_ns: DecimalString
    schema_snapshot_id: str = Field(min_length=1, max_length=256)
    robot_model_version_id: str | None = Field(default=None, min_length=1, max_length=256)
    calibration_set_id: str | None = Field(default=None, min_length=1, max_length=256)


class ManualCleaningDraftRecord(_ManualCleaningModel):
    scope: ManualIssueScope
    draft_id: CleaningDraftId
    source_issue_id: ManualIssueId
    dataset_id: DatasetId
    base_version_id: DatasetVersionId
    episode_id: EpisodeId
    base_revision_id: EpisodeRevisionId
    selected_stream_id: EpisodeStreamId
    selected_channel_path: str | None = Field(default=None, max_length=512)
    start_ns: DecimalString
    end_ns: DecimalString
    status: Literal["EDITING", "COMMITTED"]
    created_at: datetime
    updated_at: datetime


class ManualIssueResolutionCandidate(_ManualCleaningModel):
    scope: ManualIssueScope
    manual_issue_id: ManualIssueId
    version_id: DatasetVersionId
    producer_draft_id: CleaningDraftId
    root_issue_draft_id: CleaningDraftId
    lineage_depth: DecimalString


class ManualIssueAuditEvent(_ManualCleaningModel):
    project_id: str = Field(min_length=1, max_length=128)
    region_code: str = Field(min_length=1, max_length=64)
    actor_id: str = Field(min_length=1, max_length=256)
    action: str = Field(min_length=1, max_length=128)
    resource_id: str = Field(min_length=1, max_length=256)
    request_id: str = Field(min_length=1, max_length=128)
    outcome: Literal["SUCCEEDED"] = "SUCCEEDED"
    occurred_at: datetime
    before_hash: str | None = None
    after_hash: str | None = None
    details: dict[str, object] | None = None


class ManualIssueMutationRecord(_ManualCleaningModel):
    mutation_kind: Literal["ISSUE"] = "ISSUE"
    issue: ManualIssueRecord


class ManualIssueDraftMutationRecord(_ManualCleaningModel):
    mutation_kind: Literal["DRAFT"] = "DRAFT"
    scope: ManualIssueScope
    issue_etag: str = Field(min_length=3, max_length=256)
    result: ManualIssueDraftResult
