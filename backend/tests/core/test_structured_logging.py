from __future__ import annotations

import io
import json
import logging
from types import ModuleType
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.health import DependencyStatus
from hc_data_platform.core.structured_logging import (
    HcJsonFormatter,
    LogCorrelation,
    SafeStructuredLogFilter,
    StructuredLogIdentity,
    log_correlation_scope,
)
from hc_data_platform.workflow.service import TemporalWorkflowLauncher

EXPECTED_FIELDS = {
    "schema_version",
    "timestamp",
    "severity",
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
}
SENTINEL = "OBS5-01-DO-NOT-LEAK"


class ReadyProbe:
    async def check(self) -> DependencyStatus:
        return DependencyStatus(status="ready")


class FakeWorkflowHandle:
    run_id = "run-1"


class FakeTemporalClient:
    async def start_workflow(self, *_args: object, **_kwargs: object) -> FakeWorkflowHandle:
        return FakeWorkflowHandle()


def _identity(*, role: str = "api") -> StructuredLogIdentity:
    return StructuredLogIdentity(
        service=f"hc-data-platform-{role}",
        instance_id="11111111-1111-4111-8111-111111111111",
        node_name="node-a",
        role="api",
        release_id="platform-v1.2.3",
    )


def _capture_handler(identity: StructuredLogIdentity) -> tuple[io.StringIO, logging.Handler]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(SafeStructuredLogFilter(identity))
    handler.setFormatter(HcJsonFormatter(identity))
    return stream, handler


def _payloads(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def _test_app() -> object:
    package = ModuleType("structured_logging_empty_package")
    package.__path__ = []  # type: ignore[attr-defined]
    return create_app(
        settings=Settings(
            environment="test",
            runtime_backend="memory",
            instance_id=UUID("11111111-1111-4111-8111-111111111111"),
            node_name="node-a",
            secret_bundle_revision=f"sha256:{'d' * 64}",
            _env_file=None,
        ),
        readiness_probes={
            "postgresql": ReadyProbe(),
            "temporal": ReadyProbe(),
            "object_storage": ReadyProbe(),
        },
        module_package=package,
    )


def test_formatter_emits_fixed_schema_and_irreversibly_redacts_hostile_values() -> None:
    stream, handler = _capture_handler(_identity())
    test_logger = logging.getLogger("hc.tests.structured.hostile")
    previous_handlers = test_logger.handlers[:]
    previous_propagate = test_logger.propagate
    test_logger.handlers = [handler]
    test_logger.propagate = False
    test_logger.setLevel(logging.INFO)
    try:
        with log_correlation_scope(
            LogCorrelation(
                request_id=f"request password={SENTINEL}",
                operation_id=f"s3://bucket/{SENTINEL}",
                workflow_id="workflow-safe-1",
            )
        ):
            try:
                raise RuntimeError(f"postgresql://user:{SENTINEL}@db/internal")
            except RuntimeError:
                test_logger.exception(
                    "Cookie=%s password=%s object_body=%s",
                    SENTINEL,
                    SENTINEL,
                    SENTINEL,
                    extra={
                        "password": SENTINEL,
                        "authorization": f"Bearer {SENTINEL}",
                        "event_code": "SECURITY.REDACTION_PROBE",
                        "route": "/api/v1/redaction-probe",
                        "http_method": "POST",
                        "status_code": 500,
                    },
                )
    finally:
        test_logger.handlers = previous_handlers
        test_logger.propagate = previous_propagate

    rendered = stream.getvalue()
    assert SENTINEL not in rendered
    assert "postgresql://" not in rendered
    assert "Cookie=" not in rendered
    payload = _payloads(stream)[0]
    assert set(payload) == EXPECTED_FIELDS
    assert payload["event_code"] == "SECURITY.REDACTION_PROBE"
    assert payload["error_type"] == "RuntimeError"
    assert str(payload["request_id"]).startswith("id-sha256:")
    assert str(payload["operation_id"]).startswith("id-sha256:")
    assert payload["workflow_id"] == "workflow-safe-1"


def test_api_access_event_uses_route_template_and_preserves_safe_request_id() -> None:
    stream, handler = _capture_handler(_identity())
    app_logger = logging.getLogger("hc_data_platform.core.app")
    previous_handlers = app_logger.handlers[:]
    previous_propagate = app_logger.propagate
    app_logger.handlers = [handler]
    app_logger.propagate = False
    app_logger.setLevel(logging.INFO)
    request_id = "22222222-2222-4222-8222-222222222222"
    try:
        with TestClient(_test_app()) as client:  # type: ignore[arg-type]
            response = client.get(
                f"/health/live?password={SENTINEL}",
                headers={
                    "X-Request-ID": request_id,
                    "Cookie": f"session={SENTINEL}",
                },
            )
    finally:
        app_logger.handlers = previous_handlers
        app_logger.propagate = previous_propagate

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == request_id
    rendered = stream.getvalue()
    assert SENTINEL not in rendered
    event = next(
        payload
        for payload in _payloads(stream)
        if payload["event_code"] == "HTTP.REQUEST_COMPLETED"
    )
    assert set(event) == EXPECTED_FIELDS
    assert event["request_id"] == request_id
    assert event["route"] == "/health/live"
    assert event["http_method"] == "GET"
    assert event["status_code"] == 200
    assert isinstance(event["duration_ms"], int | float)


@pytest.mark.asyncio
async def test_temporal_launch_event_links_request_to_workflow() -> None:
    stream, handler = _capture_handler(_identity(role="worker"))
    workflow_logger = logging.getLogger("hc_data_platform.workflow.service")
    previous_handlers = workflow_logger.handlers[:]
    previous_propagate = workflow_logger.propagate
    workflow_logger.handlers = [handler]
    workflow_logger.propagate = False
    workflow_logger.setLevel(logging.INFO)
    request_id = "33333333-3333-4333-8333-333333333333"
    workflow_id = "ingest-rollout/project-a/rollout-1"
    token = bind_request_context(RequestContext(request_id=request_id))
    try:
        launcher = TemporalWorkflowLauncher("unused", client=FakeTemporalClient())
        result = await launcher.start(
            workflow_name="IngestRolloutWorkflow",
            workflow_input={"safe": True},
            workflow_id=workflow_id,
            job_type="ingest",
            project_id="project-a",
            resource_id="rollout-1",
        )
    finally:
        reset_request_context(token)
        workflow_logger.handlers = previous_handlers
        workflow_logger.propagate = previous_propagate

    assert result.workflow_id == workflow_id
    event = _payloads(stream)[0]
    assert event["event_code"] == "WORKFLOW.STARTED"
    assert event["request_id"] == request_id
    assert event["operation_id"] == workflow_id
    assert event["workflow_id"] == workflow_id
