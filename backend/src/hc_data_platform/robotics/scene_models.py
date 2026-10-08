"""Immutable model inputs captured for a data snapshot, independent of storage layout."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from hc_data_platform.registry.models import RobotModelAsset, RobotModelJointMapping


def scene_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


class FrozenRobotScene(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal["hc-robot-scene/1"] = "hc-robot-scene/1"
    # This is a processing-time binding observation, never a claimed acquisition fact.
    captured_at_stage: Literal["PACKAGE_PLANNING"] = "PACKAGE_PLANNING"
    robot_id: str
    status: Literal["FROZEN", "NO_MODEL_BINDING"]
    binding_id: str | None = None
    model_id: str | None = None
    model_version_id: str | None = None
    asset_manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    assets: tuple[RobotModelAsset, ...] = ()
    joint_mapping_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    joint_mappings: tuple[RobotModelJointMapping, ...] = ()
    calibration: JsonValue
    calibration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_hashes(self) -> FrozenRobotScene:
        if scene_hash(self.calibration) != self.calibration_sha256:
            raise ValueError("frozen calibration digest mismatch")
        if self.status == "NO_MODEL_BINDING":
            if any(
                (
                    self.binding_id,
                    self.model_id,
                    self.model_version_id,
                    self.assets,
                    self.joint_mappings,
                    self.asset_manifest_sha256,
                    self.joint_mapping_sha256,
                )
            ):
                raise ValueError("missing binding cannot claim a model")
        elif (
            not self.binding_id
            or not self.model_id
            or not self.model_version_id
            or not self.assets
            or scene_hash([item.model_dump(mode="json") for item in self.assets])
            != self.asset_manifest_sha256
            or scene_hash([item.model_dump(mode="json") for item in self.joint_mappings])
            != self.joint_mapping_sha256
        ):
            raise ValueError("frozen model inputs or digest mismatch")
        return self

    @property
    def content_sha256(self) -> str:
        return scene_hash(self.model_dump(mode="json"))
