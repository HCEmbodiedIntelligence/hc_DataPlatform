"""Strict Pydantic v2 schemas for manual cleaning."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.int64 import Int64Str


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Id = Annotated[str, Field(min_length=1, max_length=256)]
Sha256 = Annotated[str, Field(pattern=r"^(sha256:)?[a-f0-9]{64}$")]


class Scope(StrictModel):
    organization_id: Id
    project_id: Id
    region_code: Id


class FieldError(StrictModel):
    path: str
    code: str
    message: str


class BlockedReason(StrictModel):
    code: str
    message: str


type ManualIssueStatus = Literal["OPEN", "IN_PROGRESS", "RESOLVED"]
type ManualIssueSeverity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
type ManualIssueType = Literal[
    "POSE_JITTER",
    "TIMESTAMP_DRIFT",
    "MISSING_FRAME",
    "STREAM_GAP",
    "CALIBRATION_MISMATCH",
    "INVALID_MASK",
    "OTHER",
]


class CreateManualIssueRequest(StrictModel):
    origin_dataset_version_id: Id
    episode_id: Id
    episode_revision_id: Id
    episode_stream_id: Id
    start_ns: Int64Str
    end_ns: Int64Str
    issue_type: ManualIssueType
    severity: ManualIssueSeverity
    note: Annotated[str, Field(max_length=8192)]

    @model_validator(mode="after")
    def validate_range(self) -> CreateManualIssueRequest:
        if int(self.start_ns) < 0 or int(self.start_ns) >= int(self.end_ns):
            raise ValueError("range must be a non-empty half-open interval")
        return self


class TriageManualIssueRequest(StrictModel):
    target_status: Literal["OPEN", "IN_PROGRESS"]
    severity: ManualIssueSeverity
    assignee_id: Id | None
    reason: Annotated[str, Field(min_length=1, max_length=2048)]

    @model_validator(mode="after")
    def in_progress_has_assignee(self) -> TriageManualIssueRequest:
        if self.target_status == "IN_PROGRESS" and not self.assignee_id:
            raise ValueError("IN_PROGRESS requires assignee_id")
        return self


class ResolveManualIssueRequest(StrictModel):
    resolution_version_id: Id
    resolution_note: Annotated[str, Field(min_length=1, max_length=8192)]


class DraftFromIssueInitialRequest(StrictModel):
    """Initial Issue-to-Draft request; all source context is server-derived."""


class DraftFromIssueSelectionRequest(StrictModel):
    selection_token: Id
    candidate_draft_id: Id


type CreateDraftFromIssueRequest = DraftFromIssueInitialRequest | DraftFromIssueSelectionRequest


class CleaningOperationBase(StrictModel):
    id: Id
    sequence_no: int = Field(ge=0)
    enabled: bool
    schema_version: Literal["1"]


class TrimOperation(CleaningOperationBase):
    type: Literal["TRIM"]
    start_ns: Int64Str
    end_ns: Int64Str


class ExcludeRangeOperation(CleaningOperationBase):
    type: Literal["EXCLUDE_RANGE"]
    start_ns: Int64Str
    end_ns: Int64Str
    reason: Annotated[str, Field(max_length=2048)] | None


class SplitOperation(CleaningOperationBase):
    type: Literal["SPLIT"]
    at_ns: Int64Str


class TimeOffsetOperation(CleaningOperationBase):
    type: Literal["TIME_OFFSET"]
    episode_stream_id: Id
    offset_ns: Int64Str
    scope: Literal["EPISODE"]
    reference_stream_id: Id | None


class DisableChannelOperation(CleaningOperationBase):
    type: Literal["DISABLE_CHANNEL"]
    episode_stream_id: Id


class SetMetadataOperation(CleaningOperationBase):
    type: Literal["SET_METADATA"]
    patch: dict[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")], str | bool | None]


class InvalidateEpisodeOperation(CleaningOperationBase):
    type: Literal["INVALIDATE_EPISODE"]
    reason_code: Annotated[str, Field(min_length=1, max_length=96)]
    note: Annotated[str, Field(max_length=2048)] | None


class InvalidMaskOperation(CleaningOperationBase):
    type: Literal["INVALID_MASK"]
    episode_stream_id: Id | None
    start_ns: Int64Str
    end_ns: Int64Str
    reason_code: Annotated[str, Field(max_length=96)] | None


type CleaningOperation = Annotated[
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


class SaveCleaningEdlRequest(StrictModel):
    expected_edl_revision: Int64Str
    expected_operation_hash: Sha256 | None
    operations: list[CleaningOperation]
    client_mutation_id: Id

    @field_validator("operations")
    @classmethod
    def contiguous_and_unique(cls, value: list[CleaningOperation]) -> list[CleaningOperation]:
        if [op.sequence_no for op in value] != list(range(len(value))):
            raise ValueError("sequence_no must be contiguous from zero")
        if len({op.id for op in value}) != len(value):
            raise ValueError("operation IDs must be unique")
        return value


class CreateCleaningPreviewRequest(StrictModel):
    base_revision_id: Id
    edl_revision: Int64Str
    operation_hash: Sha256


class CommitCleaningDraftRequest(StrictModel):
    preview_id: Id
    base_revision_id: Id
    edl_revision: Int64Str
    operation_hash: Sha256
    successor_composition_hash: Sha256 | None
    acknowledgement: bool


class AcquireLeaseRequest(StrictModel):
    client_session_id: Id
    client_instance_id: Id


class RenewLeaseRequest(StrictModel):
    lease_revision: Int64Str


class TimeMappingSegment(StrictModel):
    source_start_ns: Int64Str
    source_end_ns: Int64Str
    output_revision_id: Id
    output_start_ns: Int64Str
    output_end_ns: Int64Str

    @model_validator(mode="after")
    def non_empty_ranges(self) -> TimeMappingSegment:
        if int(self.source_start_ns) >= int(self.source_end_ns):
            raise ValueError("source range must be non-empty")
        if int(self.output_start_ns) >= int(self.output_end_ns):
            raise ValueError("output range must be non-empty")
        return self


class SourceToOutputMap(StrictModel):
    segments: Annotated[list[TimeMappingSegment], Field(min_length=1)]
    mapping_version: Int64Str


class ManualIssue(StrictModel):
    id: Id
    etag: str
    scope: Scope
    dataset_id: Id
    origin_dataset_version_id: Id
    episode_id: Id
    episode_revision_id: Id
    episode_stream_id: Id
    context_status: str
    schema_snapshot_id: Id
    robot_model_version_id: Id | None
    calibration_set_id: Id | None
    start_ns: Int64Str
    end_ns: Int64Str
    issue_type: ManualIssueType
    severity: ManualIssueSeverity
    status: ManualIssueStatus
    note: str
    assignee: dict[str, Any] | None
    related_drafts: list[dict[str, Any]]
    resolution_version: dict[str, Any] | None
    resolution_note: str | None
    resolved_at: datetime | None
    resolved_by: dict[str, Any] | None
    allowed_actions: list[str]
    blocked_reasons: list[BlockedReason]
    created_at: datetime
    updated_at: datetime


class Envelope(StrictModel):
    data: Any
    scope: Scope
    request_id: str
    contract_version: str = "manual-cleaning.v1"


class DirectPageEnvelope(StrictModel):
    items: list[Any]
    page_info: dict[str, Any]
    snapshot_at: datetime
    scope: Scope
    request_id: str
    contract_version: str = "manual-cleaning.v1"
