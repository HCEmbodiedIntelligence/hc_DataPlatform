from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.collection_tasks.router import configure_collection_tasks, router
from hc_data_platform.collection_tasks.service import CollectionTaskService
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.security.auth import AuthContext

ORGANIZATION_ID = "org-a"


def auth(
    role: str,
    *,
    projects: tuple[str, ...] = ("project-a",),
    service_identity: bool = False,
) -> AuthContext:
    return AuthContext(
        subject_id="user-1",
        project_ids=frozenset(projects),
        region_codes=frozenset({"cn-test"}),
        roles=frozenset({role}),
        service_identity=service_identity,
        organization_ids=frozenset({ORGANIZATION_ID}),
        organization_scope_triples=frozenset(
            (ORGANIZATION_ID, project_id, None) for project_id in projects
        )
        | frozenset((ORGANIZATION_ID, project_id, "cn-test") for project_id in projects),
    )


def payload() -> dict[str, object]:
    return {
        "name": "Bin picking",
        "type": "COLLECTION",
        "scenario": "transparent-parts",
        "description": "Collect representative packages.",
        "target": {"package_count": 20},
        "quality_threshold": None,
    }


@pytest.fixture
def api() -> Iterator[tuple[TestClient, dict[str, AuthContext | None]]]:
    service = CollectionTaskService()
    configure_collection_tasks(service)
    current: dict[str, AuthContext | None] = {"auth": auth("uploader")}
    app = FastAPI()

    @app.middleware("http")
    async def install_auth(request: Request, call_next: Any) -> Any:
        request.state.auth_context = current["auth"]
        return await call_next(request)

    @app.exception_handler(ProblemException)
    async def handle_problem(_request: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.problem.status,
            content=exc.problem.model_dump(mode="json", exclude_none=True),
            media_type="application/problem+json",
        )

    app.include_router(router)
    with TestClient(app, headers={"X-Organization-Id": ORGANIZATION_ID}) as client:
        yield client, current
    configure_collection_tasks(CollectionTaskService())


def create(client: TestClient, *, project_id: str = "project-a") -> Any:
    return client.post(
        f"/api/v1/projects/{project_id}/collection-tasks",
        json=payload(),
        headers={"Idempotency-Key": "create-1"},
    )


def test_create_list_detail_update_close_progress(api: tuple[TestClient, dict[str, Any]]) -> None:
    client, _ = api
    created = create(client)
    assert created.status_code == 201
    assert created.headers["Cache-Control"] == "no-store"
    assert created.headers["Idempotency-Replayed"] == "false"
    assert created.headers["ETag"] == '"v1"'
    task_id = created.json()["collection_task_id"]
    assert created.json()["task_code"] == "00000001"
    assert created.json()["status"] == "ACTIVE"

    replay = create(client)
    assert replay.status_code == 201
    assert replay.headers["Cache-Control"] == "no-store"
    assert replay.json() == created.json()
    assert replay.headers["Idempotency-Replayed"] == "true"

    listed = client.get("/api/v1/projects/project-a/collection-tasks")
    assert listed.status_code == 200
    assert listed.headers["Cache-Control"] == "no-store"
    assert [item["collection_task_id"] for item in listed.json()["items"]] == [task_id]

    detail = client.get(f"/api/v1/projects/project-a/collection-tasks/{task_id}")
    assert detail.status_code == 200
    assert detail.headers["Cache-Control"] == "no-store"
    assert detail.headers["ETag"] == '"v1"'

    updated = client.patch(
        f"/api/v1/projects/project-a/collection-tasks/{task_id}",
        json={"description": "Updated"},
        headers={"If-Match": detail.headers["ETag"]},
    )
    assert updated.status_code == 200
    assert updated.headers["Cache-Control"] == "no-store"
    assert updated.json()["description"] == "Updated"
    assert updated.headers["ETag"] == '"v2"'

    progress = client.get(
        f"/api/v1/projects/project-a/collection-tasks/{task_id}/progress",
        headers={"X-Region-Code": "cn-test"},
    )
    assert progress.status_code == 200
    assert progress.headers["Cache-Control"] == "no-store"
    assert progress.json()["received_package_count"] == 0
    assert progress.json()["qc"]["pass_rate"] == {
        "numerator": 0,
        "denominator": 0,
        "value": None,
    }

    closed = client.post(
        f"/api/v1/projects/project-a/collection-tasks/{task_id}:close",
        headers={"If-Match": updated.headers["ETag"], "Idempotency-Key": "close-1"},
    )
    assert closed.status_code == 200
    assert closed.headers["Cache-Control"] == "no-store"
    assert closed.json()["status"] == "CLOSED"
    assert closed.headers["ETag"] == '"v3"'

    rejected = client.patch(
        f"/api/v1/projects/project-a/collection-tasks/{task_id}",
        json={"description": "Too late"},
        headers={"If-Match": closed.headers["ETag"]},
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "COLLECTION_TASK_CLOSED"


def test_cancel_and_reopen_are_explicit_cas_idempotent_lifecycle_operations(
    api: tuple[TestClient, dict[str, Any]],
) -> None:
    client, _ = api
    created = create(client)
    task_id = created.json()["collection_task_id"]

    cancelled = client.post(
        f"/api/v1/projects/project-a/collection-tasks/{task_id}:cancel",
        headers={"If-Match": created.headers["ETag"], "Idempotency-Key": "cancel-1"},
    )
    assert cancelled.status_code == 200
    assert cancelled.headers["Cache-Control"] == "no-store"
    assert cancelled.headers["Idempotency-Replayed"] == "false"
    assert cancelled.headers["ETag"] == '"v2"'
    assert cancelled.json()["status"] == "CANCELLED"

    cancel_replay = client.post(
        f"/api/v1/projects/project-a/collection-tasks/{task_id}:cancel",
        headers={"If-Match": created.headers["ETag"], "Idempotency-Key": "cancel-1"},
    )
    assert cancel_replay.status_code == 200
    assert cancel_replay.headers["Idempotency-Replayed"] == "true"
    assert cancel_replay.json() == cancelled.json()

    reopened = client.post(
        f"/api/v1/projects/project-a/collection-tasks/{task_id}:reopen",
        headers={"If-Match": cancelled.headers["ETag"], "Idempotency-Key": "reopen-1"},
    )
    assert reopened.status_code == 200
    assert reopened.headers["Cache-Control"] == "no-store"
    assert reopened.headers["ETag"] == '"v3"'
    assert reopened.json()["status"] == "ACTIVE"


def test_scope_idor_and_read_write_permissions(api: tuple[TestClient, dict[str, Any]]) -> None:
    client, current = api
    created = create(client)
    task_id = created.json()["collection_task_id"]

    current["auth"] = auth("uploader", projects=("project-b",))
    denied_scope = client.get(f"/api/v1/projects/project-a/collection-tasks/{task_id}")
    assert denied_scope.status_code == 403
    assert denied_scope.json()["code"] == "ORGANIZATION_SCOPE_DENIED"

    hidden_identity = client.get(f"/api/v1/projects/project-b/collection-tasks/{task_id}")
    assert hidden_identity.status_code == 404
    assert hidden_identity.json()["code"] == "COLLECTION_TASK_NOT_FOUND"

    current["auth"] = auth("uploader")
    wrong_organization = client.get(
        f"/api/v1/projects/project-a/collection-tasks/{task_id}",
        headers={"X-Organization-Id": "org-b"},
    )
    assert wrong_organization.status_code == 403
    assert wrong_organization.json()["code"] == "ORGANIZATION_SCOPE_DENIED"

    current["auth"] = auth("uploader", service_identity=True)
    denied_region = client.get(
        f"/api/v1/projects/project-a/collection-tasks/{task_id}/progress",
        headers={"X-Region-Code": "cn-other"},
    )
    assert denied_region.status_code == 403
    assert denied_region.json()["code"] == "ORGANIZATION_SCOPE_DENIED"

    current["auth"] = auth("annotator")
    readable = client.get("/api/v1/projects/project-a/collection-tasks")
    assert readable.status_code == 200
    forbidden_write = client.post(
        "/api/v1/projects/project-a/collection-tasks",
        json=payload(),
        headers={"Idempotency-Key": "read-only"},
    )
    assert forbidden_write.status_code == 403
    assert forbidden_write.json()["code"] == "PERMISSION_REQUIRED"


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "assignment",
        "assignee",
        "person",
        "pico",
        "robot",
        "device",
        "start_at",
        "end_at",
        "effective_period",
        "pause",
        "resume",
        "continue",
        "required_modalities",
        "required_topics",
        "topics",
    ],
)
def test_create_schema_rejects_disabled_fields(
    api: tuple[TestClient, dict[str, Any]],
    forbidden_field: str,
) -> None:
    client, _ = api
    body = payload()
    body[forbidden_field] = "forbidden"
    response = client.post(
        "/api/v1/projects/project-a/collection-tasks",
        json=body,
        headers={"Idempotency-Key": f"forbidden-{forbidden_field}"},
    )
    assert response.status_code == 422


def test_required_precondition_and_idempotency_headers(
    api: tuple[TestClient, dict[str, Any]],
) -> None:
    client, _ = api
    assert (
        client.post(
            "/api/v1/projects/project-a/collection-tasks",
            json=payload(),
        ).status_code
        == 422
    )

    created = create(client)
    task_id = created.json()["collection_task_id"]
    assert (
        client.get(f"/api/v1/projects/project-a/collection-tasks/{task_id}/progress").status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/v1/projects/project-a/collection-tasks/{task_id}",
            json={"name": "new"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/v1/projects/project-a/collection-tasks/{task_id}:close",
            headers={"If-Match": created.headers["ETag"]},
        ).status_code
        == 422
    )


def test_anonymous_request_is_rejected(api: tuple[TestClient, dict[str, Any]]) -> None:
    client, current = api
    current["auth"] = None
    response = client.get("/api/v1/projects/project-a/collection-tasks")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"
