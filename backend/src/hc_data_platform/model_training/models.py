from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

TrainingId = Annotated[str, Field(min_length=1, max_length=128)]
TrainingValue = bool | int | float | str
ExecutionKind = Literal["supervised", "offline_rl", "online_rl", "simulation"]
RunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class TrainingWire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class TrainingAvailability(TrainingWire):
    configured: bool


class TrainingOption(TrainingWire):
    id: TrainingId
    label: Annotated[str, Field(min_length=1, max_length=256)]


class TrainingOptions(TrainingWire):
    items: list[TrainingOption]


class TrainingConfig(TrainingOption):
    parameters: dict[str, TrainingValue]


class TrainingConfigs(TrainingWire):
    items: list[TrainingConfig]


class TrainingStart(TrainingWire):
    dataset_id: TrainingId
    model_id: TrainingId
    card_id: TrainingId
    config_id: TrainingId
    parameters: dict[str, TrainingValue]


class TrainingRun(TrainingWire):
    id: TrainingId
    dataset: TrainingOption
    model: TrainingOption
    card: TrainingOption
    config: TrainingOption
    parameters: dict[str, TrainingValue]
    status: RunStatus
    progress_percent: Annotated[float, Field(ge=0, le=100)] | None = None
    status_message: Annotated[str, Field(max_length=1024)] | None = None
    created_at: AwareDatetime


class TrainingRunPage(TrainingWire):
    items: list[TrainingRun]
    total: Annotated[int, Field(ge=0)]


class ExecutionProfile(TrainingOption):
    kind: ExecutionKind
    model_revision: TrainingId | None = None
    environment_revision: TrainingId | None = None
    dataset_requirement: Literal["required", "optional", "none"]
    parameters_schema: dict[str, JsonValue]


class ExecutionProfilePage(TrainingWire):
    items: list[ExecutionProfile]
    total: Annotated[int, Field(ge=0)]


class ExecutionStart(TrainingWire):
    profile_id: TrainingId
    dataset_version_id: TrainingId | None = None
    parameters: dict[str, JsonValue]


class ExecutionArtifact(TrainingOption):
    kind: TrainingId


class ExecutionRun(ExecutionStart):
    id: TrainingId
    kind: ExecutionKind
    status: RunStatus
    progress_percent: Annotated[float, Field(ge=0, le=100)] | None = None
    status_message: Annotated[str, Field(max_length=1024)] | None = None
    created_at: AwareDatetime
    artifacts: list[ExecutionArtifact]


class ExecutionRunPage(TrainingWire):
    items: list[ExecutionRun]
    total: Annotated[int, Field(ge=0)]
