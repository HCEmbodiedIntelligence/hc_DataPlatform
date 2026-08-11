"""Pydantic v2 wire schemas for dataset/version/review operations.

The names and field spellings in this module mirror
``dataset-version-review-schemas.json``.  Domain services never accept ``latest``
or ``current`` as a version identifier.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.int64 import Int64Str


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=False)


Id = Annotated[str, Field(min_length=1, max_length=256)]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
ReviewToken = Annotated[str, Field(min_length=32, max_length=2048)]


class Scope(StrictModel):
    organization_id: Id
    project_id: Id
    region_code: Id


class ActorSummary(StrictModel):
    id: Id
    display_name: str


class BlockedReason(StrictModel):
    code: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{1,127}$")]
    message: Annotated[str, Field(min_length=1, max_length=1024)]
    resource_type: str | None = None
    resource_id: str | None = None


class AllowedAction(StrictModel):
    action: str
    allowed: bool
    blocked_reasons: list[BlockedReason]


class PageInfo(StrictModel):
    after: str | None
    before: str | None
    has_next: bool
    has_previous: bool
    limit: int | None = Field(default=None, ge=1, le=500)
    total_count: Int64Str | None = None


class CreateDatasetRequest(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=256)]
    description: Annotated[str, Field(max_length=4096)]
    labels: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=96)]], Field(max_length=64)
    ]

    @field_validator("labels")
    @classmethod
    def unique_labels(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("labels must be unique")
        return value


class DatasetVersionPair(StrictModel):
    dataset_id: Id
    version_id: Id

    @field_validator("version_id")
    @classmethod
    def exact_version_id(cls, value: str) -> str:
        if value.lower() in {"latest", "current"}:
            raise ValueError("an exact immutable version_id is required")
        return value


class CreateDatasetManifestListExportRequest(StrictModel):
    selections: Annotated[list[DatasetVersionPair], Field(min_length=1, max_length=500)]
    format: Literal["CSV", "JSONL"]

    @field_validator("selections")
    @classmethod
    def unique_selections(cls, value: list[DatasetVersionPair]) -> list[DatasetVersionPair]:
        identities = {(item.dataset_id, item.version_id) for item in value}
        if len(identities) != len(value):
            raise ValueError("selections must be unique")
        return value


class DiffJobRequest(StrictModel):
    compare_to: Id
    snapshot_token: Annotated[str, Field(min_length=16)]


class ManifestJobRequest(StrictModel):
    manifest_format_version: Annotated[str, Field(min_length=1, max_length=64)]


class DownloadAuthorizationRequest(StrictModel):
    purpose: Literal["VIEW", "EXPORT"]


class ReviewChecksRequest(StrictModel):
    expected_status: Literal["REVIEWING"]


class ReviewFindingInput(StrictModel):
    output_revision_id: Id
    episode_stream_id: Id
    start_ns: Int64Str
    end_ns: Int64Str
    finding_type: Annotated[str, Field(min_length=1, max_length=96, pattern=r"^[A-Z][A-Z0-9_:-]*$")]
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    note: Annotated[str, Field(min_length=1, max_length=8192)]

    @model_validator(mode="after")
    def half_open_range(self) -> ReviewFindingInput:
        if int(self.start_ns) < 0 or int(self.start_ns) >= int(self.end_ns):
            raise ValueError("range must be a non-empty half-open interval")
        return self


class ApproveReviewCommand(StrictModel):
    expected_status: Literal["REVIEWING"]
    review_token: ReviewToken


class ReturnReviewCommand(StrictModel):
    expected_status: Literal["REVIEWING"]
    review_token: ReviewToken
    finding_catalog_version: Annotated[str, Field(min_length=1, max_length=128)]
    findings: Annotated[list[ReviewFindingInput], Field(min_length=1, max_length=500)]


class DeletionPreflightRequest(StrictModel):
    intent: Literal["DELETE"]
    reason: Annotated[str, Field(min_length=8, max_length=2048)]
    expected_etag: str


class Dataset(StrictModel):
    scope: Scope
    dataset_id: Id
    name: str
    description: str
    labels: list[str]
    availability: Literal["ACTIVE", "FROZEN"]
    owner: ActorSummary
    created_at: datetime
    updated_at: datetime
    etag: str
    allowed_actions: list[AllowedAction]


class VersionIdentity(StrictModel):
    scope: Scope
    dataset_id: Id
    version_id: Id
    display_version: str
    kind: Literal["RAW", "CLEANED"]
    status: Literal["REVIEWING", "RETURNED", "READY"]
    created_at: datetime
    etag: str
    version_token: str

    @field_validator("version_id")
    @classmethod
    def immutable_id_only(cls, value: str) -> str:
        if value.lower() in {"latest", "current"}:
            raise ValueError("an exact immutable version_id is required")
        return value


class ReviewDecision(StrictModel):
    id: Id
    output_version_id: Id
    decision: Literal["APPROVED", "RETURNED"]
    immutable: Literal[True] = True
    created_at: datetime


class ReviewFinding(StrictModel):
    """Immutable P07 fact; deliberately has no ManualIssue lifecycle fields."""

    id: Id
    output_revision_id: Id
    episode_stream_id: Id
    start_ns: Int64Str
    end_ns: Int64Str
    finding_type: str
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    note: str
    immutable: Literal[True] = True
    created_at: datetime


class ReturnedVersionRef(StrictModel):
    id: Id
    status: Literal["RETURNED"]
    version_token: Annotated[str, Field(min_length=16)]


class ReturnReviewResult(StrictModel):
    scope: Scope
    review_decision: ReviewDecision
    findings: Annotated[list[ReviewFinding], Field(min_length=1)]
    review_finding_ids: Annotated[list[Id], Field(min_length=1)]
    output_version: ReturnedVersionRef
    successor_draft_id: Id
    supersedes_draft_id: Id
    returned_from_version_id: Id
    returned_from_review_decision_id: Id

    @model_validator(mode="after")
    def atomic_result_is_self_consistent(self) -> ReturnReviewResult:
        if self.review_finding_ids != [finding.id for finding in self.findings]:
            raise ValueError("review_finding_ids must match findings in order")
        if self.successor_draft_id == self.supersedes_draft_id:
            raise ValueError("successor draft must have a new ID")
        if not (
            self.output_version.id
            == self.returned_from_version_id
            == self.review_decision.output_version_id
        ):
            raise ValueError("returned Version lineage is inconsistent")
        if self.returned_from_review_decision_id != self.review_decision.id:
            raise ValueError("returned Decision lineage is inconsistent")
        return self


class DeletionCheck(StrictModel):
    check_type: str
    passed: bool
    blocked_reasons: list[BlockedReason]
    observed_policy_version: str | None = None
    retained_until: datetime | None = None


class AsyncImpact(StrictModel):
    object_count: Int64Str
    estimated_bytes: Int64Str
    dependent_projection_count: Int64Str
    requires_async_job: Literal[True] = True


class DeletionPreflightResult(StrictModel):
    scope: Scope
    resource_type: Literal["DATASET", "DATASET_VERSION"]
    resource_id: Id
    capability_status: Literal["RESERVED_CONDITIONAL"] = "RESERVED_CONDITIONAL"
    executable: Literal[False] = False
    domain_clear: bool
    preflight_token: ReviewToken
    expires_at: datetime
    checks: Annotated[list[DeletionCheck], Field(min_length=7, max_length=7)]
    async_impact: AsyncImpact
    blocked_reasons: list[BlockedReason]


class Envelope(StrictModel):
    data: Any
    scope: Scope
    request_id: str
    contract_version: str = "dataset-version-review.v1alpha1"


class PageEnvelope(StrictModel):
    items: list[Any]
    page_info: PageInfo
    snapshot_at: datetime
    snapshot_id: str
    scope: Scope
    request_id: str
    contract_version: str = "dataset-version-review.v1alpha1"
