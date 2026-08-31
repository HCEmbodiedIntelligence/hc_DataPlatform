from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hc_data_platform.ingest.models import CompletedPart, Identifier, PartAuthorization
from hc_data_platform.tools import hf_unitree_g1_to_mcap as converter
from hc_data_platform.tools.lerobot_unitree_g1_import import (
    is_canonical_lerobot_object,
    validate_source_info,
)


def _safe_source_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip("/")
    if (
        normalized != value
        or "\x00" in normalized
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise ValueError("file path must be a normalized relative LeRobot source path")
    return value


class LeRobotSourceFileV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=1024)
    size: int = Field(gt=0, le=5 * 1024**4)
    part_count: int = Field(ge=1, le=10_000)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _safe_source_path(value)


class CreateLeRobotImportV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["lerobot-web-import/v1"] = "lerobot-web-import/v1"
    dataset_id: Identifier
    collection_task_id: Identifier
    robot_id: Identifier
    info: dict[str, Any]
    files: tuple[LeRobotSourceFileV1, ...] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def validate_revision(self) -> CreateLeRobotImportV1:
        info = validate_source_info(self.info)
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("LeRobot source file paths must be unique")
        path_set = set(paths)
        if "meta/info.json" not in path_set:
            raise ValueError("LeRobot source must contain meta/info.json")
        canonical_paths = {path for path in paths if is_canonical_lerobot_object(path)}
        if not any(path.startswith("meta/episodes/") for path in canonical_paths):
            raise ValueError("LeRobot source must contain episode metadata Parquet")
        if not any(path.startswith("data/") for path in canonical_paths):
            raise ValueError("LeRobot source must contain data Parquet")
        camera_features = {
            path.split("/", 2)[1]
            for path in canonical_paths
            if path.startswith("videos/") and path.endswith(".mp4")
        }
        expected_cameras = {camera.feature_key for camera in converter.CAMERAS}
        if camera_features != expected_cameras:
            raise ValueError("LeRobot source must contain every supported Unitree G1 camera")
        total_episodes = info.get("total_episodes")
        if not isinstance(total_episodes, int) or isinstance(total_episodes, bool):
            raise ValueError("LeRobot metadata must declare total_episodes")
        if not 1 <= total_episodes <= 100:
            raise ValueError("browser LeRobot import supports between 1 and 100 episodes")
        return self

    @property
    def episode_count(self) -> int:
        return int(self.info["total_episodes"])


class LeRobotAssetUploadGrantV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    multipart_upload_id: str | None = None
    parts: tuple[PartAuthorization, ...] = ()
    completed: bool = False


class LeRobotImportGrantV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["lerobot-web-import-grant/v1"] = "lerobot-web-import-grant/v1"
    import_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    assets: tuple[LeRobotAssetUploadGrantV1, ...]


class AuthorizeLeRobotPartsV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: Identifier
    path: str = Field(min_length=1, max_length=1024)
    multipart_upload_id: str = Field(min_length=1, max_length=2048)
    part_numbers: tuple[int, ...] = Field(min_length=1, max_length=256)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _safe_source_path(value)

    @field_validator("part_numbers")
    @classmethod
    def validate_part_numbers(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if len(value) != len(set(value)) or any(not 1 <= item <= 10_000 for item in value):
            raise ValueError("part_numbers must be unique values between 1 and 10000")
        return value


class LeRobotPartGrantV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    parts: tuple[PartAuthorization, ...]


class CompleteLeRobotAssetV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: Identifier
    path: str = Field(min_length=1, max_length=1024)
    multipart_upload_id: str = Field(min_length=1, max_length=2048)
    size: int = Field(gt=0, le=5 * 1024**4)
    part_count: int = Field(ge=1, le=10_000)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _safe_source_path(value)


class LeRobotAssetCompletedV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    size: int
    parts: tuple[CompletedPart, ...]


class CommitLeRobotImportV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest: CreateLeRobotImportV1


class LeRobotImportAcceptedV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["lerobot-web-import-accepted/v1"] = "lerobot-web-import-accepted/v1"
    import_id: str
    status: Literal["EPISODES_QUEUED"] = "EPISODES_QUEUED"
    episode_count: int = Field(ge=1, le=100)
    source_file_count: int = Field(ge=1, le=10_000)
    episode_task_count: int = Field(ge=1, le=100)
    episode_plan_key: str = Field(min_length=1, max_length=2048)
