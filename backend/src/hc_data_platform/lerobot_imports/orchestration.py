from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hc_data_platform.ingest.models import Identifier

from .source_profile import MAX_LEROBOT_IMPORT_EPISODES


class LeRobotEpisodeSourceRefV1(BaseModel):
    """Small immutable locator carried by one Episode processing task."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_format: Literal["lerobot_v3"] = "lerobot_v3"
    raw_upload_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    raw_manifest_key: str = Field(min_length=1, max_length=2048)
    episode_index: int = Field(ge=0, lt=MAX_LEROBOT_IMPORT_EPISODES)


class LeRobotEpisodeTaskV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["lerobot-episode-task/v1"] = "lerobot-episode-task/v1"
    task_id: str = Field(min_length=1, max_length=256)
    organization_id: Identifier
    project_id: Identifier
    region_code: Identifier
    dataset_id: Identifier
    collection_task_id: Identifier
    robot_id: Identifier
    source: LeRobotEpisodeSourceRefV1
    status: Literal["PENDING"] = "PENDING"


class LeRobotImportPlanV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["lerobot-import-plan/v1"] = "lerobot-import-plan/v1"
    raw_upload_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    raw_manifest_key: str = Field(min_length=1, max_length=2048)
    source_format: Literal["lerobot_v3"] = "lerobot_v3"
    episode_tasks: tuple[LeRobotEpisodeTaskV1, ...] = Field(
        min_length=1, max_length=MAX_LEROBOT_IMPORT_EPISODES
    )

    @model_validator(mode="after")
    def validate_episode_set(self) -> LeRobotImportPlanV1:
        indexes = tuple(item.source.episode_index for item in self.episode_tasks)
        if indexes != tuple(range(len(indexes))):
            raise ValueError("LeRobot Episode tasks must cover contiguous indexes from zero")
        if any(
            item.source.raw_upload_id != self.raw_upload_id
            or item.source.raw_manifest_key != self.raw_manifest_key
            for item in self.episode_tasks
        ):
            raise ValueError("LeRobot Episode task Raw lineage must match its import plan")
        return self


def build_import_plan(
    *,
    organization_id: str,
    project_id: str,
    region_code: str,
    dataset_id: str,
    collection_task_id: str,
    robot_id: str,
    raw_upload_id: str,
    raw_manifest_key: str,
    episode_count: int,
) -> LeRobotImportPlanV1:
    return LeRobotImportPlanV1(
        raw_upload_id=raw_upload_id,
        raw_manifest_key=raw_manifest_key,
        episode_tasks=tuple(
            LeRobotEpisodeTaskV1(
                task_id=f"lerobot:{raw_upload_id}:episode:{episode_index}",
                organization_id=organization_id,
                project_id=project_id,
                region_code=region_code,
                dataset_id=dataset_id,
                collection_task_id=collection_task_id,
                robot_id=robot_id,
                source=LeRobotEpisodeSourceRefV1(
                    raw_upload_id=raw_upload_id,
                    raw_manifest_key=raw_manifest_key,
                    episode_index=episode_index,
                ),
            )
            for episode_index in range(episode_count)
        ),
    )
