from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Annotated
from uuid import uuid4

from pydantic import BaseModel, Field, StringConstraints, model_validator

Identifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$"),
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
Etag = Annotated[str, StringConstraints(min_length=1, max_length=512)]


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


class RolloutManifestV1(BaseModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    project_id: Identifier
    task_id: Identifier
    collection_job_id: Identifier
    rollout_id: Identifier
    sequence_no: int = Field(ge=1)
    robot_id: Identifier
    start_time: datetime
    end_time: datetime
    expected_topics: list[str]
    actual_topics: list[str]
    file_size: int = Field(gt=0)
    sha256: Sha256
    crc64: int = Field(ge=0, le=2**64 - 1)
    compression: str
    recorder_version: str

    @model_validator(mode="after")
    def validate_time_range(self) -> RolloutManifestV1:
        if self.start_time.tzinfo is None or self.end_time.tzinfo is None:
            raise ValueError("start_time and end_time must include a timezone")
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
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
    object_key: str
    multipart_upload_id: str
    expected_sha256: Sha256
    expected_size: int = Field(gt=0)
    expected_crc64: int = Field(ge=0, le=2**64 - 1)
    manifest_fingerprint: Sha256
    status: UploadStatus = UploadStatus.REGISTERED
    etag: str | None = None
    failure_code: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class UploadObject(BaseModel):
    object_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    project_id: Identifier
    region_code: Identifier
    rollout_id: Identifier
    object_key: str
    expected_size: int = Field(gt=0)
    expected_sha256: Sha256
    expected_crc64: int = Field(ge=0, le=2**64 - 1)
    actual_size: int | None = Field(default=None, ge=0)
    actual_crc64: int | None = Field(default=None, ge=0, le=2**64 - 1)
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
    crc64: int | None = Field(default=None, ge=0, le=2**64 - 1)
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


class RawObjectCommittedV1(BaseModel):
    event_type: str = "rollout.raw_committed.v1"
    schema_version: int = 1
    project_id: Identifier
    region_code: Identifier
    rollout_id: Identifier
    object_key: str
    manifest_key: str
    sha256: Sha256
    file_size: int = Field(gt=0)
    committed_at: datetime = Field(default_factory=utc_now)


def raw_object_key(manifest: RolloutManifestV1) -> str:
    return (
        "raw/v1/"
        f"project={manifest.project_id}/"
        f"date={manifest.rollout_date.isoformat()}/"
        f"robot={manifest.robot_id}/"
        f"job={manifest.collection_job_id}/"
        f"rollout={manifest.sequence_no:06d}-{manifest.rollout_id}/"
        f"sha256={manifest.sha256}/recording.mcap"
    )


def manifest_object_key(raw_key: str) -> str:
    return raw_key.rsplit("/", maxsplit=1)[0] + "/rollout_manifest.json"
