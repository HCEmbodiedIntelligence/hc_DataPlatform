from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

TrainingId = Annotated[str, Field(min_length=1, max_length=128)]
TrainingValue = bool | int | float | str


class TrainingWire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


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
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    progress_percent: Annotated[float, Field(ge=0, le=100)] | None = None
    status_message: Annotated[str, Field(max_length=1024)] | None = None
    created_at: AwareDatetime


class TrainingRunPage(TrainingWire):
    items: list[TrainingRun]
    total: Annotated[int, Field(ge=0)]
