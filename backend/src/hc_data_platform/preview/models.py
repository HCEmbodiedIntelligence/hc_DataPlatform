from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ViewMode(str, Enum):
    ORIGINAL = "original"
    EDITED = "edited"
    COMPARE = "compare"


class EncodingProfileV1(BaseModel):
    """Deterministic, cache-keyed constraints for temporary preview media."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(default="h264-cmaf-preview-v1", min_length=1)
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
    annotation_revision: int = Field(ge=0)
    camera_id: str = Field(min_length=1)
    view_mode: ViewMode
    frequency_hz: float = Field(default=30.0, gt=0, le=240)
    encoding_profile: EncodingProfileV1 = Field(default_factory=EncodingProfileV1)
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
    cache_key: str
    project_id: str
    dataset_id: str
    rollout_id: str
    lance_version: str
    annotation_revision: int
    camera_id: str
    view_mode: ViewMode
    encoding_profile: EncodingProfileV1
    playlist_url: str
    media_type: str
    frame_count: int = Field(ge=0)
    placeholder_count: int = Field(ge=0)
    placeholders: tuple[PlaceholderDescriptorV1, ...] = ()
    duration_seconds: float = Field(ge=0)
    timeline: TimelineMappingV1
    cache_expires_at: datetime
    signed_url_expires_at: datetime
