"""Pure MCAP configuration contracts, independent of decoding and media dependencies."""

from functools import cached_property
from typing import Annotated, Any, Literal

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


class ActionPostprocessingRules(BaseModel):
    """Rules applied to the derived MCAP before quality checks."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    version: Literal[1]
    missing_position_axes: tuple[str, ...]
    nan_position_axes: tuple[str, ...]
    max_state_delta_ms: float = Field(gt=0, le=1000)
    missing_action_topic: Literal["reject", "copy_state"]


AxisNames = Annotated[tuple[Annotated[str, Field(min_length=1, max_length=128)], ...], Field(max_length=64)]


class RuleOption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    enabled: bool


class ZeroJoints(RuleOption):
    kind: Literal["zero_joints"]
    axes: AxisNames


class ActionFromState(RuleOption):
    kind: Literal["action_from_state"]
    axes: AxisNames
    match: Literal["at_or_after"]


class FillPosition(RuleOption):
    kind: Literal["fill_position"]
    missing_position_axes: AxisNames | None
    nan_position_axes: AxisNames
    max_state_delta_ms: float = Field(gt=0, le=1000)


HAND_COMMAND_FEATURES = frozenset({"action.hand.left", "action.hand.right"})


class ForwardFillHands(RuleOption):
    kind: Literal["forward_fill_hands"]


class FillActionFromNextFrame(RuleOption):
    kind: Literal["fill_action_from_next_frame"]


class FillInitialAction(RuleOption):
    kind: Literal["fill_initial_action"]


class GenerateAction(RuleOption):
    kind: Literal["generate_action"]
    source: Literal["state"]


Operation = Annotated[ZeroJoints | ActionFromState | FillPosition | GenerateAction | FillInitialAction | FillActionFromNextFrame | ForwardFillHands, Field(discriminator="kind")]


class JointEditRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[2]
    rules: tuple[Operation, ...] = Field(max_length=7)

    @cached_property
    def active(self):
        return {op.kind: op for op in self.rules if op.enabled}

    @property
    def zero_joints(self):
        return self.active["zero_joints"].axes if "zero_joints" in self.active else ()

    @property
    def action_from_state(self):
        return self.active["action_from_state"].axes if "action_from_state" in self.active else ()

    @property
    def missing_action_topic(self):
        return "copy_state" if "generate_action" in self.active else "reject"

    @model_validator(mode="after")
    def distinct_operations(self) -> Self:
        if len({op.kind for op in self.rules}) != len(self.rules):
            raise ValueError("duplicate rule")
        for op in self.rules:
            groups = ((op.axes,) if isinstance(op, (ZeroJoints, ActionFromState)) else
                      (op.missing_position_axes or (), op.nan_position_axes) if isinstance(op, FillPosition) else ())
            if any(len(set(g)) != len(g) for g in groups):
                raise ValueError("duplicate joint in rule")
            if isinstance(op, (ZeroJoints, ActionFromState)) and op.enabled and not op.axes:
                raise ValueError("select at least one joint")
        if "fill_action_from_next_frame" in self.active and {"fill_initial_action", "generate_action"} & self.active.keys():
            raise ValueError("select only one action generation policy")
        if set(self.zero_joints) & set(self.action_from_state):
            raise ValueError("enabled zero and replacement joints must be disjoint")
        return self


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
    postprocessing_rules: ActionPostprocessingRules | JointEditRules | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    # Selected calibration evidence is retained without becoming a tensor column.
    metadata_sources: dict[Literal["head.calibration", "head.extrinsics"], str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def axes(self) -> Self:
        rules = self.postprocessing_rules
        if rules is not None:
            if rules.version == 1 and (not self.normalization or not self.state_topic or self.state_topic == self.action_topic):
                raise ValueError("action repair requires distinct HC TJ state and action topics")
            axes = set(self.layout("action")[0])
            if isinstance(rules, JointEditRules):
                groups = [g for op in rules.rules if op.enabled for g in (
                    (op.axes,) if isinstance(op, ActionFromState) else
                    (op.missing_position_axes or (), op.nan_position_axes) if isinstance(op, FillPosition) else ())]
            else:
                groups = (rules.missing_position_axes, rules.nan_position_axes)
            for names in groups:
                if len(names) != len(set(names)) or set(names) - axes:
                    raise ValueError("action repair axes must be unique named action joints")
            if isinstance(rules, JointEditRules) and "forward_fill_hands" in rules.active:
                hands = HAND_COMMAND_FEATURES.intersection(self.numeric_sources)
                features = (self.semantic_output or {}).get("features", {})
                if not hands or any(features.get(name, {}).get("role") != "numeric"
                                    or tuple(features[name].get("shape", ())) != (1,)
                                    for name in hands):
                    raise ValueError("hand forward fill requires a mapped scalar hand command")
            if isinstance(rules, JointEditRules) and rules.active.keys() - {"forward_fill_hands"}:
                state, state_units = self.layout("state")
                action, action_units = self.layout("action")
                if not self.state_topic or not self.action_topic or self.state_topic == self.action_topic:
                    raise ValueError("joint edits require distinct state/action topics")
                if self.derive_action_from_next_state or not state or not action:
                    raise ValueError("joint edits require explicitly named state/action layouts")
                if not self.semantic_output:
                    raise ValueError("joint edits require a capture scheme output definition")
                needed = set(rules.action_from_state)
                fill = rules.active.get("fill_position")
                if fill:
                    if fill.missing_position_axes is None and not self.normalization:
                        raise ValueError("select explicit missing position axes for this scheme")
                    needed.update(fill.missing_position_axes or ())
                    needed.update(fill.nan_position_axes)
                if {"fill_initial_action", "fill_action_from_next_frame"} & rules.active.keys() and any(
                    signal.topic == self.action_topic for signal in self.numeric_sources.values()
                ):
                    raise ValueError("action gap fill requires a dedicated joint action topic")
                if (rules.missing_action_topic == "copy_state" or
                        {"fill_initial_action", "fill_action_from_next_frame"} & rules.active.keys()):
                    needed.update(set(action) - set(rules.zero_joints))
                su, au = dict(zip(state, state_units)), dict(zip(action, action_units))
                if any(n not in su or not su[n] or su[n] != au.get(n) for n in needed):
                    raise ValueError("state source joints must have matching names and units")
            if rules.version == 1 and self.layout("state") != self.layout("action"):
                raise ValueError("state fallback requires matching state/action joints and units")
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
