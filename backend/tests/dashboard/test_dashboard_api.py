from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.core.errors import ProblemException, problem
from hc_data_platform.dashboard.repository import InMemoryDashboardRepository
from hc_data_platform.dashboard.router import configure_dashboard, router
from hc_data_platform.dashboard.service import DashboardService
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.capabilities import CAPABILITY_DASHBOARD_READ


def auth(
    *,
    project: str = "project-a",
    region: str = "cn-east",
    capability: bool = True,
    capabilities: tuple[str, ...] = (),
) -> AuthContext:
    return AuthContext(
        subject_id="principal-a",
        project_ids=frozenset({project}),
        region_codes=frozenset({region}),
        roles=frozenset(),
        scope_pairs=frozenset({(project, region)}),
        scoped_capabilities=frozenset(
            (project, value)
            for value in (
                (CAPABILITY_DASHBOARD_READ, *capabilities) if capability else capabilities
            )
        ),
    )


@pytest.fixture
def api() -> Iterator[
    tuple[TestClient, dict[str, AuthContext | None], InMemoryDashboardRepository]
]:
    repository = InMemoryDashboardRepository()
    configure_dashboard(DashboardService(repository, cursor_secret="api-test"))
    current: dict[str, AuthContext | None] = {"auth": auth()}
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
    with TestClient(app) as client:
        yield client, current, repository
    configure_dashboard(DashboardService())


def params(**changes: str) -> dict[str, str]:
    values = {
        "region_code": "cn-east",
        "from": "2026-08-17T08:00:00Z",
        "to": "2026-08-18T08:00:00Z",
        "timezone": "Asia/Shanghai",
    }
    values.update(changes)
    return values


def test_four_paths_return_factual_or_explicitly_blocked_contracts(api: tuple[Any, ...]) -> None:
    client, _, repository = api
    expected_sections = {
        "snapshot": "sections",
        "activity": "activity",
        "coverage": "coverage",
        "pending-items": "pending_items",
    }
    for endpoint, section in expected_sections.items():
        response = client.get(
            f"/api/v1/projects/project-a/dashboard/{endpoint}",
            params=params(),
        )
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        body = response.json()
        assert body["as_of"].endswith("Z") or body["as_of"].endswith("+00:00")
        assert body["project_id"] == "project-a"
        assert body["region_code"] == "cn-east"
        assert body["from"] == "2026-08-17T08:00:00Z"
        if endpoint == "snapshot":
            assert "storage" not in body[section]
            assert body[section]["signal_pipeline"]["status"] == "BLOCKED"
            assert body[section]["episodes"]["status"] == "BLOCKED"
            assert body[section]["work"]["status"] == "BLOCKED"
            assert body[section]["signal_pipeline"]["published_region"]["status"] == ("BLOCKED")
            assert body[section]["signal_pipeline"]["stages"] == [
                "COLLECTED",
                "RECEIVED",
                "AUTO_QC",
                "ALIGNED_30_HZ",
                "LANCE",
                "ANNOTATION",
                "REVIEW",
                "PUBLISHED",
            ]
        elif endpoint == "coverage":
            assert body[section]["status"] == "BLOCKED"
            assert body[section]["error"]["needs_product_confirmation"] is True
        elif endpoint == "activity":
            assert body[section]["status"] == "EMPTY"
            assert body[section]["items"] == []
            assert body[section]["page_info"] is not None
        else:
            assert body[section]["status"] == "EMPTY"
            assert body[section]["authorized_source_types"] == []
            assert body[section]["items"] == []
    assert [record.endpoint for record in repository.audits] == list(expected_sections)


def test_cross_project_region_capability_and_anonymous_requests_are_denied(
    api: tuple[Any, ...],
) -> None:
    client, current, _ = api
    current["auth"] = auth(project="project-b")
    denied_project = client.get(
        "/api/v1/projects/project-a/dashboard/snapshot",
        params=params(),
    )
    assert denied_project.status_code == 403
    assert denied_project.json()["code"] == "PROJECT_SCOPE_DENIED"

    current["auth"] = auth(region="cn-west")
    denied_region = client.get(
        "/api/v1/projects/project-a/dashboard/snapshot",
        params=params(),
    )
    assert denied_region.status_code == 403
    assert denied_region.json()["code"] == "REGION_SCOPE_DENIED"

    current["auth"] = auth(capability=False)
    denied_capability = client.get(
        "/api/v1/projects/project-a/dashboard/snapshot",
        params=params(),
    )
    assert denied_capability.status_code == 403
    assert denied_capability.json()["code"] == "CAPABILITY_REQUIRED"

    current["auth"] = None
    anonymous = client.get(
        "/api/v1/projects/project-a/dashboard/snapshot",
        params=params(),
    )
    assert anonymous.status_code == 401
    assert anonymous.json()["code"] == "AUTHENTICATION_REQUIRED"


def test_invalid_range_timezone_page_and_cursor_fail_without_internal_details(
    api: tuple[Any, ...],
) -> None:
    client, _, _ = api
    invalid_range = client.get(
        "/api/v1/projects/project-a/dashboard/activity",
        params=params(**{"from": "2026-08-18T08:00:00Z", "to": "2026-08-17T08:00:00Z"}),
    )
    assert invalid_range.status_code == 422
    assert invalid_range.json()["code"] == "DASHBOARD_TIME_RANGE_INVALID"

    invalid_timezone = client.get(
        "/api/v1/projects/project-a/dashboard/activity",
        params=params(timezone="Mars/Olympus_Mons"),
    )
    assert invalid_timezone.status_code == 422
    assert invalid_timezone.json()["code"] == "DASHBOARD_TIMEZONE_INVALID"

    bad_cursor = client.get(
        "/api/v1/projects/project-a/dashboard/activity",
        params={**params(), "cursor": "not-a-dashboard-cursor"},
    )
    assert bad_cursor.status_code == 400
    assert bad_cursor.json()["code"] == "INVALID_CURSOR"
    assert "traceback" not in str(bad_cursor.json()).lower()


class AlwaysLimited:
    def check(self, *, auth: AuthContext, endpoint: str, query: object) -> None:
        del auth, endpoint, query
        raise problem(
            status=429,
            code="DASHBOARD_QUERY_RATE_LIMITED",
            title="Dashboard query rate limited",
            detail="The configured query admission policy denied this request.",
            retryable=True,
        )


def test_injected_query_admission_policy_returns_429(api: tuple[Any, ...]) -> None:
    client, _, _ = api
    configure_dashboard(DashboardService(admission=AlwaysLimited()))
    response = client.get(
        "/api/v1/projects/project-a/dashboard/coverage",
        params=params(),
    )
    assert response.status_code == 429
    assert response.json()["code"] == "DASHBOARD_QUERY_RATE_LIMITED"
