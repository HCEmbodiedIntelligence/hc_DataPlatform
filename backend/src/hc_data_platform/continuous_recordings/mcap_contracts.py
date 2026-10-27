"""Pure MCAP configuration contracts, independent of decoding and media dependencies."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing_extensions import Self


class JointNormalization(BaseModel):
    """Immutable position/clock semantics for the HC TJ full-body profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    profile: Literal["hc_tj_17dof/v1"] = "hc_tj_17dof/v1"
    clock_domain: str = Field(min_length=1, max_length=256)
    clock_evidence: str = Field(min_length=1, max_length=2048)
    capture_clock: Literal["header_stamp"] = "header_stamp"
    waist_fallback_ms: Literal[20] = 20
    invalid_action: Literal["drop"] = "drop"
    camera_mode: Literal["legacy_reference", "calibrated"] = "legacy_reference"
    hand_encoding: Literal["independent_ban_command"] = "independent_ban_command"


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


class NumericMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    topic: str = Field(min_length=1, max_length=512)
    message_type: str = Field(min_length=1, max_length=256)
    fields: tuple[str, ...] = Field(min_length=1, max_length=256)


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
    # Explicit logical output for shared schemes, including auxiliary signals.
    # Kept absent for legacy adapters; its identity is checked against the scheme.
    semantic_output: dict[str, Any] | None = None
    numeric_sources: dict[str, NumericMapping] = Field(default_factory=dict)
    alignment_policy: Literal["PRESERVE", "FIXED_GRID"] = "PRESERVE"
    # Explicit aliases select a single source; ambiguous simultaneous sources fail.
    topic_aliases: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    state_required_axes: tuple[str, ...] | None = None
    action_required_axes: tuple[str, ...] | None = None
    normalization: JointNormalization | None = None
    # Selected calibration evidence is retained without becoming a tensor column.
    metadata_sources: dict[Literal["head.calibration", "head.extrinsics"], str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def axes(self) -> Self:
        if self.normalization:
            from .joint_normalization import JOINTS

            if any(self.layout(role)[0] != JOINTS for role in ("state", "action")):
                raise ValueError("hc_tj_17dof/v1 requires ordered complete 17-axis state/action")
            if self.derive_action_from_next_state or not self.action_topic:
                raise ValueError("hc_tj_17dof/v1 requires independent absolute action commands")
            for side, joint in (("right", "R_ban"), ("left", "L_ban")):
                mapping = self.numeric_sources.get("action.hand." + side)
                if mapping and (mapping.topic != f"/action/{side}_hand/joint_states" or mapping.fields != ("position." + joint,)):
                    raise ValueError("hand commands require their independent named ban source")
        if self.joint_units and len(self.joint_units) != len(self.joint_names):
            raise ValueError("joint names/units must have equal length")
        if len(set(self.joint_names)) != len(self.joint_names):
            raise ValueError("joint names must be unique")
        for role in ("state", "action"):
            names, units = self.layout(role)
            if len(set(names)) != len(names) or (units and len(units) != len(names)):
                raise ValueError(f"{role} names must be unique and units must match axes")
            required = getattr(self, role + "_required_axes")
            if required is not None and (len(set(required)) != len(required) or set(required) - set(names)):
                raise ValueError(f"{role} required axes must belong to the selected axes")
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

    def required_axes(self, role: str) -> tuple[str, ...]:
        names = self.layout(role)[0]
        feature = (self.semantic_output or {}).get("features", {}).get("observation.state" if role == "state" else "action", {})
        required = feature.get("required_axes", getattr(self, role + "_required_axes"))
        return tuple(required) if required is not None else names


class McapProcessingConfig(McapDraftConfig):
    fps: int = Field(default=30, ge=1, le=240)

    @field_validator("fps", mode="before")
    @classmethod
    def nominal_rate(cls, value):
        # Scheme adapters omit rates. The decoder resolves the actual source clock.
        return 30 if value is None else value
