"""Strict wire and persistence models for the P11 CleaningDraft workbench.

The workbench is deliberately a projection over immutable source revisions.  It
stores EDL revisions, preview identities, and logical output-version commits;
it never mutates a Raw source revision in place.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[
    str,
    Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", min_length=1, max_length=128),
]
CleaningDraftId = Annotated[
    str,
    Field(pattern=r"^draft_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"),
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
    Field(pattern=r"^stream_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"),
]
ManualIssueId = Annotated[
    str,
    Field(pattern=r"^issue_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"),
]
ReviewDecisionId = Annotated[
    str,
    Field(pattern=r"^review_decision_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"),
]
ReviewFindingId = Annotated[
    str,
    Field(pattern=r"^review_finding_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"),
]
PreviewId = Annotated[
    str,
    Field(pattern=r"^preview_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"),
]
CommitId = Annotated[
    str,
    Field(pattern=r"^commit_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"),
]
JobId = Identifier
DecimalString = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]*)$")]
SignedDecimalString = Annotated[str, Field(pattern=r"^-?(0|[1-9][0-9]*)$")]
Sha256Signature = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]
Etag = Annotated[str, Field(pattern=r'^"[^"\r\n]+"$', min_length=3, max_length=256)]

CleaningDraftStatus: TypeAlias = Literal["EDITING", "COMMITTED"]
PreviewStatus: TypeAlias = Literal[
    "NONE", "QUEUED", "RUNNING", "READY", "FAILED", "EXPIRED", "STALE"
]
CommitStatus: TypeAlias = Literal["NONE", "QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
OutputVersionStatus: TypeAlias = Literal["REVIEWING", "READY", "RETURNED"]
AllowedAction: TypeAlias = Literal[
    "VIEW",
    "ACQUIRE_LEASE",
    "SAVE_EDL",
    "CREATE_PREVIEW",
    "COMMIT",
    "OPEN_REVIEW",
    "OPEN_SUCCESSOR",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CleaningWorkbenchScope(_Model):
    organization_id: Identifier
    project_id: Identifier
    region_code: Annotated[
        str,
        Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=1, max_length=64),
    ]


class CleaningStream(_Model):
    stream_id: EpisodeStreamId
    channel_path: str = Field(min_length=1, max_length=512)
    kind: str = Field(min_length=1, max_length=64)
    duration_ns: DecimalString


class IssueDerivedContext(_Model):
    schema_version: Literal[1] = 1
    dataset_id: DatasetId
    base_version_id: DatasetVersionId
    episode_id: EpisodeId
    base_revision_id: EpisodeRevisionId
    selected_stream_id: EpisodeStreamId
    selected_channel_path: str | None = Field(default=None, max_length=512)
    start_ns: DecimalString
    end_ns: DecimalString
    manual_issue_ids: tuple[ManualIssueId, ...] = Field(min_length=1, max_length=1)

    @model_validator(mode="after")
    def _range_is_half_open(self) -> IssueDerivedContext:
        if int(self.start_ns) >= int(self.end_ns):
            raise ValueError("ManualIssue handoff range must be non-empty and half-open")
        return self


class ReviewReturnLineage(_Model):
    lineage_type: Literal["REVIEW_RETURN"] = "REVIEW_RETURN"
    supersedes_draft_id: CleaningDraftId
    returned_from_version_id: DatasetVersionId
    returned_from_review_decision_id: ReviewDecisionId


class IssueDerivedOrigin(_Model):
    origin_type: Literal["ISSUE_DERIVED"] = "ISSUE_DERIVED"
    manual_issue_context: IssueDerivedContext
    review_return_lineage: None = None


class ReviewReturnOrigin(_Model):
    origin_type: Literal["REVIEW_RETURN"] = "REVIEW_RETURN"
    manual_issue_context: None = None
    review_return_lineage: ReviewReturnLineage


CleaningDraftOrigin: TypeAlias = Annotated[
    IssueDerivedOrigin | ReviewReturnOrigin,
    Field(discriminator="origin_type"),
]


class ReviewReturnSummary(_Model):
    review_decision_id: ReviewDecisionId
    review_finding_ids: tuple[ReviewFindingId, ...] = Field(min_length=1)
    finding_count: DecimalString
    successor_draft_id: CleaningDraftId
    supersedes_draft_id: CleaningDraftId
    returned_from_version_id: DatasetVersionId
    returned_from_review_decision_id: ReviewDecisionId
    output_version_status: Literal["RETURNED"] = "RETURNED"

    @model_validator(mode="after")
    def _lineage_is_consistent(self) -> ReviewReturnSummary:
        if self.review_decision_id != self.returned_from_review_decision_id:
            raise ValueError("Review return must retain the immutable decision identity")
        if int(self.finding_count) != len(self.review_finding_ids):
            raise ValueError("Review finding count must match its immutable IDs")
        if self.successor_draft_id == self.supersedes_draft_id:
            raise ValueError("Review return successor must be a new Draft")
        return self


class CleaningDraft(_Model):
    draft_id: CleaningDraftId
    etag: Etag
    status: CleaningDraftStatus
    origin: CleaningDraftOrigin
    base_version_id: DatasetVersionId
    base_revision_id: EpisodeRevisionId
    episode_id: EpisodeId
    manual_issue_count: Literal["0", "1"]
    preview_status: PreviewStatus
    commit_status: CommitStatus
    output_version_status: OutputVersionStatus | None = None
    review_decision_id: ReviewDecisionId | None = None
    successor_draft_id: CleaningDraftId | None = None
    review_finding_count: DecimalString = "0"
    review_summary: ReviewReturnSummary | None = None
    allowed_actions: tuple[AllowedAction, ...] = ()
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _projection_is_consistent(self) -> CleaningDraft:
        issue_derived = isinstance(self.origin, IssueDerivedOrigin)
        if (self.manual_issue_count == "1") != issue_derived:
            raise ValueError("manual_issue_count must match Draft origin")
        returned = self.output_version_status == "RETURNED"
        return_values = (self.review_decision_id, self.successor_draft_id, self.review_summary)
        if returned:
            if any(value is None for value in return_values) or self.review_finding_count == "0":
                raise ValueError("RETURNED Draft requires its immutable review facts")
        elif any(value is not None for value in return_values) or self.review_finding_count != "0":
            raise ValueError("only RETURNED Drafts may contain return-review facts")
        if self.updated_at < self.created_at:
            raise ValueError("Draft updated_at must not precede created_at")
        return self


class CleaningOperationBase(_Model):
    id: Identifier
    sequence_no: int = Field(ge=0)
    enabled: bool
    schema_version: Literal["1"] = "1"


class TrimOperation(CleaningOperationBase):
    type: Literal["TRIM"] = "TRIM"
    start_ns: DecimalString
    end_ns: DecimalString


class ExcludeRangeOperation(CleaningOperationBase):
    type: Literal["EXCLUDE_RANGE"] = "EXCLUDE_RANGE"
    start_ns: DecimalString
    end_ns: DecimalString
    reason: str | None = Field(default=None, max_length=2048)


class SplitOperation(CleaningOperationBase):
    type: Literal["SPLIT"] = "SPLIT"
    at_ns: DecimalString


class TimeOffsetOperation(CleaningOperationBase):
    type: Literal["TIME_OFFSET"] = "TIME_OFFSET"
    episode_stream_id: EpisodeStreamId
    offset_ns: SignedDecimalString
    scope: Literal["EPISODE"] = "EPISODE"
    reference_stream_id: EpisodeStreamId | None = None


class DisableChannelOperation(CleaningOperationBase):
    type: Literal["DISABLE_CHANNEL"] = "DISABLE_CHANNEL"
    episode_stream_id: EpisodeStreamId


class SetMetadataOperation(CleaningOperationBase):
    type: Literal["SET_METADATA"] = "SET_METADATA"
    patch: dict[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")], str | bool | None]


class InvalidateEpisodeOperation(CleaningOperationBase):
    type: Literal["INVALIDATE_EPISODE"] = "INVALIDATE_EPISODE"
    reason_code: str = Field(min_length=1, max_length=96, pattern=r"^[A-Z][A-Z0-9_:-]*$")
    note: str | None = Field(default=None, max_length=2048)


class InvalidMaskOperation(CleaningOperationBase):
    type: Literal["INVALID_MASK"] = "INVALID_MASK"
    episode_stream_id: EpisodeStreamId | None = None
    start_ns: DecimalString
    end_ns: DecimalString
    reason_code: str | None = Field(default=None, max_length=96)


CleaningOperation: TypeAlias = Annotated[
    TrimOperation
    | ExcludeRangeOperation
    | SplitOperation
    | TimeOffsetOperation
    | DisableChannelOperation
    | SetMetadataOperation
    | InvalidateEpisodeOperation
    | InvalidMaskOperation,
    Field(discriminator="type"),
]


class CleaningValidationIssue(_Model):
    code: str = Field(pattern=r"^[A-Z0-9_:-]+$", min_length=1, max_length=128)
    severity: Literal["BLOCKER", "WARNING", "INFO"]
    operation_id: Identifier | None = None
    json_pointer: str | None = Field(default=None, pattern=r"^/[^\s]*$")
    message: str = Field(min_length=1, max_length=2048)


class CleaningValidation(_Model):
    status: Literal["PASSED", "FAILED"]
    issues: tuple[CleaningValidationIssue, ...] = ()
    validated_edl_revision: DecimalString
    validated_operation_hash: Sha256Signature


class CleaningSummary(_Model):
    source_duration_ns: DecimalString
    trimmed_domain_duration_ns: DecimalString
    excluded_union_duration_ns: DecimalString
    invalid_mask_union_duration_ns: DecimalString
    output_duration_ns: DecimalString
    output_segment_count: DecimalString
    disabled_stream_count: DecimalString
    reused_source_bytes: DecimalString
    new_derived_bytes: DecimalString
    reuse_rate: str = Field(pattern=r"^(0|1|0\.[0-9]+)$")
    requires_materialization: bool
    estimate_status: Literal["ESTIMATED", "CONFIRMED", "COMPUTING", "FAILED"]
    calculated_at: datetime


class CleaningEdl(_Model):
    edl_revision: DecimalString
    etag: Etag
    operation_hash: Sha256Signature
    operations: tuple[CleaningOperation, ...] = ()
    validation: CleaningValidation
    summary: CleaningSummary
    updated_at: datetime

    @model_validator(mode="after")
    def _identity_is_consistent(self) -> CleaningEdl:
        if self.validation.validated_edl_revision != self.edl_revision:
            raise ValueError("EDL validation revision must match the EDL")
        if self.validation.validated_operation_hash != self.operation_hash:
            raise ValueError("EDL validation hash must match the EDL")
        sequence_numbers = tuple(operation.sequence_no for operation in self.operations)
        if sequence_numbers != tuple(range(len(self.operations))):
            raise ValueError("EDL operation sequence numbers must be contiguous")
        ids = tuple(operation.id for operation in self.operations)
        if len(set(ids)) != len(ids):
            raise ValueError("EDL operation identifiers must be unique")
        return self


class SourceToOutputMapSegment(_Model):
    source_start_ns: DecimalString
    source_end_ns: DecimalString
    output_revision_id: EpisodeRevisionId
    output_start_ns: DecimalString
    output_end_ns: DecimalString

    @model_validator(mode="after")
    def _ranges_are_half_open(self) -> SourceToOutputMapSegment:
        if int(self.source_start_ns) >= int(self.source_end_ns):
            raise ValueError("source mapping range must be non-empty and half-open")
        if int(self.output_start_ns) >= int(self.output_end_ns):
            raise ValueError("output mapping range must be non-empty and half-open")
        return self


class SourceToOutputMap(_Model):
    segments: tuple[SourceToOutputMapSegment, ...] = Field(min_length=1)
    mapping_version: DecimalString


class ViewerManifest(_Model):
    manifest_id: Identifier
    manifest_hash: Sha256Signature
    expires_at: datetime
    streams: tuple[EpisodeStreamId, ...] = Field(min_length=1)


class CleaningPreviewIdentity(_Model):
    preview_id: PreviewId
    draft_id: CleaningDraftId
    base_revision_id: EpisodeRevisionId
    edl_revision: DecimalString
    operation_hash: Sha256Signature
    job_id: JobId
    created_at: datetime


class PreviewQueued(CleaningPreviewIdentity):
    status: Literal["QUEUED"] = "QUEUED"
    expires_at: None = None


class PreviewRunning(CleaningPreviewIdentity):
    status: Literal["RUNNING"] = "RUNNING"
    expires_at: None = None


class PreviewReady(CleaningPreviewIdentity):
    status: Literal["READY"] = "READY"
    viewer_manifest: ViewerManifest
    source_to_output_map: SourceToOutputMap
    validation: CleaningValidation
    summary: CleaningSummary
    expires_at: datetime


class PreviewFailed(CleaningPreviewIdentity):
    status: Literal["FAILED"] = "FAILED"
    error_ref: Identifier
    expires_at: None = None


class PreviewExpiredOrStale(CleaningPreviewIdentity):
    status: Literal["EXPIRED", "STALE"]
    expires_at: datetime | None = None


CleaningPreview: TypeAlias = Annotated[
    PreviewQueued | PreviewRunning | PreviewReady | PreviewFailed | PreviewExpiredOrStale,
    Field(discriminator="status"),
]


class CleaningOutputRevision(_Model):
    revision_id: EpisodeRevisionId
    ordinal: int = Field(ge=0)
    episode_id: EpisodeId
    episode_stream_ids: tuple[EpisodeStreamId, ...] = Field(min_length=1)
    source_revision_id: EpisodeRevisionId
    member_mode: Literal["EDIT_RESULT", "CARRY_FORWARD"]


class CleaningOutputVersion(_Model):
    version_id: DatasetVersionId
    status: OutputVersionStatus
    draft_id: CleaningDraftId
    commit_id: CommitId


class CleaningCommitIdentity(_Model):
    commit_id: CommitId
    draft_id: CleaningDraftId
    preview_id: PreviewId
    job_id: JobId
    created_at: datetime
    successor_composition_hash: Sha256Signature | None = None


class CommitQueued(CleaningCommitIdentity):
    status: Literal["QUEUED"] = "QUEUED"
    output_revisions: tuple[()] = ()
    output_version: None = None
    materialization_status: Literal["NOT_STARTED", "RUNNING"]


class CommitSucceeded(CleaningCommitIdentity):
    status: Literal["SUCCEEDED"] = "SUCCEEDED"
    completed_at: datetime
    output_revisions: tuple[CleaningOutputRevision, ...] = Field(min_length=1)
    output_version: CleaningOutputVersion
    materialization_status: Literal["NOT_STARTED", "RUNNING", "SUCCEEDED", "FAILED"]
    operation_hash: Sha256Signature


class CommitFailed(CleaningCommitIdentity):
    status: Literal["FAILED"] = "FAILED"
    error_ref: Identifier
    output_revisions: tuple[()] = ()
    output_version: None = None
    materialization_status: Literal["NOT_STARTED", "FAILED"]


CleaningCommit: TypeAlias = Annotated[
    CommitQueued | CommitSucceeded | CommitFailed,
    Field(discriminator="status"),
]


class ReviewFindingProjection(_Model):
    id: ReviewFindingId
    output_revision_id: EpisodeRevisionId
    episode_stream_id: EpisodeStreamId
    start_ns: DecimalString
    end_ns: DecimalString
    finding_type: str = Field(pattern=r"^[A-Z][A-Z0-9_:-]*$", min_length=1, max_length=96)
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    note: str = Field(min_length=1, max_length=8192)
    immutable: Literal[True] = True
    created_at: datetime

    @model_validator(mode="after")
    def _range_is_half_open(self) -> ReviewFindingProjection:
        if int(self.start_ns) >= int(self.end_ns):
            raise ValueError("Review finding range must be non-empty and half-open")
        return self


class ImmutableReviewDecision(_Model):
    id: ReviewDecisionId
    output_version_id: DatasetVersionId
    decision: Literal["RETURNED"] = "RETURNED"
    immutable: Literal[True] = True
    created_at: datetime


class ReviewReturnFeedback(_Model):
    review_decision: ImmutableReviewDecision
    output_version: CleaningOutputVersion
    output_version_id: DatasetVersionId
    output_version_status: Literal["RETURNED"] = "RETURNED"
    findings: tuple[ReviewFindingProjection, ...] = Field(min_length=1)
    review_finding_ids: tuple[ReviewFindingId, ...] = Field(min_length=1)
    successor_draft_id: CleaningDraftId
    supersedes_draft_id: CleaningDraftId
    returned_from_version_id: DatasetVersionId
    returned_from_review_decision_id: ReviewDecisionId

    @model_validator(mode="after")
    def _facts_are_immutable_and_consistent(self) -> ReviewReturnFeedback:
        if (
            self.review_decision.id != self.returned_from_review_decision_id
            or self.review_decision.output_version_id != self.returned_from_version_id
            or self.output_version_id != self.returned_from_version_id
            or self.output_version.version_id != self.returned_from_version_id
            or self.output_version.status != "RETURNED"
        ):
            raise ValueError("Review return lineage is inconsistent")
        if self.successor_draft_id == self.supersedes_draft_id:
            raise ValueError("Review return successor must be a new Draft")
        finding_ids = tuple(finding.id for finding in self.findings)
        if finding_ids != self.review_finding_ids:
            raise ValueError("Review finding IDs must preserve the immutable sequence")
        return self


class ReviewSuccessorCompositionMember(_Model):
    source_revision_id: EpisodeRevisionId
    source_ordinal: int = Field(ge=0)
    handling: Literal["EDITABLE_BASE", "CARRY_FORWARD"]


class ReviewSuccessorComposition(_Model):
    schema_version: Literal[1] = 1
    source_version_id: DatasetVersionId
    editable_base_revision_id: EpisodeRevisionId
    members: tuple[ReviewSuccessorCompositionMember, ...] = Field(min_length=1)
    composition_hash: Sha256Signature

    @model_validator(mode="after")
    def _has_one_editable_member(self) -> ReviewSuccessorComposition:
        editable = tuple(member for member in self.members if member.handling == "EDITABLE_BASE")
        if len(editable) != 1 or editable[0].source_revision_id != self.editable_base_revision_id:
            raise ValueError("Review successor composition needs exactly one editable base")
        return self


class CooperativeLease(_Model):
    session_id: Identifier
    draft_id: CleaningDraftId
    etag: Etag
    lease_revision: DecimalString
    holder_summary: None = None
    expires_at: datetime
    read_only: bool


class CleaningDraftBase(_Model):
    dataset_id: DatasetId
    version_id: DatasetVersionId
    episode_id: EpisodeId
    revision_id: EpisodeRevisionId
    schema_snapshot_id: Identifier
    robot_model_version_id: Identifier | None = None
    calibration_set_id: Identifier | None = None


class CleaningDraftBootstrapData(_Model):
    draft: CleaningDraft
    origin: CleaningDraftOrigin
    base: CleaningDraftBase
    streams: tuple[CleaningStream, ...] = Field(min_length=1)
    edl: CleaningEdl
    successor_composition: ReviewSuccessorComposition | None = None
    active_preview: CleaningPreview | None = None
    active_commit: CleaningCommit | None = None
    review_feedback: ReviewReturnFeedback | None = None
    lease: CooperativeLease | None = None
    allowed_actions: tuple[AllowedAction, ...] = ()

    @model_validator(mode="after")
    def _bootstrap_matches_draft(self) -> CleaningDraftBootstrapData:
        if self.origin != self.draft.origin:
            raise ValueError("Bootstrap origin must match the Draft")
        if (
            self.base.version_id != self.draft.base_version_id
            or self.base.revision_id != self.draft.base_revision_id
            or self.base.episode_id != self.draft.episode_id
        ):
            raise ValueError("Bootstrap base identity must match the Draft")
        review_successor = isinstance(self.origin, ReviewReturnOrigin)
        if review_successor != (self.successor_composition is not None):
            raise ValueError("Only a review-return Draft may have a successor composition")
        return self


class CleaningDraftBootstrapEnvelope(_Model):
    data: CleaningDraftBootstrapData
    scope: CleaningWorkbenchScope
    request_id: Identifier
    contract_version: Literal["manual-cleaning.v1"] = "manual-cleaning.v1"


class SaveCleaningEdlCommand(_Model):
    expected_edl_revision: DecimalString
    expected_operation_hash: Sha256Signature | None = None
    operations: tuple[CleaningOperation, ...]
    client_mutation_id: Identifier


class SaveCleaningEdlData(_Model):
    draft: CleaningDraft
    edl: CleaningEdl
    request_id: Identifier


class SaveCleaningEdlEnvelope(_Model):
    data: SaveCleaningEdlData
    scope: CleaningWorkbenchScope
    request_id: Identifier
    contract_version: Literal["manual-cleaning.v1"] = "manual-cleaning.v1"


class CreateCleaningPreviewCommand(_Model):
    base_revision_id: EpisodeRevisionId
    edl_revision: DecimalString
    operation_hash: Sha256Signature


class CommitAcknowledgement(_Model):
    reviewed_summary: Literal[True]
    compared_preview: Literal[True]


class CommitCleaningDraftCommand(_Model):
    preview_id: PreviewId
    base_revision_id: EpisodeRevisionId
    edl_revision: DecimalString
    operation_hash: Sha256Signature
    successor_composition_hash: Sha256Signature | None = None
    acknowledgement: CommitAcknowledgement


class CleaningAsyncJob(_Model):
    job_id: JobId
    kind: Literal["CLEANING_PREVIEW", "CLEANING_COMMIT"]
    status: Literal[
        "QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLING", "CANCELLED", "EXPIRED"
    ]
    stage: str = Field(min_length=1, max_length=128)
    progress: object | None = None
    result_ref: dict[str, str] | None = None
    error: object | None = None
    scope: CleaningWorkbenchScope
    resource_ref: dict[str, str | None]
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime
    expires_at: datetime | None = None
    etag: Etag
    cancellable: bool
    retry_of_job_id: JobId | None = None


class PreviewAcceptedEnvelope(_Model):
    preview: CleaningPreview
    job: CleaningAsyncJob
    scope: CleaningWorkbenchScope
    request_id: Identifier
    contract_version: Literal["manual-cleaning.v1"] = "manual-cleaning.v1"


class CommitAcceptedEnvelope(_Model):
    commit: CleaningCommit
    job: CleaningAsyncJob
    scope: CleaningWorkbenchScope
    request_id: Identifier
    contract_version: Literal["manual-cleaning.v1"] = "manual-cleaning.v1"


class ReviewFindingsData(_Model):
    feedback: ReviewReturnFeedback


class ReviewFindingsEnvelope(_Model):
    data: ReviewFindingsData
    scope: CleaningWorkbenchScope
    request_id: Identifier
    contract_version: Literal["manual-cleaning.v1"] = "manual-cleaning.v1"


class CleaningWorkbenchAuditEvent(_Model):
    project_id: Identifier
    region_code: str = Field(min_length=1, max_length=64)
    actor_id: str = Field(min_length=1, max_length=256)
    action: str = Field(min_length=1, max_length=128)
    resource_id: Identifier
    request_id: Identifier
    occurred_at: datetime
    before_hash: str | None = None
    after_hash: str | None = None
    details: dict[str, object] | None = None


class CleaningWorkbenchState(_Model):
    """Repository-only state used to build one complete server bootstrap."""

    scope: CleaningWorkbenchScope
    workbench_version: int = Field(gt=0)
    draft: CleaningDraft
    base: CleaningDraftBase
    streams: tuple[CleaningStream, ...] = Field(min_length=1)
    edl: CleaningEdl
    successor_composition: ReviewSuccessorComposition | None = None
    active_preview: CleaningPreview | None = None
    active_commit: CleaningCommit | None = None
    review_feedback: ReviewReturnFeedback | None = None
