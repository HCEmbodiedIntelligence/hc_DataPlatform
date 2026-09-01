from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import cast
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hc_data_platform.backup.catalog import (
    BackupCatalogListItem,
    BackupCatalogPage,
    BackupCatalogRepository,
    CatalogStatus,
)
from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.platform_control.release_identity import release_identity_from_settings
from hc_data_platform.platform_ops.instances import (
    InMemoryPlatformInstanceRepository,
    InstanceReadinessSummary,
    InstanceRole,
    PlatformInstanceIdentity,
    PlatformInstanceService,
)
from hc_data_platform.platform_ops.logs import (
    InMemoryPlatformLogRepository,
    LokiPlatformLogRepository,
    PlatformLogQueryError,
    PlatformLogService,
    RuntimeLogRecord,
)
from hc_data_platform.platform_ops.maintenance import InMemoryMaintenanceWriteGate
from hc_data_platform.platform_ops.overview import PlatformOperationsOverviewService
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.capabilities import CAPABILITY_PLATFORM_ADMIN
from hc_data_platform.security.http import require_auth_context

NOW = datetime(2026, 8, 29, 8, tzinfo=timezone.utc)
RELEASE_DIGEST = f"sha256:{'a' * 64}"
RELEASE_SHA = "a" * 64


def _settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "runtime_backend": "memory",
        "platform_environment_id": "production-cn-east",
        "release_id": "platform-v0.1.0-test.1",
        "git_commit": "1" * 40,
        "release_manifest_digest": RELEASE_DIGEST,
        "migration_manifest_digest": RELEASE_DIGEST,
        "component_image_digest": RELEASE_DIGEST,
        "component_role": "api",
        "cursor_secret": "platform-ui-reference-secret",
        "_env_file": None,
    }
    values.update(updates)
    return Settings(**values)  # type: ignore[arg-type]


def _auth(capability: str | None) -> AuthContext:
    return AuthContext(
        subject_id="ordinary-platform-administrator",
        project_ids=frozenset(),
        region_codes=frozenset(),
        capabilities=frozenset({capability}) if capability else frozenset(),
    )


def _log_record(*, timestamp: datetime = NOW, severity: str = "INFO") -> RuntimeLogRecord:
    return RuntimeLogRecord(
        schema_version="hc-runtime-log/v1",
        timestamp=timestamp,
        severity=severity,
        service="hc-data-platform-api",
        instance_id="instance-sensitive-sentinel",
        node_name="host-sensitive-sentinel",
        role="api",
        release_id="platform-v0.1.0-test.1",
        request_id="request-001",
        trace_id="trace-sensitive-sentinel",
        operation_id="operation-001",
        workflow_id=None,
        event_code="PLATFORM.READY",
        duration_ms=12.5,
        retry_count=0,
        error_type=None,
        route="/api/v1/platform/overview",
        http_method="GET",
        status_code=200,
    )  # type: ignore[arg-type]


def _identity(index: int, role: InstanceRole) -> PlatformInstanceIdentity:
    return PlatformInstanceIdentity(
        instance_id=UUID(int=index),
        node_name=f"host-sensitive-{role}",
        role=role,
        release_id="platform-v0.1.0-test.1",
        release_manifest_digest=RELEASE_DIGEST,
        component_image_digest=RELEASE_DIGEST,
        runtime_version="runtime-sensitive-sentinel",
        pod_name=f"pod-sensitive-{role}",
        kubernetes_node_name=f"machine-sensitive-{role}",
        kubernetes_zone="zone-sensitive-sentinel",
    )


class _BackupPageRepository:
    def __init__(self, status: CatalogStatus = "RESTORE_VERIFIED") -> None:
        self._status = status

    def list_page(self, **_: object) -> BackupCatalogPage:
        return BackupCatalogPage(
            observed_at=NOW,
            count=1,
            items=(
                BackupCatalogListItem(
                    backup_id="backup-sensitive-001",
                    format_version="hc-platform-backup/v1",
                    mode="portable",
                    status=self._status,
                    source_environment_id="production-cn-east",
                    repository_id="repository-sensitive-sentinel",
                    release_manifest_sha256=RELEASE_SHA,
                    backup_created_at=NOW - timedelta(hours=2),
                    backup_completed_at=NOW - timedelta(hours=1),
                    status_occurred_at=NOW - timedelta(minutes=30),
                ),
            ),
            next_cursor=None,
        )


def _overview_service(
    backup_status: CatalogStatus = "RESTORE_VERIFIED",
) -> PlatformOperationsOverviewService:
    settings = _settings()
    repository = InMemoryPlatformInstanceRepository(clock=lambda: NOW)
    for index, role in enumerate(("frontend", "api", "worker", "media-worker"), start=1):
        PlatformInstanceService(repository, _identity(index, role)).heartbeat(
            InstanceReadinessSummary(status="ready")
        )
    return PlatformOperationsOverviewService(
        instance_service=PlatformInstanceService(repository, _identity(10, "api")),
        backup_repository=cast(BackupCatalogRepository, _BackupPageRepository(backup_status)),
        release_identity=release_identity_from_settings(settings),
        environment_id=settings.platform_environment_id,
        reference_secret=settings.cursor_secret,
        central_log_search_configured=True,
        clock=lambda: NOW,
    )


def test_log_projection_filters_records_and_removes_host_identity() -> None:
    service = PlatformLogService(
        InMemoryPlatformLogRepository(
            (_log_record(), _log_record(timestamp=NOW - timedelta(hours=2), severity="ERROR"))
        ),
        clock=lambda: NOW,
    )
    page = service.query(
        occurred_from=NOW - timedelta(hours=1),
        occurred_to=NOW + timedelta(seconds=1),
        severity="INFO",
    )
    assert page.count == 1
    assert page.items[0].event_code == "PLATFORM.READY"
    serialized = page.model_dump_json().lower()
    for forbidden in ("instance-sensitive", "host-sensitive", "trace-sensitive", "node_name"):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    ("occurred_from", "occurred_to"),
    (
        (datetime(2026, 8, 29), NOW),
        (NOW, NOW),
        (NOW, NOW - timedelta(seconds=1)),
        (NOW - timedelta(days=8), NOW),
    ),
)
def test_log_service_rejects_unsafe_time_windows(
    occurred_from: datetime, occurred_to: datetime
) -> None:
    service = PlatformLogService(InMemoryPlatformLogRepository())
    with pytest.raises(PlatformLogQueryError) as captured:
        service.query(occurred_from=occurred_from, occurred_to=occurred_to)
    assert captured.value.code == "PLATFORM_LOG_TIME_WINDOW_INVALID"


def test_loki_adapter_builds_only_fixed_logql_and_validates_the_envelope() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "streams",
                    "result": [
                        {
                            "stream": {"service_name": "hc-data-platform-api"},
                            "values": [
                                [
                                    str(int(NOW.timestamp() * 1_000_000_000)),
                                    _log_record().model_dump_json(),
                                ]
                            ],
                        }
                    ],
                },
            },
        )

    repository = LokiPlatformLogRepository(
        "https://logs.internal/loki/api/v1/query_range",
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = repository.query(
        occurred_from=NOW - timedelta(hours=1),
        occurred_to=NOW + timedelta(seconds=1),
        service="hc-data-platform-api",
        severity="INFO",
        event_code="PLATFORM.READY",
        request_id="request-001",
        operation_id=None,
        workflow_id=None,
        limit=2,
    )
    assert len(result) == 1
    query = requests[0].url.params["query"]
    assert query == (
        '{service_name="hc-data-platform-api"} | json | severity="INFO" '
        '| event_code="PLATFORM.READY" | request_id="request-001"'
    )
    assert requests[0].url.params["direction"] == "backward"


def test_log_url_and_runtime_record_contracts_fail_closed() -> None:
    with pytest.raises(ValidationError):
        _settings(observability_log_query_url="https://logs.internal/loki/api/v1/query")
    with pytest.raises(ValidationError):
        _log_record(timestamp=datetime(2026, 8, 29))


def test_operations_overview_is_ready_and_contains_only_opaque_references() -> None:
    overview = _overview_service().overview()
    assert overview.upgrade_preflight.status == "READY"
    assert overview.node_count == 4
    assert overview.backup_count == 1
    assert all(node.node_ref.startswith("id-hmac-sha256:") for node in overview.nodes)
    assert overview.backups[0].backup_ref.startswith("id-hmac-sha256:")
    serialized = overview.model_dump_json().lower()
    for forbidden in (
        "host-sensitive",
        "pod-sensitive",
        "machine-sensitive",
        "zone-sensitive",
        "runtime-sensitive",
        "backup-sensitive",
        "repository-sensitive",
        "production-cn-east",
    ):
        assert forbidden not in serialized


def test_upgrade_preflight_requires_restore_verified_evidence_separately() -> None:
    overview = _overview_service("INTEGRITY_VERIFIED").overview()

    assert overview.upgrade_preflight.status == "BLOCKED"
    checks = {check.code: check for check in overview.upgrade_preflight.checks}
    assert checks["VERIFIED_BACKUP"].status == "PASS"
    assert checks["VERIFIED_RESTORE"].model_dump() == {
        "code": "VERIFIED_RESTORE",
        "status": "BLOCKED",
        "reason_code": "CURRENT_RELEASE_RESTORE_EVIDENCE_MISSING",
    }


def test_mock_off_operations_apis_require_capability_and_never_return_host_details() -> None:
    app = create_app(
        settings=_settings(),
        platform_log_repository=InMemoryPlatformLogRepository((_log_record(),)),
    )
    app.state.platform_operations_overview_service = _overview_service()
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/api/v1/platform/overview").status_code == 401
        app.dependency_overrides[require_auth_context] = lambda: _auth(None)
        assert client.get("/api/v1/platform/overview").status_code == 403

        app.dependency_overrides[require_auth_context] = lambda: _auth(CAPABILITY_PLATFORM_ADMIN)
        overview = client.get("/api/v1/platform/overview")
        logs = client.get(
            "/api/v1/platform/logs",
            params={
                "occurred_from": (NOW - timedelta(hours=1)).isoformat(),
                "occurred_to": (NOW + timedelta(seconds=1)).isoformat(),
                "limit": 20,
            },
        )
        injection = client.get(
            "/api/v1/platform/logs",
            params={
                "occurred_from": (NOW - timedelta(hours=1)).isoformat(),
                "occurred_to": NOW.isoformat(),
                "event_code": 'READY" | line_format "{{.node_name}}',
            },
        )

    assert overview.status_code == logs.status_code == 200
    assert (
        overview.headers["Cache-Control"] == logs.headers["Cache-Control"] == ("private, no-store")
    )
    assert injection.status_code == 422
    serialized = f"{overview.text}\n{logs.text}".lower()
    for forbidden in (
        "host-sensitive",
        "instance-sensitive",
        "trace-sensitive",
        "repository-sensitive",
        "node_name",
        "pod_name",
        "manifest_uri",
    ):
        assert forbidden not in serialized
    gate = app.state.maintenance_write_gate
    assert isinstance(gate, InMemoryMaintenanceWriteGate)
    assert any(
        event.action == "platform.operations.overview.read" for event in gate.platform_audit_events
    )
    assert any(event.action == "platform.logs.queried" for event in gate.platform_audit_events)


def test_runtime_openapi_exposes_bounded_ui_operations() -> None:
    document = create_app(settings=_settings()).openapi()
    for path, operation_id in (
        ("/api/v1/platform/overview", "getPlatformOperationsOverview"),
        ("/api/v1/platform/logs", "queryPlatformRuntimeLogs"),
    ):
        operation = document["paths"][path]["get"]
        assert operation["operationId"] == operation_id
        assert operation["security"] == [{"bearerAuth": []}]
        assert operation["x-hc-platform-capability-policy"] == {
            "mode": "any-exact",
            "capabilities": ["platform.admin", "platform.operations.read"],
        }
    assert "PlatformOperationsOverview" in document["components"]["schemas"]
    assert "PlatformLogPage" in document["components"]["schemas"]


def test_loki_invalid_contract_never_partially_returns_records() -> None:
    repository = LokiPlatformLogRepository(
        "https://logs.internal/loki/api/v1/query_range",
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "status": "success",
                        "data": {
                            "resultType": "streams",
                            "result": [
                                {"stream": {}, "values": [["1", json.dumps({"message": "raw"})]]}
                            ],
                        },
                    },
                )
            )
        ),
    )
    with pytest.raises(PlatformLogQueryError) as captured:
        repository.query(
            occurred_from=NOW - timedelta(hours=1),
            occurred_to=NOW,
            service=None,
            severity=None,
            event_code=None,
            request_id=None,
            operation_id=None,
            workflow_id=None,
            limit=20,
        )
    assert captured.value.code == "PLATFORM_LOG_BACKEND_CONTRACT_INVALID"
