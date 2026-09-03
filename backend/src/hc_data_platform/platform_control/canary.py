"""Deterministic API/frontend canary gates with exact-digest rollback decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .release_feed import ReleaseImagesV1


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CanaryThresholdsV1(_StrictModel):
    minimum_window_seconds: int = Field(default=300, ge=60, le=3_600)
    minimum_requests: int = Field(default=1_000, ge=1, le=10_000_000)
    maximum_5xx_rate: float = Field(default=0.01, ge=0, le=1)
    maximum_p95_latency_ms: float = Field(default=500, gt=0, le=60_000)


class CanaryComponentObservationV1(_StrictModel):
    component: Literal["api", "frontend"]
    started_at: datetime
    observed_at: datetime
    request_count: int = Field(ge=0)
    http_5xx_count: int = Field(ge=0)
    p95_latency_ms: float = Field(ge=0)
    ready_replicas: int = Field(ge=0)
    desired_replicas: int = Field(gt=0)
    observed_image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_window_and_counts(self) -> CanaryComponentObservationV1:
        if self.started_at.tzinfo is None or self.observed_at.tzinfo is None:
            raise ValueError("canary observation times must be timezone-aware")
        if self.observed_at <= self.started_at:
            raise ValueError("canary observation window must be positive")
        if self.http_5xx_count > self.request_count:
            raise ValueError("canary 5xx count cannot exceed request count")
        if self.ready_replicas > self.desired_replicas:
            raise ValueError("ready canary replicas cannot exceed desired replicas")
        return self


class CanaryGateDecisionV1(_StrictModel):
    format_version: Literal["hc-platform-canary-decision/v1"] = "hc-platform-canary-decision/v1"
    action: Literal["PROCEED", "HOLD", "ROLLBACK"]
    reason_codes: tuple[str, ...]
    rollback_images: ReleaseImagesV1 | None = None

    @model_validator(mode="after")
    def bind_rollback_payload(self) -> CanaryGateDecisionV1:
        if (self.action == "ROLLBACK") != (self.rollback_images is not None):
            raise ValueError("only rollback decisions may carry exact rollback images")
        return self


def evaluate_canary(
    observations: tuple[CanaryComponentObservationV1, ...],
    *,
    target_images: ReleaseImagesV1,
    source_images: ReleaseImagesV1,
    thresholds: CanaryThresholdsV1 | None = None,
) -> CanaryGateDecisionV1:
    """Fail closed on incomplete samples and roll back on an observed regression."""

    policy = thresholds or CanaryThresholdsV1()
    by_component = {item.component: item for item in observations}
    if set(by_component) != {"api", "frontend"} or len(observations) != 2:
        return CanaryGateDecisionV1(
            action="HOLD",
            reason_codes=("CANARY_COMPONENT_OBSERVATION_INCOMPLETE",),
        )

    holds: list[str] = []
    regressions: list[str] = []
    expected_digests = {
        "api": target_images.api.rsplit("@", 1)[1],
        "frontend": target_images.frontend.rsplit("@", 1)[1],
    }
    for component in ("api", "frontend"):
        observation = by_component[component]
        window_seconds = (observation.observed_at - observation.started_at).total_seconds()
        if observation.observed_image_digest != expected_digests[component]:
            regressions.append(f"CANARY_{component.upper()}_DIGEST_MISMATCH")
        if observation.ready_replicas != observation.desired_replicas:
            regressions.append(f"CANARY_{component.upper()}_NOT_READY")
        if observation.request_count:
            rate = observation.http_5xx_count / observation.request_count
            if rate > policy.maximum_5xx_rate:
                regressions.append(f"CANARY_{component.upper()}_5XX_REGRESSION")
        if observation.p95_latency_ms > policy.maximum_p95_latency_ms:
            regressions.append(f"CANARY_{component.upper()}_LATENCY_REGRESSION")
        if window_seconds < policy.minimum_window_seconds:
            holds.append(f"CANARY_{component.upper()}_WINDOW_INCOMPLETE")
        if observation.request_count < policy.minimum_requests:
            holds.append(f"CANARY_{component.upper()}_SAMPLE_INCOMPLETE")

    if regressions:
        return CanaryGateDecisionV1(
            action="ROLLBACK",
            reason_codes=tuple(sorted(set(regressions))),
            rollback_images=source_images,
        )
    if holds:
        return CanaryGateDecisionV1(
            action="HOLD",
            reason_codes=tuple(sorted(set(holds))),
        )
    return CanaryGateDecisionV1(action="PROCEED", reason_codes=("CANARY_SLO_PASSED",))


__all__ = [
    "CanaryComponentObservationV1",
    "CanaryGateDecisionV1",
    "CanaryThresholdsV1",
    "evaluate_canary",
]
