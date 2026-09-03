from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

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

Identifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$"),
]
SourceFormat = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._+-]+$"),
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


def _parse_crc64(value: object) -> int:
    if isinstance(value, str):
        if not value.isascii() or not value.isdigit() or (len(value) > 1 and value.startswith("0")):
            raise ValueError("CRC64 must be a canonical unsigned decimal string")
        parsed = int(value)
    elif isinstance(value, int) and not isinstance(value, bool):
        parsed = value
    else:
        raise ValueError("CRC64 must be a canonical unsigned decimal string")
    if not 0 <= parsed <= 2**64 - 1:
        raise ValueError("CRC64 must fit in an unsigned 64-bit integer")
    return parsed


Crc64 = Annotated[
    int,
    BeforeValidator(_parse_crc64),
    PlainSerializer(lambda value: str(value), return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "pattern": r"^(?:0|[1-9][0-9]*)$"}),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _contains_sensitive_key(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = "".join(
                character
                for character in str(key).casefold()
                if character.isascii() and character.isalnum()
            )
            if (
                normalized
                in {
                    "token",
                    "accesstoken",
                    "refreshtoken",
                    "sessiontoken",
                    "authorization",
                    "password",
                    "secret",
                    "secretkey",
                    "clientsecret",
                    "privatekey",
                    "accesskey",
                    "accesskeyid",
                    "apikey",
                    "credential",
                    "credentials",
                    "awsaccesskeyid",
                    "awssecretaccesskey",
                    "ossaccesskeyid",
                    "securitytoken",
                }
                or normalized.endswith(
                    (
                        "token",
                        "password",
                        "secret",
                        "privatekey",
                        "apikey",
                        "accesskey",
                        "credential",
                        "credentials",
                    )
                )
                or _contains_sensitive_key(item)
            ):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_sensitive_key(item) for item in value)
    return False


class CaptureMode(str, Enum):
    PRESEGMENTED = "PRESEGMENTED"
    CONTINUOUS = "CONTINUOUS"


class IdentityState(str, Enum):
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"


class CredentialState(str, Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class UploadState(str, Enum):
    UPLOADING = "UPLOADING"
    PAUSED = "PAUSED"
    READY_TO_COMMIT = "READY_TO_COMMIT"
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AssetState(str, Enum):
    UPLOADING = "UPLOADING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class QualityStatus(str, Enum):
    PENDING = "PENDING"
    PASS = "PASS"
    RISK = "RISK"
    REJECT = "REJECT"


class RobotIngestProcessingStatus(str, Enum):
    PENDING = "PENDING"
    DISCOVERING_EPISODES = "DISCOVERING_EPISODES"
    PROCESSING = "PROCESSING"
    READY = "READY"
    PARTIALLY_FAILED = "PARTIALLY_FAILED"
    FAILED = "FAILED"


class AttemptOutcome(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"


class RobotIngestUploadPolicy(StrictModel):
    code: str = Field(default="STANDARD", min_length=1, max_length=128)
    max_asset_size_bytes: int = Field(default=5 * 1024**4, gt=0)
    max_batch_size_bytes: int = Field(default=10 * 1024**4, gt=0)
    max_assets: int = Field(default=256, ge=1, le=4096)
    part_authorization_ttl_seconds: int = Field(default=900, ge=60, le=86_400)
    session_retention_hours: int = Field(default=168, ge=1, le=24 * 365)
    require_sha256: bool = True
    require_crc64: bool = True


class RobotIngestAuthContext(StrictModel):
    principal_type: Literal["ROBOT"] = "ROBOT"
    organization_id: Identifier
    authenticated_robot_id: Identifier
    ingest_identity_id: Identifier
    credential_id: Identifier
    credential_version: int = Field(ge=1)
    allowed_transports: tuple[str, ...]
    allowed_formats: tuple[SourceFormat, ...]
    upload_policy: RobotIngestUploadPolicy


class RobotIngestIdentity(StrictModel):
    ingest_identity_id: Identifier
    organization_id: Identifier
    robot_id: Identifier
    display_name: str | None = Field(default=None, max_length=200)
    state: IdentityState = IdentityState.ENABLED
    allowed_transports: tuple[str, ...] = ("HTTPS",)
    allowed_formats: tuple[SourceFormat, ...]
    upload_policy: RobotIngestUploadPolicy = Field(default_factory=RobotIngestUploadPolicy)
    credential_revision: int = Field(default=0, ge=0)
    last_authenticated_at: datetime | None = None
    last_seen_at: datetime | None = None
    last_upload_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_permissions(self) -> RobotIngestIdentity:
        if not self.allowed_transports or len(self.allowed_transports) != len(
            set(self.allowed_transports)
        ):
            raise ValueError("allowed_transports must be non-empty and unique")
        if not self.allowed_formats or len(self.allowed_formats) != len(set(self.allowed_formats)):
            raise ValueError("allowed_formats must be non-empty and unique")
        return self


class CreateRobotIngestIdentity(StrictModel):
    robot_id: Identifier
    display_name: str | None = Field(default=None, max_length=200)
    allowed_transports: tuple[str, ...] = ("HTTPS",)
    allowed_formats: tuple[SourceFormat, ...]
    upload_policy: RobotIngestUploadPolicy = Field(default_factory=RobotIngestUploadPolicy)

    @model_validator(mode="after")
    def validate_permissions(self) -> CreateRobotIngestIdentity:
        if not self.allowed_transports or len(self.allowed_transports) != len(
            set(self.allowed_transports)
        ):
            raise ValueError("allowed_transports must be non-empty and unique")
        if not self.allowed_formats or len(self.allowed_formats) != len(set(self.allowed_formats)):
            raise ValueError("allowed_formats must be non-empty and unique")
        return self


class UpdateRobotIngestIdentity(StrictModel):
    allowed_transports: tuple[str, ...]
    allowed_formats: tuple[SourceFormat, ...]
    upload_policy: RobotIngestUploadPolicy


class IssueCredentialCommand(StrictModel):
    expires_at: AwareDatetime | None = None
    revoke_previous: bool = False


class IssuedRobotCredential(StrictModel):
    credential_id: Identifier
    credential_version: int = Field(ge=1)
    token: str = Field(min_length=32, max_length=1024, repr=False)
    token_prefix: str = Field(min_length=8, max_length=96)
    issued_at: datetime
    expires_at: datetime | None = None


class RobotCredentialSummary(StrictModel):
    credential_id: Identifier
    credential_version: int = Field(ge=1)
    state: CredentialState
    token_prefix: str = Field(min_length=8, max_length=96)
    issued_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_authenticated_at: datetime | None = None


class RobotIdentityEnvelope(StrictModel):
    data: RobotIngestIdentity
    credential: IssuedRobotCredential | None = None


class RobotIdentityList(StrictModel):
    items: tuple[RobotIngestIdentity, ...]


class FpsRational(StrictModel):
    numerator: int = Field(gt=0, le=1_000_000)
    denominator: int = Field(gt=0, le=1_000_000)


class RobotIngestAssetManifest(StrictModel):
    asset_id: Identifier
    path: str = Field(min_length=1, max_length=1024)
    role: str = Field(default="RAW", min_length=1, max_length=64)
    media_type: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(gt=0)
    sha256: Sha256
    crc64: Crc64
    camera_id: Identifier | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def safe_relative_path(self) -> RobotIngestAssetManifest:
        components = self.path.replace("\\", "/").split("/")
        if self.path.startswith(("/", "\\")) or any(
            component in {"", ".", ".."} for component in components
        ):
            raise ValueError("asset path must be a normalized relative path")
        if _contains_sensitive_key(self.metadata):
            raise ValueError("asset metadata must not contain credential material")
        return self


class RobotIngestCameraManifest(StrictModel):
    camera_id: Identifier
    asset_id: Identifier | None = None
    path: str | None = Field(default=None, min_length=1, max_length=1024)
    codec: str = Field(min_length=1, max_length=128)
    width: int = Field(gt=0, le=65_535)
    height: int = Field(gt=0, le=65_535)
    declared_fps: FpsRational
    declared_frame_count: int = Field(ge=0)
    declared_duration_ns: int = Field(ge=0)
    clock_domain: str = Field(min_length=1, max_length=128)
    time_base: FpsRational | None = None
    capture_started_at: AwareDatetime
    capture_ended_at: AwareDatetime

    @model_validator(mode="after")
    def validate_camera(self) -> RobotIngestCameraManifest:
        if (self.asset_id is None) == (self.path is None):
            raise ValueError("camera must reference exactly one asset_id or path")
        if self.capture_ended_at <= self.capture_started_at:
            raise ValueError("camera capture_ended_at must be after capture_started_at")
        return self


class RobotIngestUploadManifest(StrictModel):
    schema_version: Literal["robot-ingest/v1"] = "robot-ingest/v1"
    client_upload_id: Annotated[
        str,
        StringConstraints(
            pattern=(
                r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
                r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
            )
        ),
    ]
    collection_task_id: Identifier
    collection_job_id: Identifier | None = None
    robot_id: Identifier
    capture_mode: CaptureMode
    source_format: SourceFormat
    source_format_version: str = Field(min_length=1, max_length=128)
    capture_started_at: AwareDatetime
    capture_ended_at: AwareDatetime
    declared_episode_count: int | None = Field(default=None, ge=0, le=1_000_000)
    assets: tuple[RobotIngestAssetManifest, ...] = Field(min_length=1, max_length=4096)
    cameras: tuple[RobotIngestCameraManifest, ...] = Field(default=(), max_length=128)
    format_metadata: dict[str, Any] = Field(default_factory=dict)
    organization_id: Identifier | None = None
    project_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_manifest(self) -> RobotIngestUploadManifest:
        if self.capture_ended_at <= self.capture_started_at:
            raise ValueError("capture_ended_at must be after capture_started_at")
        asset_ids = [asset.asset_id for asset in self.assets]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("asset_id values must be unique")
        asset_paths = [asset.path for asset in self.assets]
        if len(asset_paths) != len(set(asset_paths)):
            raise ValueError("asset paths must be unique")
        camera_ids = [camera.camera_id for camera in self.cameras]
        if len(camera_ids) != len(set(camera_ids)):
            raise ValueError("camera_id values must be unique")
        known_assets = set(asset_ids)
        known_paths = set(asset_paths)
        known_cameras = set(camera_ids)
        for asset in self.assets:
            if asset.camera_id is not None and asset.camera_id not in known_cameras:
                raise ValueError("asset camera_id does not exist in cameras")
        for camera in self.cameras:
            if camera.asset_id is not None and camera.asset_id not in known_assets:
                raise ValueError("camera asset_id does not exist in assets")
            if camera.path is not None and camera.path not in known_paths:
                raise ValueError("camera path does not exist in assets")
        if _contains_sensitive_key(self.format_metadata):
            raise ValueError("format_metadata must not contain credential material")
        return self


class UploadTarget(StrictModel):
    collection_task_id: Identifier
    organization_id: Identifier
    project_id: Identifier
    dataset_id: Identifier
    region_code: Identifier
    task_status: Literal["ACTIVE", "CLOSED", "CANCELLED"]
    processing_config: dict[str, Any] = Field(default_factory=dict)


class UploadedPart(StrictModel):
    part_number: int = Field(ge=1, le=10_000)
    etag: str = Field(min_length=1, max_length=512)
    size_bytes: int = Field(ge=0)
    crc64: Crc64 | None = None


class RobotPartAuthorization(StrictModel):
    part_number: int = Field(ge=1, le=10_000)
    url: str = Field(min_length=1, max_length=8192)
    expires_at: datetime


class AuthorizePartsCommand(StrictModel):
    part_numbers: tuple[int, ...] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_numbers(self) -> AuthorizePartsCommand:
        if list(self.part_numbers) != sorted(self.part_numbers) or len(self.part_numbers) != len(
            set(self.part_numbers)
        ):
            raise ValueError("part_numbers must be unique and ascending")
        if any(not 1 <= value <= 10_000 for value in self.part_numbers):
            raise ValueError("part_numbers must be between 1 and 10000")
        return self


class PartAuthorizationGrant(StrictModel):
    upload_id: Identifier
    asset_id: Identifier
    uploaded_parts: tuple[UploadedPart, ...]
    authorizations: tuple[RobotPartAuthorization, ...]


class CompletedPart(StrictModel):
    part_number: int = Field(ge=1, le=10_000)
    etag: str = Field(min_length=1, max_length=512)


class CompleteAssetCommand(StrictModel):
    parts: tuple[CompletedPart, ...] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def validate_parts(self) -> CompleteAssetCommand:
        numbers = [part.part_number for part in self.parts]
        if numbers != sorted(numbers) or len(numbers) != len(set(numbers)):
            raise ValueError("parts must be unique and ascending")
        return self


class RobotIngestAsset(StrictModel):
    asset_id: Identifier
    path: str
    role: str
    media_type: str
    object_key: str
    multipart_upload_id: str
    expected_size_bytes: int = Field(gt=0)
    expected_sha256: Sha256
    expected_crc64: Crc64
    actual_size_bytes: int | None = Field(default=None, ge=0)
    actual_sha256: Sha256 | None = None
    actual_crc64: Crc64 | None = None
    etag: str | None = None
    state: AssetState = AssetState.UPLOADING
    completed_at: datetime | None = None


class CameraVerification(StrictModel):
    camera_id: Identifier
    verified_fps: FpsRational | None = None
    verified_frame_count: int | None = Field(default=None, ge=0)
    verified_duration_ns: int | None = Field(default=None, ge=0)
    synchronization_status: str = Field(default="PENDING", min_length=1, max_length=64)
    continuity_status: str = Field(default="PENDING", min_length=1, max_length=64)
    validation_status: str = Field(default="PENDING", min_length=1, max_length=64)
    failure_code: str | None = Field(default=None, max_length=128)


class RobotIngestUpload(StrictModel):
    upload_id: Identifier
    client_upload_id: str
    ingest_identity_id: Identifier
    authenticated_robot_id: Identifier
    request_robot_id: Identifier
    credential_id: Identifier
    credential_version: int = Field(ge=1)
    target: UploadTarget
    collection_job_id: Identifier
    capture_mode: CaptureMode
    source_format: SourceFormat
    source_format_version: str
    capture_started_at: datetime
    capture_ended_at: datetime
    declared_episode_count: int | None = Field(default=None, ge=0)
    verified_episode_count: int | None = Field(default=None, ge=0)
    derived_episode_count: int = Field(default=0, ge=0)
    verified_frame_count: int = Field(default=0, ge=0)
    verified_sample_count: int = Field(default=0, ge=0)
    qc_pass_episode_count: int = Field(default=0, ge=0)
    qc_risk_episode_count: int = Field(default=0, ge=0)
    qc_reject_episode_count: int = Field(default=0, ge=0)
    upload_batch_count: int = Field(default=1, ge=1)
    raw_capture_count: int = Field(default=1, ge=1)
    total_bytes: int = Field(gt=0)
    state: UploadState
    processing_status: RobotIngestProcessingStatus = RobotIngestProcessingStatus.PENDING
    quality_status: QualityStatus = QualityStatus.PENDING
    manifest_fingerprint: Sha256
    manifest: RobotIngestUploadManifest
    assets: tuple[RobotIngestAsset, ...]
    cameras: tuple[RobotIngestCameraManifest, ...]
    camera_verification: tuple[CameraVerification, ...] = ()
    raw_source_id: Identifier | None = None
    created_at: datetime
    expires_at: datetime
    updated_at: datetime
    committed_at: datetime | None = None


class RobotIngestUploadEnvelope(StrictModel):
    data: RobotIngestUpload
    resumed: bool = False


class RobotIngestAttempt(StrictModel):
    attempt_id: Identifier
    organization_id: Identifier | None = None
    project_id: Identifier | None = None
    region_code: Identifier | None = None
    authenticated_robot_id: Identifier | None = None
    request_robot_id: Identifier | None = None
    collection_task_id: Identifier | None = None
    source_format: SourceFormat | None = None
    outcome: AttemptOutcome
    failure_stage: str = Field(min_length=1, max_length=64)
    failure_code: str | None = Field(default=None, max_length=128)
    upload_id: Identifier | None = None
    raw_source_id: Identifier | None = None
    occurred_at: datetime


class RobotIngestAttemptList(StrictModel):
    items: tuple[RobotIngestAttempt, ...]


class RobotIngestStatistics(StrictModel):
    robot_id: Identifier
    upload_batch_count: int = Field(ge=0)
    committed_raw_count: int = Field(ge=0)
    episode_count: int = Field(ge=0)
    frame_count: int = Field(ge=0)
    sample_count: int = Field(ge=0)
    capture_duration_ns: int = Field(ge=0)
    raw_bytes: int = Field(ge=0)
    qc_pass_count: int = Field(ge=0)
    qc_risk_count: int = Field(ge=0)
    qc_reject_count: int = Field(ge=0)
    technical_failure_count: int = Field(ge=0)
    qualified_rate: float | None = Field(default=None, ge=0, le=1)
    evaluated_episode_count: int = Field(ge=0)


class RobotIngestEpisodeResult(StrictModel):
    episode_id: Identifier
    source_episode_index: int = Field(ge=0)
    status: Literal["PENDING", "PROCESSING", "READY", "FAILED"]
    frame_count: int | None = Field(default=None, gt=0)
    sample_count: int | None = Field(default=None, ge=0)
    dataset_version: int | None = Field(default=None, gt=0)
    lance_version: int | None = Field(default=None, gt=0)
    quality_status: QualityStatus = QualityStatus.PENDING
    qc_report_id: Identifier | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_ready_versions(self) -> RobotIngestEpisodeResult:
        if self.status == "READY" and (self.dataset_version is None or self.lance_version is None):
            raise ValueError("READY Episodes require Dataset and Lance versions")
        return self


class RobotIngestEpisodeResultList(StrictModel):
    upload_id: Identifier
    raw_source_id: Identifier
    items: tuple[RobotIngestEpisodeResult, ...]


class UploadList(StrictModel):
    items: tuple[RobotIngestUpload, ...]
