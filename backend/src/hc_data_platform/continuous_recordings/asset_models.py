from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_serializer,
    model_validator,
)

from hc_data_platform.episode_store.models import DataVersionRef
from hc_data_platform.ingest.models import (
    CompletedPart,
    Identifier,
    PartAuthorization,
    Sha256,
    utc_now,
)
from hc_data_platform.quality.evidence import RuleExecution
from hc_data_platform.quality.models import QcReportV1

from .models import EpisodeId, Nanoseconds, RecordingScope


class RecordingAssetRole(str, Enum):
    RAW_VIDEO = "RAW_VIDEO"
    SENSOR_DATA = "SENSOR_DATA"
    RECORDING_CONFIG = "RECORDING_CONFIG"
    CALIBRATION = "CALIBRATION"
    AUXILIARY = "AUXILIARY"


class RecordingAssetStatus(str, Enum):
    UPLOADING = "UPLOADING"
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"


class RecordingUploadStatus(str, Enum):
    UPLOADING = "UPLOADING"
    READY_TO_COMMIT = "READY_TO_COMMIT"
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"


class EpisodeProcessingStatus(str, Enum):
    PENDING_QC = "PENDING_QC"
    QC_RUNNING = "QC_RUNNING"
    QC_FAILED = "QC_FAILED"
    PENDING_ALIGNMENT = "PENDING_ALIGNMENT"
    ALIGNING = "ALIGNING"
    READY = "READY"
    FAILED = "FAILED"
    DISCARDED = "DISCARDED"


class SensorTimestampMode(str, Enum):
    ABSOLUTE_NS = "ABSOLUTE_NS"
    RECORDING_OFFSET_NS = "RECORDING_OFFSET_NS"


class CameraRecordingConfigV1(BaseModel):
    """Queryable camera timing facts copied from the immutable config object."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_mode_override="validation",
    )

    camera_id: Identifier
    topic: str | None = Field(default=None, min_length=1, max_length=512)
    fps: float = Field(gt=0, le=1000)
    width: int | None = Field(default=None, ge=2, le=32768)
    height: int | None = Field(default=None, ge=2, le=32768)
    codec: str = Field(min_length=1, max_length=64)
    clock_domain: str = Field(min_length=1, max_length=128)
    time_base_numerator: int = Field(default=1, ge=1)
    time_base_denominator: int = Field(ge=1, le=1_000_000_000)
    capture_start_offset_ns: Nanoseconds = 0

    @property
    def modality_key(self) -> str:
        return self.topic or self.camera_id


class SensorRecordingConfigV1(BaseModel):
    """Queryable non-video timing facts copied from the immutable config object."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    topic: str = Field(min_length=1, max_length=512)
    clock_domain: str = Field(min_length=1, max_length=128)
    timestamp_mode: SensorTimestampMode = SensorTimestampMode.ABSOLUTE_NS
    required: bool = True


class RecordingConfigurationV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["recording-config/v1", "recording-config/v2"] = "recording-config/v1"
    collection_mode: Literal["RGB", "JOINT_ONLY"] = "RGB"
    time_source: Literal["RGB_LOG_TIME", "STATE_LOG_TIME"] = "RGB_LOG_TIME"
    recorder_version: str = Field(min_length=1, max_length=128)
    primary_clock_domain: str = Field(min_length=1, max_length=128)
    cameras: tuple[CameraRecordingConfigV1, ...] = Field(max_length=128)
    sensors: tuple[SensorRecordingConfigV1, ...] = Field(default=(), max_length=2048)

    @model_validator(mode="after")
    def validate_cameras(self) -> RecordingConfigurationV1:
        if self.collection_mode == "JOINT_ONLY":
            if (
                self.schema_version != "recording-config/v2"
                or self.cameras
                or self.time_source != "STATE_LOG_TIME"
            ):
                raise ValueError(
                    "JOINT_ONLY requires recording-config/v2, no cameras and STATE_LOG_TIME"
                )
            if not {
                "/observation/state",
                "/action",
                "/openarm/capture_validity",
                "source.timestamp_ns",
            } <= {s.topic for s in self.sensors if s.required}:
                raise ValueError(
                    "JOINT_ONLY requires state, action, source timestamps and validity evidence"
                )
        elif not self.cameras or self.schema_version != "recording-config/v1":
            raise ValueError("legacy RGB recording-config/v1 requires cameras")
        camera_ids = [camera.camera_id for camera in self.cameras]
        if len(camera_ids) != len(set(camera_ids)):
            raise ValueError("recording config camera_id values must be unique")
        modality_keys = [camera.modality_key for camera in self.cameras]
        sensor_topics = [sensor.topic for sensor in self.sensors]
        if len(modality_keys) != len(set(modality_keys)):
            raise ValueError("recording config camera topic values must be unique")
        if len(sensor_topics) != len(set(sensor_topics)):
            raise ValueError("recording config sensor topic values must be unique")
        if set(modality_keys).intersection(sensor_topics):
            raise ValueError("camera and sensor modality keys must be disjoint")
        return self

    @model_serializer(mode="wrap")
    def serialize_compatible(self, handler):
        document = handler(self)
        if self.schema_version == "recording-config/v1":
            document.pop("collection_mode", None)
            # Preserve historical RGB-clock bytes without discarding an explicit
            # state clock for a recording that also contains camera streams.
            if self.time_source == "RGB_LOG_TIME":
                document.pop("time_source", None)
        return document


class RecordingAssetManifestV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=1024)
    role: RecordingAssetRole
    camera_id: Identifier | None = None
    media_type: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=1)
    sha256: Sha256
    crc64: int | None = Field(default=None, ge=0, le=2**64 - 1)
    part_count: int = Field(default=1, ge=1, le=10_000)

    @model_validator(mode="after")
    def validate_asset(self) -> RecordingAssetManifestV1:
        path = PurePosixPath(self.path)
        if (
            path.is_absolute()
            or "\\" in self.path
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("asset path must be a safe relative POSIX path")
        if self.role is RecordingAssetRole.RAW_VIDEO:
            if self.camera_id is None:
                raise ValueError("RAW_VIDEO assets require camera_id")
            if not self.media_type.startswith("video/"):
                raise ValueError("RAW_VIDEO assets require a video media_type")
        elif self.camera_id is not None:
            raise ValueError("only RAW_VIDEO assets may declare camera_id")
        return self


class CreateRecordingUploadCommand(BaseModel):
    """Immutable multi-object manifest for one uninterrupted recording."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["continuous-recording-upload/v2", "continuous-recording-upload/v3"] = (
        "continuous-recording-upload/v2"
    )
    recording_id: Identifier
    rollout_id: Identifier
    data_package_id: Identifier
    collection_task_id: Identifier
    collection_job_id: Identifier
    robot_id: Identifier
    device_id: Identifier
    capture_started_at: AwareDatetime
    capture_ended_at: AwareDatetime
    recording_config: RecordingConfigurationV1
    assets: tuple[RecordingAssetManifestV1, ...] = Field(min_length=2, max_length=4096)

    @model_validator(mode="after")
    def validate_upload(self) -> CreateRecordingUploadCommand:
        if self.capture_ended_at <= self.capture_started_at:
            raise ValueError("capture_ended_at must be after capture_started_at")
        paths = [asset.path for asset in self.assets]
        if len(paths) != len(set(paths)):
            raise ValueError("asset paths must be unique")
        roles = [asset.role for asset in self.assets]
        if roles.count(RecordingAssetRole.RECORDING_CONFIG) != 1:
            raise ValueError("exactly one RECORDING_CONFIG asset is required")
        if RecordingAssetRole.SENSOR_DATA not in roles:
            raise ValueError("at least one SENSOR_DATA asset is required")
        videos = [asset for asset in self.assets if asset.role is RecordingAssetRole.RAW_VIDEO]
        joints = self.recording_config.collection_mode == "JOINT_ONLY"
        if joints != (self.schema_version == "continuous-recording-upload/v3"):
            raise ValueError(
                "JOINT_ONLY requires continuous-recording-upload/v3; old "
                "contracts retain video requirements"
            )
        if not videos and not joints:
            raise ValueError("at least one RAW_VIDEO asset is required")
        video_camera_ids = [asset.camera_id for asset in videos]
        if len(video_camera_ids) != len(set(video_camera_ids)):
            raise ValueError("only one RAW_VIDEO asset per camera is supported in v2")
        configured = {camera.camera_id for camera in self.recording_config.cameras}
        if set(video_camera_ids) != configured:
            raise ValueError("RAW_VIDEO camera ids must exactly match recording config cameras")
        return self


class RecordingUpload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["continuous-recording-upload/v2", "continuous-recording-upload/v3"] = (
        "continuous-recording-upload/v2"
    )
    scope: RecordingScope
    upload_id: UUID
    command: CreateRecordingUploadCommand
    manifest_sha256: Sha256
    status: RecordingUploadStatus = RecordingUploadStatus.UPLOADING
    created_by: str = Field(min_length=1, max_length=512)
    created_at: AwareDatetime = Field(default_factory=utc_now)
    updated_at: AwareDatetime = Field(default_factory=utc_now)


class RecordingAsset(BaseModel):
    """Internal immutable Raw object identity plus mutable upload state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["recording-asset/v1"] = "recording-asset/v1"
    scope: RecordingScope
    upload_id: UUID
    asset_id: UUID
    manifest: RecordingAssetManifestV1
    object_key: str = Field(min_length=1, max_length=2048)
    multipart_upload_id: str = Field(min_length=1, max_length=2048)
    status: RecordingAssetStatus = RecordingAssetStatus.UPLOADING
    object_etag: str | None = None
    committed_at: AwareDatetime | None = None
    failure_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]+$")


class RecordingAssetSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: UUID
    path: str
    role: RecordingAssetRole
    camera_id: Identifier | None = None
    media_type: str
    size: int
    sha256: Sha256
    status: RecordingAssetStatus


class RecordingAssetUploadGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset: RecordingAssetSummary
    multipart_upload_id: str
    parts: tuple[PartAuthorization, ...]


class RecordingUploadGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    upload: RecordingUpload
    assets: tuple[RecordingAssetUploadGrant, ...]


class RecordingUploadEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    upload: RecordingUpload
    assets: tuple[RecordingAssetSummary, ...]


class CompleteRecordingAssetCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    parts: tuple[CompletedPart, ...] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def validate_parts(self) -> CompleteRecordingAssetCommand:
        numbers = [part.part_number for part in self.parts]
        if numbers != sorted(numbers) or len(numbers) != len(set(numbers)):
            raise ValueError("parts must be unique and ordered by part_number")
        return self


class AuthorizeRecordingAssetPartsCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    part_numbers: tuple[int, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_part_numbers(self) -> AuthorizeRecordingAssetPartsCommand:
        if len(set(self.part_numbers)) != len(self.part_numbers):
            raise ValueError("part_numbers must be unique")
        if any(number < 1 or number > 10_000 for number in self.part_numbers):
            raise ValueError("part_numbers must be between 1 and 10000")
        return self


class RecordingAssetPartGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: UUID
    parts: tuple[PartAuthorization, ...]


class RetryEpisodeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: UUID
    expected_workflow_id: str = Field(min_length=1, max_length=1024)


class EpisodeProcessing(BaseModel):
    """Canonical database row for a continuous-recording Episode."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["recording-episode/v1"] = "recording-episode/v1"
    scope: RecordingScope
    recording_id: Identifier
    episode_id: EpisodeId
    finalized_revision: int = Field(ge=1)
    start_offset_ns: Nanoseconds
    end_offset_ns: Nanoseconds
    status: EpisodeProcessingStatus = EpisodeProcessingStatus.PENDING_QC
    workflow_id: str | None = Field(default=None, min_length=1, max_length=1024)
    event_id: UUID | None = None
    qc_report_id: str | None = None
    dispatch_error_code: str | None = None
    alignment_attempt_id: str | None = None
    dataset_id: str | None = Field(default=None, min_length=1, max_length=128)
    dataset_episode_id: str | None = Field(default=None, min_length=1, max_length=128)
    dataset_version: int | None = Field(default=None, ge=1)
    lance_version: int | None = Field(default=None, ge=1)
    data_ref: DataVersionRef | None = None
    collection_version_id: str | None = Field(default=None, min_length=1, max_length=256)
    annotation_task_id: str | None = Field(default=None, min_length=1, max_length=128)
    aligned_media_camera_count: int | None = Field(default=None, ge=0, le=128)
    failure_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]+$")
    failure_stage: str | None = Field(default=None, min_length=1, max_length=128)
    created_at: AwareDatetime = Field(default_factory=utc_now)
    updated_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_processing_evidence(self) -> EpisodeProcessing:
        if (
            self.status
            in {
                EpisodeProcessingStatus.QC_FAILED,
                EpisodeProcessingStatus.PENDING_ALIGNMENT,
                EpisodeProcessingStatus.ALIGNING,
                EpisodeProcessingStatus.READY,
            }
            and self.qc_report_id is None
        ):
            raise ValueError("post-QC Episode states require qc_report_id")
        if (
            self.status
            in {
                EpisodeProcessingStatus.ALIGNING,
                EpisodeProcessingStatus.READY,
            }
            and self.alignment_attempt_id is None
        ):
            raise ValueError("alignment Episode states require alignment_attempt_id")
        ready_values = (
            self.dataset_id,
            self.dataset_version,
            self.aligned_media_camera_count,
        )
        if self.status is EpisodeProcessingStatus.READY and any(
            value is None for value in ready_values
        ):
            raise ValueError("READY Episodes require Dataset, task, and media evidence")
        if self.status is EpisodeProcessingStatus.READY and (
            (self.lance_version is None) == (self.data_ref is None)
        ):
            raise ValueError("READY Episode requires exactly one immutable storage binding")
        if self.data_ref is not None:
            identity = self.data_ref.identity
            if (
                self.collection_version_id is None
                or identity.scope
                != (self.scope.organization_id, self.scope.project_id, self.scope.region_code)
                or identity.dataset_id != self.dataset_id
                or identity.episode_id != self.dataset_episode_id
                or identity.rollout_id != f"{self.recording_id}:{self.episode_id}"
            ):
                raise ValueError("Episode package binding differs from the finalized recording")
        elif self.collection_version_id is not None:
            raise ValueError("a package collection requires its Episode data reference")
        if self.status is not EpisodeProcessingStatus.READY and any(
            value is not None
            for value in (
                *ready_values,
                self.annotation_task_id,
                self.lance_version,
                self.data_ref,
                self.dataset_episode_id,
            )
        ):
            raise ValueError("only READY Episodes expose Dataset, task, and media evidence")
        failed = self.status in {
            EpisodeProcessingStatus.QC_FAILED,
            EpisodeProcessingStatus.FAILED,
        }
        if failed != (self.failure_code is not None and self.failure_stage is not None):
            raise ValueError("failed Episode states require failure code and stage evidence")
        return self


class EpisodeProcessingEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    episode: EpisodeProcessing


class RecordingEpisodeQc(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    episode_id: EpisodeId
    report_id: str
    status: Literal["PASS", "REJECT"]
    finding_codes: tuple[str, ...] = ()
    quality_report: QcReportV1
    rules: tuple[RuleExecution, ...] = ()


class EpisodeProcessingPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    qc_reports: tuple[RecordingEpisodeQc, ...] = ()
    items: tuple[EpisodeProcessing, ...]
    total: int = Field(ge=0)


class EpisodeVideoSource(BaseModel):
    """Direct original-video playback descriptor; no preview artifact is generated."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["episode-video-source/v1"] = "episode-video-source/v1"
    recording_id: Identifier
    episode_id: EpisodeId
    asset_id: UUID
    camera_id: Identifier
    modality: Literal["rgb", "depth"] = "rgb"
    preview_status: Literal["READY", "UNAVAILABLE"] = "READY"
    media_type: str
    source_url: str
    start_offset_ns: Nanoseconds
    end_offset_ns: Nanoseconds
    fps: float = Field(gt=0)
    codec: str
    expires_at: datetime
    byte_range_supported: bool = True
    materialization: Literal["ORIGINAL_VIDEO_TIME_WINDOW", "DEPTH_PREVIEW_TIME_WINDOW"] = (
        "ORIGINAL_VIDEO_TIME_WINDOW"
    )


class EpisodeVideoSourceEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sources: tuple[EpisodeVideoSource, ...]


class RecordingVideoSource(BaseModel):
    """Direct whole-recording playback descriptor used by the slice editor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["recording-video-source/v1"] = "recording-video-source/v1"
    recording_id: Identifier
    asset_id: UUID
    camera_id: Identifier
    modality: Literal["rgb", "depth"] = "rgb"
    preview_status: Literal["READY", "UNAVAILABLE"] = "READY"
    media_type: str
    source_url: str
    duration_ns: Nanoseconds
    fps: float = Field(gt=0)
    codec: str
    expires_at: datetime
    byte_range_supported: bool = True
    materialization: Literal["ORIGINAL_RECORDING", "DEPTH_PREVIEW"] = "ORIGINAL_RECORDING"


class RecordingVideoSourceEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sources: tuple[RecordingVideoSource, ...]


class RecordingSensorSample(BaseModel):
    """One decoded sample on the original recording clock."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    offset_ns: Nanoseconds
    source_timestamp_ns: Nanoseconds
    value: JsonValue


class RecordingSensorWindow(BaseModel):
    """Bounded raw SENSOR_DATA window; independent from Episode processing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["recording-sensor-window/v1"] = "recording-sensor-window/v1"
    recording_id: Identifier
    topic: str = Field(min_length=1, max_length=512)
    start_offset_ns: Nanoseconds
    end_offset_ns: Nanoseconds
    samples: tuple[RecordingSensorSample, ...]
    truncated: bool = False
