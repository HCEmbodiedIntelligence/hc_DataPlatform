from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.lance_catalog.audit import InMemoryLanceCatalogAuditRecorder
from hc_data_platform.lance_catalog.models import StepRecord, StepWindow
from hc_data_platform.lance_catalog.router import (
    configure_lance_catalog,
    configure_lance_catalog_audit_recorder,
)
from hc_data_platform.lance_catalog.router import (
    router as lance_catalog_router,
)
from hc_data_platform.preview.router import router as preview_router
from hc_data_platform.publishing.router import router as publishing_router
from hc_data_platform.security import AuthContext
from hc_data_platform.workflow.router import router as workflow_router


def _auth(*roles: str, projects: tuple[str, ...] = ("project-a",)) -> AuthContext:
    return AuthContext(
        subject_id="router-test",
        project_ids=frozenset(projects),
        region_codes=frozenset(),
        roles=frozenset(roles),
    )


@pytest.fixture
def protected_api() -> Iterator[tuple[TestClient, dict[str, AuthContext | None]]]:
    app = FastAPI()
    app.include_router(preview_router)
    app.include_router(publishing_router)
    app.include_router(workflow_router)
    app.include_router(lance_catalog_router)
    current: dict[str, AuthContext | None] = {"auth": None}

    @app.middleware("http")
    async def install_verified_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.auth_context = current["auth"]
        return await call_next(request)

    @app.exception_handler(ProblemException)
    async def problem_handler(_: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.problem.status,
            content=exc.problem.model_dump(mode="json", exclude_none=True),
            media_type="application/problem+json",
        )

    with TestClient(app) as client:
        yield client, current


def _preview_request(project_id: str = "project-a") -> dict[str, object]:
    return {
        "project_id": project_id,
        "dataset_id": "dataset-a",
        "rollout_id": "rollout-a",
        "lance_version": "1",
        "annotation_revision": 0,
        "camera_id": "front",
        "view_mode": "original",
    }


def _publication_request() -> dict[str, str]:
    return {
        "project_id": "project-a",
        "dataset_id": "dataset-a",
        "dataset_version": "v1",
        "base_lance_version": "1",
    }


def test_preview_rejects_anonymous_and_cross_project_requests(
    protected_api: tuple[TestClient, dict[str, AuthContext | None]],
) -> None:
    client, current = protected_api
    anonymous = client.post("/api/v1/previews/sessions", json=_preview_request())
    assert anonymous.status_code == 401
    assert anonymous.json()["code"] == "AUTHENTICATION_REQUIRED"

    current["auth"] = _auth("annotator", projects=("project-b",))
    denied = client.post("/api/v1/previews/sessions", json=_preview_request())
    assert denied.status_code == 403
    assert denied.json()["code"] == "PROJECT_SCOPE_DENIED"


def test_publication_requires_publish_permission_before_accessing_adapters(
    protected_api: tuple[TestClient, dict[str, AuthContext | None]],
) -> None:
    client, current = protected_api
    current["auth"] = _auth("uploader")
    denied = client.post("/api/v1/datasets/publication-preflight", json=_publication_request())
    assert denied.status_code == 403
    assert denied.json()["code"] == "PERMISSION_REQUIRED"


def test_job_listing_requires_an_unambiguous_project_scope(
    protected_api: tuple[TestClient, dict[str, AuthContext | None]],
) -> None:
    client, current = protected_api
    current["auth"] = _auth("uploader", projects=("project-a", "project-b"))
    response = client.get("/api/v1/jobs")
    assert response.status_code == 400
    assert response.json()["code"] == "PROJECT_SCOPE_REQUIRED"


def test_lance_reconciliation_selects_rls_scope_after_admin_authorization(
    protected_api: tuple[TestClient, dict[str, AuthContext | None]],
) -> None:
    class ScopedCatalog:
        def reconcile(
            self, dataset_id: str, *, project_id: str | None = None
        ) -> tuple[object, ...]:
            context = current_request_context()
            assert context.project_id == "project-a"
            assert project_id == "project-a"
            assert dataset_id == "dataset-a"
            return ()

    client, current = protected_api
    configure_lance_catalog(ScopedCatalog())  # type: ignore[arg-type]
    current["auth"] = _auth("admin")

    response = client.post("/api/v1/projects/project-a/datasets/dataset-a/reconciliation")

    assert response.status_code == 200
    assert response.json() == []


def test_lance_step_window_is_scoped_audited_and_preserves_nanosecond_precision(
    protected_api: tuple[TestClient, dict[str, AuthContext | None]],
) -> None:
    class ScopedCatalog:
        def read_steps(
            self,
            dataset_id: str,
            rollout_id: str,
            start_step: int,
            end_step: int,
            *,
            version: int | None = None,
            project_id: str | None = None,
        ) -> StepWindow:
            assert (dataset_id, rollout_id, start_step, end_step, version, project_id) == (
                "dataset-a",
                "rollout-a",
                0,
                1,
                7,
                "project-a",
            )
            return StepWindow(
                project_id="project-a",
                dataset_id="dataset-a",
                dataset_version=7,
                rollout_id="rollout-a",
                start_step=0,
                end_step=1,
                steps=(
                    StepRecord(
                        rollout_id="rollout-a",
                        step_index=0,
                        timestamp_ns=9_007_199_254_740_993,
                        modalities={"joint.position": [0.25]},
                    ),
                ),
            )

    audit = InMemoryLanceCatalogAuditRecorder()
    client, current = protected_api
    configure_lance_catalog(ScopedCatalog())  # type: ignore[arg-type]
    configure_lance_catalog_audit_recorder(audit)
    current["auth"] = _auth("annotator")

    response = client.get(
        "/api/v1/projects/project-a/datasets/dataset-a/rollouts/rollout-a/steps",
        params={"start_step": 0, "end_step": 1, "version": 7},
    )

    assert response.status_code == 200
    assert response.json()["steps"][0]["timestamp_ns"] == "9007199254740993"
    assert len(audit.events) == 1
    event = audit.events[0]
    assert event.actor_id == "router-test"
    assert event.request_id
    assert event.returned_step_count == 1
    assert event.start_step == 0 and event.end_step == 1
