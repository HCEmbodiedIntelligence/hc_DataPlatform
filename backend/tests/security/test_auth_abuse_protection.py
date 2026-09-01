from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.security.abuse import (
    AuthAbusePolicy,
    InMemoryAbuseProtection,
    PublicAuthAttempt,
    RateLimit,
    client_network_from_request,
)
from hc_data_platform.security.access_repository import InMemoryAccessRepository
from hc_data_platform.security.access_service import AccessService
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.capabilities import CAPABILITY_PLATFORM_ACCOUNT_SECURITY_MANAGE


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 20, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def _policy(*, source_limit: int = 30) -> AuthAbusePolicy:
    return AuthAbusePolicy(
        login_source=RateLimit(source_limit, 60),
        login_subject=RateLimit(100, 900),
        register_source=RateLimit(source_limit, 60),
        register_subject=RateLimit(100, 900),
        global_limit=RateLimit(100, 60),
        failure_window_seconds=900,
        challenge_after_failures=3,
        delay_after_failures=5,
        delay_initial_seconds=30,
        delay_max_seconds=600,
        lock_after_failures=10,
        lock_seconds=3600,
    )


def _attempt(operation: str = "LOGIN") -> PublicAuthAttempt:
    return PublicAuthAttempt(
        operation=operation,  # type: ignore[arg-type]
        source_network="198.51.100.0/24",
        subject_hint="person@example.test",
    )


def test_memory_policy_limits_hmac_keyed_source_window_without_raw_identifiers() -> None:
    clock = _Clock()
    policy = InMemoryAbuseProtection(
        hmac_secret="test-auth-abuse-key-material-at-least-32",
        policy=_policy(source_limit=2),
        clock=clock,
    )
    attempt = _attempt()
    policy.check(attempt, request_id="one")
    policy.check(attempt, request_id="two")
    with pytest.raises(ProblemException) as captured:
        policy.check(attempt, request_id="three")
    assert captured.value.problem.status == 429
    assert captured.value.problem.code == "AUTH_RATE_LIMITED"
    assert captured.value.problem.retry_after_seconds == 60
    persisted = repr(policy._buckets) + repr(policy.events)  # noqa: SLF001
    assert attempt.source_network not in persisted
    assert attempt.subject_hint not in persisted

    clock.advance(60)
    policy.check(attempt, request_id="window-reset")


def test_memory_policy_applies_progressive_delay_lock_and_success_reset() -> None:
    clock = _Clock()
    policy = InMemoryAbuseProtection(
        hmac_secret="test-auth-abuse-key-material-at-least-32",
        policy=_policy(),
        clock=clock,
    )
    attempt = _attempt()
    for index in range(5):
        policy.record_login_failure(attempt, principal_id=None, request_id=f"failure-{index}")
    with pytest.raises(ProblemException) as delayed:
        policy.check(attempt, request_id="delay")
    assert delayed.value.problem.code == "AUTH_ACCOUNT_TEMPORARILY_LOCKED"
    assert delayed.value.problem.retry_after_seconds == 30

    clock.advance(30)
    for index in range(5, 10):
        policy.record_login_failure(attempt, principal_id=None, request_id=f"failure-{index}")
    with pytest.raises(ProblemException) as locked:
        policy.check(attempt, request_id="locked")
    assert locked.value.problem.code == "AUTH_ACCOUNT_TEMPORARILY_LOCKED"
    assert locked.value.problem.retry_after_seconds == 3600
    assert any(action == "auth.login.locked" for action, _ in policy.events)

    policy.record_login_success(attempt, request_id="valid-credential")
    policy.check(attempt, request_id="reset")


def test_global_security_capability_unlocks_temporary_lock_without_project_admin_bypass() -> None:
    clock = _Clock()
    policy = InMemoryAbuseProtection(
        hmac_secret="test-auth-abuse-key-material-at-least-32",
        policy=_policy(),
        clock=clock,
    )
    service = AccessService(InMemoryAccessRepository(), abuse_protection=policy)
    principal_id = "locked-account"
    attempt = _attempt()
    for index in range(10):
        policy.record_login_failure(
            attempt,
            principal_id=principal_id,
            request_id=f"failure-{index}",
        )

    project_admin = AuthContext(
        subject_id="project-admin",
        project_ids=frozenset({"project-a"}),
        region_codes=frozenset(),
        capabilities=frozenset({"access.manage"}),
        scope_pairs=frozenset({("project-a", None)}),
    )
    with pytest.raises(ProblemException) as denied:
        service.unlock_account(
            auth=project_admin,
            principal_id=principal_id,
            request_id="project-admin-unlock",
        )
    assert denied.value.problem.code == "CAPABILITY_REQUIRED"

    global_security_operator = AuthContext(
        subject_id="platform-security-operator",
        project_ids=frozenset(),
        region_codes=frozenset(),
        capabilities=frozenset({CAPABILITY_PLATFORM_ACCOUNT_SECURITY_MANAGE}),
    )
    service.unlock_account(
        auth=global_security_operator,
        principal_id=principal_id,
        request_id="unlock-once",
    )
    service.unlock_account(
        auth=global_security_operator,
        principal_id=principal_id,
        request_id="unlock-replay",
    )

    policy.check(attempt, request_id="after-unlock")
    assert [action for action, _ in policy.events].count("auth.login.unlocked") == 1


def test_client_network_attribution_rejects_untrusted_proxy_headers() -> None:
    assert (
        client_network_from_request(
            peer_host="198.51.100.77",
            forwarded_for="203.0.113.99",
            mode="peer",
            trusted_proxy_cidrs=(),
        )
        == "198.51.100.0/24"
    )
    assert (
        client_network_from_request(
            peer_host="10.0.0.2",
            forwarded_for="203.0.113.9, 10.0.0.1",
            mode="trusted_proxy",
            trusted_proxy_cidrs=("10.0.0.0/8",),
        )
        == "203.0.113.0/24"
    )
    with pytest.raises(ProblemException) as captured:
        client_network_from_request(
            peer_host="198.51.100.77",
            forwarded_for="203.0.113.9",
            mode="trusted_proxy",
            trusted_proxy_cidrs=("10.0.0.0/8",),
        )
    assert captured.value.problem.code == "CLIENT_NETWORK_INVALID"


def test_enabled_memory_api_rate_limits_public_registration_without_storing_client_address() -> (
    None
):
    settings = Settings(
        environment="test",
        runtime_backend="memory",
        auth_abuse_enabled=True,
        auth_abuse_hmac_secret="test-auth-abuse-key-material-at-least-32",
        auth_registration_source_rate_limit=1,
        auth_registration_source_rate_window_seconds=60,
        _env_file=None,
    )
    app = create_app(settings=settings)
    with TestClient(app, client=("198.51.100.9", 50_000)) as client:
        first = client.post(
            "/api/v1/auth/registrations",
            json={"username": "first", "password": "valid password phrase 2026"},
        )
        second = client.post(
            "/api/v1/auth/registrations",
            json={"username": "second", "password": "valid password phrase 2026"},
        )
    assert first.status_code == 201
    assert second.status_code == 429
    assert second.json()["code"] == "AUTH_RATE_LIMITED"
    assert second.headers["Retry-After"].isdigit()
    assert "198.51.100.9" not in second.text


def test_enabled_abuse_policy_requires_a_secret_and_consistent_thresholds() -> None:
    with pytest.raises(ValueError, match="HC_AUTH_ABUSE_HMAC_SECRET"):
        Settings(
            environment="test", runtime_backend="memory", auth_abuse_enabled=True, _env_file=None
        )
    with pytest.raises(ValueError, match="thresholds must be nondecreasing"):
        Settings(
            environment="test",
            runtime_backend="memory",
            auth_login_challenge_after_failures=6,
            auth_login_delay_after_failures=5,
            _env_file=None,
        )
