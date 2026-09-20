from __future__ import annotations

import pytest
from pydantic import ValidationError

from hc_data_platform.core.config import Settings


def _production_settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "environment": "production",
        "secret_bundle_revision": f"sha256:{'d' * 64}",
        "release_id": "platform-v0.1.0-test.1",
        "git_commit": "1" * 40,
        "release_manifest_digest": f"sha256:{'a' * 64}",
        "migration_manifest_digest": f"sha256:{'b' * 64}",
        "component_image_digest": f"sha256:{'c' * 64}",
        "object_store_endpoint": "http://minio:9000",
        "object_store_public_endpoint": "https://uploads.example.com",
        "object_store_secret_key": "production-object-store-secret",
        "cursor_secret": "production-cursor-secret",
        "data_source_credential_key": "production-data-source-credential-key",
        "auth_abuse_enabled": True,
        "auth_abuse_hmac_secret": "production-auth-abuse-hmac-secret-at-least-32",
        "auth_challenge_provider": "turnstile",
        "auth_turnstile_site_key": "production-turnstile-site-key",
        "auth_turnstile_secret": "production-turnstile-secret-at-least-32",
        "auth_turnstile_expected_hostnames": ("app.example.com",),
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


def test_dynamic_project_discovery_is_explicit_and_local_only() -> None:
    assert Settings(
        environment="local", local_scope_discovery=True, _env_file=None
    ).local_scope_discovery
    with pytest.raises(ValidationError, match="restricted to HC_ENVIRONMENT=local"):
        Settings(environment="test", local_scope_discovery=True, _env_file=None)


def test_oss_provider_requires_https_native_endpoints() -> None:
    settings = Settings(
        environment="test",
        object_store_provider="oss",
        object_store_endpoint="https://oss-cn-hangzhou-internal.aliyuncs.com",
        object_store_public_endpoint="https://oss-cn-hangzhou.aliyuncs.com",
        object_store_bucket="hc-oss-test",
        object_store_access_key="test-access-key",
        object_store_secret_key="test-secret-key",
        _env_file=None,
    )
    assert settings.object_store_provider == "oss"
    assert settings.object_store_bucket == "hc-oss-test"

    unconfigured = Settings(
        environment="test",
        object_store_provider="oss",
        object_store_endpoint="",
        object_store_public_endpoint="",
        object_store_bucket="",
        object_store_access_key="",
        object_store_secret_key="",
        object_store_region="",
        _env_file=None,
    )
    assert unconfigured.object_store_endpoint == "https://object-store-unconfigured.invalid"
    assert unconfigured.object_store_public_endpoint == "https://object-store-unconfigured.invalid"
    assert unconfigured.object_store_bucket == "hc-unconfigured"
    assert unconfigured.object_store_access_key == "unconfigured-access-key"
    assert unconfigured.object_store_secret_key == "unconfigured-secret-key"
    assert unconfigured.object_store_region == "cn-hangzhou"

    with pytest.raises(ValidationError, match="OSS endpoints must use HTTPS"):
        Settings(
            environment="test",
            object_store_provider="oss",
            object_store_endpoint="http://oss-cn-hangzhou.aliyuncs.com",
            object_store_public_endpoint="https://oss-cn-hangzhou.aliyuncs.com",
            object_store_bucket="hc-oss-test",
            object_store_access_key="test-access-key",
            object_store_secret_key="test-secret-key",
            _env_file=None,
        )


def test_production_oss_operations_may_use_public_endpoint_before_vpc_deployment() -> None:
    settings = _production_settings(
        object_store_provider="oss",
        object_store_endpoint="https://oss-cn-hangzhou.aliyuncs.com",
        object_store_public_endpoint="https://oss-cn-hangzhou.aliyuncs.com",
        object_store_bucket="hc-oss-production",
        object_store_access_key="production-access-key",
        object_store_secret_key="production-object-store-secret",
    )

    assert settings.object_store_endpoint == "https://oss-cn-hangzhou.aliyuncs.com"


def test_production_requires_an_explicit_public_https_fqdn() -> None:
    settings = _production_settings()
    assert settings.object_store_endpoint == "http://minio:9000"
    assert settings.object_store_public_endpoint == "https://uploads.example.com"

    with pytest.raises(ValidationError, match="object_store_public_endpoint"):
        _production_settings(object_store_public_endpoint=None)


def test_multipart_authorization_ttl_is_bounded_and_operator_configurable() -> None:
    assert Settings(environment="test", _env_file=None).ingest_part_authorization_ttl_seconds == 900
    assert (
        _production_settings(
            ingest_part_authorization_ttl_seconds=3600
        ).ingest_part_authorization_ttl_seconds
        == 3600
    )
    with pytest.raises(ValidationError):
        _production_settings(ingest_part_authorization_ttl_seconds=3601)


def test_production_auth_abuse_policy_fails_closed_without_complete_network_configuration() -> None:
    with pytest.raises(ValidationError, match="HC_AUTH_ABUSE_ENABLED"):
        _production_settings(auth_abuse_enabled=False)
    with pytest.raises(ValidationError, match="HC_AUTH_ABUSE_HMAC_SECRET"):
        _production_settings(auth_abuse_hmac_secret=None)
    with pytest.raises(ValidationError, match="HC_AUTH_TRUSTED_PROXY_CIDRS"):
        _production_settings(auth_client_ip_mode="trusted_proxy")
    trusted = _production_settings(
        auth_client_ip_mode="trusted_proxy",
        auth_trusted_proxy_cidrs=("10.0.0.0/8",),
    )
    assert trusted.auth_abuse_enabled is True
    assert trusted.auth_trusted_proxy_cidrs == ("10.0.0.0/8",)


def test_production_turnstile_configuration_fails_closed_and_normalizes_hostnames() -> None:
    with pytest.raises(ValidationError, match="HC_AUTH_CHALLENGE_PROVIDER=turnstile"):
        _production_settings(auth_challenge_provider="disabled")
    with pytest.raises(ValidationError, match="HC_AUTH_TURNSTILE_SITE_KEY"):
        _production_settings(auth_turnstile_site_key=None)
    with pytest.raises(ValidationError, match="HC_AUTH_TURNSTILE_SECRET"):
        _production_settings(auth_turnstile_secret=None)
    with pytest.raises(ValidationError, match="HC_AUTH_TURNSTILE_EXPECTED_HOSTNAMES"):
        _production_settings(auth_turnstile_expected_hostnames=())
    configured = _production_settings(
        auth_turnstile_expected_hostnames=("APP.Example.Com.", "login.example.com"),
    )
    assert configured.auth_turnstile_expected_hostnames == (
        "app.example.com",
        "login.example.com",
    )


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
