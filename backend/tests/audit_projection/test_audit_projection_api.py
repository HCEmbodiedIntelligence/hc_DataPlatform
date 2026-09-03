from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.audit_projection.repository import (
    AuditEventRecord,
    AuditQueryFilters,
    InMemoryAuditProjectionRepository,
)
from hc_data_platform.audit_projection.router import configure_audit_projection, router
from hc_data_platform.audit_projection.service import AuditProjectionService
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.security.auth import AuthContext

ORGANIZATION_ID = "org-p19"
OTHER_ORGANIZATION_ID = "org-p19-other"
PROJECT_ID = "project-p19"
FOREIGN_PROJECT_ID = "project-p19-foreign"
REGION_CODE = "region-p19"
NOW = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)


def _events() -> tuple[AuditEventRecord, ...]:
    return (
        AuditEventRecord(
            audit_id="audit-p19-03",
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            actor_id="actor-p19",
            action="cleaning.draft.updated",
            resource_type="CLEANING_DRAFT",
            resource_id="draft-p19",
            request_id="request-p19-03",
            before_hash="a" * 64,
            after_hash="b" * 64,
            occurred_at=NOW - timedelta(minutes=1),
        ),
        AuditEventRecord(
            audit_id="audit-p19-02",
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            actor_id="actor-p19",
            action="access.grant.revoked",
            resource_type="ACCESS_GRANT",
            resource_id="grant-p19",
            request_id="request-p19-02",
            before_hash=None,
            after_hash=None,
            occurred_at=NOW - timedelta(minutes=2),
        ),
        AuditEventRecord(
            audit_id="audit-p19-01",
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            actor_id="actor-p19-other",
            action="manual_issue.created",
            resource_type="MANUAL_ISSUE",
            resource_id="issue-p19",
            request_id="request-p19-01",
            before_hash=None,
            after_hash=None,
            occurred_at=NOW - timedelta(minutes=3),
        ),
        AuditEventRecord(
            audit_id="audit-p19-foreign",
            organization_id=ORGANIZATION_ID,
            project_id=FOREIGN_PROJECT_ID,
            region_code=REGION_CODE,
            actor_id="actor-p19-foreign",
            action="access.grant.created",
            resource_type="ACCESS_GRANT",
            resource_id="grant-p19-foreign",
            request_id="request-p19-foreign",
            before_hash=None,
            after_hash=None,
            occurred_at=NOW - timedelta(minutes=1),
        ),
    )


def _service() -> AuditProjectionService:
    return AuditProjectionService(
        InMemoryAuditProjectionRepository(_events()),
        cursor_secret="p19-api-test-cursor-secret",
        clock=lambda: NOW,
    )


def _auth(*, audit: bool = True, access: bool = False, revision: int = 3) -> AuthContext:
    capabilities = {"audit.read"} if audit else set()
    if access:
        capabilities.add("access.read")
    return AuthContext(
        subject_id="actor-p19-reader",
        organization_ids=frozenset({ORGANIZATION_ID}),
        project_ids=frozenset({PROJECT_ID}),
        region_codes=frozenset({REGION_CODE}),
        capability_revision=revision,
        scope_pairs=frozenset({(PROJECT_ID, REGION_CODE)}),
        scoped_capabilities=frozenset((PROJECT_ID, capability) for capability in capabilities),
        organization_scope_triples=frozenset({(ORGANIZATION_ID, PROJECT_ID, REGION_CODE)}),
        organization_scoped_capabilities=frozenset(
            (ORGANIZATION_ID, PROJECT_ID, capability) for capability in capabilities
        ),
    )


def _app(current: dict[str, AuthContext | None]) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(ProblemException)
    async def handle_problem(_request: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(exc.problem.model_dump(mode="json"), status_code=exc.problem.status)

    @app.middleware("http")
    async def install_auth(request: Request, call_next: Any) -> Any:
        request.state.auth_context = current["value"]
        request.state.request_id = "request-p19-router"
        return await call_next(request)

    app.include_router(router)
    return app


def _headers(**extra: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer test",
        "X-Organization-Id": ORGANIZATION_ID,
        "X-Project-Id": PROJECT_ID,
        "X-Region-Code": REGION_CODE,
        **extra,
    }


def _params(**extra: str | int) -> dict[str, str | int]:
    return {
        "occurred_from": (NOW - timedelta(days=1)).isoformat(),
        "occurred_to": NOW.isoformat(),
        **extra,
    }


def test_p19_routes_are_real_read_projection_with_server_redaction_and_stable_cursors() -> None:
    configure_audit_projection(_service())
    current: dict[str, AuthContext | None] = {"value": _auth()}
    client = TestClient(_app(current))
    root = f"/api/v1/projects/{PROJECT_ID}/audit"

    bootstrap = client.get(f"{root}/bootstrap", headers=_headers(), params=_params())
    assert bootstrap.status_code == 200
    assert bootstrap.headers["cache-control"] == "private, no-store"
    assert bootstrap.json()["data"]["metrics"] == {
        "today": "3",
        "high_risk": "1",
        "failed": "0",
        "active_actors": "2",
    }
    assert bootstrap.json()["data"]["integrity"] == "PASSED"

    integrity = client.get(f"{root}/integrity", headers=_headers())
    assert integrity.status_code == 200
    assert integrity.headers["cache-control"] == "private, no-store"
    assert integrity.json()["data"] == {
        "status": "PASSED",
        "version": "core-audit-integrity-chain-v1",
        "checked_at": NOW.isoformat().replace("+00:00", "Z"),
        "checked_event_count": 3,
        "checked_chain_count": 1,
        "verified_through": (NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
    }

    facets = client.get(f"{root}/events/facets", headers=_headers(), params=_params())
    assert facets.status_code == 200
    assert facets.json()["data"]["actor_ids"] == []
    assert facets.json()["data"]["event_names"] == [
        "access.grant.revoked",
        "cleaning.draft.updated",
        "manual_issue.created",
    ]

    first = client.get(f"{root}/events", headers=_headers(), params=_params(limit=20))
    assert first.status_code == 200
    payload = first.json()
    assert [item["event_id"] for item in payload["items"]] == [
        "audit-p19-03",
        "audit-p19-02",
        "audit-p19-01",
    ]
    event = payload["items"][0]
    assert event["actor"] == {
        "type": "USER",
        "principal_id": None,
        "display_name": "已脱敏主体",
        "role_ids": [],
        "delegated_by_principal_id": None,
    }
    assert event["change"] is None
    assert event["integrity"]["record_digest"] is None
    assert event["request"]["ip_address"] is None
    assert "aaaaaaaa" not in str(payload)
    assert "bbbbbbbb" not in str(payload)

    detail = client.get(f"{root}/events/audit-p19-03", headers=_headers())
    assert detail.status_code == 200
    assert detail.headers["cache-control"] == "private, no-store"
    assert detail.json()["data"]["event_id"] == "audit-p19-03"

    # List ordering remains deterministic on a shared timestamp and the cursor
    # token is bound to this exact request window.
    limited = client.get(f"{root}/events", headers=_headers(), params=_params(limit=20))
    cursor = limited.json()["page_info"]["end_cursor"]
    mismatched = client.get(
        f"{root}/events",
        headers=_headers(),
        params=_params(after=cursor, event_name="manual_issue.created"),
    )
    assert mismatched.status_code == 400
    assert mismatched.json()["code"] == "INVALID_CURSOR"


def test_p19_actor_identity_capability_is_bound_to_the_exact_organization() -> None:
    service = _service()
    auth = AuthContext(
        subject_id="actor-p19-cross-organization-reader",
        organization_ids=frozenset({ORGANIZATION_ID, OTHER_ORGANIZATION_ID}),
        project_ids=frozenset({PROJECT_ID}),
        region_codes=frozenset({REGION_CODE}),
        scope_pairs=frozenset({(PROJECT_ID, REGION_CODE)}),
        scoped_capabilities=frozenset({(PROJECT_ID, "audit.read"), (PROJECT_ID, "access.read")}),
        organization_scope_triples=frozenset(
            {
                (ORGANIZATION_ID, PROJECT_ID, REGION_CODE),
                (OTHER_ORGANIZATION_ID, PROJECT_ID, REGION_CODE),
            }
        ),
        organization_scoped_capabilities=frozenset(
            {
                (ORGANIZATION_ID, PROJECT_ID, "audit.read"),
                (OTHER_ORGANIZATION_ID, PROJECT_ID, "access.read"),
            }
        ),
    )
    filters = AuditQueryFilters(NOW - timedelta(days=1), NOW)

    page = service.list_events(
        auth=auth,
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
        filters=filters,
        after=None,
        before=None,
        limit=20,
        request_id="request-p19-exact-organization-capability",
    )
    facets = service.facets(
        auth=auth,
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
        filters=filters,
        request_id="request-p19-exact-organization-capability",
    )

    assert page.items
    assert all(item.actor.principal_id is None for item in page.items)
    assert facets.data.actor_ids == ()


def test_p19_cursor_pagination_authorization_and_scope_fail_closed() -> None:
    configure_audit_projection(_service())
    current: dict[str, AuthContext | None] = {"value": _auth(access=True)}
    client = TestClient(_app(current))
    root = f"/api/v1/projects/{PROJECT_ID}/audit"

    first = client.get(f"{root}/events", headers=_headers(), params=_params(limit=20))
    assert first.status_code == 200
    assert first.json()["items"][0]["actor"]["principal_id"] == "actor-p19"
    cursor = first.json()["page_info"]["end_cursor"]

    current["value"] = _auth(access=True, revision=4)
    authorization_changed = client.get(
        f"{root}/events", headers=_headers(), params=_params(after=cursor)
    )
    assert authorization_changed.status_code == 409
    assert authorization_changed.json()["code"] == "AUDIT_CURSOR_AUTH_CHANGED"

    current["value"] = _auth(audit=False)
    forbidden = client.get(f"{root}/events", headers=_headers(), params=_params())
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "CAPABILITY_REQUIRED"

    current["value"] = _auth()
    foreign = client.get(
        f"/api/v1/projects/{FOREIGN_PROJECT_ID}/audit/events",
        headers={**_headers(), "X-Project-Id": FOREIGN_PROJECT_ID},
        params=_params(),
    )
    assert foreign.status_code == 403
    assert foreign.json()["code"] == "ORGANIZATION_SCOPE_DENIED"

    mismatched_region = client.get(
        f"{root}/events",
        headers=_headers(),
        params=[
            ("occurred_from", (NOW - timedelta(days=1)).isoformat()),
            ("occurred_to", NOW.isoformat()),
            ("region_code", "region-other"),
        ],
    )
    assert mismatched_region.status_code == 422
    assert mismatched_region.json()["code"] == "AUDIT_REGION_FILTER_MISMATCH"


def test_p19_service_rejects_expired_cursor_and_invalid_window() -> None:
    service = _service()
    auth = _auth()
    filters = AuditQueryFilters(NOW - timedelta(days=1), NOW)
    page = service.list_events(
        auth=auth,
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
        filters=filters,
        after=None,
        before=None,
        limit=20,
        request_id="request-p19-service",
    )
    cursor = page.page_info.end_cursor
    assert cursor is not None
    later = AuditProjectionService(
        InMemoryAuditProjectionRepository(_events()),
        cursor_secret="p19-api-test-cursor-secret",
        clock=lambda: NOW + timedelta(minutes=16),
    )
    with pytest.raises(ProblemException) as expired:
        later.list_events(
            auth=auth,
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            filters=filters,
            after=cursor,
            before=None,
            limit=20,
            request_id="request-p19-service",
        )
    assert expired.value.problem.status == 410
    assert expired.value.problem.code == "AUDIT_CURSOR_EXPIRED"

    with pytest.raises(ProblemException) as too_large:
        service.list_events(
            auth=auth,
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            filters=AuditQueryFilters(NOW - timedelta(days=32), NOW),
            after=None,
            before=None,
            limit=20,
            request_id="request-p19-service",
        )
    assert too_large.value.problem.code == "AUDIT_TIME_RANGE_TOO_LARGE"


def test_p19_integrity_failure_is_visible_only_as_safe_aggregate() -> None:
    service = AuditProjectionService(
        InMemoryAuditProjectionRepository(_events(), integrity_status="FAILED"),
        cursor_secret="p19-api-test-cursor-secret",
        clock=lambda: NOW,
    )
    result = service.integrity(
        auth=_auth(),
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
        request_id="request-p19-integrity-failure",
    )

    assert result.data.status == "FAILED"
    assert result.data.checked_event_count == 3
    assert result.data.checked_chain_count == 1
    assert result.data.verified_through == NOW - timedelta(minutes=1)
