from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import HTTPException
from fastapi.testclient import TestClient

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.errors import problem

HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
ANONYMOUS_OPERATIONS = frozenset(
    {
        (
            "GET",
            "/api/v1/organizations/{organization_id}/robot-model-assets/content",
        ),
        (
            "PUT",
            "/api/v1/organizations/{organization_id}/robot-model-assets/upload-part",
        ),
        ("GET", "/api/v1/auth/config"),
        ("POST", "/api/v1/auth/registrations"),
        ("POST", "/api/v1/auth/sessions"),
        ("POST", "/api/v1/auth/password-recovery-requests"),
        ("POST", "/api/v1/auth/password-recovery-confirmations"),
        ("GET", "/api/v1/capabilities/auto-annotation"),
        ("GET", "/api/v1/platform/version"),
    }
)
CONNECTION_REDEMPTION = ("POST", "/api/v1/robot-ingest/connections:redeem")
ROBOT_CREDENTIAL_PATH_PREFIXES = (
    "/api/v1/robot-ingest/me",
    "/api/v1/robot-ingest/tasks",
    "/api/v1/robot-ingest/teleoperation/",
    "/api/v1/robot-ingest/uploads",
    "/api/v1/robot-ingest/heartbeat",
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
            if key == CONNECTION_REDEMPTION:
                assert response.status_code == 422, key
                assert response.json()["code"] == "REQUEST_VALIDATION_FAILED", key
                continue
            if key in ANONYMOUS_OPERATIONS:
                assert response.status_code != 401, key
                continue
            assert response.status_code == 401, (key, response.status_code, response.text)
            assert response.headers["content-type"].startswith("application/problem+json"), key
            if path.startswith(ROBOT_CREDENTIAL_PATH_PREFIXES):
                assert response.json()["code"] == "ROBOT_CREDENTIAL_INVALID", key
            else:
                assert response.json()["code"] in {
                    "AUTHENTICATION_REQUIRED",
                    "AUTH_CONTEXT_REQUIRED",
                }, key
            assert all(sentinel not in response.text for sentinel in SENSITIVE_SENTINELS), key


def test_runtime_operations_are_enumerated_in_security_matrix_or_module_contracts() -> None:
    schema = create_app(settings=_settings()).openapi()
    tests_dir = Path(__file__).resolve().parents[1]
    matrix = (tests_dir / "security" / "BE23-PUBLIC-SECURITY-MATRIX.md").read_text(encoding="utf-8")
    covered = {
        (method, path)
        for path, methods in re.findall(r"^\| `([^`]+)` \| ([^|]+) \|", matrix, re.MULTILINE)
        for method in re.findall(r"\b(?:GET|POST|PUT|PATCH|DELETE)\b", methods)
    }
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    for contract in sorted((tests_dir.parent / "openapi").glob("*.yaml")):
        fragment = yaml.load(contract.read_text(encoding="utf-8"), Loader=loader)
        covered.update(
            (method.upper(), path)
            for path, item in fragment.get("paths", {}).items()
            for method in item
            if method in HTTP_METHODS
        )
    runtime = {
        (method.upper(), path)
        for path, item in schema["paths"].items()
        for method in item
        if method in HTTP_METHODS
    }
    assert not (runtime - covered), sorted(runtime - covered)


def test_openapi_declares_the_expected_credentials_for_every_operation() -> None:
    schema = create_app(settings=_settings()).openapi()
    operations = {(method, path): op for method, path, op in _operations(schema)}
    assert CONNECTION_REDEMPTION in operations
    for key, operation in operations.items():
        if key in ANONYMOUS_OPERATIONS or key == CONNECTION_REDEMPTION:
            expected = []
        elif key[1].startswith(ROBOT_CREDENTIAL_PATH_PREFIXES):
            expected = [{"robotBearerAuth": []}]
        else:
            expected = [{"bearerAuth": []}]
        assert operation.get("security") == expected, key


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
