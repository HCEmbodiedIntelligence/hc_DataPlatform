"""QC options belong to a capture scheme, not a separate project configuration."""

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator
from hc_data_platform.quality.models import (
    ActionQualityProfileV1, CompleteStepProfileV1, ImageQualityProfileV1,
    ModalityOffsetProfileV1, PointCloudQualityProfileV1, TopicTimingProfileV1,
)


class CaptureTimingRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    minimum_frequency_ratio_risk: float = Field(default=0.9, ge=0, le=1)
    minimum_frequency_ratio_reject: float = Field(default=0.75, ge=0, le=1)
    minimum_coverage_ratio_risk: float = Field(default=0.98, ge=0, le=1)
    minimum_coverage_ratio_reject: float = Field(default=0.8, ge=0, le=1)
    maximum_gap_ns_risk: int = Field(default=100_000_000, ge=0)
    maximum_gap_ns_reject: int = Field(default=1_000_000_000, ge=0)
    maximum_consecutive_missing_risk: int = Field(default=2, ge=0)
    maximum_consecutive_missing_reject: int = Field(default=29, ge=0)
    maximum_duplicate_timestamps_risk: int = Field(default=0, ge=0)
    maximum_duplicate_timestamps_reject: int | None = Field(default=None, ge=0)
    maximum_backward_timestamps_reject: int = Field(default=0, ge=0)

    def compile(self, frequency):
        value = self.model_dump(exclude={"minimum_frequency_ratio_risk", "minimum_frequency_ratio_reject"})
        return TopicTimingProfileV1(**value, target_frequency_hz=frequency,
            minimum_frequency_hz_risk=frequency * self.minimum_frequency_ratio_risk,
            minimum_frequency_hz_reject=frequency * self.minimum_frequency_ratio_reject)

    @model_validator(mode="after")
    def bounds(self):
        self.compile(30)
        return self


class CaptureQualityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True,
                              json_schema_extra=lambda schema: schema.pop("required", None))
    missing_required: Literal["reject"] = "reject"
    timing: CaptureTimingRules = Field(default_factory=CaptureTimingRules)
    feature_timing: dict[str, CaptureTimingRules] = Field(default_factory=dict)
    image: ImageQualityProfileV1 = Field(default_factory=ImageQualityProfileV1)
    feature_images: dict[str, ImageQualityProfileV1] = Field(default_factory=dict)
    joint_limits: dict[str, tuple[float, float]] = Field(default_factory=dict)
    joint_out_of_range_is_reject: bool = True
    action: ActionQualityProfileV1 = Field(default_factory=ActionQualityProfileV1)
    point_cloud: PointCloudQualityProfileV1 = Field(default_factory=PointCloudQualityProfileV1)
    feature_point_clouds: dict[str, PointCloudQualityProfileV1] = Field(default_factory=dict)
    synchronization: ModalityOffsetProfileV1 = Field(default_factory=ModalityOffsetProfileV1)
    completeness: CompleteStepProfileV1 = Field(default_factory=CompleteStepProfileV1)

    @model_serializer(mode="wrap")
    def historical_definition(self, handler):
        return {key: value for key, value in handler(self).items() if key in self.model_fields_set}

    @model_validator(mode="after")
    def axes(self):
        for name, (low, high) in self.joint_limits.items():
            if not name.strip() or not math.isfinite(low) or not math.isfinite(high) or low >= high:
                raise ValueError("joint limits require a name and finite ordered bounds")
        if self.action.topic != "/action" or self.completeness.topic != "__multimodal_step__":
            raise ValueError("QC source roles come from the capture scheme fields")
        return self
