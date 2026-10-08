"""Parent and child HTTP routes cannot borrow another application's services."""

import subprocess
import sys
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.episode_store.router import router
from tests.security.test_runtime_router_auth import _auth


def application(calls=None):
    app = FastAPI()
    if calls is not None:

        def record(kind):
            def read(*args, **kwargs):
                calls.append(kind)
                return []

            return read

        app.state.episode_packages = SimpleNamespace(
            repository=SimpleNamespace(identity=lambda *a: None, list_versions=record("versions")),
            deliveries=SimpleNamespace(list=record("deliveries")),
            tag_derivations=SimpleNamespace(list=record("tags")),
        )
    app.include_router(router)

    @app.middleware("http")
    async def context(request: Request, call_next):
        request.state.auth_context = _auth("dataset_version.read")
        token = bind_request_context(
            RequestContext(
                organization_id="organization-a", project_id="project-a", region_code="cn-test"
            )
        )
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


def test_parent_and_children_use_only_their_app_and_unconfigured_app_fails_closed():
    first, second = [], []
    paths = [
        "/api/v1/projects/project-a/regions/cn-test/datasets/d/episodes/e/data-versions",
        "/api/v1/projects/project-a/regions/cn-test/episode-deliveries",
        "/api/v1/projects/project-a/regions/cn-test/annotation-tasks/t/tag-episodes",
    ]
    with TestClient(application(first)) as a, TestClient(application(second)) as b:
        with TestClient(application()) as unavailable:
            for path in paths:
                assert a.get(path).status_code == 200
                assert b.get(path).status_code == 200
                assert unavailable.get(path).status_code == 503
        assert first == second == ["versions", "deliveries", "tags"]
        # Constructing/closing another app never replaces this app's bindings.
        for path in paths:
            assert a.get(path).status_code == 200
        assert len(first) == 6 and len(second) == 3


def test_error_and_scope_contract_imports_do_not_load_repository_or_service():
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from hc_data_platform.episode_store.errors import PackageConflict
from hc_data_platform.episode_store.scope import assert_scope, collection_scope
for name in ('hc_data_platform.episode_store.postgres',
             'hc_data_platform.episode_store.collections',
             'hc_data_platform.episode_store.service', 'psycopg', 'fastapi'):
    assert name not in sys.modules, name
from hc_data_platform.episode_store.postgres import PackageConflict as LegacyConflict
from hc_data_platform.episode_store.postgres import assert_scope as legacy_scope
from hc_data_platform.episode_store.collections import collection_scope as legacy_collection_scope
assert PackageConflict is LegacyConflict
assert assert_scope is legacy_scope
assert collection_scope is legacy_collection_scope
""",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
