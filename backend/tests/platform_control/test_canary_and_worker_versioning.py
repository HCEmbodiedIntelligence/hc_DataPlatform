from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hc_data_platform.platform_control.canary import (
    CanaryComponentObservationV1,
    evaluate_canary,
)
from hc_data_platform.platform_control.release_feed import ReleaseImagesV1
from hc_data_platform.platform_control.worker_versioning import (
    WorkerVersioningPlanV1,
    install_compatible_build_routing,
)

NOW = datetime(2026, 8, 29, 10, tzinfo=timezone.utc)
A = "a" * 64
B = "b" * 64
SOURCE = ReleaseImagesV1(
    frontend=f"registry/frontend@sha256:{A}",
    api=f"registry/api@sha256:{A}",
    worker=f"registry/worker@sha256:{A}",
    media_worker=f"registry/media-worker@sha256:{A}",
)
TARGET = ReleaseImagesV1(
    frontend=f"registry/frontend@sha256:{B}",
    api=f"registry/api@sha256:{B}",
    worker=f"registry/worker@sha256:{B}",
    media_worker=f"registry/media-worker@sha256:{B}",
)


def _observation(component: str, **updates: object) -> CanaryComponentObservationV1:
    values: dict[str, object] = {
        "component": component,
        "started_at": NOW,
        "observed_at": NOW + timedelta(minutes=6),
        "request_count": 2_000,
        "http_5xx_count": 1,
        "p95_latency_ms": 120,
        "ready_replicas": 1,
        "desired_replicas": 1,
        "observed_image_digest": f"sha256:{B}",
    }
    values.update(updates)
    return CanaryComponentObservationV1.model_validate(values)


def test_canary_proceeds_only_after_complete_api_and_frontend_slo_window() -> None:
    decision = evaluate_canary(
        (_observation("api"), _observation("frontend")),
        target_images=TARGET,
        source_images=SOURCE,
    )
    assert decision.action == "PROCEED"
    assert decision.rollback_images is None


@pytest.mark.parametrize(
    ("component", "updates", "reason"),
    (
        ("api", {"http_5xx_count": 25}, "CANARY_API_5XX_REGRESSION"),
        ("frontend", {"p95_latency_ms": 900}, "CANARY_FRONTEND_LATENCY_REGRESSION"),
    ),
)
def test_injected_regression_stops_and_rolls_back_exact_source_digests(
    component: str, updates: dict[str, object], reason: str
) -> None:
    observations = tuple(
        _observation(name, **(updates if name == component else {})) for name in ("api", "frontend")
    )
    decision = evaluate_canary(
        observations,
        target_images=TARGET,
        source_images=SOURCE,
    )
    assert decision.action == "ROLLBACK"
    assert reason in decision.reason_codes
    assert decision.rollback_images == SOURCE


def test_canary_holds_without_the_full_window_or_both_components() -> None:
    decision = evaluate_canary(
        (_observation("api", request_count=12),),
        target_images=TARGET,
        source_images=SOURCE,
    )
    assert decision.action == "HOLD"


@pytest.mark.asyncio
async def test_temporal_target_build_is_compatible_default_on_every_queue() -> None:
    calls: list[tuple[str, object]] = []

    class Client:
        async def update_worker_build_id_compatibility(
            self, task_queue: str, operation: object
        ) -> None:
            calls.append((task_queue, operation))

    plan = WorkerVersioningPlanV1(
        task_queues=("hc-data-pipeline", "hc-media-pipeline"),
        source_build_id="hc-platform-0.1.0",
        target_build_id="hc-platform-0.1.1",
    )
    installed = await install_compatible_build_routing(
        Client(),
        plan,
        operation_factory=lambda target, source: (target, source, "compatible-default"),
    )
    assert installed == plan.task_queues
    assert calls == [
        ("hc-data-pipeline", ("hc-platform-0.1.1", "hc-platform-0.1.0", "compatible-default")),
        ("hc-media-pipeline", ("hc-platform-0.1.1", "hc-platform-0.1.0", "compatible-default")),
    ]
