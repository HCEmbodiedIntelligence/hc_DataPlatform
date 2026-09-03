from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.logs.v1.logs_pb2 import LogRecord, ResourceLogs, ScopeLogs
from opentelemetry.proto.metrics.v1.metrics_pb2 import Metric, ResourceMetrics, ScopeMetrics
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span

BACKEND = Path(__file__).parents[2]


def _load(name: str) -> ModuleType:
    path = BACKEND / "observability" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_system_script(name: str) -> ModuleType:
    path = BACKEND / "tests" / "system" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_log_verifier_accepts_contextual_records_and_rejects_secrets() -> None:
    verifier = _load("verify_logs")
    safe = {
        "schema_version": "hc-runtime-log/v1",
        "timestamp": "2026-08-29T00:00:00.000Z",
        "severity": "INFO",
        "service": "hc-data-platform-worker",
        "instance_id": "instance-1",
        "node_name": "node-1",
        "role": "worker",
        "release_id": "platform-v1.0.0",
        "request_id": "request-1",
        "trace_id": "a" * 32,
        "operation_id": "ingest:v1:project-1:rollout-1",
        "workflow_id": "ingest:v1:project-1:rollout-1",
        "event_code": "ACTIVITY.COMPLETED",
        "duration_ms": 12.5,
        "retry_count": 0,
        "error_type": None,
    }
    assert verifier.inspect_record(safe, require_context=True) == []

    leaking = {
        **safe,
        "message": "request failed with Authorization: Bearer header.payload.signature",
        "media_url": "https://storage.invalid/object?X-Amz-Signature=deadbeef",
    }
    failures = verifier.inspect_record(leaking, require_context=True)
    assert any("sensitive value" in failure for failure in failures)
    assert any("signature" in failure.lower() for failure in failures)


def test_metric_verifier_requires_runtime_metrics_with_bounded_labels() -> None:
    verifier = _load("verify_metrics")
    lines = [
        'hc_data_upload_backlog{queue="raw-verification"} 1',
        'hc_data_workflow_failures_total{workflow_kind="ingest",error_code="timeout"} 0',
        'hc_data_qc_outcomes_total{outcome="PASS",profile_id="default"} 1',
        'hc_data_lance_commits_total{outcome="success"} 1',
        'hc_data_transcode_duration_seconds_bucket{outcome="success",view_mode="grid",le="1"} 1',
        'hc_data_exports_total{outcome="success",format="parquet"} 1',
        (
            'hc_platform_http_requests_total{route="/health/ready",method="GET",'
            'status_class="2xx",status_code="200"} 1'
        ),
        (
            "hc_platform_http_request_duration_seconds_bucket"
            '{route="/health/ready",method="GET",le="1"} 1'
        ),
    ]
    assert verifier.verify(lines)["passed"] is True

    incomplete = verifier.verify(lines[:-1])
    assert incomplete["passed"] is False
    assert any(
        "hc_platform_http_request_duration_seconds" in item for item in incomplete["failures"]
    )

    leaking = verifier.verify(
        [
            *lines,
            'hc_data_upload_backlog{queue="raw-verification",project_id="private"} 1',
        ]
    )
    assert leaking["passed"] is False
    assert any("forbidden high-cardinality" in item for item in leaking["failures"])


def test_otlp_capture_retains_counts_and_names_but_never_log_bodies(tmp_path: Path) -> None:
    capture_module = _load_system_script("otlp_capture")
    output = tmp_path / "capture.json"
    capture = capture_module.Capture(output)
    resource = Resource(
        attributes=[KeyValue(key="service.name", value=AnyValue(string_value="api"))]
    )
    capture.record_traces(
        ExportTraceServiceRequest(
            resource_spans=[
                ResourceSpans(
                    resource=resource,
                    scope_spans=[ScopeSpans(spans=[Span(name="GET /health/ready")])],
                )
            ]
        )
    )
    capture.record_metrics(
        ExportMetricsServiceRequest(
            resource_metrics=[
                ResourceMetrics(
                    resource=resource,
                    scope_metrics=[ScopeMetrics(metrics=[Metric(name="http.server.duration")])],
                )
            ]
        )
    )
    capture.record_logs(
        ExportLogsServiceRequest(
            resource_logs=[
                ResourceLogs(
                    resource=resource,
                    scope_logs=[
                        ScopeLogs(
                            log_records=[
                                LogRecord(body=AnyValue(string_value="Bearer must-not-be-retained"))
                            ]
                        )
                    ],
                )
            ]
        )
    )

    text = output.read_text(encoding="utf-8")
    result = json.loads(text)
    assert "must-not-be-retained" not in text
    assert result["services"] == ["api"]
    assert result["spans"] == result["metrics"] == result["log_records"] == 1
