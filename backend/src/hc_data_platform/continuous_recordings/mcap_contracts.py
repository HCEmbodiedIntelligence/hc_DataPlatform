"""Pure MCAP configuration contracts, independent of decoding and media dependencies."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing_extensions import Self


class CameraMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    camera_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    topic: str = Field(min_length=1, max_length=512)
    message_type: str = Field(min_length=1, max_length=256)
    kind: Literal["rgb", "depth"] = "rgb"
    required: bool = True

    @model_validator(mode="after")
    def identifier_kind(self) -> Self:
        if self.camera_id.endswith("_depth") != (self.kind == "depth"):
            raise ValueError(
                "depth camera identifiers must end with _depth; RGB identifiers must not"
            )
        return self


class McapDraftConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    collection_mode: Literal["RGB", "JOINT_ONLY"] = "RGB"
    time_source: Literal["RGB_LOG_TIME", "STATE_LOG_TIME"] = "RGB_LOG_TIME"
    cameras: tuple[CameraMapping, ...] = ()
    depth_unit_evidence: str | None = Field(default=None, max_length=2048)
    # Discovery provides candidates only; role assignments are explicit.
    state_topic: str | None = None
    action_topic: str | None = None
    primary_camera: str | None = None
    joint_names: tuple[str, ...] = ()
    joint_units: tuple[str, ...] = ()
    state_joint_names: tuple[str, ...] = ()
    state_joint_units: tuple[str, ...] = ()
    action_joint_names: tuple[str, ...] = ()
    action_joint_units: tuple[str, ...] = ()
    depth_scale_m_per_unit: float | None = Field(default=None, gt=0, le=1)
    derive_action_from_next_state: bool = False
    maximum_time_error_ms: float = Field(default=50, gt=0, le=1000)
    fps: int | None = Field(default=None, ge=1, le=240)

    @model_validator(mode="after")
    def axes(self) -> Self:
        if self.joint_units and len(self.joint_units) != len(self.joint_names):
            raise ValueError("joint names/units must have equal length")
        if len(set(self.joint_names)) != len(self.joint_names):
            raise ValueError("joint names must be unique")
        for role in ("state", "action"):
            names, units = self.layout(role)
            if len(set(names)) != len(names) or (units and len(units) != len(names)):
                raise ValueError(f"{role} names must be unique and units must match axes")
        if len({c.camera_id for c in self.cameras}) != len(self.cameras) or len(
            {c.topic for c in self.cameras}
        ) != len(self.cameras):
            raise ValueError("camera identifiers and topics must be unique")
        return self

    def layout(self, role: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return (
            getattr(self, role + "_joint_names") or self.joint_names,
            getattr(self, role + "_joint_units") or self.joint_units,
        )


class McapProcessingConfig(McapDraftConfig):
    fps: int = Field(default=30, ge=1, le=240)
