from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    StringConstraints,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from hc_data_platform.dataset_registry.models import DatasetIngestViewerTarget

Identifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$"),
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
Etag = Annotated[str, StringConstraints(min_length=1, max_length=512)]
TopicName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=512, pattern=r"^/[A-Za-z0-9_./-]+$"),
]
RelativeObjectPath = Annotated[str, StringConstraints(min_length=1, max_length=1024)]

CRC64_MAX = 2**64 - 1
CRC64_DECIMAL_PATTERN = r"^(?:0|[1-9][0-9]{0,19})$"


def _parse_crc64(value: object) -> int:
    if isinstance(value, str):
        if (
            not value
            or len(value) > 20
            or not value.isascii()
            or not value.isdigit()
            or (len(value) > 1 and value.startswith("0"))
        ):
            raise ValueError("CRC64 must be a canonical unsigned decimal string")
        parsed = int(value)
    elif isinstance(value, int) and not isinstance(value, bool):
        # Internal domain code and persistence adapters use exact Python integers.
        parsed = value
    elif isinstance(value, Decimal):
        # psycopg decodes PostgreSQL numeric(20,0) as Decimal. It is an internal
        # exact representation, unlike a JSON number, so accept only integral
        # values before canonical JSON serialization turns it back into a string.
        if not value.is_finite() or value != value.to_integral_value():
            raise ValueError("CRC64 must be an exact unsigned integer")
        parsed = int(value)
    else:
        raise ValueError("CRC64 must be a canonical unsigned decimal string")
    if parsed < 0 or parsed > CRC64_MAX:
        raise ValueError("CRC64 must fit in an unsigned 64-bit integer")
    return parsed


Crc64 = Annotated[
    int,
    BeforeValidator(_parse_crc64),
    PlainSerializer(lambda value: str(value), return_type=str, when_used="json"),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": CRC64_DECIMAL_PATTERN,
            "maxLength": 20,
            "description": (
                "Unsigned CRC64 encoded as a canonical decimal string so JSON clients "
                "do not lose uint64 precision."
            ),
        }
    ),
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CollectionJobStatus(str, Enum):
    REGISTERED = "REGISTERED"
    COLLECTING = "COLLECTING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RolloutStatus(str, Enum):
    REGISTERED = "REGISTERED"
    UPLOADING = "UPLOADING"
    RAW_COMMITTED = "RAW_COMMITTED"
    VERIFYING = "VERIFYING"
    RAW_VERIFIED = "RAW_VERIFIED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class UploadStatus(str, Enum):
    """Upload-session states kept as the public name used by the first implementation."""

    REGISTERED = "REGISTERED"
    UPLOADING = "UPLOADING"
    PAUSED = "PAUSED"
    MULTIPART_COMPLETED = "MULTIPART_COMPLETED"
    RAW_COMMITTED = "RAW_COMMITTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


UploadSessionStatus = UploadStatus


class UploadObjectStatus(str, Enum):
    PENDING = "PENDING"
    MULTIPART_COMPLETED = "MULTIPART_COMPLETED"
    VERIFIED = "VERIFIED"
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class UploadPartStatus(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    UPLOADED = "UPLOADED"
    FAILED = "FAILED"


class UploadSourceType(str, Enum):
    BROWSER_MULTIPART = "BROWSER_MULTIPART"
    OBJECT_STORAGE_REFERENCE = "OBJECT_STORAGE_REFERENCE"


class IdempotencyOutcome(str, Enum):
    CREATED = "CREATED"
    RESUMED = "RESUMED"
    ALREADY_COMMITTED = "ALREADY_COMMITTED"


class IngestTriggerStatus(str, Enum):
    PENDING = "PENDING"
    DISPATCHED = "DISPATCHED"
    RETRY_WAIT = "RETRY_WAIT"


class IngestProcessingMode(str, Enum):
    """Whether a committed package is already one episode or needs manual slicing."""

    DIRECT_EPISODE = "DIRECT_EPISODE"
    CONTINUOUS_RECORDING = "CONTINUOUS_RECORDING"


class DeviceCaptureEventType(str, Enum):
    CAPTURED = "CAPTURED"
    SAVED = "SAVED"


class DeviceCaptureFactRequest(BaseModel):
    """Immutable device-agent assertion; never inferred from a cloud upload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["device-capture-fact/v1"]
    source_event_id: Identifier
    event_type: DeviceCaptureEventType
    collection_task_id: Identifier
    collection_job_id: Identifier
    recording_request_id: Identifier
    data_package_id: Identifier
    robot_id: Identifier
    device_id: Identifier
    device_sequence_no: int = Field(ge=0)
    capture_started_at: AwareDatetime
    capture_ended_at: AwareDatetime
    saved_at: AwareDatetime | None
    local_artifact_size: int | None = Field(gt=0, le=5 * 1024**4)
    local_artifact_sha256: Sha256 | None
    recorder_version: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._/+:-]*$",
    )
    occurred_at: AwareDatetime

    @model_validator(mode="after")
    def validate_event_semantics(self) -> DeviceCaptureFactRequest:
        if self.capture_ended_at <= self.capture_started_at:
            raise ValueError("capture_ended_at must be after capture_started_at")
        if self.occurred_at < self.capture_ended_at:
            raise ValueError("occurred_at must not precede capture_ended_at")
        saved_fields = (
            self.saved_at,
            self.local_artifact_size,
            self.local_artifact_sha256,
        )
        if self.event_type is DeviceCaptureEventType.CAPTURED:
            if any(value is not None for value in saved_fields):
                raise ValueError("CAPTURED must not claim local persistence fields")
        else:
            if any(value is None for value in saved_fields):
                raise ValueError("SAVED requires time, size, and SHA256 of the local artifact")
            if self.saved_at is not None and self.saved_at < self.capture_ended_at:
                raise ValueError("saved_at must not precede capture_ended_at")
            if self.saved_at is not None and self.occurred_at < self.saved_at:
                raise ValueError("occurred_at must not precede saved_at")
        return self

    @property
    def capture_duration_seconds(self) -> float:
        return (self.capture_ended_at - self.capture_started_at).total_seconds()


class DeviceCaptureFact(DeviceCaptureFactRequest):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_id: UUID
    organization_id: Identifier
    project_id: Identifier
    region_code: Identifier
    producer_subject_id: str = Field(min_length=1, max_length=512)
    received_at: AwareDatetime


class IngestWorkflowLocator(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    workflow_id: str = Field(min_length=1)
    status: IngestTriggerStatus
    attempts: int = Field(default=0, ge=0)
    last_error_code: str | None = Field(default=None, max_length=128)
    updated_at: datetime = Field(default_factory=utc_now)


class ManifestFileV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: RelativeObjectPath
    size: int = Field(gt=0, le=5 * 1024**4)
    sha256: Sha256
    crc64: Crc64
    media_type: str = Field(default="application/octet-stream", min_length=1, max_length=128)
    role: Literal["RAW_MCAP", "CAPTURE_BUNDLE", "AUXILIARY"] = "RAW_MCAP"

    @field_validator("path")
    @classmethod
    def validate_safe_relative_path(cls, value: str) -> str:
        if (
            value.startswith(("/", "\\"))
            or "\\" in value
            or "\x00" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError("file path must be a normalized relative POSIX path")
        return value


class ManifestCameraV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    camera_id: Identifier
    topic: TopicName
    frame_id: Identifier | None = None
    encoding: str | None = Field(default=None, max_length=128)


class ManifestTopicV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: TopicName
    required: bool = False
    message_encoding: str | None = Field(default=None, max_length=128)
    schema_name: str | None = Field(default=None, max_length=256)


class HuggingFaceEpisodeSourceV1(BaseModel):
    """Stable source recording identity, separate from converter artifact identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["HUGGING_FACE_EPISODE"] = "HUGGING_FACE_EPISODE"
    repository: str = Field(
        min_length=3,
        max_length=256,
        pattern=r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$",
    )
    resolved_revision: str = Field(
        min_length=7,
        max_length=64,
        pattern=r"^[A-Fa-f0-9]+$",
    )
    episode_index: int = Field(ge=0)

    @field_validator("repository", "resolved_revision")
    @classmethod
    def normalize_source_locator(cls, value: str) -> str:
        return value.lower()


class ContinuousCaptureSourceV1(BaseModel):
    """Stable identity of one uninterrupted device recording, not an episode."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["CONTINUOUS_CAPTURE"] = "CONTINUOUS_CAPTURE"
    recording_id: Identifier
    device_id: Identifier
    recorder_boot_id: Identifier | None = None


class RolloutManifestV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    project_id: Identifier
    task_id: Identifier
    collection_job_id: Identifier
    rollout_id: Identifier
    collection_session_id: Identifier
    recording_request_id: Identifier
    data_package_id: Identifier
    pico_instance_id: Identifier | None = None
    sequence_no: int = Field(ge=1)
    robot_id: Identifier
    start_time: datetime
    end_time: datetime
    cameras: list[ManifestCameraV1] = Field(max_length=128)
    topics: list[ManifestTopicV1] = Field(max_length=2048)
    expected_topics: list[TopicName] = Field(max_length=2048)
    actual_topics: list[TopicName] = Field(max_length=2048)
    files: list[ManifestFileV1] = Field(min_length=1, max_length=256)
    file_size: int = Field(gt=0, le=5 * 1024**4)
    sha256: Sha256
    crc64: Crc64
    compression: Literal["none", "zstd"]
    recorder_version: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._/+:-]*$",
    )
    processing_mode: IngestProcessingMode = IngestProcessingMode.DIRECT_EPISODE
    source_recording: HuggingFaceEpisodeSourceV1 | ContinuousCaptureSourceV1 | None = None

    @model_validator(mode="after")
    def validate_time_range(self) -> RolloutManifestV1:
        if self.start_time.tzinfo is None or self.end_time.tzinfo is None:
            raise ValueError("start_time and end_time must include a timezone")
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        if len(self.expected_topics) != len(set(self.expected_topics)):
            raise ValueError("expected_topics must not contain duplicates")
        if len(self.actual_topics) != len(set(self.actual_topics)):
            raise ValueError("actual_topics must not contain duplicates")
        topic_names = [topic.name for topic in self.topics]
        if len(topic_names) != len(set(topic_names)):
            raise ValueError("topics must not contain duplicate names")
        if set(topic_names) != set(self.actual_topics):
            raise ValueError("topics inventory must exactly match actual_topics")
        camera_ids = [camera.camera_id for camera in self.cameras]
        if len(camera_ids) != len(set(camera_ids)):
            raise ValueError("cameras must not contain duplicate camera_id values")
        if any(camera.topic not in set(self.actual_topics) for camera in self.cameras):
            raise ValueError("camera topics must be present in actual_topics")
        file_paths = [item.path for item in self.files]
        if len(file_paths) != len(set(file_paths)):
            raise ValueError("files must not contain duplicate paths")
        raw_files = [item for item in self.files if item.role == "RAW_MCAP"]
        capture_bundles = [item for item in self.files if item.role == "CAPTURE_BUNDLE"]
        if self.processing_mode is IngestProcessingMode.DIRECT_EPISODE:
            if len(raw_files) != 1 or capture_bundles:
                raise ValueError("direct episode ingest requires exactly one RAW_MCAP file")
            if isinstance(self.source_recording, ContinuousCaptureSourceV1):
                raise ValueError("a continuous capture source requires continuous recording mode")
            primary = raw_files[0]
        else:
            if len(capture_bundles) != 1 or raw_files:
                raise ValueError(
                    "continuous recording ingest requires exactly one CAPTURE_BUNDLE file"
                )
            if not isinstance(self.source_recording, ContinuousCaptureSourceV1):
                raise ValueError("continuous recording ingest requires a CONTINUOUS_CAPTURE source")
            primary = capture_bundles[0]
        if (primary.size, primary.sha256, primary.crc64) != (
            self.file_size,
            self.sha256,
            self.crc64,
        ):
            raise ValueError("top-level size and checksums must match the primary package file")
        if sum(item.size for item in self.files) > 5 * 1024**4:
            raise ValueError("manifest package exceeds the 5 TiB resource limit")
        return self

    @property
    def rollout_date(self) -> date:
        return self.start_time.astimezone(timezone.utc).date()

    @property
    def source_fingerprint(self) -> str | None:
        """Return the logical recording identity without revision or converter version.

        A source revision is retained as provenance, but a revised conversion of the
        same repository episode must use an explicit replacement flow instead of
        silently registering another business package in the same task.
        """

        if self.source_recording is None:
            return None
        if isinstance(self.source_recording, HuggingFaceEpisodeSourceV1):
            identity = (
                self.source_recording.repository,
                str(self.source_recording.episode_index),
            )
        else:
            identity = (
                self.source_recording.device_id,
                self.source_recording.recording_id,
            )
        canonical = "\n".join(
            ("source-recording/v1", self.task_id, self.source_recording.kind, *identity)
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CollectionJob(BaseModel):
    project_id: Identifier
    region_code: Identifier
    task_id: Identifier
    collection_job_id: Identifier
    robot_id: Identifier
    status: CollectionJobStatus = CollectionJobStatus.REGISTERED
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Rollout(BaseModel):
    project_id: Identifier
    region_code: Identifier
    collection_job_id: Identifier
    rollout_id: Identifier
    collection_session_id: Identifier
    recording_request_id: Identifier
    data_package_id: Identifier
    pico_instance_id: Identifier | None = None
    sequence_no: int = Field(ge=1)
    robot_id: Identifier
    source_sha256: Sha256
    source_fingerprint: Sha256 | None = None
    status: RolloutStatus = RolloutStatus.REGISTERED
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class UploadSession(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: Identifier
    region_code: Identifier
    rollout_id: Identifier
    data_package_id: Identifier
    object_key: str
    source_type: UploadSourceType = UploadSourceType.BROWSER_MULTIPART
    multipart_upload_id: str | None = None
    expected_sha256: Sha256
    expected_size: int = Field(gt=0)
    expected_crc64: Crc64
    manifest_fingerprint: Sha256
    status: UploadStatus = UploadStatus.REGISTERED
    etag: str | None = None
    failure_code: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    workflow: IngestWorkflowLocator | None = None


class UploadObject(BaseModel):
    object_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    project_id: Identifier
    region_code: Identifier
    rollout_id: Identifier
    object_key: str
    expected_size: int = Field(gt=0)
    expected_sha256: Sha256
    expected_crc64: Crc64
    actual_size: int | None = Field(default=None, ge=0)
    actual_crc64: Crc64 | None = None
    actual_sha256: Sha256 | None = None
    etag: str | None = None
    status: UploadObjectStatus = UploadObjectStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class UploadPart(BaseModel):
    session_id: str
    project_id: Identifier
    region_code: Identifier
    part_number: int = Field(ge=1, le=10_000)
    status: UploadPartStatus
    etag: str | None = None
    size: int | None = Field(default=None, ge=0)
    crc64: Crc64 | None = None
    retry_count: int = Field(default=0, ge=0, le=10)
    failure_code: str | None = Field(default=None, max_length=128)
    authorization_expires_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class CompletedPart(BaseModel):
    part_number: int = Field(ge=1, le=10_000)
    etag: Etag


class PartAuthorization(BaseModel):
    part_number: int = Field(ge=1, le=10_000)
    url: str
    expires_at: datetime


class UploadSessionGrant(BaseModel):
    session: UploadSession
    parts: list[PartAuthorization]
    idempotency_outcome: IdempotencyOutcome


class FailedPartV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_number: int = Field(ge=1, le=10_000)
    failure_code: str = Field(min_length=1, max_length=128, pattern=r"^[A-Z0-9_]+$")


class ManifestIdentifiersV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    collection_session_id: Identifier
    recording_request_id: Identifier
    data_package_id: Identifier
    robot_id: Identifier
    pico_instance_id: Identifier | None = None


class ManifestTimeRangeV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start_time: datetime
    end_time: datetime


class ManifestDiscoveryV1(BaseModel):
    """Read-only package facts; no code in this domain writes P20 task configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["MANIFEST"] = "MANIFEST"
    read_only: Literal[True] = True
    robot_id: Identifier | None = None
    cameras: tuple[ManifestCameraV1, ...]
    topics: tuple[ManifestTopicV1, ...]
    missing_expected_topics: tuple[TopicName, ...] = ()


class ManifestPreflightResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["manifest-preflight/v1"] = "manifest-preflight/v1"
    manifest_fingerprint: Sha256
    source_fingerprint: Sha256 | None = None
    identifiers: ManifestIdentifiersV1
    time_range: ManifestTimeRangeV1
    files: tuple[ManifestFileV1, ...]
    total_file_size: int = Field(gt=0, le=5 * 1024**4)
    discovery: ManifestDiscoveryV1
    manifest: RolloutManifestV1


class UploadSessionListV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[UploadSession, ...]
    total: int = Field(ge=0)
    next_cursor: str | None = Field(min_length=16, max_length=16_384)


class RawObjectCommittedV1(BaseModel):
    event_type: str = "rollout.raw_committed.v1"
    schema_version: int = 1
    project_id: Identifier
    region_code: Identifier
    rollout_id: Identifier
    data_package_id: Identifier
    object_key: str
    manifest_key: str
    sha256: Sha256
    file_size: int = Field(gt=0)
    processing_mode: IngestProcessingMode = IngestProcessingMode.DIRECT_EPISODE
    continuous_recording_id: Identifier | None = None
    committed_at: datetime = Field(default_factory=utc_now)
    workflow: IngestWorkflowLocator | None = None


class RawMediaSourceV1(BaseModel):
    """A short-lived handle to one immutable MCAP or whole capture bundle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["raw-media-source/v1"] = "raw-media-source/v1"
    format: Literal["MCAP", "CAPTURE_BUNDLE"] = "MCAP"
    media_type: str = Field(default="application/x-mcap", min_length=1, max_length=128)
    download_url: str = Field(min_length=1, max_length=8_192)
    expires_at: datetime
    byte_length: int = Field(gt=0)
    sha256: Sha256


class UploadProcessingState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    TECHNICAL_FAILED = "TECHNICAL_FAILED"
    QUALITY_RISK = "QUALITY_RISK"
    QUALITY_REJECTED = "QUALITY_REJECTED"
    CANCELLED = "CANCELLED"


class UploadAlignedMediaTargetV1(BaseModel):
    """Bounded facts for authorizing ingest-created canonical camera MP4s."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["upload-aligned-media-target/v1"]
    project_id: Identifier
    dataset_id: Identifier
    rollout_id: Identifier
    dataset_version: int = Field(ge=1)
    annotation_task_id: Identifier
    fps: Literal[30] = 30
    start_step: int = Field(ge=0)
    end_step: int = Field(gt=0)

    @model_validator(mode="after")
    def non_empty_window(self) -> UploadAlignedMediaTargetV1:
        if self.end_step <= self.start_step:
            raise ValueError("aligned media step window must be non-empty")
        return self


class UploadProcessingStatusV1(BaseModel):
    """Uploader-safe processing projection; never exposes a raw Workflow result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["upload-processing-status/v1"]
    session_id: UUID
    rollout_id: Identifier
    workflow_id: str = Field(min_length=1)
    status: UploadProcessingState
    stage: str = Field(min_length=1, max_length=128)
    attempt: int = Field(ge=0)
    aligned_media: UploadAlignedMediaTargetV1 | None
    viewer: DatasetIngestViewerTarget | None
    error_code: str | None = Field(max_length=128, pattern=r"^[A-Z0-9_]+$")
    updated_at: datetime

    @model_validator(mode="after")
    def aligned_media_matches_state(self) -> UploadProcessingStatusV1:
        if (self.status is UploadProcessingState.SUCCEEDED) != (self.aligned_media is not None):
            raise ValueError("only a successful upload may expose aligned media")
        if (self.status is UploadProcessingState.SUCCEEDED) != (self.viewer is not None):
            raise ValueError("only a successful upload may expose a viewer target")
        if self.aligned_media is not None and self.aligned_media.rollout_id != self.rollout_id:
            raise ValueError("aligned media rollout must match processing rollout")
        if (
            self.aligned_media is not None
            and self.viewer is not None
            and (
                self.aligned_media.dataset_id != self.viewer.dataset_id
                or self.viewer.version_id != f"version_lance_{self.aligned_media.dataset_version}"
            )
        ):
            raise ValueError("media and viewer targets must identify the same Dataset version")
        return self


class RawMediaAccessAuditEvent(BaseModel):
    """Internal audit payload; it deliberately excludes object keys and signed URLs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: Identifier
    region_code: Identifier
    actor_id: Identifier
    request_id: Identifier
    session_id: Identifier
    rollout_id: Identifier
    byte_length: int = Field(gt=0)
    occurred_at: datetime = Field(default_factory=utc_now)


def raw_object_key(manifest: RolloutManifestV1) -> str:
    filename = (
        "capture.bundle"
        if manifest.processing_mode is IngestProcessingMode.CONTINUOUS_RECORDING
        else "recording.mcap"
    )
    return (
        "raw/v1/"
        f"project={manifest.project_id}/"
        f"date={manifest.rollout_date.isoformat()}/"
        f"robot={manifest.robot_id}/"
        f"job={manifest.collection_job_id}/"
        f"package={manifest.data_package_id}/"
        f"rollout={manifest.sequence_no:06d}-{manifest.rollout_id}/"
        f"sha256={manifest.sha256}/{filename}"
    )


def manifest_object_key(raw_key: str) -> str:
    return raw_key.rsplit("/", maxsplit=1)[0] + "/rollout_manifest.json"
