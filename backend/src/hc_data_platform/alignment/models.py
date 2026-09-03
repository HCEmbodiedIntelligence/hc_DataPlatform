"""Versioned contracts for aligned staging fragments."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModalityKind(str, Enum):
    IMAGE = "image"
    POINT_CLOUD = "point_cloud"
    CONTINUOUS = "continuous"
    ACTION = "action"
    DISCRETE = "discrete"
    IMU = "imu"
    FORCE = "force"


class AlignmentStrategy(str, Enum):
    NEAREST = "nearest"
    LINEAR = "linear"
    CAUSAL = "causal"
    RECENT = "recent"
    WINDOW_MEAN = "window_mean"


class TimedSampleV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp_ns: int = Field(ge=0)
    value: Any


class ModalityStreamV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: ModalityKind
    samples: tuple[TimedSampleV1, ...]

    @model_validator(mode="after")
    def require_strict_time_order(self) -> ModalityStreamV1:
        timestamps = [sample.timestamp_ns for sample in self.samples]
        if any(right <= left for left, right in zip(timestamps, timestamps[1:], strict=False)):
            raise ValueError("stream timestamps must be strictly increasing")
        return self


class AlignmentProfileV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["alignment-profile/v1"] = "alignment-profile/v1"
    profile_id: str = Field(min_length=1)
    converter_version: str = Field(default="be07-align/1", min_length=1)
    frequency_hz: int = Field(default=30, gt=0, le=1000)
    required_modalities: frozenset[str]
    stream_strategies: dict[str, AlignmentStrategy] = Field(default_factory=dict)
    stream_tolerance_ns: dict[str, int] = Field(default_factory=dict)
    default_tolerance_ns: int = Field(default=20_000_000, ge=0)

    @model_validator(mode="after")
    def validate_tolerances(self) -> AlignmentProfileV1:
        if any(value < 0 for value in self.stream_tolerance_ns.values()):
            raise ValueError("stream tolerance cannot be negative")
        configured_names = (
            self.required_modalities
            | self.stream_strategies.keys()
            | self.stream_tolerance_ns.keys()
        )
        if any(not name for name in configured_names):
            raise ValueError("modality names cannot be empty")
        return self


class AlignmentInputV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["alignment-input/v1"] = "alignment-input/v1"
    rollout_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt_id: str = Field(min_length=1)
    start_ns: int = Field(ge=0)
    end_ns: int = Field(gt=0)
    streams: dict[str, ModalityStreamV1]

    @model_validator(mode="after")
    def validate_window(self) -> AlignmentInputV1:
        if self.end_ns <= self.start_ns:
            raise ValueError("end_ns must be greater than start_ns")
        if not self.streams:
            raise ValueError("at least one modality stream is required")
        if any(not name for name in self.streams):
            raise ValueError("modality names cannot be empty")
        return self


class AlignedValueV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: Any = None
    source_timestamps_ns: tuple[int, ...] = ()
    time_error_ns: int | None = Field(default=None, ge=0)
    valid: bool
    repeated: bool = False
    strategy: AlignmentStrategy

    @model_validator(mode="after")
    def validate_provenance(self) -> AlignedValueV1:
        if any(timestamp < 0 for timestamp in self.source_timestamps_ns):
            raise ValueError("source timestamps cannot be negative")
        if self.valid:
            if self.value is None:
                raise ValueError("a valid aligned value cannot be None")
            if not self.source_timestamps_ns or self.time_error_ns is None:
                raise ValueError("a valid aligned value requires source provenance")
        elif self.value is not None:
            raise ValueError("an invalid aligned value must be None")
        if self.repeated and (not self.valid or not self.source_timestamps_ns):
            raise ValueError("only a valid sourced value can be repeated")
        return self


class AlignedRowV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    rollout_id: str = Field(min_length=1)
    step_index: int = Field(ge=0)
    timestamp_ns: int = Field(ge=0)
    modalities: dict[str, AlignedValueV1]
    sample_valid: bool


class AlignedFragmentManifestV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["aligned-fragment-manifest/v1"] = "aligned-fragment-manifest/v1"
    rollout_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    converter_version: str = Field(min_length=1)
    frequency_hz: int = Field(gt=0)
    row_count: int = Field(ge=0)
    schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    staging_uri: str = Field(min_length=1)
    staging_format: Literal["arrow-ipc/v1"] = "arrow-ipc/v1"


class AlignedFragmentReadyV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_version: Literal["aligned-fragment-ready/v1"] = "aligned-fragment-ready/v1"
    rollout_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    converter_version: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    staging_uri: str = Field(min_length=1)
