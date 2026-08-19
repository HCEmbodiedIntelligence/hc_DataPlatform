from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.annotation import InMemoryAnnotationService, SelfReviewPolicy
from hc_data_platform.annotation.router import (
    get_annotation_auth,
    get_annotation_service,
    router,
)
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.security import AuthContext


def auth(subject: str, *roles: str, projects: tuple[str, ...] = ("project-a",)):
    return AuthContext(
        subject_id=subject,
        project_ids=frozenset(projects),
        region_codes=frozenset(),
        roles=frozenset(roles),
    )


@pytest.fixture
def api() -> Iterator[tuple[TestClient, dict[str, AuthContext], InMemoryAnnotationService]]:
    service = InMemoryAnnotationService(self_review_policy=SelfReviewPolicy.DENY)
    service.create_task(
        task_id="task-api",
        project_id="project-a",
        dataset_id="dataset-a",
        dataset_version=8,
        rollout_id="rollout-api",
        base_step_count=2_000,
    )
    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(ProblemException)
    async def problem_handler(_: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.problem.status,
            content=exc.problem.model_dump(mode="json", exclude_none=True),
            media_type="application/problem+json",
        )

    current = {"auth": auth("alice", "annotator")}
    app.dependency_overrides[get_annotation_service] = lambda: service
    app.dependency_overrides[get_annotation_auth] = lambda: current["auth"]
    with TestClient(app) as client:
        yield client, current, service


def test_api_requires_verified_be02_context() -> None:
    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(ProblemException)
    async def problem_handler(_: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.problem.status,
            content=exc.problem.model_dump(mode="json", exclude_none=True),
        )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/annotation-tasks/unknown")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"


def test_claim_save_replay_submit_review_publish_and_invalidate_api(
    api: tuple[TestClient, dict[str, AuthContext], InMemoryAnnotationService],
) -> None:
    client, current, service = api
    claimed = client.post("/api/v1/annotation-tasks/task-api/claim")
    assert claimed.status_code == 200
    claimed_etag = claimed.headers["etag"]

    save_body = {
        "expected_revision": 0,
        "client_mutation_id": "browser-save-1",
        "operations": [
            {
                "operation_id": "exclude-1",
                "kind": "EXCLUDE",
                "start_step": 300,
                "end_step": 450,
            }
        ],
    }
    saved = client.post(
        "/api/v1/annotation-tasks/task-api/revisions",
        headers={"If-Match": claimed_etag},
        json=save_body,
    )
    assert saved.status_code == 201
    assert saved.json()["client_mutation_id"] == "browser-save-1"
    saved_etag = saved.headers["etag"]

    replay = client.post(
        "/api/v1/annotation-tasks/task-api/revisions",
        headers={"If-Match": claimed_etag},
        json=save_body,
    )
    assert replay.status_code == 201
    assert replay.json() == saved.json()
    assert len(service.list_revisions("task-api")) == 2

    stale = client.post(
        "/api/v1/annotation-tasks/task-api/revisions",
        headers={"If-Match": claimed_etag},
        json={
            "expected_revision": 0,
            "client_mutation_id": "browser-save-2",
            "operations": [],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "ANNOTATION_REVISION_CONFLICT"

    submitted = client.post(
        "/api/v1/annotation-tasks/task-api/submit",
        headers={"If-Match": saved_etag, "Idempotency-Key": "submit-api-1"},
        json={"expected_revision": 1},
    )
    assert submitted.status_code == 201
    assert submitted.json()["revision"] == 1
    assert submitted.json()["task_id"] == "task-api"

    current["auth"] = auth("alice", "annotator", "reviewer")
    self_review = client.post(
        "/api/v1/annotation-tasks/task-api/reviews",
        headers={"If-Match": submitted.headers["etag"]},
        json={"revision": 1, "decision": "APPROVE"},
    )
    assert self_review.status_code == 403

    current["auth"] = auth("bob", "reviewer")
    reviewed = client.post(
        "/api/v1/annotation-tasks/task-api/reviews",
        headers={"If-Match": submitted.headers["etag"]},
        json={"revision": 1, "decision": "APPROVE", "comment": "ready"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["approved_revision"] == 1
    approved_etag = reviewed.headers["etag"]

    reviewer_read = client.get(
        "/api/v1/projects/project-a/rollouts/rollout-api/approved-annotation"
    )
    assert reviewer_read.status_code == 403
    current["auth"] = auth("pat", "publisher")
    snapshot = client.get("/api/v1/projects/project-a/rollouts/rollout-api/approved-annotation")
    assert snapshot.status_code == 200
    assert snapshot.json()["annotation_revision"] == 1
    assert snapshot.json()["excluded_ranges"] == [
        {"start_step": 300, "end_step": 450, "modality_scope": "ALL_MODALITIES"}
    ]

    current["auth"] = auth("alice", "annotator")
    edited = client.post(
        "/api/v1/annotation-tasks/task-api/revisions",
        headers={"If-Match": approved_etag},
        json={
            "expected_revision": 1,
            "client_mutation_id": "browser-save-3",
            "operations": [
                {
                    "operation_id": "restore-1",
                    "kind": "RESTORE",
                    "start_step": 350,
                    "end_step": 400,
                }
            ],
        },
    )
    assert edited.status_code == 201
    current["auth"] = auth("pat", "publisher")
    no_longer_approved = client.get(
        "/api/v1/projects/project-a/rollouts/rollout-api/approved-annotation"
    )
    assert no_longer_approved.status_code == 409
    assert len(service.list_reviews("task-api")) == 1


def test_api_denies_cross_project_and_wrong_role_and_disables_vlm(
    api: tuple[TestClient, dict[str, AuthContext], InMemoryAnnotationService],
) -> None:
    client, current, _ = api
    current["auth"] = auth("mallory", "reviewer", projects=("project-b",))
    cross_project = client.get("/api/v1/annotation-tasks/task-api")
    assert cross_project.status_code == 403

    current["auth"] = auth("uploader", "uploader")
    wrong_role = client.get("/api/v1/annotation-tasks/task-api")
    assert wrong_role.status_code == 403

    capability = client.get("/api/v1/capabilities/auto-annotation")
    assert capability.status_code == 200
    assert capability.json() == {"enabled": False, "code": "FEATURE_DISABLED"}

    current["auth"] = auth("alice", "annotator")
    disabled = client.post(
        "/api/v1/annotation-tasks/task-api/auto-annotation",
        json={"revision": 0},
    )
    assert disabled.status_code == 501
    assert disabled.json()["code"] == "FEATURE_DISABLED"
