"""Exercise HTTP authorization and app-local dependencies with actual scheme transactions.

The replaceable sample validator here supplies synthetic evidence. This proves
dependency selection and authorization, not real source decoding.
"""

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.capture_schemes.common import digest
from hc_data_platform.capture_schemes.components import CaptureServices
from hc_data_platform.capture_schemes.router import router
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    current_request_context,
    reset_request_context,
)
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.security.auth import AuthContext

from .test_contracts import draft
from .test_transactions import environment as environment


def identity(scope, *capabilities):
    org, project, region = scope
    return AuthContext(
        subject_id="capture-http-tester",
        organization_ids=frozenset({org}),
        project_ids=frozenset({project}),
        region_codes=frozenset({region}),
        organization_scope_triples=frozenset({scope}),
        organization_scoped_capabilities=frozenset((org, project, c) for c in capabilities),
    )


class Samples:
    def __init__(self, name):
        self.name, self.calls = name, []

    def validate(self, organization_id, document, sample, mapping=None):
        context = current_request_context()
        self.calls.append((organization_id, context.project_id, context.region_code, sample))
        return {
            "source_format": sample.source_format,
            "parser_version": "lerobot-v3/1",
            "sample_sha256": "1" * 64,
            "draft_sha256": digest(document),
            "status": "PASSED",
            "test_fixture": self.name,
        }


def application(auth, services=None):
    app = FastAPI()
    app.state.capture_schemes = services
    app.include_router(router)

    @app.middleware("http")
    async def context(request: Request, call_next):
        request.state.auth_context = auth
        token = bind_request_context(RequestContext(subject_id="capture-http-tester"))
        try:
            return await call_next(request)
        finally:
            reset_request_context(token)

    @app.exception_handler(ProblemException)
    async def handle_problem(request, exc):
        return JSONResponse(
            status_code=exc.problem.status, content=exc.problem.model_dump(mode="json")
        )

    return app


def test_two_apps_keep_their_connections_and_sample_validators(environment):
    dsn, scope, _ = environment
    first, second = Samples("first"), Samples("second")
    connections = []

    def components(name, samples):
        def connect():
            context = current_request_context()
            connections.append((name, context.organization_id, context.project_id))
            return psycopg.connect(dsn)

        return CaptureServices(connect, samples, lambda org: org == scope[0])

    auth = identity(scope, "robot.read", "robot.manage", "upload.read")
    path = f"/api/v1/organizations/{scope[0]}/capture-schemes/http-scheme"
    command = {
        "expected_revision": 1,
        "sample": {
            "project_id": scope[1],
            "region_code": scope[2],
            "raw_source_id": "synthetic-sample",
            "source_format": "LEROBOT_V3",
        },
    }
    with TestClient(application(auth, components("first", first))) as a:
        document = draft().model_dump(mode="json")
        document.pop("software")
        saved = a.put(path, json=document)
        assert saved.status_code == 200, saved.text
        assert saved.json()["draft"]["software"] == ""
        assert a.post(path + ":validate", json=command).status_code == 200
        with TestClient(application(auth, components("second", second))) as b:
            assert b.post(path + ":validate", json=command).status_code == 200
            with TestClient(application(auth)) as unavailable:
                missing = unavailable.get(path)
                assert missing.status_code == 503
                assert missing.json()["code"] == "CAPTURE_STORE_UNAVAILABLE"
        # Another application's lifetime cannot change A's dependencies.
        assert a.post(path + ":validate", json=command).status_code == 200
        forbidden = {
            **command,
            "sample": {**command["sample"], "project_id": "ungranted-project"},
        }
        assert a.post(path + ":validate", json=forbidden).status_code == 403
        assert len(first.calls) == 2 and len(second.calls) == 1
        assert all(call[:3] == scope for call in first.calls + second.calls)
        result = a.get(path).json()
        assert {v["test_fixture"] for v in result["validations"]} == {"first", "second"}
        published = a.post(path + ":publish", json={"expected_revision": 1})
        assert published.status_code == 200
        assert published.json()["software"] == ""
    assert {name for name, _, _ in connections} == {"first", "second"}
    assert all(org == scope[0] and project is None for _, org, project in connections)


def test_authorization_precedes_unavailable_dependencies_for_org_and_task_routes():
    scope = ("org", "project", "region")
    path = "/api/v1/organizations/org/capture-schemes/scheme"
    with TestClient(application(None)) as anonymous:
        assert anonymous.get(path).status_code == 401
    with TestClient(application(identity(scope, "robot.read"))) as reader:
        assert reader.get(path).status_code == 503
        assert reader.put(path, json=draft().model_dump(mode="json")).status_code == 403
        assert reader.get(path.replace("/org/", "/other/")).status_code == 403
        task_path = "/api/v1/projects/project/collection-tasks/task/capture-plan"
        headers = {"X-Organization-Id": "org", "X-Region-Code": "region"}
        assert reader.get(task_path, headers=headers).status_code == 403
    with TestClient(application(identity(scope, "upload.read"))) as task_reader:
        assert task_reader.get(task_path, headers=headers).status_code == 503
        assert (
            task_reader.get(task_path, headers={**headers, "X-Region-Code": "other"}).status_code
            == 403
        )


def test_admin_missing_organization_uses_explicit_directory_before_scheme_store():
    lookups = []

    def exists(org):
        lookups.append(org)
        return False

    def never_connect():
        raise AssertionError("absent organization must not reach the scheme store")

    admin = AuthContext(
        subject_id="admin",
        capabilities=frozenset({"platform.admin"}),
        project_ids=frozenset(),
        region_codes=frozenset(),
    )
    services = CaptureServices(never_connect, Samples("unused"), exists)
    with TestClient(application(admin, services)) as client:
        result = client.get("/api/v1/organizations/absent/capture-schemes")
        assert result.status_code == 404, result.text
        assert result.json()["code"] == "ORGANIZATION_NOT_FOUND"
    assert lookups == ["absent"]
