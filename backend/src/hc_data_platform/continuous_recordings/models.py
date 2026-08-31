from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    StringConstraints,
    WithJsonSchema,
    model_validator,
)

from hc_data_platform.ingest.models import Identifier, Sha256, utc_now

EpisodeId = Annotated[
    str,
    StringConstraints(pattern=r"^episode_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"),
]


def _parse_nanoseconds(value: object) -> int:
    if isinstance(value, str):
        if not value.isascii() or not value.isdigit() or (len(value) > 1 and value.startswith("0")):
            raise ValueError("nanoseconds must be a canonical unsigned decimal string")
        parsed = int(value)
    elif isinstance(value, int) and not isinstance(value, bool):
        parsed = value
    else:
        raise ValueError("nanoseconds must be a canonical unsigned decimal string")
    if parsed < 0 or parsed > 2**63 - 1:
        raise ValueError("nanoseconds must fit in a signed 64-bit integer")
    return parsed


Nanoseconds = Annotated[
    int,
    BeforeValidator(_parse_nanoseconds),
    PlainSerializer(lambda value: str(value), return_type=str, when_used="json"),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": r"^(?:0|[1-9][0-9]*)$",
            "description": "Nanoseconds encoded as a decimal string for JSON precision.",
        }
    ),
]


class RecordingStatus(str, Enum):
    READY_FOR_SLICING = "READY_FOR_SLICING"
    SLICED = "SLICED"


class SliceRevisionStatus(str, Enum):
    DRAFT = "DRAFT"
    FINALIZED = "FINALIZED"


class SliceAuthoringMode(str, Enum):
    HUMAN = "HUMAN"
    MODEL = "MODEL"


class RecordingScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: Identifier
    project_id: Identifier
    region_code: Identifier


class ContinuousRecording(BaseModel):
    """One immutable, uninterrupted capture package uploaded as a whole."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["continuous-recording/v1", "continuous-recording/v2"] = (
        "continuous-recording/v1"
    )
    scope: RecordingScope
    recording_id: Identifier
    upload_session_id: UUID | None = None
    recording_upload_id: UUID | None = None
    rollout_id: Identifier
    data_package_id: Identifier
    collection_task_id: Identifier
    collection_job_id: Identifier
    robot_id: Identifier
    device_id: Identifier
    capture_started_at: AwareDatetime
    capture_ended_at: AwareDatetime
    duration_ns: Nanoseconds
    source_sha256: Sha256
    manifest_fingerprint: Sha256
    video_asset_count: int = Field(default=0, ge=0, le=128)
    status: RecordingStatus = RecordingStatus.READY_FOR_SLICING
    current_revision: int = Field(default=0, ge=0)
    finalized_revision: int | None = Field(default=None, ge=1)
    etag: str = Field(pattern=r'^"v[1-9][0-9]*"$')
    created_at: AwareDatetime = Field(default_factory=utc_now)
    updated_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_recording(self) -> ContinuousRecording:
        if self.capture_ended_at <= self.capture_started_at:
            raise ValueError("capture_ended_at must be after capture_started_at")
        expected = _datetime_ns(self.capture_ended_at) - _datetime_ns(self.capture_started_at)
        if self.duration_ns != expected:
            raise ValueError("duration_ns must exactly match the capture time range")
        if (self.status is RecordingStatus.SLICED) != (self.finalized_revision is not None):
            raise ValueError("only a sliced recording has a finalized revision")
        if (self.upload_session_id is None) == (self.recording_upload_id is None):
            raise ValueError("exactly one of upload_session_id and recording_upload_id is required")
        if self.schema_version == "continuous-recording/v1" and self.upload_session_id is None:
            raise ValueError("v1 recordings require upload_session_id")
        if self.schema_version == "continuous-recording/v2" and self.recording_upload_id is None:
            raise ValueError("v2 recordings require recording_upload_id")
        return self


class RegisterContinuousRecordingCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    upload_session_id: UUID


class EpisodeSliceInput(BaseModel):
    """A manual half-open interval in the source recording timeline."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_id: EpisodeId
    start_offset_ns: Nanoseconds
    end_offset_ns: Nanoseconds
    title: str | None = Field(default=None, min_length=1, max_length=256)
    task_label: str | None = Field(default=None, min_length=1, max_length=128)
    notes: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def validate_window(self) -> EpisodeSliceInput:
        if self.end_offset_ns <= self.start_offset_ns:
            raise ValueError("an episode slice must have a non-empty half-open interval")
        return self


class SaveSliceDraftCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slices: tuple[EpisodeSliceInput, ...] = Field(default=(), max_length=10_000)

    @model_validator(mode="after")
    def validate_slice_set(self) -> SaveSliceDraftCommand:
        episode_ids = [item.episode_id for item in self.slices]
        if len(episode_ids) != len(set(episode_ids)):
            raise ValueError("episode_id values must be unique within a slice revision")
        ordered = sorted(self.slices, key=lambda item: (item.start_offset_ns, item.end_offset_ns))
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if current.start_offset_ns < previous.end_offset_ns:
                raise ValueError("episode slices must not overlap")
        return self


class ModelSliceProposalMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str = Field(min_length=1, max_length=256)
    model_version: str = Field(min_length=1, max_length=256)
    prompt_version: str | None = Field(default=None, min_length=1, max_length=256)
    confidence: float | None = Field(default=None, ge=0, le=1)


class ProposeModelSlicesCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slices: tuple[EpisodeSliceInput, ...] = Field(min_length=1, max_length=10_000)
    model: ModelSliceProposalMetadata

    @model_validator(mode="after")
    def validate_slice_set(self) -> ProposeModelSlicesCommand:
        SaveSliceDraftCommand(slices=self.slices)
        return self


class FinalizeSliceCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_draft_revision: int = Field(ge=1)


class EpisodeSlice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["recording-episode-slice/v1"] = "recording-episode-slice/v1"
    scope: RecordingScope
    recording_id: Identifier
    revision: int = Field(ge=1)
    ordinal: int = Field(ge=0)
    episode_id: EpisodeId
    start_offset_ns: Nanoseconds
    end_offset_ns: Nanoseconds
    started_at: AwareDatetime
    ended_at: AwareDatetime
    title: str | None = Field(default=None, min_length=1, max_length=256)
    task_label: str | None = Field(default=None, min_length=1, max_length=128)
    notes: str | None = Field(default=None, max_length=4096)
    source_sha256: Sha256
    source_upload_session_id: UUID | None = None
    source_recording_upload_id: UUID | None = None

    @model_validator(mode="after")
    def validate_source(self) -> EpisodeSlice:
        if (self.source_upload_session_id is None) == (self.source_recording_upload_id is None):
            raise ValueError("an episode slice must identify exactly one source upload")
        return self


class RecordingSliceRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["recording-slice-revision/v1"] = "recording-slice-revision/v1"
    scope: RecordingScope
    recording_id: Identifier
    revision: int = Field(ge=1)
    status: SliceRevisionStatus
    slices: tuple[EpisodeSlice, ...]
    authoring_mode: SliceAuthoringMode = SliceAuthoringMode.HUMAN
    model: ModelSliceProposalMetadata | None = None
    created_by: str = Field(min_length=1, max_length=512)
    created_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_authoring(self) -> RecordingSliceRevision:
        if (self.authoring_mode is SliceAuthoringMode.MODEL) != (self.model is not None):
            raise ValueError("MODEL revisions require model metadata and HUMAN revisions forbid it")
        return self


class ContinuousRecordingPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ContinuousRecording, ...]
    total: int = Field(ge=0)


class ContinuousRecordingEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data: ContinuousRecording
    current_slice_revision: RecordingSliceRevision | None = None


class SliceRevisionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    recording: ContinuousRecording
    revision: RecordingSliceRevision


def recording_duration_ns(started_at: datetime, ended_at: datetime) -> int:
    return _datetime_ns(ended_at) - _datetime_ns(started_at)


def absolute_recording_time(started_at: datetime, offset_ns: int) -> datetime:
    return started_at.astimezone(timezone.utc) + timedelta(microseconds=offset_ns // 1000)


def _datetime_ns(value: datetime) -> int:
    utc = value.astimezone(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = utc - epoch
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000
