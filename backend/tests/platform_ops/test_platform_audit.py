from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.platform_ops.audit import (
    InMemoryPlatformAuditRepository,
    PlatformAuditEventRecord,
    PlatformAuditService,
)
from hc_data_platform.platform_ops.maintenance import InMemoryMaintenanceWriteGate
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.capabilities import (
    CAPABILITY_PLATFORM_MAINTENANCE_VERIFY,
    CAPABILITY_PLATFORM_OPERATIONS_READ,
    CAPABILITY_PLATFORM_RELEASE_OPERATE,
)
from hc_data_platform.security.http import require_auth_context

NOW = datetime(2026, 8, 29, tzinfo=timezone.utc)


def _auth(capability: str) -> AuthContext:
    return AuthContext(
        subject_id=f"actor-{capability.replace('.', '-')}",
        project_ids=frozenset(),
        region_codes=frozenset(),
        roles=frozenset(),
        capabilities=frozenset({capability}),
    )


def _record(
    sequence_no: int,
    *,
    action: str,
    operation_kind: str,
    resource_id: str,
) -> PlatformAuditEventRecord:
    return PlatformAuditEventRecord(
        event_id=UUID(int=sequence_no),
        actor_id=f"operator-{sequence_no}",
        action=action,
        resource_type="maintenance_operation",
        resource_id=resource_id,
        request_id=f"request-{sequence_no}",
        outcome="SUCCEEDED",
        safe_details={
            "operation_kind": operation_kind,
            "status": "VERIFIED",
            "password": "do-not-export-password",
            "object_key": "do-not-export-object-key",
            "provider_url": "https://do-not-export.example/signed",
        },
        occurred_at=NOW + timedelta(seconds=sequence_no),
        sequence_no=sequence_no,
    )


def _repository() -> InMemoryPlatformAuditRepository:
    return InMemoryPlatformAuditRepository(
        (
            _record(
                1,
                action="platform.backup.catalog.verified",
                operation_kind="BACKUP",
                resource_id="backup-secret-id",
            ),
            _record(
                2,
                action="platform.maintenance.transitioned",
                operation_kind="RESTORE",
                resource_id="restore-secret-id",
            ),
            _record(
                3,
                action="platform.maintenance.transitioned",
                operation_kind="RELEASE",
                resource_id="release-secret-id",
            ),
        ),
        clock=lambda: NOW + timedelta(days=2),
    )


def test_platform_audit_projection_is_capability_bound_and_value_redacted() -> None:
    service = PlatformAuditService(
        _repository(), cursor_secret="platform-audit-test-secret", clock=lambda: NOW + timedelta(1)
    )

    with pytest.raises(ProblemException) as denied:
        service.list_events(
            auth=_auth(CAPABILITY_PLATFORM_RELEASE_OPERATE),
            request_id="request-denied",
        )
    assert denied.value.problem.status == 403
    assert denied.value.problem.code == "PLATFORM_CAPABILITY_REQUIRED"

    page = service.list_events(
        auth=_auth(CAPABILITY_PLATFORM_OPERATIONS_READ),
        request_id="request-list",
        limit=2,
    )
    assert page.count == 2
    assert page.next_cursor is not None
    assert [event.domain for event in page.items] == ["RELEASE", "RESTORE"]
    serialized = page.model_dump_json()
    for forbidden in (
        "operator-",
        "secret-id",
        "do-not-export",
        "https://",
        "password",
        "object_key",
        "provider_url",
    ):
        assert forbidden not in serialized
    assert all(
        event.redaction.retained_detail_keys == ("operation_kind", "status") for event in page.items
    )

    second = service.list_events(
        auth=_auth(CAPABILITY_PLATFORM_OPERATIONS_READ),
        request_id="request-list-2",
        cursor=page.next_cursor,
        limit=2,
    )
    assert [event.domain for event in second.items] == ["BACKUP"]

    with pytest.raises(ProblemException) as changed_filter:
        service.list_events(
            auth=_auth(CAPABILITY_PLATFORM_OPERATIONS_READ),
            request_id="request-changed-filter",
            occurred_from=NOW,
            cursor=page.next_cursor,
            limit=2,
        )
    assert changed_filter.value.problem.status == 400
    assert changed_filter.value.problem.code == "INVALID_CURSOR"


@pytest.mark.parametrize(
    ("occurred_from", "occurred_to"),
    (
        (NOW, NOW),
        (NOW + timedelta(seconds=1), NOW),
        (datetime(2026, 8, 29), NOW),
        (NOW, datetime(2026, 8, 30)),
    ),
)
def test_platform_audit_rejects_invalid_time_windows(
    occurred_from: datetime,
    occurred_to: datetime,
) -> None:
    service = PlatformAuditService(
        _repository(), cursor_secret="platform-audit-test-secret", clock=lambda: NOW + timedelta(1)
    )
    for operation in (service.list_events, service.export):
        with pytest.raises(ProblemException) as invalid:
            operation(
                auth=_auth(
                    CAPABILITY_PLATFORM_OPERATIONS_READ
                    if operation == service.list_events
                    else CAPABILITY_PLATFORM_MAINTENANCE_VERIFY
                ),
                request_id="request-invalid-window",
                occurred_from=occurred_from,
                occurred_to=occurred_to,
            )
        assert invalid.value.problem.status == 422
        assert invalid.value.problem.code == "PLATFORM_AUDIT_TIME_WINDOW_INVALID"


def test_platform_audit_export_requires_verifier_and_fails_closed_on_integrity() -> None:
    repository = _repository()
    service = PlatformAuditService(
        repository, cursor_secret="platform-audit-test-secret", clock=lambda: NOW + timedelta(1)
    )
    with pytest.raises(ProblemException) as denied:
        service.export(
            auth=_auth(CAPABILITY_PLATFORM_OPERATIONS_READ),
            request_id="request-export-denied",
        )
    assert denied.value.problem.status == 403

    exported = service.export(
        auth=_auth(CAPABILITY_PLATFORM_MAINTENANCE_VERIFY),
        request_id="request-export",
    )
    assert exported.event_count == 3
    rows = [json.loads(line) for line in exported.payload.decode().splitlines()]
    assert {row["domain"] for row in rows} == {"BACKUP", "RESTORE", "RELEASE"}
    assert all(row["integrity"] == "CHAINED" for row in rows)
    assert "do-not-export" not in exported.payload.decode()
    assert repository.integrity().checked_event_count == 4

    broken = InMemoryPlatformAuditRepository(
        (
            _record(
                2,
                action="platform.backup.catalog.verified",
                operation_kind="BACKUP",
                resource_id="backup-id",
            ),
        )
    )
    broken_service = PlatformAuditService(
        broken, cursor_secret="platform-audit-test-secret", clock=lambda: NOW + timedelta(1)
    )
    with pytest.raises(ProblemException) as integrity_failed:
        broken_service.export(
            auth=_auth(CAPABILITY_PLATFORM_MAINTENANCE_VERIFY),
            request_id="request-broken-export",
        )
    assert integrity_failed.value.problem.status == 409
    assert integrity_failed.value.problem.code == "PLATFORM_AUDIT_INTEGRITY_FAILED"


def test_platform_audit_http_contract_returns_no_store_redacted_jsonl() -> None:
    repository = _repository()
    gate = InMemoryMaintenanceWriteGate()
    app = create_app(
        settings=Settings(environment="test", runtime_backend="memory", _env_file=None),
        maintenance_write_gate=gate,
        platform_audit_repository=repository,
    )

    app.dependency_overrides[require_auth_context] = lambda: _auth(
        CAPABILITY_PLATFORM_OPERATIONS_READ
    )
    with TestClient(app) as client:
        listed = client.get("/api/v1/platform/audit/events?limit=2")
    assert listed.status_code == 200
    assert listed.headers["Cache-Control"] == "private, no-store"
    assert listed.json()["count"] == 2

    app.dependency_overrides[require_auth_context] = lambda: _auth(
        CAPABILITY_PLATFORM_MAINTENANCE_VERIFY
    )
    with TestClient(app) as client:
        integrity = client.get("/api/v1/platform/audit/integrity")
        exported = client.get("/api/v1/platform/audit/events:export")
    assert integrity.status_code == 200
    assert integrity.json()["status"] == "PASSED"
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("application/x-ndjson")
    assert exported.headers["Cache-Control"] == "private, no-store"
    assert exported.headers["Content-Disposition"].startswith("attachment; filename=")
    assert "do-not-export" not in exported.text
