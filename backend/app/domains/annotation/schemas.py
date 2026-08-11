"""Strict Pydantic v2 schemas for data annotation."""

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


class Assignment(StrictModel):
    mode: Literal["UNASSIGNED", "CLAIMED", "DIRECT"]
    assignee_id: Id | None
    assigned_by: Id | None
    assigned_at: datetime | None


class TaskSourceBinding(StrictModel):
    dataset_id: Id
    dataset_version_id: Id
    episode_id: Id
    base_revision_id: Id
    stream_ids: Annotated[list[Id], Field(min_length=1)]
    start_ns: Int64Str
    end_ns: Int64Str

    @model_validator(mode="after")
    def half_open_range(self) -> TaskSourceBinding:
        if int(self.start_ns) < 0 or int(self.start_ns) >= int(self.end_ns):
            raise ValueError("source range must be a non-empty half-open interval")
        if len(self.stream_ids) != len(set(self.stream_ids)):
            raise ValueError("stream_ids must be unique")
        return self


class OntologyBinding(StrictModel):
    ontology_id: Id
    ontology_version: Id
    ontology_hash: Sha256


class TemporalRangeAnchor(StrictModel):
    anchor_type: Literal["TIME_RANGE"]
    coordinate_system: Literal["REVISION_NS"]
    start_ns: Int64Str
    end_ns: Int64Str

    @model_validator(mode="after")
    def half_open_range(self) -> TemporalRangeAnchor:
        if int(self.start_ns) < 0 or int(self.start_ns) >= int(self.end_ns):
            raise ValueError("anchor range must be a non-empty half-open interval")
        return self


class TimePointAnchor(StrictModel):
    anchor_type: Literal["TIME_POINT"]
    coordinate_system: Literal["REVISION_NS"]
    at_ns: Int64Str


class BoundingBox2D(StrictModel):
    geometry_type: Literal["BOUNDING_BOX_2D"]
    x: float
    y: float
    width: Annotated[float, Field(gt=0)]
    height: Annotated[float, Field(gt=0)]


class Point3D(StrictModel):
    geometry_type: Literal["POINT_3D"]
    frame_id: Id
    x: float
    y: float
    z: float


type ObjectGeometry = Annotated[BoundingBox2D | Point3D, Field(discriminator="geometry_type")]


class ObjectObservation(StrictModel):
    at_ns: Int64Str
    geometry: ObjectGeometry
    occluded: bool


class ObjectTrackAnchor(StrictModel):
    anchor_type: Literal["OBJECT_TRACK"]
    coordinate_system: Literal["REVISION_NS"]
    stream_id: Id
    start_ns: Int64Str
    end_ns: Int64Str
    observations: list[ObjectObservation]

    @model_validator(mode="after")
    def observations_are_ordered_and_bounded(self) -> ObjectTrackAnchor:
        start, end = int(self.start_ns), int(self.end_ns)
        points = [int(item.at_ns) for item in self.observations]
        if start < 0 or start >= end:
            raise ValueError("track range must be a non-empty half-open interval")
        if points != sorted(points) or len(points) != len(set(points)):
            raise ValueError("observations must be strictly monotonic")
        if any(point < start or point >= end for point in points):
            raise ValueError("observations must lie inside the track range")
        return self


type AnnotationAttributeValue = (
    str | int | float | bool | None | list[str] | list[int] | list[float]
)


class AnnotationEntryBase(StrictModel):
    annotation_id: Id
    label_code: Id
    attributes: dict[str, AnnotationAttributeValue]


class ActionAnnotation(AnnotationEntryBase):
    semantic_type: Literal["ACTION"]
    anchor: TemporalRangeAnchor


class PhaseAnnotation(AnnotationEntryBase):
    semantic_type: Literal["PHASE"]
    anchor: TemporalRangeAnchor


class ObjectAnnotation(AnnotationEntryBase):
    semantic_type: Literal["OBJECT"]
    anchor: ObjectTrackAnchor


class EventAnnotation(AnnotationEntryBase):
    semantic_type: Literal["EVENT"]
    anchor: TimePointAnchor


class KeyframeAnnotation(AnnotationEntryBase):
    semantic_type: Literal["KEYFRAME"]
    anchor: TimePointAnchor


type AnnotationEntry = Annotated[
    ActionAnnotation | PhaseAnnotation | ObjectAnnotation | EventAnnotation | KeyframeAnnotation,
    Field(discriminator="semantic_type"),
]


class CreateAnnotationTaskRequest(StrictModel):
    task_source: Literal["COVERAGE_GAP", "DIRECT_ASSIGNMENT", "CORRECTION"]
    source: TaskSourceBinding
    ontology: OntologyBinding
    priority: int = Field(ge=0, le=1000)
    assignee_id: Id | None
    correction_of_annotation_set_id: Id | None

    @model_validator(mode="after")
    def source_requirements(self) -> CreateAnnotationTaskRequest:
        if self.task_source == "DIRECT_ASSIGNMENT" and not self.assignee_id:
            raise ValueError("DIRECT_ASSIGNMENT requires assignee_id")
        if self.task_source == "CORRECTION" and not self.correction_of_annotation_set_id:
            raise ValueError("CORRECTION requires correction_of_annotation_set_id")
        return self


class MaterializeAnnotationTaskRequest(StrictModel):
    entry_resolution_token: Annotated[str, Field(min_length=32, max_length=4096)]
    client_session_id: Id


class ClaimAnnotationTaskRequest(StrictModel):
    client_session_id: Id


class AssignAnnotationTaskRequest(StrictModel):
    assignee_id: Id
    reason: Annotated[str, Field(min_length=1, max_length=2048)]


class SaveAnnotationDraftRequest(StrictModel):
    expected_draft_revision: int = Field(ge=0)
    client_mutation_id: Id
    entries: Annotated[list[AnnotationEntry], Field(max_length=100000)]

    @field_validator("entries")
    @classmethod
    def annotation_ids_are_unique(cls, value: list[AnnotationEntry]) -> list[AnnotationEntry]:
        ids = [entry.annotation_id for entry in value]
        if len(ids) != len(set(ids)):
            raise ValueError("annotation_id values must be unique")
        return value


class SubmitPreflightRequest(StrictModel):
    expected_draft_revision: int = Field(ge=0)
    expected_content_hash: Sha256


class SubmitAnnotationTaskRequest(StrictModel):
    expected_draft_revision: int = Field(ge=0)
    expected_content_hash: Sha256
    preflight_id: Id


class RebaseAnnotationTaskRequest(StrictModel):
    target_revision_id: Id
    reason: Annotated[str, Field(min_length=1, max_length=4096)]
    successor_assignee_id: Id | None


class ReviewAnnotationSubmissionRequest(StrictModel):
    decision: Literal["APPROVED", "RETURNED"]
    reason_codes: list[Annotated[str, Field(min_length=1, max_length=96)]]
    comment: Annotated[str, Field(max_length=4096)] | None

    @model_validator(mode="after")
    def returned_requires_reason(self) -> ReviewAnnotationSubmissionRequest:
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("reason_codes must be unique")
        if self.decision == "RETURNED" and not self.reason_codes:
            raise ValueError("RETURNED requires an actionable reason code")
        return self


class AnnotationResolvedContext(StrictModel):
    source: TaskSourceBinding
    ontology: OntologyBinding
    coverage_key_hash: Sha256


class AnnotationTaskEntryResolution(StrictModel):
    resolution_id: Id
    state: Literal["OPEN_EXISTING", "CLAIMABLE", "CAN_CREATE", "ASSIGNED_TO_OTHER", "FORBIDDEN"]
    resolved_context: AnnotationResolvedContext | None
    task_id: Id | None
    task_etag: str | None
    entry_resolution_token: str | None
    next_action: Literal["OPEN_TASK", "CLAIM_TASK", "CREATE_TASK", "NONE"]
    blocked_reason: str | None
    expires_at: datetime


class AnnotationTask(StrictModel):
    task_id: Id
    scope: Scope
    task_source: Literal["COVERAGE_GAP", "DIRECT_ASSIGNMENT", "CORRECTION", "REVISION_REBASE"]
    workflow_status: Literal[
        "QUEUED",
        "ASSIGNED",
        "IN_PROGRESS",
        "BLOCKED",
        "SUBMITTED",
        "RETURNED",
        "APPROVED",
        "CANCELLED",
    ]
    source_status: Literal["CURRENT", "STALE"]
    block_source: Literal["ADMINISTRATIVE", "DEPENDENCY"] | None
    block_reason: str | None
    source: TaskSourceBinding
    ontology: OntologyBinding
    assignment: Assignment
    priority: int
    current_draft_revision: int
    current_draft_hash: Sha256
    current_submission_id: Id | None
    submitted_annotation_set_id: Id | None
    correction_of_annotation_set_id: Id | None
    predecessor_task_id: Id | None
    successor_task_id: Id | None
    latest_review_id: Id | None
    created_at: datetime
    updated_at: datetime
    etag: str
    allowed_actions: list[str]
    action_reasons: list[dict[str, Any]]


class AnnotationDraft(StrictModel):
    task_id: Id
    draft_revision: int = Field(ge=0)
    state: Literal["ACTIVE", "FROZEN_SUBMITTED", "STALE_READ_ONLY"]
    entries: list[AnnotationEntry]
    content_hash: Sha256
    saved_by: Id
    saved_at: datetime
    etag: str


class Envelope(StrictModel):
    data: Any
    scope: Scope
    request_id: str
    contract_version: str = "data-annotation.v1"


class PageInfo(StrictModel):
    has_next_page: bool
    has_previous_page: bool
    start_cursor: str | None
    end_cursor: str | None


class DirectPageEnvelope(StrictModel):
    items: list[Any]
    page_info: PageInfo
    snapshot_id: str
    snapshot_at: datetime
    scope: Scope
    request_id: str
    contract_version: str = "data-annotation.v1"
