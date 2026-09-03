"""Capability-bound search over the sanitized central runtime-log stream."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, field_validator

RuntimeLogService = Literal[
    "hc-data-platform-api",
    "hc-data-platform-worker",
    "hc-data-platform-media-worker",
    "hc-data-platform-frontend",
    "hc-data-platform-gateway",
]
RuntimeLogRole = Literal["api", "worker", "media-worker", "frontend", "gateway"]
RuntimeLogSeverity = Literal["TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL"]
RuntimeHttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
SafeCorrelation = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=253,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9._:/-]{0,251}[A-Za-z0-9])?$",
    ),
]
SafeEventCode = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Z][A-Z0-9_.-]{0,127}$"),
]
SafeErrorType = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_.]{0,127}$"),
]
SafeRoute = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=255,
        pattern=r"^/[A-Za-z0-9{}_.:/-]*$",
    ),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RuntimeLogRecord(_StrictModel):
    """Internal fixed OBS5-01 envelope; host/process identity never reaches the UI model."""

    schema_version: Literal["hc-runtime-log/v1"]
    timestamp: datetime
    severity: RuntimeLogSeverity
    service: RuntimeLogService
    instance_id: SafeCorrelation | None
    node_name: SafeCorrelation | None
    role: RuntimeLogRole
    release_id: SafeCorrelation
    request_id: SafeCorrelation | None
    trace_id: SafeCorrelation | None
    operation_id: SafeCorrelation | None
    workflow_id: SafeCorrelation | None
    event_code: SafeEventCode
    duration_ms: float | None = Field(default=None, ge=0)
    retry_count: int | None = Field(default=None, ge=0)
    error_type: SafeErrorType | None
    route: SafeRoute | None = None
    http_method: RuntimeHttpMethod | None = None
    status_code: int | None = Field(default=None, ge=100, le=599)

    @field_validator("timestamp")
    @classmethod
    def require_timezone_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("runtime-log timestamps must be timezone-aware")
        return value


class PlatformLogEvent(_StrictModel):
    schema_version: Literal["hc-platform-log-event/v1"] = "hc-platform-log-event/v1"
    timestamp: datetime
    severity: RuntimeLogSeverity
    service: RuntimeLogService
    role: RuntimeLogRole
    release_id: SafeCorrelation
    event_code: SafeEventCode
    request_id: SafeCorrelation | None
    operation_id: SafeCorrelation | None
    workflow_id: SafeCorrelation | None
    duration_ms: float | None = Field(default=None, ge=0)
    retry_count: int | None = Field(default=None, ge=0)
    error_type: SafeErrorType | None
    route: SafeRoute | None = None
    http_method: RuntimeHttpMethod | None = None
    status_code: int | None = Field(default=None, ge=100, le=599)


class PlatformLogPage(_StrictModel):
    format_version: Literal["hc-platform-log-page/v1"] = "hc-platform-log-page/v1"
    observed_at: datetime
    occurred_from: datetime
    occurred_to: datetime
    count: int = Field(ge=0, le=200)
    truncated: bool
    items: tuple[PlatformLogEvent, ...] = Field(max_length=200)


class PlatformLogQueryError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class PlatformLogRepository(Protocol):
    def query(
        self,
        *,
        occurred_from: datetime,
        occurred_to: datetime,
        service: RuntimeLogService | None,
        severity: RuntimeLogSeverity | None,
        event_code: SafeEventCode | None,
        request_id: SafeCorrelation | None,
        operation_id: SafeCorrelation | None,
        workflow_id: SafeCorrelation | None,
        limit: int,
    ) -> tuple[RuntimeLogRecord, ...]: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _logql(
    *,
    service: RuntimeLogService | None,
    severity: RuntimeLogSeverity | None,
    event_code: SafeEventCode | None,
    request_id: SafeCorrelation | None,
    operation_id: SafeCorrelation | None,
    workflow_id: SafeCorrelation | None,
) -> str:
    selector = (
        f"{{service_name={_quoted(service)}}}"
        if service is not None
        else '{service_name=~"hc-data-platform-(api|worker|media-worker|frontend|gateway)"}'
    )
    filters = ["json"]
    for key, value in (
        ("severity", severity),
        ("event_code", event_code),
        ("request_id", request_id),
        ("operation_id", operation_id),
        ("workflow_id", workflow_id),
    ):
        if value is not None:
            filters.append(f"{key}={_quoted(value)}")
    return " | ".join((selector, *filters))


class InMemoryPlatformLogRepository:
    def __init__(self, records: Sequence[RuntimeLogRecord] = ()) -> None:
        self.records = tuple(records)

    def query(
        self,
        *,
        occurred_from: datetime,
        occurred_to: datetime,
        service: RuntimeLogService | None,
        severity: RuntimeLogSeverity | None,
        event_code: SafeEventCode | None,
        request_id: SafeCorrelation | None,
        operation_id: SafeCorrelation | None,
        workflow_id: SafeCorrelation | None,
        limit: int,
    ) -> tuple[RuntimeLogRecord, ...]:
        selected = [
            record
            for record in self.records
            if occurred_from <= record.timestamp < occurred_to
            and (service is None or record.service == service)
            and (severity is None or record.severity == severity)
            and (event_code is None or record.event_code == event_code)
            and (request_id is None or record.request_id == request_id)
            and (operation_id is None or record.operation_id == operation_id)
            and (workflow_id is None or record.workflow_id == workflow_id)
        ]
        selected.sort(key=lambda record: record.timestamp, reverse=True)
        return tuple(selected[:limit])


class UnavailablePlatformLogRepository:
    def query(self, **_: object) -> tuple[RuntimeLogRecord, ...]:
        raise PlatformLogQueryError(
            "PLATFORM_LOG_SEARCH_UNAVAILABLE",
            "central runtime-log search is not configured",
            retryable=True,
        )


HttpClientFactory = Callable[[], httpx.Client]


class LokiPlatformLogRepository:
    def __init__(
        self,
        query_url: str,
        *,
        bearer_token: SecretStr | None = None,
        client_factory: HttpClientFactory | None = None,
    ) -> None:
        self._query_url = query_url
        self._bearer_token = bearer_token
        self._client_factory = client_factory or (
            lambda: httpx.Client(timeout=httpx.Timeout(5.0, connect=2.0))
        )

    def query(
        self,
        *,
        occurred_from: datetime,
        occurred_to: datetime,
        service: RuntimeLogService | None,
        severity: RuntimeLogSeverity | None,
        event_code: SafeEventCode | None,
        request_id: SafeCorrelation | None,
        operation_id: SafeCorrelation | None,
        workflow_id: SafeCorrelation | None,
        limit: int,
    ) -> tuple[RuntimeLogRecord, ...]:
        headers = {"Accept": "application/json"}
        if self._bearer_token is not None:
            headers["Authorization"] = f"Bearer {self._bearer_token.get_secret_value()}"
        try:
            with self._client_factory() as client:
                response = client.get(
                    self._query_url,
                    headers=headers,
                    params={
                        "query": _logql(
                            service=service,
                            severity=severity,
                            event_code=event_code,
                            request_id=request_id,
                            operation_id=operation_id,
                            workflow_id=workflow_id,
                        ),
                        "start": str(int(occurred_from.timestamp() * 1_000_000_000)),
                        "end": str(int(occurred_to.timestamp() * 1_000_000_000)),
                        "direction": "backward",
                        "limit": str(limit),
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            raise PlatformLogQueryError(
                "PLATFORM_LOG_BACKEND_UNAVAILABLE",
                "the central runtime-log backend did not return a valid response",
                retryable=True,
            ) from exc
        return self._records(payload, limit=limit)

    @staticmethod
    def _records(payload: object, *, limit: int) -> tuple[RuntimeLogRecord, ...]:
        try:
            root = payload if isinstance(payload, Mapping) else {}
            if root.get("status") != "success":
                raise ValueError("Loki status is not success")
            data = root.get("data")
            if not isinstance(data, Mapping) or data.get("resultType") != "streams":
                raise ValueError("Loki response is not a streams result")
            results = data.get("result")
            if not isinstance(results, list):
                raise ValueError("Loki result is not a list")
            records: list[RuntimeLogRecord] = []
            for stream in results:
                if not isinstance(stream, Mapping) or not isinstance(stream.get("values"), list):
                    raise ValueError("Loki stream values are malformed")
                for pair in stream["values"]:
                    if (
                        not isinstance(pair, list)
                        or len(pair) != 2
                        or not isinstance(pair[0], str)
                        or not pair[0].isdigit()
                        or not isinstance(pair[1], str)
                    ):
                        raise ValueError("Loki log entry is malformed")
                    decoded = json.loads(pair[1])
                    records.append(RuntimeLogRecord.model_validate(decoded))
            records.sort(key=lambda record: record.timestamp, reverse=True)
            return tuple(records[:limit])
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise PlatformLogQueryError(
                "PLATFORM_LOG_BACKEND_CONTRACT_INVALID",
                "the central runtime-log backend violated the fixed log contract",
                retryable=False,
            ) from exc


class PlatformLogService:
    def __init__(
        self,
        repository: PlatformLogRepository,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.repository = repository
        self._clock = clock

    def query(
        self,
        *,
        occurred_from: datetime,
        occurred_to: datetime,
        service: RuntimeLogService | None = None,
        severity: RuntimeLogSeverity | None = None,
        event_code: SafeEventCode | None = None,
        request_id: SafeCorrelation | None = None,
        operation_id: SafeCorrelation | None = None,
        workflow_id: SafeCorrelation | None = None,
        limit: int = 100,
    ) -> PlatformLogPage:
        if occurred_from.tzinfo is None or occurred_to.tzinfo is None:
            raise PlatformLogQueryError(
                "PLATFORM_LOG_TIME_WINDOW_INVALID",
                "log time bounds must be timezone-aware",
                retryable=False,
            )
        if occurred_from >= occurred_to or occurred_to - occurred_from > timedelta(days=7):
            raise PlatformLogQueryError(
                "PLATFORM_LOG_TIME_WINDOW_INVALID",
                "log time bounds must be ordered and no wider than seven days",
                retryable=False,
            )
        if limit < 1 or limit > 200:
            raise PlatformLogQueryError(
                "PLATFORM_LOG_LIMIT_INVALID",
                "log result limit must be between 1 and 200",
                retryable=False,
            )
        records = self.repository.query(
            occurred_from=occurred_from,
            occurred_to=occurred_to,
            service=service,
            severity=severity,
            event_code=event_code,
            request_id=request_id,
            operation_id=operation_id,
            workflow_id=workflow_id,
            limit=limit + 1,
        )
        visible = records[:limit]
        return PlatformLogPage(
            observed_at=self._clock(),
            occurred_from=occurred_from,
            occurred_to=occurred_to,
            count=len(visible),
            truncated=len(records) > limit,
            items=tuple(
                PlatformLogEvent(
                    timestamp=record.timestamp,
                    severity=record.severity,
                    service=record.service,
                    role=record.role,
                    release_id=record.release_id,
                    event_code=record.event_code,
                    request_id=record.request_id,
                    operation_id=record.operation_id,
                    workflow_id=record.workflow_id,
                    duration_ms=record.duration_ms,
                    retry_count=record.retry_count,
                    error_type=record.error_type,
                    route=record.route,
                    http_method=record.http_method,
                    status_code=record.status_code,
                )
                for record in visible
            ),
        )
