from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.data_sources.repository import InMemoryDataSourceRepository
from hc_data_platform.data_sources.router import configure_data_sources, router
from hc_data_platform.data_sources.service import DataSourceService
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.workflow.router import router as workflow_router

PROJECT_ID = "p02-project"
REGION_CODE = "p02-region"
ORGANIZATION_ID = "p02-organization"


def _auth(*, read: bool = True, manage: bool = True) -> AuthContext:
    capabilities: set[tuple[str, str]] = set()
    if read:
        capabilities.add((PROJECT_ID, "ingest_source.read"))
    if manage:
        capabilities.add((PROJECT_ID, "ingest_source.manage"))
    return AuthContext(
        subject_id="p02-operator",
        organization_ids=frozenset({ORGANIZATION_ID}),
        project_ids=frozenset({PROJECT_ID}),
        region_codes=frozenset({REGION_CODE}),
        scope_pairs=frozenset({(PROJECT_ID, REGION_CODE)}),
        scoped_capabilities=frozenset(capabilities),
        organization_scope_triples=frozenset({(ORGANIZATION_ID, PROJECT_ID, REGION_CODE)}),
        organization_scoped_capabilities=frozenset(
            {(ORGANIZATION_ID, PROJECT_ID, capability) for _, capability in capabilities}
        ),
    )


def _service() -> tuple[DataSourceService, InMemoryDataSourceRepository]:
    repository = InMemoryDataSourceRepository(
        organization_projects=((ORGANIZATION_ID, PROJECT_ID),),
        robots=((ORGANIZATION_ID, PROJECT_ID, REGION_CODE, "p02-robot", "P02 集成机器人"),),
    )
    return (
        DataSourceService(
            repository,
            cursor_secret="p02-router-cursor-secret",
            credential_key="p02-router-credential-key",
            clock=lambda: datetime(2026, 8, 19, 12, tzinfo=timezone.utc),
        ),
        repository,
    )


def _app(current: dict[str, AuthContext | None]) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(ProblemException)
    async def handle_problem(_request: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(exc.problem.model_dump(mode="json"), status_code=exc.problem.status)

    @app.middleware("http")
    async def install_auth(request: Request, call_next: Any) -> Any:
        request.state.auth_context = current["value"]
        request.state.request_id = "p02-router-test"
        return await call_next(request)

    app.include_router(router)
    app.include_router(workflow_router)
    return app


def _headers(**extra: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer test",
        "X-Organization-Id": ORGANIZATION_ID,
        "X-Project-Id": PROJECT_ID,
        "X-Region-Code": REGION_CODE,
        **extra,
    }


def _robot_source_body(*, name: str, token: str | None = "p02-secret-token") -> dict[str, object]:
    body: dict[str, object] = {
        "name": name,
        "source_type": "ROBOT",
        "source_format": "MCAP",
        "source_format_version": "1",
        "binding": {"kind": "ROBOT", "robot_id": "p02-robot"},
        "configuration": {"kind": "ROBOT", "transport": "HTTPS"},
        "upload_policy_code": "STANDARD",
    }
    if token is not None:
        body["credential_input"] = {"kind": "TOKEN", "token": token}
    return body


def test_data_source_lifecycle_is_scoped_idempotent_and_secret_safe() -> None:
    service, repository = _service()
    configure_data_sources(service)
    current: dict[str, AuthContext | None] = {"value": _auth()}
    client = TestClient(_app(current))
    root = f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}/data-sources"

    page = client.get(f"{root}/page", headers=_headers())
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert page.json()["allowed_actions"] == ["CREATE"]

    page_with_frontend_defaults = client.get(
        f"{root}/page?limit=20&sort=updated_at%3Adesc%2Cid%3Adesc",
        headers=_headers(),
    )
    assert page_with_frontend_defaults.status_code == 200
    assert page_with_frontend_defaults.headers["cache-control"] == "no-store"

    create_headers = _headers(**{"Idempotency-Key": "p02-create-a"})
    created = client.post(
        root,
        headers=create_headers,
        json=_robot_source_body(name="P02 机器人源"),
    )
    assert created.status_code == 201
    assert created.headers["etag"] == '"v1"'
    assert created.headers["idempotency-replayed"] == "false"
    assert "p02-secret-token" not in created.text
    assert "credential_input" not in created.text
    source = created.json()["data"]
    assert source["binding"] == {
        "kind": "ROBOT",
        "robot_id": "p02-robot",
        "display_name": "P02 集成机器人",
    }
    assert source["configuration"]["endpoint_ref"] == "robot-connector:p02-robot"
    assert "ROTATE_CREDENTIAL" in source["allowed_actions"]

    replayed = client.post(
        root,
        headers=create_headers,
        json=_robot_source_body(name="P02 机器人源"),
    )
    assert replayed.status_code == 201
    assert replayed.headers["idempotency-replayed"] == "true"
    assert replayed.json()["data"]["id"] == source["id"]

    detail = client.get(f"{root}/{source['id']}", headers=_headers())
    assert detail.status_code == 200
    assert detail.headers["etag"] == '"v1"'

    updated = client.patch(
        f"{root}/{source['id']}",
        headers=_headers(**{"If-Match": '"v1"', "Idempotency-Key": "p02-update-a"}),
        json={
            "name": "P02 已更新机器人源",
            "source_format": "MCAP",
            "source_format_version": "2",
            "binding": {"kind": "ROBOT", "robot_id": "p02-robot"},
            "configuration": {"kind": "ROBOT", "transport": "MQTTS"},
            "upload_policy_code": "STANDARD",
            "change_reason": "更新传输方式",
        },
    )
    assert updated.status_code == 200
    source = updated.json()["data"]
    assert source["name"] == "P02 已更新机器人源"
    assert source["config_version"] == "2"
    assert source["etag"] == '"v2"'

    stale = client.patch(
        f"{root}/{source['id']}",
        headers=_headers(**{"If-Match": '"v1"', "Idempotency-Key": "p02-update-stale"}),
        json={
            "name": "不应写入",
            "source_format": "MCAP",
            "source_format_version": "2",
            "binding": {"kind": "ROBOT", "robot_id": "p02-robot"},
            "configuration": {"kind": "ROBOT", "transport": "MQTTS"},
            "upload_policy_code": "STANDARD",
            "change_reason": "陈旧版本",
        },
    )
    assert stale.status_code == 412
    assert stale.json()["code"] == "ETAG_MISMATCH"

    rotated = client.post(
        f"{root}/{source['id']}:rotate-credential",
        headers=_headers(**{"If-Match": source["etag"], "Idempotency-Key": "p02-rotate-a"}),
        json={
            "credential_input": {"kind": "TOKEN", "token": "p02-rotated-secret"},
            "reason": "常规轮换",
        },
    )
    assert rotated.status_code == 200
    assert "p02-rotated-secret" not in rotated.text
    source = rotated.json()["data"]
    assert source["credential_version"] == "2"
    assert source["credential"]["masked_hint"].startswith("token …")

    tested = client.post(
        f"{root}/{source['id']}:test-connection",
        headers=_headers(**{"If-Match": source["etag"], "Idempotency-Key": "p02-test-a"}),
        json={
            "observed_config_version": source["config_version"],
            "observed_credential_version": source["credential_version"],
        },
    )
    assert tested.status_code == 202
    assert tested.headers["location"].endswith(tested.json()["job"]["job_id"])
    assert tested.json()["data"]["status"] == "SUCCEEDED"
    job_id = tested.json()["job"]["job_id"]

    polled = client.get(f"/api/v1/jobs/{job_id}", headers=_headers())
    assert polled.status_code == 200
    assert polled.json()["status"] == "SUCCEEDED"
    assert polled.json()["resource_type"] == "DATA_SOURCE"

    source = client.get(f"{root}/{source['id']}", headers=_headers()).json()["data"]
    disabled = client.post(
        f"{root}/{source['id']}:disable",
        headers=_headers(**{"If-Match": source["etag"], "Idempotency-Key": "p02-disable-a"}),
        json={"reason": "维护窗口", "expected_administrative_state": "ENABLED"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["data"]["administrative_state"] == "DISABLED"

    source = disabled.json()["data"]
    enabled = client.post(
        f"{root}/{source['id']}:enable",
        headers=_headers(**{"If-Match": source["etag"], "Idempotency-Key": "p02-enable-a"}),
        json={"reason": "维护结束", "expected_administrative_state": "DISABLED"},
    )
    assert enabled.status_code == 200
    assert enabled.json()["data"]["administrative_state"] == "ENABLED"

    serialized_audit = json.dumps(
        [event.details for event in repository.audit_events], ensure_ascii=False
    )
    assert "p02-secret-token" not in serialized_audit
    assert "p02-rotated-secret" not in serialized_audit
    assert any(event.action == "data_source.connection_tested" for event in repository.audit_events)


def test_data_source_failed_connection_and_scope_capability_fail_closed() -> None:
    service, _repository = _service()
    configure_data_sources(service)
    current: dict[str, AuthContext | None] = {"value": _auth()}
    client = TestClient(_app(current))
    root = f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}/data-sources"

    created = client.post(
        root,
        headers=_headers(**{"Idempotency-Key": "p02-create-missing-credential"}),
        json={
            "name": "P02 未配置凭据边缘源",
            "source_type": "EDGE_AGENT",
            "source_format": "MCAP",
            "binding": {"kind": "EDGE_AGENT", "agent_id": "p02-agent"},
            "configuration": {
                "kind": "EDGE_AGENT",
                "agent_id": "p02-agent",
                "transport": "OUTBOUND_HTTPS",
                "heartbeat_policy_id": "p02-heartbeat",
            },
            "upload_policy_code": "STANDARD",
        },
    )
    assert created.status_code == 201
    source = created.json()["data"]
    assert source["credential"]["state"] == "MISSING"

    test = client.post(
        f"{root}/{source['id']}:test-connection",
        headers=_headers(**{"If-Match": source["etag"], "Idempotency-Key": "p02-test-missing"}),
        json={
            "observed_config_version": source["config_version"],
            "observed_credential_version": source["credential_version"],
        },
    )
    assert test.status_code == 202
    assert test.json()["job"]["status"] == "FAILED"
    assert test.json()["data"]["safe_error"]["code"] == "CREDENTIAL_MISSING"

    current["value"] = _auth(manage=False)
    forbidden = client.post(
        root,
        headers=_headers(**{"Idempotency-Key": "p02-forbidden"}),
        json=_robot_source_body(name="不得创建"),
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "CAPABILITY_REQUIRED"

    current["value"] = None
    unauthenticated = client.get(f"{root}/page", headers=_headers())
    assert unauthenticated.status_code == 401


def test_data_source_cursor_is_bound_to_the_complete_search_shape() -> None:
    service, _repository = _service()
    configure_data_sources(service)
    client = TestClient(_app({"value": _auth()}))
    root = f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}/data-sources"

    for index in range(11):
        created = client.post(
            root,
            headers=_headers(**{"Idempotency-Key": f"p02-cursor-create-{index}"}),
            json=_robot_source_body(name=f"全局搜索数据源 {index:02d}"),
        )
        assert created.status_code == 201

    first_page = client.get(
        f"{root}/page",
        params={
            "q": "全局搜索",
            "sort": "name:asc,id:asc",
            "limit": 10,
        },
        headers=_headers(),
    )
    assert first_page.status_code == 200
    cursor = first_page.json()["page_info"]["end_cursor"]
    assert isinstance(cursor, str) and cursor

    changed_query = client.get(
        f"{root}/page",
        params={
            "q": "不存在",
            "sort": "name:asc,id:asc",
            "after": cursor,
            "limit": 10,
        },
        headers=_headers(),
    )
    assert changed_query.status_code == 400
    assert changed_query.json()["code"] == "INVALID_CURSOR"
