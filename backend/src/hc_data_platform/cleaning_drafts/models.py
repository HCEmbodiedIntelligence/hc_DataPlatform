"""Strict P10 CleaningDraft read-projection models.

The P10 endpoints intentionally contain no EDL, preview, or commit mutation
commands. They summarize the server-owned P11 Draft state, including P09
handoffs and immutable P07 return-review lineage.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[
    str,
    Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", min_length=1, max_length=128),
]
OrganizationId = Identifier
ProjectId = Identifier
RegionCode = Annotated[
    str,
    Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=1, max_length=64),
]
CleaningDraftId = Annotated[
    str,
    Field(pattern=r"^draft_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"),
]
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
ReviewDecisionId = Annotated[
    str,
    Field(pattern=r"^review_decision_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"),
]
ReviewFindingId = Annotated[
    str,
    Field(pattern=r"^review_finding_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"),
]
CommitId = Annotated[
    str,
    Field(pattern=r"^commit_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"),
]
DecimalString = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]*)$")]
EnumCode = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")]
Etag = Annotated[str, Field(pattern=r'^"[^"\r\n]+"$', min_length=3, max_length=256)]
Sha256Signature = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]

CleaningDraftScopeFilter: TypeAlias = Literal[
    "mine", "actionable", "review", "returned", "submitted", "all"
]
CleaningDraftStatusFilter: TypeAlias = Literal["active", "submitted", "failed", "archived"]
CleaningDraftSort: TypeAlias = Literal[
    "updated_at:desc,id:desc",
    "updated_at:asc,id:asc",
    "created_at:desc,id:desc",
    "reuse_ratio:desc,updated_at:desc,id:desc",
    "estimated_effective_duration_ns:desc,id:desc",
]
CleaningDraftEventType: TypeAlias = Literal[
    "cleaning.draft.created",
    "cleaning.draft.updated",
    "cleaning.preview.requested",
    "cleaning.preview.completed",
    "cleaning.submit.requested",
    "cleaning.submit.completed",
    "dataset_version.review.returned",
    "cleaning.draft.successor_created",
]


class _CleaningDraftModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CleaningDraftScope(_CleaningDraftModel):
    organization_id: OrganizationId
    project_id: ProjectId
    region_code: RegionCode


class CleaningDraftIssueContext(_CleaningDraftModel):
    """The one P09 ManualIssue source context a handoff Draft is allowed to carry."""

    schema_version: Literal[1] = 1
    dataset_id: DatasetId
    base_version_id: DatasetVersionId
    episode_id: EpisodeId
    base_revision_id: EpisodeRevisionId
    selected_stream_id: Identifier
    selected_channel_path: str | None = Field(default=None, max_length=512)
    start_ns: DecimalString
    end_ns: DecimalString
    manual_issue_ids: tuple[ManualIssueId] = Field(min_length=1, max_length=1)

    @model_validator(mode="after")
    def _valid_range(self) -> CleaningDraftIssueContext:
        if int(self.start_ns) >= int(self.end_ns):
            raise ValueError("CleaningDraft source range must be half-open and non-empty")
        return self


class CleaningDraftIssueDerivedOrigin(_CleaningDraftModel):
    origin_type: Literal["ISSUE_DERIVED"] = "ISSUE_DERIVED"
    manual_issue_context: CleaningDraftIssueContext
    review_return_lineage: None = None


class CleaningDraftReviewReturnLineage(_CleaningDraftModel):
    lineage_type: Literal["REVIEW_RETURN"] = "REVIEW_RETURN"
    supersedes_draft_id: CleaningDraftId
    returned_from_version_id: DatasetVersionId
    returned_from_review_decision_id: ReviewDecisionId


class CleaningDraftReviewReturnOrigin(_CleaningDraftModel):
    origin_type: Literal["REVIEW_RETURN"] = "REVIEW_RETURN"
    manual_issue_context: None = None
    review_return_lineage: CleaningDraftReviewReturnLineage


CleaningDraftOrigin: TypeAlias = Annotated[
    CleaningDraftIssueDerivedOrigin | CleaningDraftReviewReturnOrigin,
    Field(discriminator="origin_type"),
]


class CleaningDraftReviewSummary(_CleaningDraftModel):
    review_decision_id: ReviewDecisionId
    review_finding_ids: tuple[ReviewFindingId, ...] = Field(min_length=1)
    finding_count: DecimalString
    successor_draft_id: CleaningDraftId
    supersedes_draft_id: CleaningDraftId
    returned_from_version_id: DatasetVersionId
    returned_from_review_decision_id: ReviewDecisionId
    output_version_status: Literal["RETURNED"] = "RETURNED"

    @model_validator(mode="after")
    def _consistent_lineage(self) -> CleaningDraftReviewSummary:
        if self.review_decision_id != self.returned_from_review_decision_id:
            raise ValueError("Review decision lineage must use the immutable decision ID")
        if int(self.finding_count) != len(self.review_finding_ids):
            raise ValueError("Review finding count must match immutable finding IDs")
        if self.successor_draft_id == self.supersedes_draft_id:
            raise ValueError("Review return successor must have a new Draft ID")
        return self


class CleaningDraftRecord(_CleaningDraftModel):
    draft_id: CleaningDraftId
    etag: Etag
    status: EnumCode
    origin: CleaningDraftOrigin
    base_version_id: DatasetVersionId
    base_revision_id: EpisodeRevisionId
    episode_id: EpisodeId
    manual_issue_count: Literal["0", "1"]
    preview_status: EnumCode
    commit_status: EnumCode
    output_version_status: EnumCode | None = None
    review_decision_id: ReviewDecisionId | None = None
    successor_draft_id: CleaningDraftId | None = None
    review_finding_count: DecimalString
    review_summary: CleaningDraftReviewSummary | None = None
    allowed_actions: tuple[EnumCode, ...] = ()
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _consistent_projection(self) -> CleaningDraftRecord:
        issue_derived = isinstance(self.origin, CleaningDraftIssueDerivedOrigin)
        if (self.manual_issue_count == "1") != issue_derived:
            raise ValueError("ManualIssue count must match the CleaningDraft origin")
        returned = self.output_version_status == "RETURNED"
        review_values = (
            self.review_decision_id,
            self.successor_draft_id,
            self.review_summary,
        )
        if returned:
            if any(value is None for value in review_values) or self.review_finding_count == "0":
                raise ValueError("RETURNED CleaningDraft requires immutable review-return facts")
        elif any(value is not None for value in review_values) or self.review_finding_count != "0":
            raise ValueError("only RETURNED CleaningDrafts may contain review-return facts")
        if self.updated_at < self.created_at:
            raise ValueError("CleaningDraft updated_at must not precede created_at")
        return self


class CleaningDraftListItem(_CleaningDraftModel):
    draft_id: CleaningDraftId
    etag: Etag
    status: EnumCode
    origin: CleaningDraftOrigin
    base_version_id: DatasetVersionId
    base_revision_id: EpisodeRevisionId
    episode_id: EpisodeId
    manual_issue_count: Literal["0", "1"]
    preview_status: EnumCode
    commit_status: EnumCode
    output_version_status: EnumCode | None = None
    review_decision_id: ReviewDecisionId | None = None
    successor_draft_id: CleaningDraftId | None = None
    review_finding_count: DecimalString
    review_summary: CleaningDraftReviewSummary | None = None
    allowed_actions: tuple[EnumCode, ...] = ()
    updated_at: datetime

    @model_validator(mode="after")
    def _consistent_projection(self) -> CleaningDraftListItem:
        CleaningDraftRecord(
            draft_id=self.draft_id,
            etag=self.etag,
            status=self.status,
            origin=self.origin,
            base_version_id=self.base_version_id,
            base_revision_id=self.base_revision_id,
            episode_id=self.episode_id,
            manual_issue_count=self.manual_issue_count,
            preview_status=self.preview_status,
            commit_status=self.commit_status,
            output_version_status=self.output_version_status,
            review_decision_id=self.review_decision_id,
            successor_draft_id=self.successor_draft_id,
            review_finding_count=self.review_finding_count,
            review_summary=self.review_summary,
            allowed_actions=self.allowed_actions,
            created_at=self.updated_at,
            updated_at=self.updated_at,
        )
        return self


class CleaningDraftRelationships(_CleaningDraftModel):
    commit_id: CommitId | None = None
    output_version_id: DatasetVersionId | None = None
    output_revision_ids: tuple[EpisodeRevisionId, ...] = ()
    review_decision_id: ReviewDecisionId | None = None
    review_finding_ids: tuple[ReviewFindingId, ...] = ()
    successor_draft_id: CleaningDraftId | None = None
    supersedes_draft_id: CleaningDraftId | None = None
    returned_from_version_id: DatasetVersionId | None = None
    returned_from_review_decision_id: ReviewDecisionId | None = None


class CleaningDraftEvent(_CleaningDraftModel):
    event_id: Identifier
    event_type: CleaningDraftEventType
    occurred_at: datetime
    result: Literal["SUCCESS", "FAILURE"]
    safe_summary: str | None = Field(default=None, max_length=2048)
    request_id: Identifier


class CleaningDraftPageInfo(_CleaningDraftModel):
    after: str | None = None
    before: str | None = None
    has_next: bool
    has_previous: bool


class CleaningDraftListEnvelope(_CleaningDraftModel):
    items: tuple[CleaningDraftListItem, ...]
    page_info: CleaningDraftPageInfo
    snapshot_at: datetime
    query_signature: Sha256Signature
    scope: CleaningDraftScope
    request_id: Identifier
    contract_version: Literal["manual-cleaning.v1"] = "manual-cleaning.v1"


class CleaningDraftScopeCounts(_CleaningDraftModel):
    EDITING: DecimalString
    COMMITTED: DecimalString
    RETURNED: DecimalString
    REVIEWING: DecimalString


class CleaningDraftMetrics(_CleaningDraftModel):
    active_draft_count: DecimalString
    manual_issue_derived_count: DecimalString
    review_return_count: DecimalString


class CleaningDraftJobs(_CleaningDraftModel):
    preview_queued: DecimalString
    preview_running: DecimalString
    commit_queued: DecimalString


class CleaningDraftSummaryData(_CleaningDraftModel):
    scope_counts: CleaningDraftScopeCounts
    metrics: CleaningDraftMetrics
    jobs: CleaningDraftJobs
    as_of: datetime


class CleaningDraftSummaryEnvelope(_CleaningDraftModel):
    data: CleaningDraftSummaryData
    scope: CleaningDraftScope
    request_id: Identifier
    contract_version: Literal["manual-cleaning.v1"] = "manual-cleaning.v1"


class CleaningDraftDetailData(_CleaningDraftModel):
    draft: CleaningDraftRecord
    origin: CleaningDraftOrigin
    # P11 owns the detailed EDL. P10 remains a queue/read projection and does
    # not duplicate an editable operation log in this compact response.
    ordered_operations: tuple[()] = ()
    # Preview inspection belongs to P11; P10 exposes its durable status only.
    preview_summary: None = None
    review_summary: CleaningDraftReviewSummary | None = None
    relationships: CleaningDraftRelationships

    @model_validator(mode="after")
    def _matches_draft(self) -> CleaningDraftDetailData:
        if self.origin != self.draft.origin:
            raise ValueError("CleaningDraft detail origin must match its Draft")
        if self.review_summary != self.draft.review_summary:
            raise ValueError("CleaningDraft detail review summary must match its Draft")
        return self


class CleaningDraftDetailEnvelope(_CleaningDraftModel):
    data: CleaningDraftDetailData
    scope: CleaningDraftScope
    request_id: Identifier
    contract_version: Literal["manual-cleaning.v1"] = "manual-cleaning.v1"


class CleaningDraftEventsEnvelope(_CleaningDraftModel):
    items: tuple[CleaningDraftEvent, ...]
    page_info: CleaningDraftPageInfo
    snapshot_at: datetime
    scope: CleaningDraftScope
    request_id: Identifier
    contract_version: Literal["manual-cleaning.v1"] = "manual-cleaning.v1"


class CleaningDraftProjection(_CleaningDraftModel):
    """Repository-only enriched read model; never a direct API envelope."""

    scope: CleaningDraftScope
    draft: CleaningDraftRecord
    relationships: CleaningDraftRelationships
    dataset_id: DatasetId | None = None
    creator_id: Identifier | None = None
    robot_id: Identifier | None = None
    review_finding_types: tuple[EnumCode, ...] = ()
    review_finding_severities: tuple[Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"], ...] = ()


class CleaningDraftAuditEvent(_CleaningDraftModel):
    project_id: ProjectId
    region_code: RegionCode
    actor_id: str = Field(min_length=1, max_length=256)
    action: str = Field(min_length=1, max_length=128)
    resource_id: Identifier
    request_id: Identifier
    occurred_at: datetime
    details: dict[str, object] | None = None
