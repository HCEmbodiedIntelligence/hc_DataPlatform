"""Wire models for the first (P14) registry slice.

The read representations deliberately carry no upload grants, credentials, or asset
locators.  Those are separate, approved contracts when the product enables them.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hc_data_platform.core.pagination import PageInfo


class RegistryScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: str = Field(min_length=1, max_length=128)
    project_id: str | None = Field(default=None, min_length=1, max_length=128)


class BlockedReason(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=512)


class RobotAssetRole(str, Enum):
    URDF = "URDF"
    MESH = "MESH"
    TEXTURE = "TEXTURE"
    CONFIG = "CONFIG"
    DOCUMENTATION = "DOCUMENTATION"


class RobotAssetUploadStatus(str, Enum):
    UPLOADING = "UPLOADING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class RobotJointDirection(str, Enum):
    SAME = "SAME"
    INVERTED = "INVERTED"


class RobotModelDraftScope(str, Enum):
    ASSETS = "ASSETS"
    MAPPINGS = "MAPPINGS"


def _safe_relative_path(value: str) -> str:
    if (
        value.startswith(("/", "\\"))
        or "\\" in value
        or "\x00" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("asset path must be a normalized relative POSIX path")
    return value


class RobotModelAssetUploadFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    relative_path: str = Field(min_length=1, max_length=1024)
    role: RobotAssetRole
    media_type: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(gt=0, le=5 * 1024**4)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _safe_relative_path(value)

    @model_validator(mode="after")
    def validate_role_and_path(self) -> RobotModelAssetUploadFileRequest:
        suffix = (
            self.relative_path.rsplit(".", 1)[-1].casefold() if "." in self.relative_path else ""
        )
        allowed = {
            RobotAssetRole.URDF: {"urdf", "xml"},
            RobotAssetRole.MESH: {"stl", "obj", "dae", "glb", "gltf"},
            RobotAssetRole.TEXTURE: {"png", "jpg", "jpeg", "webp", "ktx2"},
            RobotAssetRole.CONFIG: {"json", "yaml", "yml", "toml"},
            RobotAssetRole.DOCUMENTATION: {"md", "txt", "pdf"},
        }[self.role]
        if suffix not in allowed:
            raise ValueError(f"{self.role.value} assets must use an approved file extension")
        return self


class CreateRobotModelAssetUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    files: tuple[RobotModelAssetUploadFileRequest, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_unique_paths(self) -> CreateRobotModelAssetUploadRequest:
        paths = [item.relative_path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("asset upload files must not repeat a relative path")
        if sum(item.size_bytes for item in self.files) > 5 * 1024**4:
            raise ValueError("asset upload exceeds the 5 TiB resource limit")
        return self


class CreateRobotModelRequest(BaseModel):
    """Create a robot model identity together with its first editable version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    manufacturer: str = Field(min_length=1, max_length=256)
    model_code: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=256)
    version_label: str = Field(min_length=1, max_length=128)

    @field_validator("manufacturer", "model_code", "display_name", "version_label")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("robot model fields must not be blank")
        return normalized


class CreateRobotModelDraftRequest(BaseModel):
    """Create an editable successor without mutating a published version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version_label: str = Field(min_length=1, max_length=128)
    update_scope: RobotModelDraftScope

    @field_validator("version_label")
    @classmethod
    def normalize_version_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("version_label must not be blank")
        return normalized


class RobotAssetPartAuthorization(BaseModel):
    """A short-lived browser PUT authorization. Never persist or cache this URL."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    part_number: int = Field(ge=1, le=10_000)
    url: str = Field(min_length=1)
    expires_at: datetime


class RobotModelAssetUploadFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    relative_path: str = Field(min_length=1, max_length=1024)
    role: RobotAssetRole
    media_type: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: RobotAssetUploadStatus
    part_size_bytes: int = Field(gt=0)
    total_parts: int = Field(ge=1, le=10_000)
    part_authorizations: tuple[RobotAssetPartAuthorization, ...] = ()


class RobotModelAssetUploadSession(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    upload_id: str = Field(min_length=1)
    version_id: str = Field(min_length=1, max_length=128)
    status: RobotAssetUploadStatus
    files: tuple[RobotModelAssetUploadFile, ...]
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class RobotModelAssetUploadEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data: RobotModelAssetUploadSession
    scope: RegistryScope
    request_id: str = Field(min_length=1, max_length=128)


class RobotAssetCompletedPart(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    part_number: int = Field(ge=1, le=10_000)
    etag: str = Field(min_length=1, max_length=512)


class CompleteRobotModelAssetFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    relative_path: str = Field(min_length=1, max_length=1024)
    parts: tuple[RobotAssetCompletedPart, ...] = Field(min_length=1, max_length=10_000)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _safe_relative_path(value)

    @model_validator(mode="after")
    def validate_parts(self) -> CompleteRobotModelAssetFileRequest:
        numbers = [item.part_number for item in self.parts]
        if numbers != sorted(numbers) or len(numbers) != len(set(numbers)):
            raise ValueError("multipart completion parts must be unique and sorted")
        return self


class AuthorizeRobotModelAssetPartsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    relative_path: str = Field(min_length=1, max_length=1024)
    part_numbers: tuple[int, ...] = Field(min_length=1, max_length=256)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _safe_relative_path(value)

    @field_validator("part_numbers")
    @classmethod
    def validate_part_numbers(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        if (
            any(number < 1 or number > 10_000 for number in values)
            or tuple(sorted(values)) != values
            or len(values) != len(set(values))
        ):
            raise ValueError("part_numbers must be unique, sorted, and between 1 and 10000")
        return values


class RobotAssetPartAuthorizationPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[RobotAssetPartAuthorization, ...]


class RobotModelAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str = Field(min_length=1)
    relative_path: str = Field(min_length=1, max_length=1024)
    role: RobotAssetRole
    media_type: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime


class RobotModelAssetPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[RobotModelAsset, ...]
    scope: RegistryScope
    request_id: str = Field(min_length=1, max_length=128)


class RobotModelAssetDownloadAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str = Field(min_length=1)
    download_url: str = Field(min_length=1)
    expires_at: datetime
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str = Field(min_length=1, max_length=128)


class RobotModelJointMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_joint_name: str = Field(min_length=1, max_length=256)
    target_joint_name: str = Field(min_length=1, max_length=256)
    direction: RobotJointDirection


class ReplaceRobotModelJointMappingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mappings: tuple[RobotModelJointMapping, ...] = Field(max_length=1024)

    @model_validator(mode="after")
    def validate_unique_joint_names(self) -> ReplaceRobotModelJointMappingsRequest:
        source_names = [item.source_joint_name for item in self.mappings]
        target_names = [item.target_joint_name for item in self.mappings]
        if len(source_names) != len(set(source_names)):
            raise ValueError("source_joint_name values must be unique")
        if len(target_names) != len(set(target_names)):
            raise ValueError("target_joint_name values must be unique")
        return self


class RobotModelJointMappingPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[RobotModelJointMapping, ...]
    mapping_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    scope: RegistryScope
    request_id: str = Field(min_length=1, max_length=128)


class RobotModelPublishCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=128)
    passed: bool
    message: str = Field(min_length=1, max_length=512)


class RobotModelPublishPreflight(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    preflight_token: str | None = Field(default=None, min_length=32, max_length=2048)
    expires_at: datetime | None = None
    expected_etag: str = Field(min_length=1, max_length=256)
    asset_manifest_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    mapping_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    checks: tuple[RobotModelPublishCheck, ...]
    blockers: tuple[BlockedReason, ...]


class RobotModelPublishPreflightEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data: RobotModelPublishPreflight
    scope: RegistryScope
    request_id: str = Field(min_length=1, max_length=128)


class PublishRobotModelVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    preflight_token: str = Field(min_length=32, max_length=2048)


class RobotModelSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    manufacturer: str = Field(min_length=1, max_length=256)
    model_code: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=256)
    current_published_version_id: str | None = Field(default=None, min_length=1, max_length=128)


class RobotModelVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    robot_model_id: str = Field(min_length=1, max_length=128)
    version_label: str = Field(min_length=1, max_length=128)
    lifecycle: str = Field(min_length=1, max_length=64)
    asset_availability: str = Field(min_length=1, max_length=64)
    publish_readiness: str = Field(min_length=1, max_length=64)
    asset_manifest_hash: str | None = Field(default=None, min_length=1, max_length=128)
    validation_input_hash: str | None = Field(default=None, min_length=1, max_length=128)
    etag: str = Field(min_length=1, max_length=256)
    allowed_actions: tuple[str, ...] = ()
    blocked_reasons: tuple[BlockedReason, ...] = ()


class RobotModelPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[RobotModelSummary, ...]
    page_info: PageInfo
    snapshot_at: datetime
    scope: RegistryScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-19"


class RobotModelVersionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data: RobotModelVersion
    scope: RegistryScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-19"
