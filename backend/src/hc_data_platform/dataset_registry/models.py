"""P05 dataset-page wire and persistence models.

The page contract is deliberately distinct from the lower-level Lance catalog.  A
dataset may exist before it has an immutable Lance version, so this registry owns
the user-visible dataset identity and safe page projections.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
DraftId = Annotated[
    str,
    Field(pattern=r"^draft_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"),
]
ReviewDecisionId = Annotated[
    str,
    Field(pattern=r"^review_decision_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"),
]
ReviewFindingId = Annotated[
    str,
    Field(pattern=r"^review_finding_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"),
]
DecimalString = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]*)$")]


class _DatasetPageModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DatasetPageScope(_DatasetPageModel):
    organization_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    region_code: str = Field(min_length=1, max_length=64)


class DatasetPageActor(_DatasetPageModel):
    id: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=256)


class DatasetPageBlockedReason(_DatasetPageModel):
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=1000)


class DatasetPageAllowedAction(_DatasetPageModel):
    action: str = Field(min_length=1, max_length=128)
    allowed: bool
    blocked_reasons: tuple[DatasetPageBlockedReason, ...] = ()


class DatasetPageCurrentReadyVersion(_DatasetPageModel):
    version_id: DatasetVersionId
    display_version: str = Field(min_length=1, max_length=64)
    kind: str = Field(min_length=1, max_length=64)
    status: Literal["READY"] = "READY"
    published_at: datetime
    manifest_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")


class DatasetPageMetadata(_DatasetPageModel):
    """Safe filter facts; source/object locators never enter this projection."""

    robot_model_id: str | None = Field(default=None, min_length=1, max_length=256)
    robot_id: str | None = Field(default=None, min_length=1, max_length=256)
    task: str | None = Field(default=None, min_length=1, max_length=256)
    scene: str | None = Field(default=None, min_length=1, max_length=256)
    asset_state: str = Field(min_length=1, max_length=64)
    storage_class: str = Field(min_length=1, max_length=64)
    channels: tuple[str, ...] = ()


class DatasetPageRecord(_DatasetPageModel):
    """Internal durable record from which P05 wire projections are derived."""

    scope: DatasetPageScope
    dataset_id: DatasetId
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(max_length=4096)
    labels: tuple[str, ...] = ()
    availability: str = Field(min_length=1, max_length=64)
    owner: DatasetPageActor
    created_at: datetime
    updated_at: datetime
    activity_at: datetime
    etag: str = Field(min_length=3, max_length=256)
    metadata: DatasetPageMetadata
    episode_count: DecimalString = "0"
    pending_review_version_count: DecimalString = "0"
    returned_version_count: DecimalString = "0"
    actionable_draft_count: DecimalString = "0"
    current_ready_version: DatasetPageCurrentReadyVersion | None = None


class DatasetPageDataset(_DatasetPageModel):
    scope: DatasetPageScope
    dataset_id: DatasetId
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(max_length=4096)
    labels: tuple[str, ...] = ()
    availability: str = Field(min_length=1, max_length=64)
    owner: DatasetPageActor
    created_at: datetime
    updated_at: datetime
    etag: str = Field(min_length=3, max_length=256)
    allowed_actions: tuple[DatasetPageAllowedAction, ...]


class DatasetPageListItem(_DatasetPageModel):
    scope: DatasetPageScope
    dataset_id: DatasetId
    name: str = Field(min_length=1, max_length=256)
    dataset_created_at: datetime
    dataset_activity_at: datetime
    current_version: DatasetPageCurrentReadyVersion | None = None
    episode_count: DecimalString
    pending_review_version_count: DecimalString
    returned_version_count: DecimalString
    actionable_draft_count: DecimalString
    allowed_actions: tuple[DatasetPageAllowedAction, ...]


class DatasetPageInfo(_DatasetPageModel):
    after: str | None = None
    before: str | None = None
    has_next: bool
    has_previous: bool
    limit: int | None = Field(default=None, ge=1, le=500)
    total_count: DecimalString | None = None


class DatasetPageListEnvelope(_DatasetPageModel):
    items: tuple[DatasetPageListItem, ...]
    page_info: DatasetPageInfo
    snapshot_at: datetime
    snapshot_id: str = Field(min_length=1, max_length=256)
    scope: DatasetPageScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: Literal["dataset-version-review.v1alpha1"] = "dataset-version-review.v1alpha1"


class DatasetPageSummary(_DatasetPageModel):
    scope: DatasetPageScope
    dataset_count: DecimalString
    episode_count: DecimalString
    pending_review_version_count: DecimalString
    returned_version_count: DecimalString
    actionable_draft_count: DecimalString
    normalized_filters: dict[str, object]


class DatasetPageFacetValue(_DatasetPageModel):
    value: str = Field(min_length=1, max_length=256)
    count: DecimalString


class DatasetPageFacets(_DatasetPageModel):
    scope: DatasetPageScope
    normalized_filters: dict[str, object]
    robots: tuple[DatasetPageFacetValue, ...] = ()
    robot_models: tuple[DatasetPageFacetValue, ...] = ()
    tasks: tuple[DatasetPageFacetValue, ...] = ()
    scenes: tuple[DatasetPageFacetValue, ...] = ()
    asset_states: tuple[DatasetPageFacetValue, ...] = ()
    storage_classes: tuple[DatasetPageFacetValue, ...] = ()
    channels: tuple[DatasetPageFacetValue, ...] = ()


class DatasetPageCapabilities(_DatasetPageModel):
    scope: DatasetPageScope
    authorization_revision: str = Field(min_length=1, max_length=128)
    allowed_actions: tuple[str, ...] = ()
    blocked_reasons: tuple[DatasetPageBlockedReason, ...] = ()


class DatasetPageMeta(_DatasetPageModel):
    request_id: str = Field(min_length=1, max_length=128)
    trace_id: str = Field(min_length=1, max_length=128)
    correlation_id: str = Field(min_length=1, max_length=128)
    generated_at: datetime
    as_of: datetime | None = None
    projection_version: str = "p05-dataset-registry/v1"
    event_cursor: str | None = None


class DatasetPageDatasetEnvelope(_DatasetPageModel):
    data: DatasetPageDataset
    meta: DatasetPageMeta


class DatasetPageSummaryEnvelope(_DatasetPageModel):
    data: DatasetPageSummary
    meta: DatasetPageMeta


class DatasetPageFacetsEnvelope(_DatasetPageModel):
    data: DatasetPageFacets
    meta: DatasetPageMeta


class DatasetPageCapabilitiesEnvelope(_DatasetPageModel):
    data: DatasetPageCapabilities
    meta: DatasetPageMeta


class CreateDatasetCommand(_DatasetPageModel):
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(max_length=4096)
    labels: tuple[str, ...] = Field(default=(), max_length=64)


class DatasetPageMutationRecord(_DatasetPageModel):
    dataset: DatasetPageRecord


# P06 keeps the immutable, version-pinned facts alongside P05's user-visible
# Dataset identity.  These types deliberately mirror the strict browser wire
# contract: data source locators, credentials, and raw storage paths are not
# representable here.
class DatasetPageContentReference(_DatasetPageModel):
    reference_type: str = Field(min_length=1, max_length=128)
    reference_id: str = Field(min_length=1, max_length=256)
    reference_version: str = Field(min_length=1, max_length=256)
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")


class DatasetPageManifestSummary(_DatasetPageModel):
    manifest_id: str = Field(min_length=1, max_length=256)
    format_version: str = Field(min_length=1, max_length=128)
    canonicalization: str = Field(min_length=1, max_length=256)
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    entry_count: DecimalString


class DatasetPageRevisionSnapshotReference(_DatasetPageModel):
    episode_id: EpisodeId
    revision_id: EpisodeRevisionId
    ordinal: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")


class DatasetPageContentSnapshot(_DatasetPageModel):
    content_snapshot_id: str = Field(min_length=1, max_length=256)
    content_snapshot_hash: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    revision_refs: tuple[DatasetPageRevisionSnapshotReference, ...] = ()
    schema_ref: DatasetPageContentReference
    robot_model_refs: tuple[DatasetPageContentReference, ...] = ()
    calibration_refs: tuple[DatasetPageContentReference, ...] = ()
    source_manifest_refs: tuple[DatasetPageContentReference, ...] = ()


class DatasetPageVersionIdentity(_DatasetPageModel):
    scope: DatasetPageScope
    dataset_id: DatasetId
    version_id: DatasetVersionId
    display_version: str = Field(min_length=1, max_length=64)
    kind: Literal["RAW", "CLEANED"]
    created_at: datetime
    etag: str = Field(min_length=3, max_length=256)
    version_token: str = Field(min_length=16, max_length=256)
    allowed_actions: tuple[DatasetPageAllowedAction, ...] = ()


class DatasetPageReviewingVersion(DatasetPageVersionIdentity):
    status: Literal["REVIEWING"] = "REVIEWING"
    source_draft_id: DraftId
    delivery_status: str = Field(min_length=1, max_length=64)
    approved_review_decision_id: ReviewDecisionId | None = None


class DatasetPageReturnedVersion(DatasetPageVersionIdentity):
    status: Literal["RETURNED"] = "RETURNED"
    source_draft_id: DraftId
    review_decision_id: ReviewDecisionId
    review_finding_ids: tuple[ReviewFindingId, ...] = Field(min_length=1)
    successor_draft_id: DraftId
    supersedes_draft_id: DraftId
    returned_from_version_id: DatasetVersionId
    returned_from_review_decision_id: ReviewDecisionId


class DatasetPageReadyVersion(DatasetPageVersionIdentity):
    status: Literal["READY"] = "READY"
    published_at: datetime
    content_snapshot: DatasetPageContentSnapshot
    manifest: DatasetPageManifestSummary
    approved_review_decision_id: ReviewDecisionId | None = None


DatasetPageVersion = Annotated[
    DatasetPageReviewingVersion | DatasetPageReturnedVersion | DatasetPageReadyVersion,
    Field(discriminator="status"),
]


class DatasetPageDetailSummary(_DatasetPageModel):
    episode_count: DecimalString
    effective_duration_ns: DecimalString
    source_bytes: DecimalString
    required_physical_bytes: DecimalString
    actual_oss_bytes: DecimalString | None = None
    pending_review_version_count: DecimalString
    returned_version_count: DecimalString
    actionable_draft_count: DecimalString
    calculated_at: datetime
    calculation_state: Literal["CALCULATING", "PARTIAL", "SETTLED", "FAILED"]


class DatasetPageDetailFacts(_DatasetPageModel):
    """Durable, explicitly calculated P06 summary facts for one Dataset."""

    scope: DatasetPageScope
    dataset_id: DatasetId
    summary: DatasetPageDetailSummary


class DatasetPageBootstrapData(_DatasetPageModel):
    scope: DatasetPageScope
    dataset: DatasetPageDataset
    current_ready_version: DatasetPageCurrentReadyVersion | None = None
    suggested_version_id: DatasetVersionId | None = None
    summary: DatasetPageDetailSummary


class DatasetPageBootstrapEnvelope(_DatasetPageModel):
    data: DatasetPageBootstrapData
    meta: DatasetPageMeta


class DatasetPageVersionListEnvelope(_DatasetPageModel):
    items: tuple[DatasetPageVersion, ...]
    page_info: DatasetPageInfo
    snapshot_at: datetime
    snapshot_id: str = Field(min_length=1, max_length=256)
    scope: DatasetPageScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: Literal["dataset-version-review.v1alpha1"] = "dataset-version-review.v1alpha1"


class DatasetPageVersionSchemaSummary(_DatasetPageModel):
    scope: DatasetPageScope
    dataset_id: DatasetId
    version_id: DatasetVersionId
    schema_snapshot: DatasetPageContentReference
    channel_count: DecimalString | None = None


class DatasetPageVersionSchemaSummaryEnvelope(_DatasetPageModel):
    data: DatasetPageVersionSchemaSummary
    meta: DatasetPageMeta


class DatasetPageSourceProvenance(_DatasetPageModel):
    scope: DatasetPageScope
    dataset_id: DatasetId
    version_id: DatasetVersionId
    provenance_id: str = Field(min_length=1, max_length=128)
    upload_id: str = Field(min_length=1, max_length=128)
    source_id: str | None = Field(default=None, min_length=1, max_length=128)
    source_display_name: str | None = Field(default=None, min_length=1, max_length=256)
    source_manifest_id: str = Field(min_length=1, max_length=128)
    source_manifest_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    verified_object_set_hash: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    registered_at: datetime


class DatasetPageSourceProvenanceListEnvelope(_DatasetPageModel):
    items: tuple[DatasetPageSourceProvenance, ...]
    page_info: DatasetPageInfo
    snapshot_at: datetime
    snapshot_id: str = Field(min_length=1, max_length=256)
    scope: DatasetPageScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: Literal["dataset-version-review.v1alpha1"] = "dataset-version-review.v1alpha1"


class DatasetPageVersionCapacityFacts(_DatasetPageModel):
    scope: DatasetPageScope
    dataset_id: DatasetId
    version_id: DatasetVersionId
    state: Literal["CALCULATING", "PARTIAL", "SETTLED", "FAILED"]
    source_bytes: DecimalString | None = None
    required_physical_bytes: DecimalString | None = None
    actual_oss_bytes: DecimalString | None = None
    calculated_at: datetime
    basis_revision: str = Field(min_length=1, max_length=256)


class DatasetPageVersionCapacityEnvelope(_DatasetPageModel):
    data: DatasetPageVersionCapacityFacts
    meta: DatasetPageMeta


class DatasetPageEpisodeListItem(_DatasetPageModel):
    scope: DatasetPageScope
    dataset_id: DatasetId
    version_id: DatasetVersionId
    episode_id: EpisodeId
    selected_revision: DatasetPageRevisionSnapshotReference
    included: bool
    success_state: str = Field(min_length=1, max_length=64)
    task: str | None = Field(default=None, max_length=256)
    robot_id: str | None = Field(default=None, max_length=256)
    review_status: str | None = Field(default=None, max_length=64)
    review_finding_count: DecimalString | None = None


class DatasetPageEpisodeRecord(DatasetPageEpisodeListItem):
    """Stored filtering/sort facts that are intentionally absent from the public row."""

    started_at: datetime | None = None
    started_at_ns: DecimalString = "0"
    has_finding: bool = False
    change_type: str | None = Field(default=None, max_length=128)


class DatasetPageEpisodeListEnvelope(_DatasetPageModel):
    items: tuple[DatasetPageEpisodeListItem, ...]
    page_info: DatasetPageInfo
    snapshot_at: datetime
    snapshot_id: str = Field(min_length=1, max_length=256)
    scope: DatasetPageScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: Literal["dataset-version-review.v1alpha1"] = "dataset-version-review.v1alpha1"


# P07 owns fixed-version delivery, review, and inspection facts.  These are
# deliberately separate from the P06 overview projections: every detail read
# is pinned to an immutable content snapshot or an explicit operational
# revision, and no storage locator, credential, or raw object path can be
# represented by these wire models.
EpisodeStreamId = Annotated[
    str,
    Field(pattern=r"^stream_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"),
]


class DatasetPageVersionBootstrapData(_DatasetPageModel):
    scope: DatasetPageScope
    dataset_id: DatasetId
    version_id: DatasetVersionId
    snapshot_token: str = Field(min_length=16, max_length=2048)
    operational_revision: str = Field(min_length=1, max_length=256)
    version: DatasetPageVersion


class DatasetPageVersionBootstrapEnvelope(_DatasetPageModel):
    data: DatasetPageVersionBootstrapData
    meta: DatasetPageMeta


class DatasetPageEpisodeStream(_DatasetPageModel):
    episode_stream_id: EpisodeStreamId
    channel_path: str = Field(min_length=1, max_length=512)
    kind: str = Field(min_length=1, max_length=64)
    t_start_ns: DecimalString
    t_end_ns: DecimalString
    preview_binding: DatasetPageEpisodePreviewBinding | None = None
    data_binding: DatasetPageEpisodeDataBinding | None = None

    @model_validator(mode="after")
    def validate_preview_binding_kind(self) -> DatasetPageEpisodeStream:
        if self.preview_binding is not None and self.kind.strip().upper() not in {
            "VIDEO",
            "RGB",
            "RGB_VIDEO",
            "DEPTH",
        }:
            raise ValueError("preview_binding is only valid for RGB or depth camera streams")
        non_camera_kinds = {
            "POINTCLOUD",
            "JOINT_STATE",
            "ACTION",
            "FORCE",
            "POSE",
            "IMU",
            "TACTILE",
            "EVENT",
        }
        kind = self.kind.strip().upper()
        if self.data_binding is not None and kind not in non_camera_kinds:
            raise ValueError("data_binding is only valid for supported non-camera streams")
        if self.data_binding is not None and kind == "POINTCLOUD":
            if self.data_binding.value_kind != "POINTCLOUD_XYZ":
                raise ValueError("pointcloud data_binding must use POINTCLOUD_XYZ")
        elif self.data_binding is not None and kind == "EVENT":
            if self.data_binding.value_kind != "EVENT":
                raise ValueError("event data_binding must use EVENT")
        elif self.data_binding is not None and self.data_binding.value_kind not in {
            "SCALAR",
            "VECTOR",
        }:
            raise ValueError("numeric stream data_binding must use SCALAR or VECTOR")
        return self


class DatasetPageEpisodePreviewBinding(_DatasetPageModel):
    """Immutable, safe link from one collection stream to aligned preview data.

    A dataset-page revision deliberately never stores a physical Lance URI, object
    key, credential, or signed media URL.  This binding carries only the logical
    lineage the browser needs to ask the preview service for a short-lived HLS
    capability.  It is optional so historic revisions remain readable, but a
    camera stream is *not* browser-playable unless its projection supplies one.
    """

    rollout_id: str = Field(min_length=1, max_length=256)
    lance_version: int = Field(ge=1)
    annotation_revision: int = Field(ge=0)
    camera_id: str = Field(min_length=1, max_length=256)
    frequency_hz: float = Field(gt=0, le=240)
    start_step: int = Field(ge=0, le=9_007_199_254_740_991)
    end_step: int = Field(gt=0, le=9_007_199_254_740_991)

    @model_validator(mode="after")
    def validate_window(self) -> DatasetPageEpisodePreviewBinding:
        if self.end_step <= self.start_step:
            raise ValueError("end_step must be greater than start_step")
        return self


class DatasetPageEpisodeDataBinding(_DatasetPageModel):
    """Immutable logical lineage for a non-camera Viewer stream.

    This is intentionally narrower than a Lance row: it carries the committed
    catalog version, rollout, modality name and declared value shape, never a
    physical table/object location or arbitrary expression.  The browser can
    therefore request only the bounded step interval promised by its immutable
    episode revision.
    """

    rollout_id: str = Field(min_length=1, max_length=256)
    lance_version: int = Field(ge=1)
    modality_key: str = Field(min_length=1, max_length=512)
    value_kind: Literal["SCALAR", "VECTOR", "POINTCLOUD_XYZ", "EVENT"]
    start_step: int = Field(ge=0, le=9_007_199_254_740_991)
    end_step: int = Field(gt=0, le=9_007_199_254_740_991)

    @model_validator(mode="after")
    def validate_window(self) -> DatasetPageEpisodeDataBinding:
        if self.end_step <= self.start_step:
            raise ValueError("end_step must be greater than start_step")
        return self


class DatasetPageEpisodeRevision(_DatasetPageModel):
    scope: DatasetPageScope
    dataset_id: DatasetId
    version_id: DatasetVersionId
    episode_id: EpisodeId
    revision_id: EpisodeRevisionId
    ordinal: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    started_at_ns: DecimalString
    duration_ns: DecimalString
    streams: tuple[DatasetPageEpisodeStream, ...] = Field(min_length=1)


class DatasetPageEpisodeRevisionEnvelope(_DatasetPageModel):
    data: DatasetPageEpisodeRevision
    meta: DatasetPageMeta


class DatasetPageEpisodeRevisionHistoryItem(_DatasetPageModel):
    """One immutable Episode selection as it appeared in a Dataset version.

    A revision is never overwritten. Restoring a historical selection must
    therefore create a later candidate version; this read model lets a caller
    inspect the exact historical version before initiating that workflow.
    """

    scope: DatasetPageScope
    dataset_id: DatasetId
    episode_id: EpisodeId
    version_id: DatasetVersionId
    display_version: str = Field(min_length=1, max_length=64)
    version_kind: Literal["RAW", "CLEANED"]
    version_status: Literal["REVIEWING", "RETURNED", "READY"]
    version_created_at: datetime
    version_published_at: datetime | None = None
    selected_revision: DatasetPageRevisionSnapshotReference

    @model_validator(mode="after")
    def validate_selected_revision(self) -> DatasetPageEpisodeRevisionHistoryItem:
        if self.selected_revision.episode_id != self.episode_id:
            raise ValueError("selected revision must belong to the history Episode")
        return self


class DatasetPageEpisodeRevisionHistoryEnvelope(_DatasetPageModel):
    items: tuple[DatasetPageEpisodeRevisionHistoryItem, ...]
    page_info: DatasetPageInfo
    snapshot_at: datetime
    snapshot_id: str = Field(min_length=1, max_length=256)
    scope: DatasetPageScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: Literal["dataset-version-review.v1alpha1"] = "dataset-version-review.v1alpha1"


class DatasetIngestViewerTarget(_DatasetPageModel):
    """Compact stable route identity returned after an ingest projection commits."""

    schema_version: Literal["dataset-ingest-viewer-target/v1"] = "dataset-ingest-viewer-target/v1"
    dataset_id: DatasetId
    version_id: DatasetVersionId
    episode_id: EpisodeId
    revision_id: EpisodeRevisionId


class DatasetPageVersionSchemaChannel(_DatasetPageModel):
    channel_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    data_type: str = Field(min_length=1, max_length=128)
    unit: str | None = Field(default=None, max_length=128)


class DatasetPageVersionSchemaDetail(_DatasetPageModel):
    """Stored schema facts before the service binds a signed snapshot token."""

    scope: DatasetPageScope
    dataset_id: DatasetId
    version_id: DatasetVersionId
    schema_snapshot: DatasetPageContentReference
    channel_count: DecimalString
    channels: tuple[DatasetPageVersionSchemaChannel, ...] = ()


class DatasetPageVersionSchema(_DatasetPageModel):
    scope: DatasetPageScope
    dataset_id: DatasetId
    version_id: DatasetVersionId
    snapshot_token: str = Field(min_length=16, max_length=2048)
    schema_snapshot: DatasetPageContentReference
    channel_count: DecimalString
    channels: tuple[DatasetPageVersionSchemaChannel, ...] = ()


class DatasetPageVersionSchemaEnvelope(_DatasetPageModel):
    data: DatasetPageVersionSchema
    meta: DatasetPageMeta


class DatasetPageVersionContentProjection(_DatasetPageModel):
    """Immutable content facts used to derive a P07 snapshot token."""

    scope: DatasetPageScope
    dataset_id: DatasetId
    version_id: DatasetVersionId
    content_snapshot: DatasetPageContentSnapshot
    manifest: DatasetPageManifestSummary
    operational_revision: str = Field(min_length=1, max_length=256)


class DatasetPageManifestEntry(_DatasetPageModel):
    entry_id: str = Field(min_length=1, max_length=256)
    episode_id: EpisodeId
    revision_id: EpisodeRevisionId
    role: Literal["SOURCE", "REVISION", "INDEX", "METADATA"]
    size_bytes: DecimalString
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    safe_locator: str | None = Field(default=None, max_length=256)


class DatasetPageVersionManifestEntryRecord(_DatasetPageModel):
    """Scoped persistence wrapper; the browser manifest row intentionally has no scope."""

    scope: DatasetPageScope
    dataset_id: DatasetId
    version_id: DatasetVersionId
    entry: DatasetPageManifestEntry


class DatasetPageVersionManifestEnvelope(_DatasetPageModel):
    items: tuple[DatasetPageManifestEntry, ...]
    page_info: DatasetPageInfo
    snapshot_at: datetime
    snapshot_id: str = Field(min_length=1, max_length=256)
    scope: DatasetPageScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: Literal["dataset-version-review.v1alpha1"] = "dataset-version-review.v1alpha1"
    dataset_id: DatasetId
    version_id: DatasetVersionId
    content_snapshot_id: str = Field(min_length=1, max_length=256)
    manifest: DatasetPageManifestSummary


class DatasetPageRequiredStorageItem(_DatasetPageModel):
    scope: DatasetPageScope
    dataset_id: DatasetId
    version_id: DatasetVersionId
    object_id: str = Field(min_length=1, max_length=128)
    role: Literal["SOURCE", "REVISION", "INDEX", "METADATA", "PREVIEW", "EXPORT"]
    size_bytes: DecimalString
    reuse: Literal["NEW", "REUSED", "SHARED", "UNKNOWN"]
    protection: Literal["NONE", "RETENTION", "LEGAL_HOLD", "IMMUTABLE", "UNKNOWN"]
    safe_locator: str | None = Field(default=None, max_length=256)


class DatasetPageRequiredStorageEnvelope(_DatasetPageModel):
    items: tuple[DatasetPageRequiredStorageItem, ...]
    page_info: DatasetPageInfo
    snapshot_at: datetime
    snapshot_id: str = Field(min_length=1, max_length=256)
    scope: DatasetPageScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: Literal["dataset-version-review.v1alpha1"] = "dataset-version-review.v1alpha1"


class DatasetPageOperationalInventoryItem(_DatasetPageModel):
    scope: DatasetPageScope
    dataset_id: DatasetId
    version_id: DatasetVersionId
    inventory_id: str = Field(min_length=1, max_length=128)
    kind: Literal["PREVIEW", "EXPORT", "MATERIALIZATION"]
    operational_revision: str = Field(min_length=1, max_length=256)
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "EXPIRED", "STALE"]
    size_bytes: DecimalString
    job_id: str | None = Field(default=None, min_length=1, max_length=128)
    created_at: datetime
    completed_at: datetime | None = None


class DatasetPageOperationalInventoryEnvelope(_DatasetPageModel):
    items: tuple[DatasetPageOperationalInventoryItem, ...]
    page_info: DatasetPageInfo
    snapshot_at: datetime
    snapshot_id: str = Field(min_length=1, max_length=256)
    scope: DatasetPageScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: Literal["dataset-version-review.v1alpha1"] = "dataset-version-review.v1alpha1"


class DatasetPageReviewFindingType(_DatasetPageModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_:-]*$", max_length=96)
    label: str = Field(min_length=1, max_length=256)
    allowed_severities: tuple[Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"], ...] = Field(
        min_length=1
    )


class DatasetPageReviewSeverity(_DatasetPageModel):
    code: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    label: str = Field(min_length=1, max_length=256)
    rank: int = Field(gt=0)


class DatasetPageReviewFindingCatalog(_DatasetPageModel):
    version: str = Field(min_length=1, max_length=128)
    finding_types: tuple[DatasetPageReviewFindingType, ...] = Field(min_length=1)
    severities: tuple[DatasetPageReviewSeverity, ...] = Field(min_length=1)
    note_min_length: int = Field(gt=0)
    note_max_length: int = Field(gt=0)


class DatasetPageReviewCheckTarget(_DatasetPageModel):
    output_revision_id: EpisodeRevisionId
    episode_id: EpisodeId
    streams: tuple[DatasetPageEpisodeStream, ...] = Field(min_length=1)


class DatasetPageReviewChecksCommand(_DatasetPageModel):
    expected_status: Literal["REVIEWING"]


class DatasetPageReviewChecksData(_DatasetPageModel):
    scope: DatasetPageScope
    dataset_id: DatasetId
    output_version_id: DatasetVersionId
    source_draft_id: DraftId
    expected_status: Literal["REVIEWING"] = "REVIEWING"
    review_token: str = Field(min_length=32, max_length=2048)
    review_token_expires_at: datetime
    version_token: str = Field(min_length=16, max_length=256)
    blockers: tuple[DatasetPageBlockedReason, ...] = ()
    finding_catalog: DatasetPageReviewFindingCatalog
    eligible_targets: tuple[DatasetPageReviewCheckTarget, ...] = Field(min_length=1)


class DatasetPageReviewChecksEnvelope(_DatasetPageModel):
    data: DatasetPageReviewChecksData
    meta: DatasetPageMeta


class DatasetPageApproveReviewCommand(_DatasetPageModel):
    expected_status: Literal["REVIEWING"]
    review_token: str = Field(min_length=32, max_length=2048)


class DatasetPageReturnReviewFindingCommand(_DatasetPageModel):
    output_revision_id: EpisodeRevisionId
    episode_stream_id: EpisodeStreamId
    start_ns: DecimalString
    end_ns: DecimalString
    finding_type: str = Field(pattern=r"^[A-Z][A-Z0-9_:-]*$", max_length=96)
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    note: str = Field(min_length=1, max_length=8192)


class DatasetPageReturnReviewCommand(_DatasetPageModel):
    expected_status: Literal["REVIEWING"]
    review_token: str = Field(min_length=32, max_length=2048)
    finding_catalog_version: str = Field(min_length=1, max_length=128)
    findings: tuple[DatasetPageReturnReviewFindingCommand, ...] = Field(
        min_length=1, max_length=500
    )


class DatasetPageReviewDecision(_DatasetPageModel):
    id: ReviewDecisionId
    output_version_id: DatasetVersionId
    decision: Literal["APPROVED", "RETURNED"]
    immutable: Literal[True] = True
    created_at: datetime


class DatasetPageReviewFinding(_DatasetPageModel):
    id: ReviewFindingId
    output_revision_id: EpisodeRevisionId
    episode_stream_id: EpisodeStreamId
    start_ns: DecimalString
    end_ns: DecimalString
    finding_type: str = Field(pattern=r"^[A-Z][A-Z0-9_:-]*$", max_length=96)
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    note: str = Field(min_length=1, max_length=8192)
    immutable: Literal[True] = True
    created_at: datetime


class DatasetPageAsyncJob(_DatasetPageModel):
    job_id: str = Field(min_length=1, max_length=256)
    job_type: str = Field(min_length=1, max_length=128)
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]
    resource_type: str = Field(min_length=1, max_length=128)
    resource_id: str = Field(min_length=1, max_length=256)
    progress: dict[str, object] | None = None
    result_ref: dict[str, object] | None = None
    error: dict[str, object] | None = None
    created_at: datetime
    updated_at: datetime
    resource_version: DecimalString


class DatasetPageApproveReviewOutputVersion(_DatasetPageModel):
    id: DatasetVersionId
    status: Literal["REVIEWING"] = "REVIEWING"
    version_token: str = Field(min_length=16, max_length=256)


class DatasetPageApproveReviewMutationRecord(_DatasetPageModel):
    scope: DatasetPageScope
    review_decision: DatasetPageReviewDecision
    output_version: DatasetPageApproveReviewOutputVersion
    job: DatasetPageAsyncJob


class DatasetPageApproveReviewResult(_DatasetPageModel):
    review_decision: DatasetPageReviewDecision
    output_version: DatasetPageApproveReviewOutputVersion
    job: DatasetPageAsyncJob
    scope: DatasetPageScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: Literal["dataset-version-review.v1alpha1"] = "dataset-version-review.v1alpha1"


class DatasetPageReturnReviewMutationRecord(_DatasetPageModel):
    scope: DatasetPageScope
    review_decision: DatasetPageReviewDecision
    findings: tuple[DatasetPageReviewFinding, ...] = Field(min_length=1)
    review_finding_ids: tuple[ReviewFindingId, ...] = Field(min_length=1)
    output_version_id: DatasetVersionId
    version_token: str = Field(min_length=16, max_length=256)
    successor_draft_id: DraftId
    supersedes_draft_id: DraftId
    returned_from_version_id: DatasetVersionId
    returned_from_review_decision_id: ReviewDecisionId


class DatasetPageReturnReviewOutputVersion(_DatasetPageModel):
    id: DatasetVersionId
    status: Literal["RETURNED"] = "RETURNED"
    version_token: str = Field(min_length=16, max_length=256)


class DatasetPageReturnReviewData(_DatasetPageModel):
    scope: DatasetPageScope
    review_decision: DatasetPageReviewDecision
    findings: tuple[DatasetPageReviewFinding, ...] = Field(min_length=1)
    review_finding_ids: tuple[ReviewFindingId, ...] = Field(min_length=1)
    output_version: DatasetPageReturnReviewOutputVersion
    successor_draft_id: DraftId
    supersedes_draft_id: DraftId
    returned_from_version_id: DatasetVersionId
    returned_from_review_decision_id: ReviewDecisionId


class DatasetPageReturnReviewEnvelope(_DatasetPageModel):
    data: DatasetPageReturnReviewData
    meta: DatasetPageMeta


class DatasetPageDiffJobCommand(_DatasetPageModel):
    compare_to: DatasetVersionId
    snapshot_token: str = Field(min_length=16, max_length=2048)


class DatasetPageAsyncJobMutationRecord(_DatasetPageModel):
    scope: DatasetPageScope
    job: DatasetPageAsyncJob


class DatasetPageJobAccepted(_DatasetPageModel):
    job: DatasetPageAsyncJob
    scope: DatasetPageScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: Literal["dataset-version-review.v1alpha1"] = "dataset-version-review.v1alpha1"


class DatasetPageDeletionPreflightCommand(_DatasetPageModel):
    intent: Literal["DELETE"]
    reason: str = Field(min_length=1, max_length=4096)
    expected_etag: str = Field(min_length=3, max_length=256)


class DatasetPageDeletionCheck(_DatasetPageModel):
    check_type: Literal[
        "ACTIVE_REFERENCES",
        "RETENTION",
        "LEGAL_HOLD",
        "PERMISSION",
        "CONCURRENT_JOBS",
        "CURRENT_READY",
        "AUDIT_PROTECTION",
    ]
    passed: bool
    blocked_reasons: tuple[DatasetPageBlockedReason, ...] = ()
    observed_policy_version: str | None = Field(default=None, max_length=256)
    retained_until: datetime | None = None


class DatasetPageDeletionAsyncImpact(_DatasetPageModel):
    object_count: DecimalString
    estimated_bytes: DecimalString
    dependent_projection_count: DecimalString
    requires_async_job: Literal[True] = True


class DatasetPageDeletionPreflight(_DatasetPageModel):
    scope: DatasetPageScope
    resource_type: Literal["DATASET", "DATASET_VERSION"]
    resource_id: str = Field(min_length=1, max_length=256)
    capability_status: Literal["RESERVED_CONDITIONAL"] = "RESERVED_CONDITIONAL"
    executable: Literal[False] = False
    domain_clear: bool
    preflight_token: str = Field(min_length=32, max_length=2048)
    expires_at: datetime
    checks: tuple[DatasetPageDeletionCheck, ...] = Field(min_length=7, max_length=7)
    async_impact: DatasetPageDeletionAsyncImpact
    blocked_reasons: tuple[DatasetPageBlockedReason, ...] = ()
