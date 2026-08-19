from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import (
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
    role: Literal["RAW_MCAP", "AUXILIARY"] = "RAW_MCAP"

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
        if len(raw_files) != 1:
            raise ValueError("schema v1 requires exactly one RAW_MCAP file")
        raw = raw_files[0]
        if (raw.size, raw.sha256, raw.crc64) != (self.file_size, self.sha256, self.crc64):
            raise ValueError("top-level size and checksums must match the RAW_MCAP file")
        if sum(item.size for item in self.files) > 5 * 1024**4:
            raise ValueError("manifest package exceeds the 5 TiB resource limit")
        return self

    @property
    def rollout_date(self) -> date:
        return self.start_time.astimezone(timezone.utc).date()


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
    cameras: tuple[ManifestCameraV1, ...]
    topics: tuple[ManifestTopicV1, ...]
    missing_expected_topics: tuple[TopicName, ...] = ()


class ManifestPreflightResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["manifest-preflight/v1"] = "manifest-preflight/v1"
    manifest_fingerprint: Sha256
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
    committed_at: datetime = Field(default_factory=utc_now)
    workflow: IngestWorkflowLocator | None = None


def raw_object_key(manifest: RolloutManifestV1) -> str:
    return (
        "raw/v1/"
        f"project={manifest.project_id}/"
        f"date={manifest.rollout_date.isoformat()}/"
        f"robot={manifest.robot_id}/"
        f"job={manifest.collection_job_id}/"
        f"package={manifest.data_package_id}/"
        f"rollout={manifest.sequence_no:06d}-{manifest.rollout_id}/"
        f"sha256={manifest.sha256}/recording.mcap"
    )


def manifest_object_key(raw_key: str) -> str:
    return raw_key.rsplit("/", maxsplit=1)[0] + "/rollout_manifest.json"
