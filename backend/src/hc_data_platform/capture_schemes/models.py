"""Public capture contracts. Device mappings can change sources, never semantics."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator
from typing_extensions import Self

from hc_data_platform.continuous_recordings.mcap_contracts import (
    McapDraftConfig,
    McapProcessingConfig,
)

from .references import AddParticipants as AddParticipants
from .references import CaptureReference as CaptureReference
from .references import Identifier as Identifier
from .references import Participant as Participant
from .references import PrepareRecording as PrepareRecording
from .references import SetTaskPlan as SetTaskPlan
from .references import Sha256 as Sha256
from .references import SourceFormat as SourceFormat
from .references import Strict as Strict
from .references import VersionReference as VersionReference


class Feature(Strict):
    """Semantic identity, independent of a batch's actual presence."""

    dtype: str = Field(min_length=1, max_length=64)
    shape: tuple[int, ...] = Field(min_length=1, max_length=8)
    names: tuple[str, ...] | dict[str, tuple[str, ...]] | None = None
    units: tuple[str, ...] = ()
    role: Literal["state", "action", "rgb", "depth", "numeric", "validity", "timestamp"]
    required: bool = True
    # Missing optional data is represented by absence and/or a validity mask.
    missing: Literal["presence_and_validity"] = "presence_and_validity"
    validity_feature: str | None = None
    media: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def dimensions(self) -> Feature:
        if any(n <= 0 for n in self.shape):
            raise ValueError("feature dimensions must be positive")
        if self.role in {"state", "action"}:
            if len(self.shape) != 1 or not isinstance(self.names, tuple):
                raise ValueError("state/action require explicit one-dimensional ordered axis names")
            if len(self.names) != self.shape[0] or len(set(self.names)) != len(self.names):
                raise ValueError("state/action axis names must be unique and match the shape")
            if len(self.units) != self.shape[0] or any(
                u.strip().lower() in {"", "unknown", "未知"} for u in self.units
            ):
                raise ValueError("state/action require an explicit unit for every axis")
        if self.validity_feature == "":
            raise ValueError("validity feature must be named")
        return self


class DraftOutputContract(Strict):
    algorithm: Literal["capture-structure/v1"] = "capture-structure/v1"
    frequency_hz: float | None = Field(default=None, gt=0, le=1000, allow_inf_nan=False)
    frequency_policy: Literal["PRESERVE"] = "PRESERVE"
    time_source: Literal["FRAME_TIMESTAMP", "STATE_LOG_TIME", "RGB_LOG_TIME"]
    features: dict[str, Feature] = Field(min_length=1)

    @model_validator(mode="after")
    def fields(self) -> Self:
        for name, feature in self.features.items():
            if not name.strip() or name.startswith("__hc_"):
                raise ValueError("invalid feature name")
            if feature.validity_feature:
                mask = self.features.get(feature.validity_feature)
                if mask is None or mask.role != "validity" or not mask.required:
                    raise ValueError("validity_feature must reference a required validity field")
        return self


class OutputContract(DraftOutputContract):
    frequency_hz: float = Field(gt=0, le=1000, allow_inf_nan=False)


class DraftMcapAdapter(Strict):
    source_format: Literal["MCAP"] = "MCAP"
    parser_version: Literal["native-mcap-recording/2"] = "native-mcap-recording/2"
    configuration: McapDraftConfig


class McapAdapter(DraftMcapAdapter):
    configuration: McapProcessingConfig


class LeRobotAdapter(Strict):
    source_format: Literal["LEROBOT_V3"] = "LEROBOT_V3"
    parser_version: Literal["lerobot-v3/1"] = "lerobot-v3/1"
    codebase_version: Literal["v3.0"] = "v3.0"
    # Sources are feature keys, not ROS topics.
    feature_sources: dict[str, str] = Field(default_factory=dict)


class DraftCaptureBundleAdapter(Strict):
    source_format: Literal["CAPTURE_BUNDLE"] = "CAPTURE_BUNDLE"
    parser_version: Literal["capture-bundle/1"] = "capture-bundle/1"
    configuration: McapDraftConfig


class CaptureBundleAdapter(DraftCaptureBundleAdapter):
    configuration: McapProcessingConfig


Adapter = Annotated[
    McapAdapter | LeRobotAdapter | CaptureBundleAdapter, Field(discriminator="source_format")
]
DraftAdapter = Annotated[
    DraftMcapAdapter | LeRobotAdapter | DraftCaptureBundleAdapter,
    Field(discriminator="source_format"),
]


class CaptureDraftDefinition(Strict):
    input_mode: Literal["EXPLICIT", "AUTO"] = "EXPLICIT"
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=10000)
    software: str = Field(default="", max_length=120)
    model_version_ids: tuple[Identifier, ...] = ()
    adapters: tuple[DraftAdapter, ...] = Field(min_length=1, max_length=3)
    output: DraftOutputContract
    quality_policy: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_adapters(self) -> Self:
        formats = [a.source_format for a in self.adapters]
        if len(formats) != len(set(formats)):
            raise ValueError("each input format must have exactly one adapter")
        if self.input_mode == "AUTO":
            if set(formats) != {"MCAP", "CAPTURE_BUNDLE", "LEROBOT_V3"}:
                raise ValueError("automatic schemes require all three input formats")
            configs = [a.configuration for a in self.adapters if not isinstance(a, LeRobotAdapter)]
            if configs[0] != configs[1] or configs[0].semantic_output is None:
                raise ValueError("automatic schemes share one MCAP source configuration")
            if self.output.time_source != "FRAME_TIMESTAMP":
                raise ValueError("automatic schemes use the shared frame timestamp")
            if not {"observation.state", "action"} <= self.output.features.keys():
                raise ValueError("automatic schemes require observation.state and action")
        for adapter in self.adapters:
            if isinstance(adapter, LeRobotAdapter):
                if set(adapter.feature_sources) - set(self.output.features):
                    raise ValueError("source mappings must name output features")
            elif (
                adapter.configuration.fps is not None
                and self.output.frequency_hz is not None
                and adapter.configuration.fps != self.output.frequency_hz
            ):
                raise ValueError("MCAP frame rate must agree with the output contract")
            if not isinstance(adapter, LeRobotAdapter) and adapter.configuration.semantic_output:
                declared = DraftOutputContract.model_validate(adapter.configuration.semantic_output)
                if declared.model_dump(mode="json") != self.output.model_dump(mode="json"):
                    raise ValueError("MCAP semantic output must match the shared output contract")
        return self


class CaptureDefinition(CaptureDraftDefinition):
    adapters: tuple[Adapter, ...] = Field(min_length=1, max_length=3)
    output: OutputContract


class SchemeDraft(CaptureDraftDefinition):
    expected_revision: int = Field(ge=0)

    @model_validator(mode="before")
    @classmethod
    def automatic_inputs(cls, value):
        from .automatic import complete_adapters

        return complete_adapters(value) if isinstance(value, dict) else value


class Revision(Strict):
    expected_revision: int = Field(ge=1)


class Sample(Strict):
    project_id: Identifier
    region_code: Identifier
    raw_source_id: Identifier
    source_path: str = Field(default="meta/info.json", min_length=1, max_length=2048)
    episode_index: int = Field(default=0, ge=0)
    source_format: SourceFormat


class ValidateDraft(Revision):
    sample: Sample


class DeviceMapping(Strict):
    # Logical role/feature -> concrete source. No arbitrary configuration patch.
    sources: dict[SourceFormat, dict[str, str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_sources(self) -> DeviceMapping:
        if any(
            not k.strip() or not v.strip()
            for sources in self.sources.values()
            for k, v in sources.items()
        ):
            raise ValueError("mapping sources cannot be blank")
        return self


class AssociateScheme(Strict):
    expected_revision: int = Field(ge=0)
    scheme: VersionReference
    mapping: DeviceMapping = Field(default_factory=DeviceMapping)
    make_default: bool = False


class ValidateMapping(Strict):
    mapping_version: int = Field(ge=1)
    sample: Sample
