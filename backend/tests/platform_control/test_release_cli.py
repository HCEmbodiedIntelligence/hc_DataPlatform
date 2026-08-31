from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hc_data_platform.platform_control.release_cli import (
    CanaryEvaluationDocumentV1,
    evaluate_canary_document,
)

NOW = datetime(2026, 8, 29, 10, tzinfo=timezone.utc)
A = "a" * 64
B = "b" * 64


def _document(*, http_5xx_count: int) -> CanaryEvaluationDocumentV1:
    source = {
        name: f"registry/{name.replace('_', '-')}@sha256:{A}"
        for name in ("frontend", "api", "worker", "media_worker")
    }
    target = {
        name: f"registry/{name.replace('_', '-')}@sha256:{B}"
        for name in ("frontend", "api", "worker", "media_worker")
    }
    observations = [
        {
            "component": component,
            "started_at": NOW,
            "observed_at": NOW + timedelta(minutes=6),
            "request_count": 2_000,
            "http_5xx_count": http_5xx_count if component == "api" else 0,
            "p95_latency_ms": 100,
            "ready_replicas": 1,
            "desired_replicas": 1,
            "observed_image_digest": f"sha256:{B}",
        }
        for component in ("api", "frontend")
    ]
    return CanaryEvaluationDocumentV1.model_validate(
        {"source_images": source, "target_images": target, "observations": observations}
    )


def test_external_controller_receives_exact_rollback_payload_on_5xx_injection() -> None:
    decision = evaluate_canary_document(_document(http_5xx_count=40))
    assert decision.action == "ROLLBACK"
    assert decision.rollback_images == _document(http_5xx_count=0).source_images
    serialized = decision.model_dump_json().lower()
    assert "kubeconfig" not in serialized
    assert "cluster-admin" not in serialized
