from __future__ import annotations

from fastapi.testclient import TestClient

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.platform_ops.maintenance import InMemoryMaintenanceWriteGate
from hc_data_platform.platform_ops.object_store_config import (
    InMemoryObjectStoreConfigurationRepository,
    ObjectStoreConfigurationService,
    ObjectStoreConfigurationUpdate,
    settings_object_store_configured,
)
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.capabilities import CAPABILITY_PLATFORM_ADMIN
from hc_data_platform.security.http import require_auth_context


def _unconfigured_settings() -> Settings:
    return Settings(
        environment="test",
        runtime_backend="memory",
        platform_environment_id="object-store-config-test",
        object_store_provider="oss",
        object_store_endpoint="",
        object_store_public_endpoint="",
        object_store_bucket="",
        object_store_access_key="",
        object_store_secret_key="",
        object_store_region="cn-hangzhou",
        _env_file=None,
    )


def _platform_admin() -> AuthContext:
    return AuthContext(
        subject_id="object-store-config-admin",
        project_ids=frozenset(),
        region_codes=frozenset(),
        roles=frozenset(),
        capabilities=frozenset({CAPABILITY_PLATFORM_ADMIN}),
    )


def _authenticated_user() -> AuthContext:
    return AuthContext(
        subject_id="object-store-location-user",
        project_ids=frozenset(),
        region_codes=frozenset(),
        roles=frozenset(),
        capabilities=frozenset(),
    )


def test_unconfigured_oss_boots_and_page_write_never_returns_credentials() -> None:
    settings = _unconfigured_settings()
    repository = InMemoryObjectStoreConfigurationRepository()
    service = ObjectStoreConfigurationService(repository, settings)

    assert settings_object_store_configured(settings) is False
    assert service.current().model_dump() == {
        "format_version": "hc-object-store-config/v1",
        "environment_id": "object-store-config-test",
        "revision": 0,
        "source": "unconfigured",
        "configured": False,
        "provider": "oss",
        "endpoint": "",
        "public_endpoint": "",
        "bucket": "",
        "region": "cn-hangzhou",
        "access_key_configured": False,
        "access_key_hint": None,
        "activation_required": False,
        "updated_at": None,
    }

    status = service.save(
        ObjectStoreConfigurationUpdate(
            expected_revision=0,
            endpoint="https://oss-cn-hangzhou-internal.aliyuncs.com",
            public_endpoint="https://oss-cn-hangzhou.aliyuncs.com",
            bucket="hc-platform-test",
            region="cn-hangzhou",
            access_key="sensitive-access-key",
            secret_key="sensitive-secret-key",
        ),
        actor_id="platform-admin",
        request_id="object-store-config-request",
    )

    payload = status.model_dump_json()
    assert status.revision == 1
    assert status.configured is True
    assert status.activation_required is True
    assert status.access_key_hint == "••••-key"
    assert "sensitive-access-key" not in payload
    assert "sensitive-secret-key" not in payload


def test_existing_credentials_are_retained_when_only_endpoints_change() -> None:
    settings = _unconfigured_settings()
    repository = InMemoryObjectStoreConfigurationRepository()
    service = ObjectStoreConfigurationService(repository, settings)
    service.save(
        ObjectStoreConfigurationUpdate(
            expected_revision=0,
            endpoint="https://oss-cn-hangzhou.aliyuncs.com",
            public_endpoint="https://oss-cn-hangzhou.aliyuncs.com",
            bucket="hc-platform-test",
            region="cn-hangzhou",
            access_key="access-key-one",
            secret_key="secret-key-one",
        ),
        actor_id="platform-admin",
        request_id="request-one",
    )

    service.save(
        ObjectStoreConfigurationUpdate(
            expected_revision=1,
            endpoint="https://oss-cn-hangzhou-internal.aliyuncs.com",
            public_endpoint="https://oss-cn-hangzhou.aliyuncs.com",
            bucket="hc-platform-test",
            region="cn-hangzhou",
        ),
        actor_id="platform-admin",
        request_id="request-two",
    )

    stored = repository.current("object-store-config-test")
    assert stored is not None
    assert stored.access_key == "access-key-one"
    assert stored.secret_key == "secret-key-one"
    assert stored.revision == 2


def test_object_store_config_http_boundary_is_admin_only_and_redacts_secrets() -> None:
    repository = InMemoryObjectStoreConfigurationRepository()
    audit = InMemoryMaintenanceWriteGate()
    app = create_app(
        settings=_unconfigured_settings(),
        object_store_config_repository=repository,
        maintenance_write_gate=audit,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/api/v1/platform/object-store-config").status_code == 401
        assert client.get("/api/v1/platform/object-store-location").status_code == 401
        app.dependency_overrides[require_auth_context] = _platform_admin
        current = client.get("/api/v1/platform/object-store-config")
        saved = client.put(
            "/api/v1/platform/object-store-config",
            json={
                "expected_revision": 0,
                "provider": "oss",
                "endpoint": "https://oss-cn-hangzhou-internal.aliyuncs.com",
                "public_endpoint": "https://oss-cn-hangzhou.aliyuncs.com",
                "bucket": "hc-platform-test",
                "region": "cn-hangzhou",
                "access_key": "http-sensitive-access-key",
                "secret_key": "http-sensitive-secret-key",
            },
        )
        app.dependency_overrides[require_auth_context] = _authenticated_user
        location = client.get("/api/v1/platform/object-store-location")

    assert current.status_code == 200
    assert current.headers["Cache-Control"] == "private, no-store"
    assert current.json()["configured"] is False
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    assert saved.json()["activation_required"] is True
    assert "http-sensitive-access-key" not in saved.text
    assert "http-sensitive-secret-key" not in saved.text
    assert location.status_code == 200
    assert location.headers["Cache-Control"] == "private, no-store"
    assert location.json() == {
        "format_version": "hc-object-store-location/v1",
        "configured": True,
        "provider": "oss",
        "public_endpoint": "https://oss-cn-hangzhou.aliyuncs.com",
        "region": "cn-hangzhou",
    }
    assert "bucket" not in location.text
    assert "internal" not in location.text
    assert "key" not in location.text
    serialized_audit = " ".join(event.model_dump_json() for event in audit.platform_audit_events)
    assert "http-sensitive-access-key" not in serialized_audit
    assert "http-sensitive-secret-key" not in serialized_audit
