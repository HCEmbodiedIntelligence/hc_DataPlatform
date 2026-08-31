"""Fail-closed structured runtime logging for API and Worker processes."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

LOG_SCHEMA_VERSION = "hc-runtime-log/v1"

_EVENT_CODE = re.compile(r"^[A-Z][A-Z0-9_.-]{0,95}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,252}$")
_SENSITIVE_IDENTIFIER = re.compile(
    r"(?i)(?:://|cookie|credential|object|password|secret|token|x-amz-)"
)
_SAFE_ROUTE = re.compile(r"^/[A-Za-z0-9_{}:./-]{0,511}$")
_SAFE_ERROR_TYPE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,127}$")
_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
_HTTP_METHODS = frozenset({"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"})
_SEVERITIES = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"})

_STANDARD_RECORD_KEYS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
    }
)
_SAFE_RECORD_KEYS = frozenset(
    {
        "service",
        "instance_id",
        "node_name",
        "role",
        "release_id",
        "request_id",
        "trace_id",
        "operation_id",
        "workflow_id",
        "event_code",
        "duration_ms",
        "retry_count",
        "error_type",
        "route",
        "http_method",
        "status_code",
        "otelTraceID",
        "otelSpanID",
        "otelTraceSampled",
        "otelServiceName",
    }
)


@dataclass(frozen=True, slots=True)
class StructuredLogIdentity:
    service: str
    instance_id: str
    node_name: str
    role: Literal["api", "worker", "media-worker", "frontend", "gateway"]
    release_id: str


@dataclass(frozen=True, slots=True)
class LogCorrelation:
    request_id: str | None = None
    trace_id: str | None = None
    operation_id: str | None = None
    workflow_id: str | None = None


_current_correlation: ContextVar[LogCorrelation | None] = ContextVar(
    "hc_log_correlation",
    default=None,
)


def bind_log_correlation(correlation: LogCorrelation) -> Token[LogCorrelation | None]:
    return _current_correlation.set(correlation)


def reset_log_correlation(token: Token[LogCorrelation | None]) -> None:
    _current_correlation.reset(token)


@contextmanager
def log_correlation_scope(correlation: LogCorrelation) -> Iterator[None]:
    token = bind_log_correlation(correlation)
    try:
        yield
    finally:
        reset_log_correlation(token)


def _safe_identifier(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    if _SAFE_IDENTIFIER.fullmatch(text) and not _SENSITIVE_IDENTIFIER.search(text):
        return text
    return f"id-sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _safe_trace_id(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).lower()
    return text if _TRACE_ID.fullmatch(text) and text != "0" * 32 else None


def _current_trace_id() -> str | None:
    try:
        from opentelemetry import trace

        span_context = trace.get_current_span().get_span_context()
        if not span_context.is_valid:
            return None
        return f"{span_context.trace_id:032x}"
    except Exception:
        return None


def _event_code(value: object | None, logger_name: str) -> str:
    if value is not None:
        candidate = str(value).upper()
        if _EVENT_CODE.fullmatch(candidate):
            return candidate
    logger_token = re.sub(r"[^A-Z0-9]+", "_", logger_name.upper()).strip("_")[:64]
    candidate = f"PYTHON.{logger_token or 'ROOT'}"
    return candidate if _EVENT_CODE.fullmatch(candidate) else "PYTHON.UNCLASSIFIED"


def _bounded_number(
    value: object | None,
    *,
    minimum: float,
    maximum: float,
) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if value < minimum or value > maximum:
        return None
    return value


def _record_trace_id(record: logging.LogRecord, correlation: LogCorrelation) -> str | None:
    explicit = _safe_trace_id(getattr(record, "trace_id", None))
    if explicit is not None:
        return explicit
    contextual = _safe_trace_id(correlation.trace_id)
    if contextual is not None:
        return contextual
    otel_trace = getattr(record, "otelTraceID", None)
    if isinstance(otel_trace, int):
        otel_trace = f"{otel_trace:032x}"
    return _safe_trace_id(otel_trace) or _current_trace_id()


class SafeStructuredLogFilter(logging.Filter):
    """Remove messages, exception text, and non-allowlisted record attributes."""

    def __init__(self, identity: StructuredLogIdentity) -> None:
        super().__init__()
        self._identity = identity

    def filter(self, record: logging.LogRecord) -> bool:
        exception_type = (
            type(record.exc_info[1]).__name__
            if record.exc_info is not None and record.exc_info[1] is not None
            else None
        )
        event_code = _event_code(getattr(record, "event_code", None), record.name)
        record.msg = event_code
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        for key in tuple(record.__dict__):
            if key not in _STANDARD_RECORD_KEYS and key not in _SAFE_RECORD_KEYS:
                del record.__dict__[key]
        record.event_code = event_code
        record.service = self._identity.service
        record.instance_id = self._identity.instance_id
        record.node_name = self._identity.node_name
        record.role = self._identity.role
        record.release_id = self._identity.release_id
        if exception_type is not None and getattr(record, "error_type", None) is None:
            record.error_type = exception_type
        return True


class HcJsonFormatter(logging.Formatter):
    def __init__(self, identity: StructuredLogIdentity) -> None:
        super().__init__()
        self._identity = identity

    def format(self, record: logging.LogRecord) -> str:
        correlation = _current_correlation.get() or LogCorrelation()
        error_type_value = getattr(record, "error_type", None)
        error_type = (
            str(error_type_value)
            if error_type_value is not None and _SAFE_ERROR_TYPE.fullmatch(str(error_type_value))
            else None
        )
        route_value = getattr(record, "route", None)
        route = (
            str(route_value)
            if route_value is not None and _SAFE_ROUTE.fullmatch(str(route_value))
            else None
        )
        method_value = str(getattr(record, "http_method", "")).upper()
        http_method = method_value if method_value in _HTTP_METHODS else None
        payload: dict[str, object | None] = {
            "schema_version": LOG_SCHEMA_VERSION,
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "severity": record.levelname if record.levelname in _SEVERITIES else "INFO",
            "service": self._identity.service,
            "instance_id": _safe_identifier(self._identity.instance_id),
            "node_name": _safe_identifier(self._identity.node_name),
            "role": self._identity.role,
            "release_id": _safe_identifier(self._identity.release_id),
            "request_id": _safe_identifier(
                getattr(record, "request_id", None) or correlation.request_id
            ),
            "trace_id": _record_trace_id(record, correlation),
            "operation_id": _safe_identifier(
                getattr(record, "operation_id", None) or correlation.operation_id
            ),
            "workflow_id": _safe_identifier(
                getattr(record, "workflow_id", None) or correlation.workflow_id
            ),
            "event_code": _event_code(getattr(record, "event_code", None), record.name),
            "duration_ms": _bounded_number(
                getattr(record, "duration_ms", None), minimum=0, maximum=86_400_000
            ),
            "retry_count": _bounded_number(
                getattr(record, "retry_count", None), minimum=0, maximum=1_000_000
            ),
            "error_type": error_type,
            "route": route,
            "http_method": http_method,
            "status_code": _bounded_number(
                getattr(record, "status_code", None), minimum=100, maximum=599
            ),
        }
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def log_event(
    logger: logging.Logger,
    level: int,
    event_code: str,
    *,
    request_id: str | None = None,
    trace_id: str | None = None,
    operation_id: str | None = None,
    workflow_id: str | None = None,
    duration_ms: int | float | None = None,
    retry_count: int | None = None,
    error_type: str | None = None,
    route: str | None = None,
    http_method: str | None = None,
    status_code: int | None = None,
) -> None:
    logger.log(
        level,
        event_code,
        extra={
            "event_code": event_code,
            "request_id": request_id,
            "trace_id": trace_id,
            "operation_id": operation_id,
            "workflow_id": workflow_id,
            "duration_ms": duration_ms,
            "retry_count": retry_count,
            "error_type": error_type,
            "route": route,
            "http_method": http_method,
            "status_code": status_code,
        },
    )


def configure_structured_logging(identity: StructuredLogIdentity) -> None:
    """Install one safe stdout contract and sanitize OTLP-bound LogRecords."""

    formatter = HcJsonFormatter(identity)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    pytest_handlers = [
        handler for handler in root.handlers if handler.__class__.__module__.startswith("_pytest.")
    ]
    runtime_handlers = [handler for handler in root.handlers if handler not in pytest_handlers]
    if not runtime_handlers and not pytest_handlers:
        root.addHandler(logging.StreamHandler(sys.stdout))
        runtime_handlers = list(root.handlers)
    for handler in runtime_handlers:
        for installed_filter in tuple(handler.filters):
            if isinstance(installed_filter, SafeStructuredLogFilter):
                handler.removeFilter(installed_filter)
        handler.setFormatter(formatter)
        handler.addFilter(SafeStructuredLogFilter(identity))

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        target = logging.getLogger(logger_name)
        for handler in target.handlers:
            for installed_filter in tuple(handler.filters):
                if isinstance(installed_filter, SafeStructuredLogFilter):
                    handler.removeFilter(installed_filter)
            handler.setFormatter(formatter)
            handler.addFilter(SafeStructuredLogFilter(identity))
        if logger_name == "uvicorn.access":
            target.disabled = True
        else:
            target.propagate = True
            target.handlers.clear()
