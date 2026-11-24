import json
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import SecretStr

from hc_data_platform.core.app import _problem_response
from hc_data_platform.core.config import Settings
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.model_training.router import router
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.http import require_auth_context

BASE = "/api/v1/projects/project/regions/region/model-training"
SCOPE = {"X-Organization-Id": "org", "X-Project-Id": "project", "X-Region-Code": "region"}
OPTIONS = {
    key: {"id": key + "-1", "label": key.upper()} for key in ("dataset", "model", "card", "config")
}
PARAMS = {"steps": 4, "amp": False, "loss": "l1"}
COMMAND = {**{key + "_id": opt["id"] for key, opt in OPTIONS.items()}, "parameters": PARAMS}
RUN = {
    **OPTIONS,
    "id": "run-1",
    "parameters": PARAMS,
    "status": "queued",
    "created_at": "2026-11-17T00:00:00Z",
}


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    app.state.settings = Settings(
        training_service_url="https://trainer.example",
        training_service_token=SecretStr("service-test"),
    )
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(
        subject_id="member",
        project_ids=frozenset({"project"}),
        region_codes=frozenset({"region"}),
        organization_ids=frozenset({"org"}),
        organization_scope_triples=frozenset({("org", "project", "region")}),
        organization_scoped_capabilities=frozenset(
            {("org", "project", "training.read"), ("org", "project", "training.create")}
        ),
    )

    @app.exception_handler(ProblemException)
    async def handle(request: Request, exc: ProblemException):
        return _problem_response(exc.problem)

    with TestClient(app, headers={**SCOPE, "Authorization": "Bearer browser-test"}) as test_client:
        yield test_client


def upstream(monkeypatch, handle):
    async def respond(self, request):
        assert request.headers["Authorization"] == "Bearer service-test"
        assert {k: request.headers[k] for k in SCOPE} == SCOPE
        assert request.url.host == "trainer.example"
        return handle(request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", respond)


def test_choices_submit_and_reread(client, monkeypatch):
    key = str(uuid4())
    cases = [
        ("datasets", {}, {"items": [OPTIONS["dataset"]]}),
        ("models", {"dataset_id": "dataset-1"}, {"items": [OPTIONS["model"]]}),
        ("cards", {"dataset_id": "dataset-1", "model_id": "model-1"}, {"items": [OPTIONS["card"]]}),
        (
            "configs",
            {k: COMMAND[k] for k in ("dataset_id", "model_id", "card_id")},
            {"items": [{**OPTIONS["config"], "parameters": PARAMS}]},
        ),
    ]

    def handle(request):
        resource = request.url.path.removeprefix("/v1/training/")
        if resource == "runs":
            if request.method == "POST":
                assert request.headers["Idempotency-Key"] == key
                assert json.loads(request.content) == COMMAND
                return httpx.Response(202, json=RUN)
            assert dict(request.url.params) == {"page": "1", "page_size": "10"}
            return httpx.Response(
                200,
                json={"items": [{**RUN, "status": "running", "progress_percent": 37}], "total": 1},
            )
        name, expected, result = next(case for case in cases if case[0] == resource)
        assert dict(request.url.params) == expected
        return httpx.Response(200, json=result)

    upstream(monkeypatch, handle)
    for resource, query, expected in cases:
        assert client.get(BASE + "/" + resource, params=query).json() == expected
    for _ in range(2):
        response = client.post(BASE + "/runs", json=COMMAND, headers={"Idempotency-Key": key})
        assert response.status_code == 202
        assert (response.json()["status"], response.json()["id"]) == ("queued", "run-1")
    snapshot = client.get(BASE + "/runs?page=1&page_size=10").json()["items"][0]
    assert (snapshot["status"], snapshot["progress_percent"]) == ("running", 37)


def test_guards_before_forwarding(client, monkeypatch):
    upstream(monkeypatch, lambda _: pytest.fail("invalid request reached trainer"))
    assert client.get(BASE + "/datasets", headers={"X-Organization-Id": "other"}).status_code == 403
    assert client.get(BASE + "/cards?model_id=m").status_code == 422
    assert client.post(BASE + "/runs", json=COMMAND).status_code == 422
    client.app.state.settings.training_service_token = None
    assert client.get(BASE + "/runs").status_code == 503


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (200, {"items": [{**RUN, "status": "unknown"}], "total": 1}, 502),
        (401, {}, 502),
        (409, {}, 409),
        (422, {}, 422),
        (429, {}, 429),
        (503, {}, 503),
    ],
)
def test_upstream_failures(client, monkeypatch, status, payload, expected):
    upstream(monkeypatch, lambda _: httpx.Response(status, json=payload))
    assert client.get(BASE + "/runs").status_code == expected


def test_timeout(client, monkeypatch):
    def timeout(_):
        raise httpx.ReadTimeout("timed out")

    upstream(monkeypatch, timeout)
    assert client.get(BASE + "/runs").status_code == 504
