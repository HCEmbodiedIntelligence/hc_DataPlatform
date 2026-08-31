from __future__ import annotations

import asyncio
import importlib
import logging
import sys
from copy import deepcopy
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY, Gauge
from pydantic import ValidationError
from starlette.responses import JSONResponse

from hc_data_platform.backup.catalog import (
    InMemoryBackupCatalogRepository,
    PostgresBackupCatalogRepository,
)
from hc_data_platform.core import health as health_module
from hc_data_platform.core import migrations as migration_module
from hc_data_platform.core.app import _request_id, create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    current_request_context,
    reset_request_context,
    select_request_scope,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.core.discovery import discover_module_routers
from hc_data_platform.core.errors import ProblemDetails, ProblemException, problem
from hc_data_platform.core.etag import ETag, etag_matches, make_etag, require_if_match
from hc_data_platform.core.health import DependencyStatus, PostgreSQLProbe
from hc_data_platform.core.openapi import (
    DuplicateOpenAPIError,
    IncompatibleOpenAPIError,
    aggregate_fragments,
    check_compatibility,
    compatibility_issues,
    digest,
    render,
)
from hc_data_platform.core.openapi import (
    main as openapi_main,
)
from hc_data_platform.core.pagination import CursorCodec
from hc_data_platform.platform_ops.instances import (
    InMemoryPlatformInstanceRepository,
    PostgresPlatformInstanceRepository,
)
from hc_data_platform.platform_ops.maintenance import (
    InMemoryMaintenanceWriteGate,
    PostgresMaintenanceRepository,
)
from hc_data_platform.platform_ops.runtime_config import (
    InMemoryRuntimeConfigRepository,
    PostgresRuntimeConfigRepository,
)


class FakeProbe:
    def __init__(self, error: str | None = None) -> None:
        self.error = error
        self.calls = 0

    async def check(self) -> DependencyStatus | bool | None:
        self.calls += 1
        if self.error is not None:
            raise RuntimeError(self.error)
        return None


class ExplodingVerifier:
    def verify(self, token: str) -> None:
        del token
        raise RuntimeError("verification backend unavailable")


class SlowProbe:
    async def check(self) -> None:
        await asyncio.sleep(1)


def _empty_package() -> ModuleType:
    package = ModuleType("empty_test_package")
    package.__path__ = []  # type: ignore[attr-defined]
    return package


def _settings() -> Settings:
    return Settings(
        environment="test",
        runtime_backend="memory",
        secret_bundle_revision=f"sha256:{'d' * 64}",
        readiness_timeout_seconds=0.5,
        _env_file=None,
    )


def _production_settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "environment": "production",
        "runtime_backend": "memory",
        "secret_bundle_revision": f"sha256:{'d' * 64}",
        "release_id": "platform-v0.1.0-test.1",
        "git_commit": "1" * 40,
        "release_manifest_digest": f"sha256:{'a' * 64}",
        "migration_manifest_digest": f"sha256:{'b' * 64}",
        "component_image_digest": f"sha256:{'c' * 64}",
        "cursor_secret": "production-cursor-secret",
        "data_source_credential_key": "production-data-source-credential-key",
        "auth_abuse_enabled": True,
        "auth_abuse_hmac_secret": "production-auth-abuse-hmac-secret-at-least-32",
        "auth_challenge_provider": "turnstile",
        "auth_turnstile_site_key": "production-turnstile-site-key",
        "auth_turnstile_secret": "production-turnstile-secret-at-least-32",
        "auth_turnstile_expected_hostnames": ("app.example.com",),
        "object_store_secret_key": "production-object-secret",
        "object_store_public_endpoint": "https://uploads.example.com",
        "jwt_issuer": "https://issuer.example/",
        "jwt_signing_key": "production-jwt-signing-key",
        "enforce_schema_migrations": True,
    }
    values.update(updates)
    return Settings(**values, _env_file=None)


def _probes(*, postgres_error: str | None = None) -> dict[str, FakeProbe]:
    return {
        "postgresql": FakeProbe(postgres_error),
        "temporal": FakeProbe(),
        "object_storage": FakeProbe(),
    }


def test_cursor_round_trip_and_tamper_detection() -> None:
    codec = CursorCodec("secret")
    cursor = codec.encode({"id": "r1", "sort": "created_at"})
    assert codec.decode(cursor)["id"] == "r1"

    replacement = "A" if cursor[-1] != "A" else "B"
    with pytest.raises(ProblemException) as captured:
        codec.decode(cursor[:-1] + replacement)
    assert captured.value.problem.code == "INVALID_CURSOR"


def test_etag_is_canonical_and_enforces_preconditions() -> None:
    current = make_etag({"revision": 2, "id": "dataset-1"})
    assert ETag.parse(str(current)) == current
    assert etag_matches(str(current), current)

    with pytest.raises(ProblemException) as missing:
        require_if_match(None, current)
    assert missing.value.problem.status == 428

    with pytest.raises(ProblemException) as stale:
        require_if_match('"stale"', current)
    assert stale.value.problem.status == 412

    for invalid_value in ("contains\x7fdelete", "contains-unicode-🙂"):
        with pytest.raises(ValueError, match="invalid characters"):
            ETag(invalid_value)


def test_invalid_configuration_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        Settings(api_port=0, _env_file=None)
    with pytest.raises(ValidationError):
        Settings(postgres_dsn="sqlite:///local.db", _env_file=None)
    with pytest.raises(ValidationError, match="insecure local values"):
        Settings(environment="production", _env_file=None)
    with pytest.raises(ValidationError, match="secret_bundle_revision"):
        Settings(secret_bundle_revision="latest", _env_file=None)
    with pytest.raises(ValidationError, match="HC_SECRET_BUNDLE_REVISION"):
        _production_settings(secret_bundle_revision="unversioned")

    monkeypatch.setenv("HC_JWT_JWKS_URL", "")
    assert Settings(environment="local", _env_file=None).jwt_jwks_url is None

    exact_issuer = Settings(
        jwt_issuer="https://issuer.example/",
        jwt_algorithms=[" RS256 "],
        jwt_signing_key="   ",
        _env_file=None,
    )
    assert exact_issuer.jwt_issuer == "https://issuer.example/"
    assert exact_issuer.jwt_algorithms == ["RS256"]
    assert exact_issuer.jwt_signing_key is None

    unconfigured_auto_annotation = Settings(
        auto_annotation_provider_endpoint="   ",
        auto_annotation_provider_api_key="\t",
        _env_file=None,
    )
    assert unconfigured_auto_annotation.auto_annotation_provider_endpoint is None
    assert unconfigured_auto_annotation.auto_annotation_provider_api_key is None

    with pytest.raises(ValidationError, match="duplicate JWT algorithms"):
        Settings(jwt_algorithms=["RS256", "RS256"], _env_file=None)
    with pytest.raises(ValidationError, match="query or fragment"):
        Settings(jwt_issuer="https://issuer.example/?tenant=one", _env_file=None)
    with pytest.raises(ValidationError, match="HC_API_DOCS_ENABLED"):
        _production_settings(api_docs_enabled=True)
    with pytest.raises(ValidationError, match="HC_ALIGNMENT_STAGING_ROOT"):
        _production_settings(alignment_staging_root="/var/lib/hc-data/alignment")
    with pytest.raises(ValidationError, match="HC_ALIGNED_MEDIA_STAGING_ROOT"):
        _production_settings(aligned_media_staging_root="relative/aligned-media")
    with pytest.raises(ValidationError, match="must not overlap"):
        Settings(
            alignment_staging_root="/tmp/hc-data/shared",
            aligned_media_staging_root="/tmp/hc-data/shared/aligned-media",
            _env_file=None,
        )


def test_normal_production_app_rejects_the_in_memory_runtime() -> None:
    with pytest.raises(RuntimeError, match="durable runtime backend"):
        create_app(settings=_production_settings())


def test_platform_control_persistence_is_reserved_for_staging_and_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local = create_app(
        settings=Settings(
            environment="local",
            runtime_backend="production",
            secret_bundle_revision=f"sha256:{'d' * 64}",
            _env_file=None,
        ),
        readiness_probes=_probes(),
        module_package=_empty_package(),
    )
    assert isinstance(
        local.state.platform_instance_service.repository,
        InMemoryPlatformInstanceRepository,
    )
    assert isinstance(
        local.state.runtime_config_service.repository,
        InMemoryRuntimeConfigRepository,
    )
    assert isinstance(local.state.maintenance_write_gate, InMemoryMaintenanceWriteGate)
    assert isinstance(local.state.backup_catalog_repository, InMemoryBackupCatalogRepository)

    monkeypatch.setattr(
        PostgresMaintenanceRepository,
        "ensure_environment",
        lambda self, environment_id: None,
    )
    staging = create_app(
        settings=_production_settings(environment="staging", runtime_backend="production"),
        readiness_probes=_probes(),
        module_package=_empty_package(),
    )
    assert isinstance(
        staging.state.platform_instance_service.repository,
        PostgresPlatformInstanceRepository,
    )
    assert isinstance(
        staging.state.runtime_config_service.repository,
        PostgresRuntimeConfigRepository,
    )
    assert isinstance(staging.state.maintenance_write_gate, PostgresMaintenanceRepository)
    assert isinstance(staging.state.backup_catalog_repository, PostgresBackupCatalogRepository)


def test_outbox_scopes_require_exact_organization_project_region_triples() -> None:
    configured = Settings(
        outbox_scopes=(
            " organization-a/project-a/cn-east ",
            "organization-b/project-b/cn-west",
        ),
        _env_file=None,
    )
    assert configured.outbox_scopes == (
        "organization-a/project-a/cn-east",
        "organization-b/project-b/cn-west",
    )
    inventory = Settings(
        storage_inventory_scopes=(" organization-a/project-a/cn-east ",),
        _env_file=None,
    )
    assert inventory.storage_inventory_scopes == ("organization-a/project-a/cn-east",)

    for invalid in (
        ("project-a/cn-east",),
        ("organization-a/project-a/cn-east/extra",),
        ("organization-a//cn-east",),
    ):
        with pytest.raises(ValidationError, match="scope triples"):
            Settings(outbox_scopes=invalid, _env_file=None)
        with pytest.raises(ValidationError, match="scope triples"):
            Settings(storage_inventory_scopes=invalid, _env_file=None)

    with pytest.raises(ValidationError, match="duplicate scope triples"):
        Settings(
            outbox_scopes=(
                "organization-a/project-a/cn-east",
                "organization-a/project-a/cn-east",
            ),
            _env_file=None,
        )
    with pytest.raises(ValidationError, match="duplicate scope triples"):
        Settings(
            storage_inventory_scopes=(
                "organization-a/project-a/cn-east",
                "organization-a/project-a/cn-east",
            ),
            _env_file=None,
        )


def test_account_recovery_configuration_is_complete_and_secure() -> None:
    with pytest.raises(ValidationError, match="HC_AUTH_RECOVERY_PUBLIC_BASE_URL"):
        Settings(auth_recovery_enabled=True, _env_file=None)

    with pytest.raises(ValidationError, match="must use HTTPS"):
        _production_settings(
            auth_recovery_enabled=True,
            auth_recovery_public_base_url="http://app.example.com",
            auth_recovery_email_from="no-reply@example.com",
            auth_smtp_host="smtp.example.com",
        )

    with pytest.raises(ValidationError, match="HC_AUTH_SMTP_STARTTLS"):
        _production_settings(
            auth_recovery_enabled=True,
            auth_recovery_public_base_url="https://app.example.com",
            auth_recovery_email_from="no-reply@example.com",
            auth_smtp_host="smtp.example.com",
            auth_smtp_starttls=False,
        )

    configured = _production_settings(
        auth_recovery_enabled=True,
        auth_recovery_public_base_url="https://app.example.com/",
        auth_recovery_email_from="no-reply@example.com",
        auth_smtp_host="smtp.example.com",
        auth_smtp_username="mailer",
        auth_smtp_password="smtp-password-from-secret-store",
    )
    assert configured.auth_recovery_enabled is True
    assert configured.auth_recovery_token_ttl_seconds == 900


def test_untrusted_request_id_must_be_visible_ascii() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-request-id", b"caf\xe9")],
            "query_string": b"",
        }
    )

    assert UUID(_request_id(request))


def test_liveness_does_not_run_readiness_probes() -> None:
    probes = _probes()
    app = create_app(
        settings=_settings(),
        readiness_probes=probes,
        module_package=_empty_package(),
    )

    request_id = "11111111-1111-4111-8111-111111111111"
    with TestClient(app) as client:
        response = client.get("/health/live", headers={"X-Request-ID": request_id})

    assert response.status_code == 200
    assert response.json() == {"status": "live"}
    assert response.headers["X-Request-ID"] == request_id
    assert all(probe.calls == 0 for probe in probes.values())


def test_readiness_probe_failure_returns_safe_problem_without_dependency_states() -> None:
    sentinel = "postgresql://service:readiness-password@db.internal/platform"
    probes = _probes(postgres_error=sentinel)
    app = create_app(
        settings=_settings(),
        readiness_probes=probes,
        module_package=_empty_package(),
    )

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert response.headers["content-type"].startswith("application/problem+json")
    assert body["code"] == "HTTP_503"
    assert body["detail"] == "The server could not complete the request."
    assert "dependencies" not in body
    assert sentinel not in response.text


def test_readiness_returns_200_only_when_every_probe_is_ready() -> None:
    probes = _probes()
    app = create_app(
        settings=_settings(),
        readiness_probes=probes,
        module_package=_empty_package(),
    )

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert all(probe.calls == 1 for probe in probes.values())


def test_production_docs_are_closed_and_test_profile_requires_explicit_enablement() -> None:
    production = create_app(
        settings=_production_settings(),
        readiness_probes=_probes(),
        module_package=_empty_package(),
    )
    test_closed = create_app(
        settings=_settings(),
        readiness_probes=_probes(),
        module_package=_empty_package(),
    )
    test_open = create_app(
        settings=Settings(
            environment="test",
            runtime_backend="memory",
            api_docs_enabled=True,
            readiness_timeout_seconds=0.5,
            _env_file=None,
        ),
        readiness_probes=_probes(),
        module_package=_empty_package(),
    )

    for application in (production, test_closed):
        with TestClient(application) as client:
            assert client.get("/docs").status_code == 404
            assert client.get("/docs/oauth2-redirect").status_code == 404
            assert client.get("/redoc").status_code == 404
            assert client.get("/openapi.json").status_code == 404
        assert "/health/live" in application.openapi()["paths"]

    with TestClient(test_open) as client:
        assert client.get("/docs").status_code == 200
        assert client.get("/redoc").status_code == 200
        assert client.get("/openapi.json").status_code == 200


@pytest.mark.asyncio
async def test_readiness_timeout_has_stable_cross_version_detail() -> None:
    report = await health_module.check_readiness(
        {
            "postgresql": SlowProbe(),
            "temporal": FakeProbe(),
            "object_storage": FakeProbe(),
        },
        timeout_seconds=0.001,
    )

    assert report.status == "not_ready"
    assert report.dependencies["postgresql"].detail == "probe timed out"


@pytest.mark.asyncio
async def test_postgresql_readiness_rejects_unknown_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeConnection:
        async def fetchval(self, query: str) -> int:
            assert query == "SELECT 1"
            return 1

        async def fetch(self, query: str) -> list[dict[str, str]]:
            assert "core.schema_migrations" in query
            return [
                {"version": "known.sql", "checksum_sha256": "known-checksum"},
                {"version": "unknown.sql", "checksum_sha256": "unknown-checksum"},
            ]

        async def close(self) -> None:
            return None

    class FakeAsyncpg:
        @staticmethod
        async def connect(*, dsn: str, timeout: float) -> FakeConnection:
            assert dsn.startswith("postgresql://")
            assert timeout > 0
            return FakeConnection()

    original_import_module = health_module.importlib.import_module

    def import_module(name: str) -> object:
        if name == "asyncpg":
            return FakeAsyncpg
        return original_import_module(name)

    monkeypatch.setattr(health_module.importlib, "import_module", import_module)
    monkeypatch.setattr(
        migration_module,
        "expected_migration_checksums",
        lambda: {"known.sql": "known-checksum"},
    )
    settings = Settings(
        environment="test",
        enforce_schema_migrations=True,
        _env_file=None,
    )

    with pytest.raises(RuntimeError, match="migrations do not match"):
        await PostgreSQLProbe(settings).check()


def test_request_context_and_problem_details_are_bound_to_request() -> None:
    app = create_app(
        settings=_settings(),
        readiness_probes=_probes(),
        module_package=_empty_package(),
    )

    @app.get("/problem")
    async def fail() -> None:
        context = current_request_context()
        raise problem(
            status=409,
            code="TEST_CONFLICT",
            title="Test conflict",
            detail="Conflict for contract verification.",
            request_id=context.request_id,
        )

    request_id = "22222222-2222-4222-8222-222222222222"
    with TestClient(app) as client:
        response = client.get("/problem", headers={"X-Request-ID": request_id})

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["X-Request-ID"] == request_id
    assert response.json()["request_id"] == request_id
    assert response.json()["instance"] == "/problem"


def test_anonymous_headers_cannot_select_database_scope() -> None:
    app = create_app(
        settings=_settings(),
        readiness_probes=_probes(),
        module_package=_empty_package(),
    )

    @app.get("/context")
    async def context() -> dict[str, str | None]:
        selected = current_request_context()
        return {"project_id": selected.project_id, "region_code": selected.region_code}

    with TestClient(app) as client:
        response = client.get(
            "/context",
            headers={"X-Project-ID": "unverified-project", "X-Region-Code": "unverified-region"},
        )

    assert response.status_code == 200
    assert response.json() == {"project_id": None, "region_code": None}


def test_project_scope_reselection_preserves_verified_organization() -> None:
    token = bind_request_context(
        RequestContext(
            organization_id="organization-a",
            project_id="project-a",
            region_code="cn-east",
            subject_id="principal-a",
            request_id="22222222-2222-4222-8222-222222222223",
        )
    )
    try:
        select_request_scope("project-a", "cn-north")
        selected = current_request_context()
        assert selected.organization_id == "organization-a"
        assert selected.project_id == "project-a"
        assert selected.region_code == "cn-north"
        assert selected.subject_id == "principal-a"
    finally:
        reset_request_context(token)


def test_project_connection_installs_verified_platform_admin_rls_bypass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingConnection:
        def __init__(self) -> None:
            self.statement = ""
            self.parameters: tuple[str, ...] = ()
            self.closed = False

        def execute(self, statement: str, parameters: tuple[str, ...]) -> None:
            self.statement = statement
            self.parameters = parameters

        def close(self) -> None:
            self.closed = True

    connection = RecordingConnection()
    monkeypatch.setitem(
        sys.modules,
        "psycopg",
        SimpleNamespace(connect=lambda _dsn: connection),
    )
    token = bind_request_context(
        RequestContext(
            organization_id="organization-a",
            project_id="project-a",
            region_code="cn-east",
            subject_id="platform-admin",
            request_id="22222222-2222-4222-8222-222222222224",
            platform_admin=True,
        )
    )
    try:
        result = psycopg_connection_factory("postgresql://platform")()
    finally:
        reset_request_context(token)

    assert result is connection
    assert "set_config('app.platform_admin', %s, false)" in connection.statement
    assert "set_config('app.is_admin', %s, false)" in connection.statement
    assert connection.parameters == (
        "organization-a",
        "project-a",
        "cn-east",
        "platform-admin",
        "22222222-2222-4222-8222-222222222224",
        "false",
        "true",
        "true",
    )


def test_framework_and_unexpected_errors_use_problem_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app(
        settings=_settings(),
        readiness_probes=_probes(),
        module_package=_empty_package(),
    )

    @app.get("/forbidden")
    async def forbidden() -> None:
        raise HTTPException(status_code=403, detail="Access denied", headers={"X-Denied": "1"})

    @app.get("/unexpected")
    async def unexpected() -> None:
        raise RuntimeError("sensitive implementation detail")

    @app.get("/http-500")
    async def http_500() -> None:
        raise HTTPException(
            status_code=500,
            detail="sensitive HTTP exception detail",
            headers={"X-Sensitive-Internal": "must-not-escape"},
        )

    @app.get("/problem-503")
    async def problem_503() -> None:
        raise problem(
            status=503,
            code="DEPENDENCY_FAILED",
            title="Dependency failed",
            detail="postgresql://service:problem-password@db.internal/platform",
            retry_after_seconds=45,
            details={"object_key": "s3://private-bucket/raw/private.mcap"},
        )

    @app.get("/problem-501")
    async def problem_501() -> None:
        raise problem(
            status=501,
            code="FEATURE_UNAVAILABLE",
            title="Feature unavailable",
            detail="This operation is deliberately unavailable in the current contract.",
        )

    @app.get("/direct-599")
    async def direct_599() -> JSONResponse:
        return JSONResponse(
            status_code=599,
            content={"detail": "Traceback (most recent call last): private stack"},
            headers={"X-Internal-DSN": "postgresql://service:header-password@db/platform"},
        )

    @app.get("/direct-501")
    async def direct_501() -> JSONResponse:
        return JSONResponse(
            status_code=501,
            media_type="application/problem+json",
            content={"detail": "postgresql://service:direct-501-secret@db/platform"},
            headers={"X-Internal-DSN": "postgresql://service:direct-501-header@db/platform"},
        )

    caplog.set_level(logging.ERROR, logger="hc_data_platform.core.app")
    missing_request_id = "33333333-3333-4333-8333-333333333333"
    failed_request_id = "44444444-4444-4444-8444-444444444444"
    with TestClient(app, raise_server_exceptions=False) as client:
        missing = client.get("/missing", headers={"X-Request-ID": missing_request_id})
        denied = client.get("/forbidden")
        failed = client.get("/unexpected", headers={"X-Request-ID": failed_request_id})
        http_failed = client.get("/http-500")
        dependency_failed = client.get("/problem-503")
        unavailable = client.get("/problem-501")
        direct_failed = client.get("/direct-599")
        forged_unavailable = client.get("/direct-501")

    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/problem+json")
    assert missing.json()["code"] == "HTTP_404"
    assert missing.json()["request_id"] == missing_request_id
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Forbidden"
    assert "X-Denied" not in denied.headers
    assert failed.status_code == 500
    assert failed.json()["code"] == "INTERNAL_SERVER_ERROR"
    assert failed.json()["request_id"] == failed_request_id
    assert "sensitive" not in failed.text
    assert http_failed.status_code == 500
    assert http_failed.json()["detail"] == "The server could not complete the request."
    assert "sensitive" not in http_failed.text.lower()
    assert "X-Sensitive-Internal" not in http_failed.headers
    assert dependency_failed.status_code == 503
    assert dependency_failed.json()["code"] == "HTTP_503"
    assert dependency_failed.json()["retry_after_seconds"] == 45
    assert dependency_failed.headers["Retry-After"] == "45"
    assert unavailable.status_code == 501
    assert unavailable.json()["code"] == "FEATURE_UNAVAILABLE"
    assert unavailable.headers["cache-control"] == "no-store"
    assert direct_failed.status_code == 500
    assert direct_failed.json()["code"] == "INTERNAL_SERVER_ERROR"
    assert forged_unavailable.status_code == 501
    assert forged_unavailable.json()["code"] == "HTTP_501"
    assert forged_unavailable.json()["detail"] == "The server could not complete the request."
    assert "direct-501-secret" not in forged_unavailable.text
    assert "direct-501-header" not in forged_unavailable.text
    assert "X-Internal-DSN" not in forged_unavailable.headers
    for response in (dependency_failed, direct_failed, forged_unavailable):
        assert response.headers["content-type"].startswith("application/problem+json")
        assert "password" not in response.text
        assert "private" not in response.text
        assert "X-Internal-DSN" not in response.headers

    safe_exception_types = {
        "HTTPException",
        "ProblemException",
        "UnhandledException",
        "UnsafeHTTPResponse",
    }
    failure_records = [
        record
        for record in caplog.records
        if getattr(record, "event_code", "").startswith("HTTP.REQUEST_FAILED.")
    ]
    assert failure_records
    assert all(record.error_type in safe_exception_types for record in failure_records)
    assert all(
        record.event_code == "HTTP.REQUEST_FAILED.INTERNAL_SERVER_ERROR"
        for record in failure_records
    )
    assert all(not hasattr(record, "project_id") for record in failure_records)
    assert "problem-password" not in caplog.text
    assert "header-password" not in caplog.text


def test_retry_after_is_typed_bounded_and_sanitized() -> None:
    base = {
        "type": "https://hc-data-platform.invalid/problems/retry-later",
        "title": "Retry later",
        "detail": "The request can be retried later.",
        "code": "RETRY_LATER",
        "retryable": True,
    }
    for status in (429, 503):
        assert ProblemDetails(status=status, retry_after_seconds=86_400, **base)
    for status, retry_after_seconds in ((403, 30), (429, 0), (503, 86_401)):
        with pytest.raises(ValidationError):
            ProblemDetails(
                status=status,
                retry_after_seconds=retry_after_seconds,
                **base,
            )

    app = create_app(
        settings=_settings(),
        readiness_probes=_probes(),
        module_package=_empty_package(),
    )

    @app.get("/limited")
    async def limited() -> None:
        raise HTTPException(status_code=429, headers={"retry-after": "00030"})

    @app.get("/unavailable")
    async def unavailable() -> None:
        raise HTTPException(status_code=503, headers={"Retry-After": "86400"})

    @app.get("/invalid-retry/{value}")
    async def invalid_retry(value: str) -> None:
        raise HTTPException(status_code=429, headers={"Retry-After": value})

    @app.get("/wrong-status")
    async def wrong_status() -> None:
        raise HTTPException(status_code=403, headers={"Retry-After": "30"})

    @app.get("/direct-limited")
    async def direct_limited() -> JSONResponse:
        return JSONResponse(status_code=429, content={}, headers={"Retry-After": "00030"})

    @app.get("/direct-wrong-status")
    async def direct_wrong_status() -> JSONResponse:
        return JSONResponse(status_code=403, content={}, headers={"Retry-After": "30"})

    with TestClient(app, raise_server_exceptions=False) as client:
        limited_response = client.get("/limited")
        unavailable_response = client.get("/unavailable")
        invalid_responses = [
            client.get(f"/invalid-retry/{value}")
            for value in ("0", "86401", "+30", "30.5", "Wed, 21 Oct 2015")
        ]
        wrong_status_response = client.get("/wrong-status")
        direct_limited_response = client.get("/direct-limited")
        direct_wrong_status_response = client.get("/direct-wrong-status")

    assert limited_response.headers["Retry-After"] == "30"
    assert limited_response.json()["retry_after_seconds"] == 30
    assert limited_response.json()["retryable"] is True
    assert unavailable_response.headers["Retry-After"] == "86400"
    assert unavailable_response.json()["retry_after_seconds"] == 86_400
    assert direct_limited_response.headers["Retry-After"] == "30"
    for response in (
        *invalid_responses,
        wrong_status_response,
        direct_wrong_status_response,
    ):
        assert "Retry-After" not in response.headers
        assert "retry_after_seconds" not in response.json()


def test_authentication_backend_failure_still_uses_problem_details() -> None:
    app = create_app(
        settings=_settings(),
        readiness_probes=_probes(),
        module_package=_empty_package(),
        jwt_verifier=ExplodingVerifier(),  # type: ignore[arg-type]
    )

    request_id = "55555555-5555-4555-8555-555555555555"
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/health/live",
            headers={"Authorization": "Bearer opaque", "X-Request-ID": request_id},
        )

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "INTERNAL_SERVER_ERROR"
    assert response.json()["request_id"] == request_id


def test_validation_and_internal_metrics_do_not_expose_user_input_or_scope_labels() -> None:
    app = create_app(
        settings=_settings(),
        readiness_probes=_probes(),
        module_package=_empty_package(),
    )

    @app.get("/validate")
    async def validate(quantity: int) -> dict[str, int]:
        return {"quantity": quantity}

    metric = Gauge(
        f"hc_br03_sensitive_metric_{uuid4().hex}",
        "BR03 sensitive metric test",
        ("project_id", "workflow_id"),
    )
    sentinel = "postgresql://service:validation-password@db.internal/platform"
    metric.labels(project_id=sentinel, workflow_id="s3://private/raw/object.mcap").set(1)
    try:
        with TestClient(app) as client:
            invalid = client.get("/validate", params={"quantity": sentinel})
            metrics = client.get("/metrics")
    finally:
        REGISTRY.unregister(metric)

    assert invalid.status_code == 422
    assert invalid.json()["details"]["errors"] == [
        {"type": "int_parsing", "loc": ["query"], "msg": "Invalid value"}
    ]
    assert sentinel not in invalid.text
    assert metrics.status_code == 200
    assert sentinel not in metrics.text
    assert "project_id=" not in metrics.text
    assert "workflow_id=" not in metrics.text
    assert "python_info" in metrics.text


def test_module_router_discovery_is_automatic_and_sorted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_name = "discovery_fixture"
    package_dir = tmp_path / package_name
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    for name, route in (("zeta", "/zeta"), ("alpha", "/alpha")):
        module_dir = package_dir / name
        module_dir.mkdir()
        (module_dir / "__init__.py").write_text("", encoding="utf-8")
        (module_dir / "router.py").write_text(
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            f"@router.get('{route}')\n"
            f"def endpoint(): return {{'module': '{name}'}}\n",
            encoding="utf-8",
        )
    no_router = package_dir / "no_router"
    no_router.mkdir()
    (no_router / "__init__.py").write_text("", encoding="utf-8")

    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    package = importlib.import_module(package_name)
    discovered = discover_module_routers(package)
    assert [module.module_name for module in discovered] == ["alpha", "zeta"]

    app = create_app(
        settings=_settings(),
        readiness_probes=_probes(),
        module_package=package,
    )
    with TestClient(app) as client:
        assert client.get("/alpha").json() == {"module": "alpha"}
        assert client.get("/zeta").json() == {"module": "zeta"}


def test_openapi_aggregation_is_deterministic(tmp_path: Path) -> None:
    (tmp_path / "b.yaml").write_text(
        "paths:\n  /b: {}\ncomponents:\n  parameters:\n    B: {name: b, in: query}\n",
        encoding="utf-8",
    )
    (tmp_path / "a.yaml").write_text("paths:\n  /a: {}\n", encoding="utf-8")
    first = aggregate_fragments(tmp_path)
    first_render = render(first)
    second = aggregate_fragments(tmp_path)

    assert list(first["paths"]) == ["/a", "/b"]
    assert first["components"]["parameters"]["B"]["name"] == "b"
    assert first_render == render(second)
    assert digest(first) == digest(second)


def test_openapi_aggregation_preserves_fragment_security_defaults(tmp_path: Path) -> None:
    (tmp_path / "secured.yaml").write_text(
        "security:\n"
        "  - bearerAuth: []\n"
        "paths:\n"
        "  /secured:\n"
        "    get: {responses: {'200': {description: ok}}}\n"
        "  /public:\n"
        "    get:\n"
        "      security: []\n"
        "      responses: {'200': {description: ok}}\n",
        encoding="utf-8",
    )

    document = aggregate_fragments(tmp_path)

    assert document["paths"]["/secured"]["get"]["security"] == [{"bearerAuth": []}]
    assert document["paths"]["/public"]["get"]["security"] == []


def test_openapi_aggregation_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    (tmp_path / "duplicate.yaml").write_text(
        "paths:\n  /duplicate: {}\n  /duplicate: {}\n",
        encoding="utf-8",
    )

    with pytest.raises(DuplicateOpenAPIError, match="duplicate YAML key"):
        aggregate_fragments(tmp_path)


@pytest.mark.parametrize("duplicate_kind", ["path", "schema"])
def test_openapi_aggregation_rejects_duplicate_ownership(
    tmp_path: Path,
    duplicate_kind: str,
) -> None:
    if duplicate_kind == "path":
        contents = "paths:\n  /duplicate: {}\n"
    else:
        contents = "components:\n  schemas:\n    Duplicate: {type: object}\n"
    (tmp_path / "a.yaml").write_text(contents, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(contents, encoding="utf-8")

    with pytest.raises(DuplicateOpenAPIError, match=f"duplicate OpenAPI {duplicate_kind}"):
        aggregate_fragments(tmp_path)


def test_incompatible_openapi_contract_is_rejected() -> None:
    previous = {
        "paths": {"/datasets": {"get": {"responses": {"200": {"description": "ok"}}}}},
        "components": {
            "schemas": {
                "Dataset": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                }
            }
        },
    }
    current = deepcopy(previous)
    del current["paths"]["/datasets"]

    with pytest.raises(IncompatibleOpenAPIError, match="removed path /datasets"):
        check_compatibility(previous, current)


def test_openapi_compatibility_detects_nested_and_reusable_contract_changes() -> None:
    previous = {
        "paths": {
            "/datasets": {
                "get": {
                    "parameters": [
                        {
                            "name": "limit",
                            "in": "query",
                            "schema": {"type": "integer", "minimum": 1},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "string"}},
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
        "components": {
            "parameters": {
                "Cursor": {"name": "cursor", "in": "query", "schema": {"type": "string"}}
            },
            "schemas": {"Dataset": {"type": "object"}},
        },
    }
    current = deepcopy(previous)
    operation = current["paths"]["/datasets"]["get"]
    operation["parameters"][0]["required"] = True
    operation["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["id"][
        "type"
    ] = "integer"
    del current["components"]["parameters"]["Cursor"]

    issues = compatibility_issues(previous, current)
    assert any("made parameter required" in issue for issue in issues)
    assert any("schema.id: changed type" in issue for issue in issues)
    assert "removed component Cursor" in issues
    with pytest.raises(IncompatibleOpenAPIError):
        check_compatibility(previous, current)


def test_openapi_compatibility_detects_operation_identity_and_auth_changes() -> None:
    previous = {
        "paths": {
            "/datasets": {
                "get": {
                    "operationId": "listDatasets",
                    "responses": {"200": {"description": "ok"}},
                }
            }
        }
    }
    current = deepcopy(previous)
    current["paths"]["/datasets"]["get"]["operationId"] = "searchDatasets"
    current["paths"]["/datasets"]["get"]["security"] = [{"bearerAuth": []}]

    issues = compatibility_issues(previous, current)

    assert any("changed operationId" in issue for issue in issues)
    assert any("added authentication requirement" in issue for issue in issues)


def test_openapi_compatibility_allows_additive_contract_changes() -> None:
    previous = {
        "paths": {"/datasets": {"get": {"responses": {"200": {"description": "ok"}}}}},
        "components": {
            "schemas": {
                "Dataset": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                }
            }
        },
    }
    current = deepcopy(previous)
    current["paths"]["/datasets"]["get"]["responses"]["404"] = {"description": "not found"}
    current["paths"]["/health"] = {"get": {"responses": {"200": {"description": "ok"}}}}
    current["components"]["schemas"]["Dataset"]["properties"]["display_name"] = {"type": "string"}

    check_compatibility(previous, current)


def test_openapi_cli_exits_nonzero_for_incompatible_baseline(tmp_path: Path) -> None:
    fragments = tmp_path / "fragments"
    fragments.mkdir()
    (fragments / "core.yaml").write_text("paths:\n  /current: {}\n", encoding="utf-8")
    baseline = tmp_path / "baseline.yaml"
    baseline.write_text(
        "openapi: 3.1.0\ninfo: {title: test, version: 1}\npaths:\n  /removed: {}\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as captured:
        openapi_main(
            [
                "--fragments",
                str(fragments),
                "--output",
                str(tmp_path / "generated.yaml"),
                "--baseline",
                str(baseline),
            ]
        )
    assert captured.value.code == 1


def test_openapi_cli_rejects_missing_baseline(tmp_path: Path) -> None:
    fragments = tmp_path / "fragments"
    fragments.mkdir()
    (fragments / "core.yaml").write_text("paths: {}\n", encoding="utf-8")

    with pytest.raises(SystemExit) as captured:
        openapi_main(
            [
                "--fragments",
                str(fragments),
                "--output",
                str(tmp_path / "generated.yaml"),
                "--baseline",
                str(tmp_path / "missing.yaml"),
            ]
        )
    assert captured.value.code == 1
