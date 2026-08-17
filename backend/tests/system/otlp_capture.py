"""Minimal OTLP gRPC sink that records metadata, never log bodies or attributes."""

from __future__ import annotations

import argparse
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import grpc  # type: ignore[import-untyped]
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2, logs_service_pb2_grpc
from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2, metrics_service_pb2_grpc
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2, trace_service_pb2_grpc


class Capture:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.lock = threading.Lock()
        self.state: dict[str, Any] = {
            "trace_exports": 0,
            "spans": 0,
            "span_names": [],
            "metric_exports": 0,
            "metrics": 0,
            "metric_names": [],
            "log_exports": 0,
            "log_records": 0,
            "services": [],
        }

    @staticmethod
    def _service(resource: Any) -> str | None:
        for attribute in resource.attributes:
            if attribute.key == "service.name":
                return str(attribute.value.string_value)
        return None

    def record_traces(self, request: Any) -> None:
        services: set[str] = set()
        names: set[str] = set()
        count = 0
        for resource_spans in request.resource_spans:
            service = self._service(resource_spans.resource)
            if service:
                services.add(service)
            for scope_spans in resource_spans.scope_spans:
                count += len(scope_spans.spans)
                names.update(span.name for span in scope_spans.spans)
        self._update("trace_exports", "spans", count, "span_names", names, services)

    def record_metrics(self, request: Any) -> None:
        services: set[str] = set()
        names: set[str] = set()
        count = 0
        for resource_metrics in request.resource_metrics:
            service = self._service(resource_metrics.resource)
            if service:
                services.add(service)
            for scope_metrics in resource_metrics.scope_metrics:
                count += len(scope_metrics.metrics)
                names.update(metric.name for metric in scope_metrics.metrics)
        self._update("metric_exports", "metrics", count, "metric_names", names, services)

    def record_logs(self, request: Any) -> None:
        services: set[str] = set()
        count = 0
        for resource_logs in request.resource_logs:
            service = self._service(resource_logs.resource)
            if service:
                services.add(service)
            for scope_logs in resource_logs.scope_logs:
                count += len(scope_logs.log_records)
        self._update("log_exports", "log_records", count, None, set(), services)

    def _update(
        self,
        export_key: str,
        count_key: str,
        count: int,
        names_key: str | None,
        names: set[str],
        services: set[str],
    ) -> None:
        with self.lock:
            self.state[export_key] += 1
            self.state[count_key] += count
            self.state["services"] = sorted(set(self.state["services"]) | services)
            if names_key is not None:
                self.state[names_key] = sorted(set(self.state[names_key]) | names)
            temporary = self.output.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(self.state, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.output)


class TraceSink(trace_service_pb2_grpc.TraceServiceServicer):
    def __init__(self, capture: Capture) -> None:
        self.capture = capture

    def Export(self, request: Any, context: grpc.ServicerContext) -> Any:  # noqa: N802
        del context
        self.capture.record_traces(request)
        return trace_service_pb2.ExportTraceServiceResponse()


class MetricSink(metrics_service_pb2_grpc.MetricsServiceServicer):
    def __init__(self, capture: Capture) -> None:
        self.capture = capture

    def Export(self, request: Any, context: grpc.ServicerContext) -> Any:  # noqa: N802
        del context
        self.capture.record_metrics(request)
        return metrics_service_pb2.ExportMetricsServiceResponse()


class LogSink(logs_service_pb2_grpc.LogsServiceServicer):
    def __init__(self, capture: Capture) -> None:
        self.capture = capture

    def Export(self, request: Any, context: grpc.ServicerContext) -> Any:  # noqa: N802
        del context
        self.capture.record_logs(request)
        return logs_service_pb2.ExportLogsServiceResponse()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", default="0.0.0.0:4319")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    capture = Capture(args.output)
    server = grpc.server(ThreadPoolExecutor(max_workers=4))
    trace_service_pb2_grpc.add_TraceServiceServicer_to_server(  # type: ignore[no-untyped-call]
        TraceSink(capture), server
    )
    metrics_service_pb2_grpc.add_MetricsServiceServicer_to_server(  # type: ignore[no-untyped-call]
        MetricSink(capture), server
    )
    logs_service_pb2_grpc.add_LogsServiceServicer_to_server(  # type: ignore[no-untyped-call]
        LogSink(capture), server
    )
    if server.add_insecure_port(args.listen) == 0:
        raise RuntimeError(f"unable to listen on {args.listen}")
    server.start()
    print("OTLP_CAPTURE_READY", flush=True)
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop(grace=0).wait()


if __name__ == "__main__":
    main()
