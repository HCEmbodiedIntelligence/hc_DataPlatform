from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hc_data_platform.ingest.models import CompletedPart, Identifier, PartAuthorization
from hc_data_platform.tools import hf_unitree_g1_to_mcap as converter

from .source_profile import (
    MAX_LEROBOT_IMPORT_EPISODES,
    is_canonical_lerobot_object,
    is_lerobot_local_cache_path,
    is_lerobot_transient_path,
    validate_source_info,
)

LEROBOT_MULTIPART_BYTES = 32 * 1024**2
MAX_LEROBOT_MULTIPART_PARTS = 10_000


def lerobot_part_plan(size: int) -> tuple[int, int]:
    part_size = max(
        LEROBOT_MULTIPART_BYTES,
        (size + MAX_LEROBOT_MULTIPART_PARTS - 1) // MAX_LEROBOT_MULTIPART_PARTS,
    )
    return part_size, (size + part_size - 1) // part_size


def lerobot_part_size(size: int, part_number: int) -> int:
    part_size, part_count = lerobot_part_plan(size)
    if not 1 <= part_number <= part_count:
        raise ValueError("part number is outside the declared LeRobot source object")
    return min(part_size, size - (part_number - 1) * part_size)


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

    @model_validator(mode="after")
    def validate_part_plan(self) -> LeRobotSourceFileV1:
        _part_size, expected_count = lerobot_part_plan(self.size)
        if self.part_count != expected_count:
            raise ValueError("part_count must match the canonical LeRobot multipart plan")
        return self


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
        local_artifacts = [
            path
            for path in paths
            if is_lerobot_local_cache_path(path) or is_lerobot_transient_path(path)
        ]
        if local_artifacts:
            raise ValueError(
                "LeRobot source cannot contain local cache or incomplete download files: "
                f"{local_artifacts[0]}"
            )
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
        if not 1 <= total_episodes <= MAX_LEROBOT_IMPORT_EPISODES:
            raise ValueError(
                "browser LeRobot import supports between 1 and "
                f"{MAX_LEROBOT_IMPORT_EPISODES} episodes"
            )
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
    completed: bool = False


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
    episode_count: int = Field(ge=1, le=MAX_LEROBOT_IMPORT_EPISODES)
    source_file_count: int = Field(ge=1, le=10_000)
    episode_task_count: int = Field(ge=1, le=MAX_LEROBOT_IMPORT_EPISODES)
    episode_plan_key: str = Field(min_length=1, max_length=2048)
