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
        "level": "INFO",
        "message": "workflow activity completed",
        "request_id": "request-1",
        "project_id": "project-1",
        "resource_id": "rollout-1",
        "workflow_id": "ingest:v1:project-1:rollout-1",
        "error_code": None,
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


def test_metric_verifier_requires_all_six_metrics_with_locator_labels() -> None:
    verifier = _load("verify_metrics")
    labels = 'project_id="p",resource_id="r",workflow_id="w"'
    lines = [
        f"hc_data_upload_backlog{{{labels}}} 1",
        f"hc_data_workflow_failures_total{{{labels}}} 0",
        f'hc_data_qc_outcomes_total{{{labels},outcome="PASS"}} 1',
        f'hc_data_lance_commits_total{{{labels},outcome="success"}} 1',
        f'hc_data_transcode_duration_seconds_bucket{{{labels},le="1"}} 1',
        f'hc_data_exports_total{{{labels},outcome="success"}} 1',
    ]
    assert verifier.verify(lines)["passed"] is True

    incomplete = verifier.verify(lines[:-1])
    assert incomplete["passed"] is False
    assert any("hc_data_exports_total" in item for item in incomplete["failures"])


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
