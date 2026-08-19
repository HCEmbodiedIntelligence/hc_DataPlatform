from __future__ import annotations

import pytest
from pydantic import ValidationError

from hc_data_platform.core.config import Settings


def _production_settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "environment": "production",
        "object_store_endpoint": "http://minio:9000",
        "object_store_public_endpoint": "https://uploads.example.com",
        "object_store_secret_key": "production-object-store-secret",
        "cursor_secret": "production-cursor-secret",
        "jwt_issuer": "https://identity.example.com/",
        "jwt_signing_key": "production-jwt-signing-key",
        "enforce_schema_migrations": True,
        "_env_file": None,
    }
    values.update(updates)
    return Settings(**values)  # type: ignore[arg-type]


def test_local_and_test_allow_explicit_browser_reachable_http_endpoints() -> None:
    local = Settings(
        environment="local",
        object_store_endpoint="http://minio:9000/",
        object_store_public_endpoint="http://127.0.0.1:9000/",
        _env_file=None,
    )
    assert local.object_store_endpoint == "http://minio:9000"
    assert local.object_store_public_endpoint == "http://127.0.0.1:9000"

    test = Settings(
        environment="test",
        object_store_endpoint="http://localhost:9000",
        object_store_public_endpoint="http://localhost:9000",
        _env_file=None,
    )
    assert test.object_store_public_endpoint == "http://localhost:9000"


def test_local_legacy_single_endpoint_falls_back_without_changing_server_operations() -> None:
    settings = Settings(
        environment="local",
        object_store_endpoint="http://minio:9000",
        _env_file=None,
    )
    assert settings.object_store_public_endpoint == settings.object_store_endpoint


def test_production_requires_an_explicit_public_https_fqdn() -> None:
    settings = _production_settings()
    assert settings.object_store_endpoint == "http://minio:9000"
    assert settings.object_store_public_endpoint == "https://uploads.example.com"

    with pytest.raises(ValidationError, match="HC_OBJECT_STORE_PUBLIC_ENDPOINT"):
        _production_settings(object_store_public_endpoint=None)


@pytest.mark.parametrize("field", ["object_store_endpoint", "object_store_public_endpoint"])
@pytest.mark.parametrize(
    "value",
    [
        "minio:9000",
        "ftp://storage.example.com",
        "http://user:password@storage.example.com",
        "https://storage.example.com?tenant=one",
        "https://storage.example.com#fragment",
    ],
)
def test_both_object_store_endpoints_reject_ambiguous_or_credentialed_urls(
    field: str,
    value: str,
) -> None:
    with pytest.raises(ValidationError):
        Settings(environment="test", _env_file=None, **{field: value})


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://uploads.example.com",
        "https://localhost:9000",
        "https://127.0.0.1:9000",
        "https://10.0.0.8",
        "https://minio:9000",
        "https://object-store.internal",
        "https://object-store.default.svc",
    ],
)
def test_production_public_endpoint_rejects_insecure_or_internal_hosts(endpoint: str) -> None:
    with pytest.raises(ValidationError, match="HC_OBJECT_STORE_PUBLIC_ENDPOINT"):
        _production_settings(object_store_public_endpoint=endpoint)


def test_object_store_secrets_do_not_appear_in_repr_or_validation_errors() -> None:
    secret = "br04-object-store-secret-must-not-leak"
    settings = Settings(
        environment="test",
        object_store_secret_key=secret,
        object_store_endpoint="http://minio:9000",
        object_store_public_endpoint="http://127.0.0.1:9000",
        _env_file=None,
    )
    assert secret not in repr(settings)

    with pytest.raises(ValidationError) as captured:
        Settings(
            environment="test",
            object_store_public_endpoint=f"https://user:{secret}@uploads.example.com",
            _env_file=None,
        )
    assert secret not in str(captured.value)
