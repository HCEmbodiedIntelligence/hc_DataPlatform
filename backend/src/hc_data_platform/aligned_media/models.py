from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


class AlignedMediaScopeV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    organization_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    region_code: str = Field(min_length=1)


class AlignedMediaEncodingProfileV1(BaseModel):
    """Server-owned training-quality canonical media profile."""

    model_config = ConfigDict(frozen=True)

    profile_id: str = Field(default="canonical-h264-crf20-v1", min_length=1)
    profile_version: str = Field(default="1", min_length=1)
    codec: Literal["h264"] = "h264"
    pixel_format: Literal["yuv420p"] = "yuv420p"
    fps: Literal[30] = 30
    crf: int = Field(default=20, ge=18, le=22)
    preset: Literal["veryfast", "faster", "fast", "medium", "slow"] = "fast"
    gop_frames: int = Field(default=30, ge=15, le=120)
    resolution_policy: Literal["preserve", "fit"] = "preserve"
    max_width: int | None = Field(default=None, ge=16, le=8192)
    max_height: int | None = Field(default=None, ge=16, le=4320)

    @model_validator(mode="after")
    def validate_resolution_policy(self) -> AlignedMediaEncodingProfileV1:
        if self.resolution_policy == "fit" and (self.max_width is None or self.max_height is None):
            raise ValueError("fit profiles require max_width and max_height")
        if self.max_width is not None and self.max_width % 2:
            raise ValueError("max_width must be even")
        if self.max_height is not None and self.max_height % 2:
            raise ValueError("max_height must be even")
        return self


class AlignmentCameraStagingArtifactV1(BaseModel):
    """One camera-only Arrow shard consumed by exactly one media activity."""

    model_config = ConfigDict(frozen=True)

    object_key: str = Field(min_length=1, max_length=2048)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    row_count: int = Field(ge=1)


class AlignmentStagingArtifactV1(BaseModel):
    """Short-lived, immutable Arrow fragment shared across worker pods."""

    model_config = ConfigDict(frozen=True)

    object_key: str = Field(min_length=1, max_length=2048)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    row_count: int = Field(ge=1)
    alignment_version: str = Field(min_length=1, max_length=256)
    camera_shards: dict[str, AlignmentCameraStagingArtifactV1] = Field(default_factory=dict)
    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_expiry(self) -> AlignmentStagingArtifactV1:
        if self.expires_at <= self.created_at:
            raise ValueError("alignment staging must expire after creation")
        if any(
            not camera_id or shard.row_count != self.row_count
            for camera_id, shard in self.camera_shards.items()
        ):
            raise ValueError("camera staging shards must match the aligned row count")
        return self


class AlignedMediaGenerationRequestV1(BaseModel):
    """One camera encode bound to a not-yet-visible Dataset version."""

    model_config = ConfigDict(frozen=True)

    project_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    rollout_id: str = Field(min_length=1)
    expected_dataset_version: int = Field(ge=1)
    camera_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    alignment: AlignmentStagingArtifactV1
    profile_id: str = Field(default="canonical-h264-crf20-v1", min_length=1)


class AlignedMediaSelectorV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    rollout_id: str = Field(min_length=1)
    dataset_version: int = Field(ge=1)
    camera_id: str = Field(min_length=1)


class AlignedMediaArtifactStatus(str, Enum):
    GENERATING = "GENERATING"
    READY = "READY"
    ABANDONING = "ABANDONING"
    FAILED = "FAILED"


class AlignedMediaJobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AlignedMediaObjectV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str = Field(min_length=1)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    etag: str | None = None
    media_type: str = Field(min_length=1)


class AlignedMediaTimelineV1(BaseModel):
    """Compact exact mapping: step N -> frame N -> PTS N at time base 1/30."""

    model_config = ConfigDict(frozen=True)

    fps: Literal[30] = 30
    frame_count: int = Field(ge=0)
    first_step: int = Field(default=0, ge=0)
    pts_time_base_numerator: Literal[1] = 1
    pts_time_base_denominator: Literal[30] = 30
    start_timestamp_ns: int = Field(ge=0)

    @field_serializer("start_timestamp_ns", when_used="json")
    def serialize_start_timestamp_ns(self, value: int) -> str:
        """Preserve nanosecond precision for JavaScript API consumers."""

        return str(value)


class EncodedAlignedMediaV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    file_uri: str = Field(min_length=1)
    media_type: Literal["video/mp4"] = "video/mp4"
    frame_count: int = Field(ge=1)
    duration_seconds: float = Field(gt=0)
    width: int = Field(ge=2)
    height: int = Field(ge=2)
    fps: Literal[30] = 30
    placeholder_count: int = Field(ge=0)
    first_timestamp_ns: int = Field(ge=0)


class PublishedAlignedMediaV1(BaseModel):
    """Exact immutable object-store publication receipt."""

    model_config = ConfigDict(frozen=True)

    object_prefix: str = Field(min_length=1)
    media_object_key: str = Field(min_length=1)
    objects: tuple[AlignedMediaObjectV1, ...] = Field(min_length=2)
    total_bytes: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    publication_token: str = Field(min_length=1, max_length=128)
    intent_key: str = Field(min_length=1)
    etag: str | None = None


class AlignedMediaArtifactV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_id: str = Field(min_length=1)
    artifact_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope: AlignedMediaScopeV1
    dataset_id: str = Field(min_length=1)
    rollout_id: str = Field(min_length=1)
    dataset_version: int = Field(ge=1)
    camera_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    profile_version: str = Field(min_length=1)
    alignment_version: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: AlignedMediaArtifactStatus
    object_prefix: str | None = None
    media_object_key: str | None = None
    objects: tuple[AlignedMediaObjectV1, ...] = ()
    total_bytes: int = Field(default=0, ge=0)
    frame_count: int = Field(default=0, ge=0)
    duration_seconds: float = Field(default=0, ge=0)
    fps: Literal[30] = 30
    width: int | None = Field(default=None, ge=2)
    height: int | None = Field(default=None, ge=2)
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    publication_token: str | None = None
    timeline: AlignedMediaTimelineV1 | None = None
    placeholder_count: int = Field(default=0, ge=0)
    created_at: datetime
    ready_at: datetime | None = None
    commit_lease_expires_at: datetime | None = None
    commit_started_at: datetime | None = None
    dataset_committed_at: datetime | None = None
    retired_at: datetime | None = None
    deleted_at: datetime | None = None
    failure_code: str | None = None
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> AlignedMediaArtifactV1:
        if self.retired_at is not None and self.dataset_committed_at is None:
            raise ValueError("only committed aligned media can be retired")
        if self.deleted_at is not None and self.retired_at is None:
            raise ValueError("deleted aligned media must have a retirement marker")
        return self


class AlignedMediaJobV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    artifact_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope: AlignedMediaScopeV1
    status: AlignedMediaJobStatus
    attempt: int = Field(default=0, ge=0)
    progress: int = Field(default=0, ge=0, le=100)
    error_code: str | None = None
    owner_id: str | None = None
    lease_expires_at: datetime | None = None
    attempt_token: str | None = None
    request: AlignedMediaGenerationRequestV1
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AlignedMediaAuthorizationV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["aligned-media-authorization/v1"] = "aligned-media-authorization/v1"
    artifact_id: str
    artifact_key: str
    project_id: str
    dataset_id: str
    rollout_id: str
    dataset_version: int
    camera_id: str
    media_url: str
    content_type: Literal["video/mp4"] = "video/mp4"
    expires_at: datetime
    fps: Literal[30] = 30
    frame_count: int = Field(ge=1)
    duration_seconds: float = Field(gt=0)
    width: int = Field(ge=2)
    height: int = Field(ge=2)
    timeline: AlignedMediaTimelineV1
    alignment_version: str
    profile_id: str
    profile_version: str


class AlignedMediaFrameReferenceV1(BaseModel):
    """Camera value stored in Lance; it never contains JPEG/base64 payloads."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["aligned-media-frame-ref/v1"] = "aligned-media-frame-ref/v1"
    camera_id: str
    artifact_id: str
    object_key: str
    frame_index: int = Field(ge=0)
    pts: int = Field(ge=0)
    pts_time_base_numerator: Literal[1] = 1
    pts_time_base_denominator: Literal[30] = 30
    timestamp_ns: int = Field(ge=0)
    valid: bool
    placeholder: bool
    repeated: bool
    dropped: bool
    source_timestamps_ns: tuple[int, ...] = ()
    alignment_version: str

    @field_serializer("timestamp_ns", when_used="json")
    def serialize_timestamp_ns(self, value: int) -> str:
        return str(value)

    @field_serializer("source_timestamps_ns", when_used="json")
    def serialize_source_timestamps_ns(self, value: tuple[int, ...]) -> tuple[str, ...]:
        return tuple(str(item) for item in value)
