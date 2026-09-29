"""Fixed-data-version detail contracts; mutable review pointers carry explicit identities."""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .models import DataVersionRef, Digest, FrozenModel
from .quality import EpisodeQualityView
from .review_models import EpisodeDataReview


class EpisodeAction(FrozenModel):
    action: str
    allowed: bool
    required_capabilities: tuple[str, ...]
    denied_reason: str | None


class EpisodeFile(FrozenModel):
    file_id: Digest
    path: str
    size: int
    sha256: Digest
    media_type: str
    category: Literal["NUMERIC", "RGB", "DEPTH", "ANNOTATION", "METADATA", "REPORT"]
    availability: Literal["AVAILABLE", "MISSING", "SIZE_MISMATCH"]
    integrity: Literal["MANIFEST_BOUND"] = "MANIFEST_BOUND"
    download: EpisodeAction


class EpisodeFilePage(FrozenModel):
    data: DataVersionRef
    items: tuple[EpisodeFile, ...]
    total: int
    next_after: str | None


class EpisodeFileGrant(FrozenModel):
    data: DataVersionRef
    file_id: Digest
    sha256: Digest
    size: int
    url: str
    expires_seconds: Literal[300] = 300


class EpisodeRevisionIdentity(FrozenModel):
    task_id: str
    revision: int
    content_hash: Digest
    author_id: str
    created_at: datetime


class EpisodeAnnotationState(FrozenModel):
    availability: Literal["AVAILABLE", "NOT_CREATED", "FORBIDDEN", "SELECTION_REQUIRED"]
    task_options: tuple[dict[str, Any], ...] = ()
    task_options_has_more: bool = False
    task_id: str | None = None
    status: str | None = None
    state_version: int | None = None
    current_revision: EpisodeRevisionIdentity | None = None
    latest_submission: dict[str, Any] | None = None
    latest_approval: dict[str, Any] | None = None
    latest_review: dict[str, Any] | None = None
    current_submission_id: str | None = None
    current_approved_review_id: str | None = None
    has_unsubmitted_changes: bool | None = None
    revision_files: tuple[dict[str, Any], ...] = ()


class EpisodeCameraDetail(FrozenModel):
    feature: str
    kind: Literal["RGB", "DEPTH"]
    shape: tuple[int, ...]
    video_file_id: Digest
    preview_status: Literal["NOT_GENERATED", "PREPARING", "READY", "FAILED", "INTEGRITY_FAILED"]
    preview_reason: str | None = None
    depth: dict[str, Any] | None = None


class EpisodeDetail(FrozenModel):
    data: DataVersionRef
    display_name: str
    display_name_origin: str
    task: dict[str, Any]
    robot_id: str
    recording_date: str
    duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    frequency_hz: float = Field(gt=0, allow_inf_nan=False)
    camera_stream_count: int
    physical_camera_count: int | None
    physical_camera_count_reason: str | None
    source: dict[str, Any]
    quality: EpisodeQualityView
    data_review_status: Literal["NOT_REVIEWED", "APPROVED", "RETURNED"]
    latest_data_review: EpisodeDataReview | None
    annotation: EpisodeAnnotationState
    subtasks: dict[str, Any]
    cameras: tuple[EpisodeCameraDetail, ...]
    files: EpisodeFilePage
    deliveries: tuple[dict[str, Any], ...]
    deliveries_has_more: bool
    package_status: Literal["COMMITTED"] = "COMMITTED"
    allowed_actions: tuple[EpisodeAction, ...]
    observed_at: datetime
