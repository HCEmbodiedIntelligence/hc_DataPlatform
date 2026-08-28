from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ViewMode(str, Enum):
    ORIGINAL = "original"
    EDITED = "edited"
    COMPARE = "compare"


class EncodingProfileV1(BaseModel):
    """Server-owned deterministic encoding profile.

    This model is deliberately absent from :class:`PreviewRequestV1`: callers select
    a small allowlisted ``profile_id`` and cannot manufacture arbitrary transcode
    variants by supplying encoder settings.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(default="annotation-h264-720p-v1", min_length=1)
    width: int = Field(default=1280, ge=16, le=4096)
    height: int = Field(default=720, ge=16, le=2160)
    video_codec: Literal["h264", "vp9"] = "h264"
    pixel_format: Literal["yuv420p"] = "yuv420p"
    video_bitrate_kbps: int = Field(default=2_000, ge=64, le=50_000)
    segment_duration_seconds: float = Field(default=2.0, ge=0.25, le=10)
    preset: Literal[
        "ultrafast",
        "superfast",
        "veryfast",
        "faster",
        "fast",
        "medium",
        "slow",
    ] = "veryfast"

    @model_validator(mode="after")
    def validate_dimensions(self) -> EncodingProfileV1:
        if self.width % 2 or self.height % 2:
            raise ValueError("preview dimensions must be even")
        return self


class StepRangeV1(BaseModel):
    """A half-open source step range: start_step <= step < end_step."""

    model_config = ConfigDict(frozen=True)

    start_step: int = Field(ge=0)
    end_step: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> StepRangeV1:
        if self.end_step <= self.start_step:
            raise ValueError("end_step must be greater than start_step")
        return self


class PreviewRequestV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    rollout_id: str = Field(min_length=1)
    lance_version: str = Field(min_length=1)
    annotation_revision: int = Field(default=0, ge=0)
    camera_id: str = Field(min_length=1)
    view_mode: ViewMode = ViewMode.ORIGINAL
    profile_id: str = Field(
        default="annotation-h264-720p-v1",
        min_length=1,
        max_length=128,
        pattern=r"^[a-z0-9][a-z0-9-]*$",
    )
    frequency_hz: float = Field(default=30.0, gt=0, le=240)
    start_step: int | None = Field(default=None, ge=0)
    end_step: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_window(self) -> PreviewRequestV1:
        if (
            self.start_step is not None
            and self.end_step is not None
            and self.end_step <= self.start_step
        ):
            raise ValueError("end_step must be greater than start_step")
        return self


class PreviewFrameV1(BaseModel):
    """A logical frame returned by the Lance-backed StepReaderPort."""

    model_config = ConfigDict(frozen=True)

    rollout_id: str
    step_index: int = Field(ge=0)
    timestamp_ns: int = Field(ge=0)
    source_timestamp_ns: int | None = Field(default=None, ge=0)
    image_ref: str | bytes | None = None
    valid: bool = True
    invalid_reason: str | None = None


class PlaceholderDescriptorV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str = "INVALID_IMAGE_STEP"
    invalid_reason: str = Field(min_length=1)
    playback_frame: int = Field(ge=0)
    step_index: int = Field(ge=0)


class RenderFrameV1(BaseModel):
    """Encoder input. A placeholder is explicit and can never look like real data."""

    model_config = ConfigDict(frozen=True)

    playback_frame: int = Field(ge=0)
    step_index: int = Field(ge=0)
    timestamp_ns: int = Field(ge=0)
    source_timestamp_ns: int | None = Field(default=None, ge=0)
    image_ref: str | bytes | None = None
    excluded: bool = False
    placeholder: PlaceholderDescriptorV1 | None = None


class TimelineSegmentV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    playback_start_seconds: float = Field(ge=0)
    playback_end_seconds: float = Field(gt=0)
    source_start_step: int = Field(ge=0)
    source_end_step: int = Field(gt=0)
    excluded: bool = False


class TimelineMappingV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    frequency_hz: float = Field(gt=0)
    segments: tuple[TimelineSegmentV1, ...] = ()


class EncodedPreviewArtifactV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_uri: str
    media_type: str = "application/vnd.apple.mpegurl"
    duration_seconds: float = Field(ge=0)
    frame_count: int = Field(ge=0)


class PreviewArtifactStatus(str, Enum):
    GENERATING = "GENERATING"
    READY = "READY"
    FAILED = "FAILED"
    DELETING = "DELETING"


class PreviewJobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PreviewScopeV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    organization_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    region_code: str = Field(min_length=1)


class PreviewObjectV1(BaseModel):
    """One exact immutable member of an artifact; used for verification and GC."""

    model_config = ConfigDict(frozen=True)

    key: str = Field(min_length=1)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    etag: str | None = None
    media_type: str = Field(min_length=1)


class PreviewArtifactV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_id: str = Field(min_length=1)
    artifact_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope: PreviewScopeV1
    dataset_id: str = Field(min_length=1)
    rollout_id: str = Field(min_length=1)
    lance_version: str = Field(min_length=1)
    camera_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    pipeline_revision: str = Field(min_length=1)
    source_start_step: int | None = Field(default=None, ge=0)
    source_end_step: int | None = Field(default=None, gt=0)
    status: PreviewArtifactStatus
    object_prefix: str | None = None
    playlist_key: str | None = None
    objects: tuple[PreviewObjectV1, ...] = ()
    total_bytes: int = Field(default=0, ge=0)
    frame_count: int = Field(default=0, ge=0)
    duration_seconds: float = Field(default=0, ge=0)
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    rebuild_source_id: str = Field(min_length=1)
    created_at: datetime
    ready_at: datetime | None = None
    last_accessed_at: datetime
    expires_at: datetime
    failure_code: str | None = None
    version: int = Field(default=1, ge=1)
    active_reference_count: int = Field(default=0, ge=0)
    legal_hold: bool = False
    governance_hold: bool = False
    retention_until: datetime | None = None


class PreviewJobV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    artifact_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope: PreviewScopeV1
    status: PreviewJobStatus
    attempt: int = Field(default=0, ge=0)
    progress: int = Field(default=0, ge=0, le=100)
    error_code: str | None = None
    request: PreviewRequestV1
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class PreviewSessionV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    artifact_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope: PreviewScopeV1
    request: PreviewRequestV1
    created_at: datetime
    expires_at: datetime


class PreviewCacheRecordV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    cache_key: str
    session_id: str
    request: PreviewRequestV1
    artifact: EncodedPreviewArtifactV1
    timeline: TimelineMappingV1
    placeholders: tuple[PlaceholderDescriptorV1, ...] = ()
    placeholder_count: int = Field(ge=0)
    created_at: datetime
    cache_expires_at: datetime


class PreviewDescriptorV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    session_id: str
    artifact_key: str
    project_id: str
    dataset_id: str
    rollout_id: str
    lance_version: str
    annotation_revision: int
    camera_id: str
    view_mode: ViewMode
    profile_id: str
    encoding_profile: EncodingProfileV1
    playlist_url: str
    media_type: str
    frame_count: int = Field(ge=0)
    placeholder_count: int = Field(ge=0)
    placeholders: tuple[PlaceholderDescriptorV1, ...] = ()
    duration_seconds: float = Field(ge=0)
    timeline: TimelineMappingV1
    artifact_expires_at: datetime
    signed_url_expires_at: datetime


class PreviewPendingDescriptorV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    status: Literal["QUEUED", "RUNNING", "FAILED"]
    artifact_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    job_id: str = Field(min_length=1)
    status_url: str = Field(min_length=1)
    retry_after_seconds: int = Field(default=2, ge=1, le=60)
    error_code: str | None = None


class PreviewJobDescriptorV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    job_id: str = Field(min_length=1)
    artifact_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: PreviewJobStatus
    progress: int = Field(ge=0, le=100)
    retry_after_seconds: int | None = Field(default=None, ge=1, le=60)
    error_code: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class PublishedPreviewArtifactV1(BaseModel):
    """Object-store publication receipt committed before PostgreSQL becomes READY."""

    model_config = ConfigDict(frozen=True)

    object_prefix: str = Field(min_length=1)
    playlist_key: str = Field(min_length=1)
    objects: tuple[PreviewObjectV1, ...] = Field(min_length=1)
    total_bytes: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    etag: str | None = None


class PreviewGcResultV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    deleted_artifacts: int = Field(ge=0)
    deleted_bytes: int = Field(ge=0)
    failed_artifacts: int = Field(ge=0)
    details: tuple[dict[str, Any], ...] = ()
