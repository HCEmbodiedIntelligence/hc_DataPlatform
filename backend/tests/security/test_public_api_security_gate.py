from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.errors import problem

HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
ANONYMOUS_OPERATIONS = frozenset(
    {
        ("GET", "/api/v1/auth/config"),
        ("POST", "/api/v1/auth/registrations"),
        ("POST", "/api/v1/auth/sessions"),
        ("POST", "/api/v1/auth/password-recovery-requests"),
        ("POST", "/api/v1/auth/password-recovery-confirmations"),
        ("GET", "/api/v1/capabilities/auto-annotation"),
        ("GET", "/api/v1/platform/version"),
    }
)
PATH_VALUES = {
    "access_request_id": "00000000-0000-0000-0000-000000000001",
    "collection_task_id": "collection-task-a",
    "dataset_id": "dataset-a",
    "dataset_version": "v1",
    "job_id": "job-a",
    "policy_id": "policy-a",
    "profile_id": "profile-a",
    "profile_version": "1",
    "project_id": "project-a",
    "region_code": "cn-hz",
    "revision": "1",
    "rollout_id": "rollout-a",
    "schema_id": "schema-a",
    "session_id": "session-a",
    "submission_id": "submission-a",
    "task_id": "task-a",
    "version": "1",
}
SENSITIVE_SENTINELS = (
    "be23-password-must-not-leak",
    "hcs_be23_session_token_must_not_leak",
    "X-Amz-Signature=be23-signature-must-not-leak",
    "s3://private-bucket/project-a/raw/private.mcap",
    "Traceback (most recent call last)",
)


def _settings() -> Settings:
    return Settings(environment="test", runtime_backend="memory", _env_file=None)


def _concrete_path(path: str) -> str:
    return re.sub(
        r"\{([^}]+)\}",
        lambda match: PATH_VALUES.get(match.group(1), "value"),
        path,
    )


def _operations(schema: dict[str, Any]) -> Iterator[tuple[str, str, dict[str, Any]]]:
    for path, item in sorted(schema["paths"].items()):
        if not path.startswith("/api/v1/"):
            continue
        for method, operation in item.items():
            if method in HTTP_METHODS:
                yield method.upper(), path, operation


def test_every_runtime_public_resource_operation_rejects_anonymous_before_validation() -> None:
    app = create_app(settings=_settings())
    operations = list(_operations(app.openapi()))
    assert operations
    assert {(method, path) for method, path, _ in operations} >= ANONYMOUS_OPERATIONS

    with TestClient(app, raise_server_exceptions=False) as client:
        for method, path, _ in operations:
            key = (method, path)
            response = client.request(
                method,
                _concrete_path(path),
                headers={"Content-Type": "application/json"},
                content=b"{}",
            )
            if key in ANONYMOUS_OPERATIONS:
                assert response.status_code != 401, key
                continue
            assert response.status_code == 401, (key, response.status_code, response.text)
            assert response.headers["content-type"].startswith("application/problem+json"), key
            assert response.json()["code"] in {
                "AUTHENTICATION_REQUIRED",
                "AUTH_CONTEXT_REQUIRED",
            }, key
            assert all(sentinel not in response.text for sentinel in SENSITIVE_SENTINELS), key


def test_runtime_openapi_paths_are_all_enumerated_in_the_be23_matrix() -> None:
    schema = create_app(settings=_settings()).openapi()
    matrix = (Path(__file__).with_name("BE23-PUBLIC-SECURITY-MATRIX.md")).read_text(
        encoding="utf-8"
    )
    missing = [path for path in sorted(schema["paths"]) if f"`{path}`" not in matrix]
    assert missing == []


def test_openapi_auth_gaps_are_runtime_protected_and_explicitly_marked() -> None:
    app = create_app(settings=_settings())
    schema = app.openapi()
    matrix = Path(__file__).with_name("BE23-PUBLIC-SECURITY-MATRIX.md").read_text(encoding="utf-8")
    gaps = [
        (method, path)
        for method, path, operation in _operations(schema)
        if (method, path) not in ANONYMOUS_OPERATIONS
        and operation.get("security") != [{"bearerAuth": []}]
    ]
    for method, path in gaps:
        row = next(line for line in matrix.splitlines() if f"`{path}`" in line)
        assert "OpenAPI authn 缺口" in row, (method, path)


def test_cross_origin_preflight_is_not_permissive_and_cookie_cannot_authenticate() -> None:
    app = create_app(settings=_settings())
    with TestClient(app, raise_server_exceptions=False) as client:
        preflight = client.options(
            "/api/v1/projects/project-a/storage/capacity",
            headers={
                "Origin": "https://attacker.invalid",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert preflight.status_code in {400, 405}
        assert "access-control-allow-origin" not in preflight.headers
        assert "access-control-allow-credentials" not in preflight.headers

        client.cookies.set("session", "hcs_cookie_auth_must_be_rejected")
        cookie_only = client.get(
            "/api/v1/projects/project-a/storage/capacity",
            headers={"Origin": "https://attacker.invalid"},
        )
        assert cookie_only.status_code == 401
        assert "access-control-allow-origin" not in cookie_only.headers
        assert "set-cookie" not in cookie_only.headers


def test_unexpected_exception_response_and_log_do_not_echo_sensitive_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app(settings=_settings())
    combined = " | ".join(SENSITIVE_SENTINELS)

    @app.get("/api/v1/be23-test/unexpected-error")
    def unexpected_error() -> None:
        raise RuntimeError(combined)

    caplog.set_level(logging.ERROR, logger="hc_data_platform.core.app")
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/be23-test/unexpected-error")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "INTERNAL_SERVER_ERROR"
    assert response.json()["detail"] == "The server could not complete the request."
    for value in SENSITIVE_SENTINELS:
        assert value not in response.text
        assert value not in caplog.text
    records = [
        record
        for record in caplog.records
        if getattr(record, "event_code", "") == "HTTP.REQUEST_FAILED.INTERNAL_SERVER_ERROR"
    ]
    assert records
    assert all(record.error_type == "UnhandledException" for record in records)
    assert any(record.request_id == response.headers["X-Request-ID"] for record in records)


def test_http_problem_dependency_validation_and_headers_never_echo_sensitive_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app(settings=_settings())
    combined = " | ".join(SENSITIVE_SENTINELS)

    @app.get("/api/v1/be23-test/http-500")
    def http_500() -> None:
        raise HTTPException(
            status_code=500,
            detail=combined,
            headers={
                "Authorization": f"Bearer {SENSITIVE_SENTINELS[1]}",
                "Set-Cookie": f"session={SENSITIVE_SENTINELS[1]}",
                "X-Object-Path": SENSITIVE_SENTINELS[3],
            },
        )

    @app.get("/api/v1/be23-test/dependency-503")
    def dependency_503() -> None:
        raise problem(
            status=503,
            code="DEPENDENCY_FAILED",
            title="Dependency failed",
            detail=combined,
            details={
                "dsn": "postgresql://service:be23-password@db.internal/platform",
                "nested": {"object_key": SENSITIVE_SENTINELS[3]},
            },
        )

    @app.get("/api/v1/be23-test/validation")
    def validation(quantity: int) -> dict[str, int]:
        return {"quantity": quantity}

    caplog.set_level(logging.ERROR, logger="hc_data_platform.core.app")
    with TestClient(app, raise_server_exceptions=False) as client:
        http_failed = client.get("/api/v1/be23-test/http-500")
        dependency_failed = client.get("/api/v1/be23-test/dependency-503")
        invalid = client.get(
            "/api/v1/be23-test/validation",
            params={"quantity": combined},
        )

    assert http_failed.status_code == 500
    assert http_failed.json()["code"] == "INTERNAL_SERVER_ERROR"
    assert dependency_failed.status_code == 503
    assert dependency_failed.json()["code"] == "HTTP_503"
    assert invalid.status_code == 422
    assert invalid.json()["details"]["errors"] == [
        {"type": "int_parsing", "loc": ["query"], "msg": "Invalid value"}
    ]
    for response in (http_failed, dependency_failed, invalid):
        assert response.headers["content-type"].startswith("application/problem+json")
        assert "authorization" not in response.headers
        assert "set-cookie" not in response.headers
        assert "x-object-path" not in response.headers
        for value in SENSITIVE_SENTINELS:
            assert value not in response.text
            assert value not in caplog.text
