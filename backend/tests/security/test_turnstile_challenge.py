from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import pytest

from hc_data_platform.core.config import Settings
from hc_data_platform.core.errors import ProblemException, problem
from hc_data_platform.security.abuse import (
    AuthAbusePolicy,
    InMemoryAbuseProtection,
    PublicAuthAttempt,
    RateLimit,
)
from hc_data_platform.security.access_models import (
    LoginCommand,
    PublicAuthChallengeConfiguration,
    PublicAuthChallengeProvider,
    RegistrationCommand,
)
from hc_data_platform.security.access_repository import InMemoryAccessRepository
from hc_data_platform.security.access_service import AccessService
from hc_data_platform.security.challenge import (
    TurnstileChallengeVerifier,
    challenge_verifier_from_settings,
)
from hc_data_platform.security.passwords import PasswordHasher, ScryptParameters


def _response(payload: object, *, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def test_turnstile_factory_composes_the_validated_runtime_settings() -> None:
    settings = Settings(
        environment="test",
        runtime_backend="memory",
        auth_abuse_enabled=True,
        auth_abuse_hmac_secret="turnstile-factory-hmac-secret-at-least-32",
        auth_challenge_provider="turnstile",
        auth_turnstile_site_key="public-site-key",
        auth_turnstile_secret="private-turnstile-secret",
        auth_turnstile_expected_hostnames=("AUTH.Example.Test.",),
        _env_file=None,
    )

    verifier = challenge_verifier_from_settings(settings)

    assert isinstance(verifier, TurnstileChallengeVerifier)
    assert verifier.configuration == PublicAuthChallengeConfiguration(
        provider=PublicAuthChallengeProvider.TURNSTILE,
        site_key="public-site-key",
    )


def test_turnstile_verifier_checks_success_action_hostname_and_retries_transport_once() -> None:
    requests: list[dict[str, str]] = []
    attempts = 0

    def post(payload: dict[str, str]) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        requests.append(payload.copy())
        if attempts == 1:
            raise httpx.ConnectTimeout("provider unavailable")
        return _response({"success": True, "action": "login", "hostname": "auth.example.test"})

    verifier = TurnstileChallengeVerifier(
        site_key="public-site-key",
        secret="private-turnstile-secret",
        expected_hostnames=("auth.example.test",),
        connect_timeout_seconds=1,
        read_timeout_seconds=2,
        post=post,
    )
    verifier.verify(response_token="opaque-response-token", operation="LOGIN")

    assert len(requests) == 2
    assert requests[0] == requests[1]
    assert requests[0]["secret"] == "private-turnstile-secret"
    assert requests[0]["response"] == "opaque-response-token"
    assert requests[0]["idempotency_key"]


@pytest.mark.parametrize(
    "payload",
    (
        {"success": False, "action": "login", "hostname": "auth.example.test"},
        {"success": True, "action": "register", "hostname": "auth.example.test"},
        {"success": True, "action": "login", "hostname": "other.example.test"},
    ),
)
def test_turnstile_verifier_rejects_invalid_provider_outcomes_without_secret_echo(
    payload: object,
) -> None:
    secret = "private-turnstile-secret"
    verifier = TurnstileChallengeVerifier(
        site_key="public-site-key",
        secret=secret,
        expected_hostnames=("auth.example.test",),
        connect_timeout_seconds=1,
        read_timeout_seconds=2,
        post=lambda _: _response(payload),
    )
    with pytest.raises(ProblemException) as captured:
        verifier.verify(response_token="opaque-response-token", operation="LOGIN")
    assert captured.value.problem.status == 403
    assert captured.value.problem.code == "AUTH_CHALLENGE_INVALID"
    assert secret not in str(captured.value)
    assert "opaque-response-token" not in str(captured.value)


def test_turnstile_verifier_fails_closed_for_missing_token_and_provider_failure() -> None:
    verifier = TurnstileChallengeVerifier(
        site_key="public-site-key",
        secret="private-turnstile-secret",
        expected_hostnames=("auth.example.test",),
        connect_timeout_seconds=1,
        read_timeout_seconds=2,
        post=lambda _: (_ for _ in ()).throw(httpx.ReadTimeout("provider unavailable")),
    )
    with pytest.raises(ProblemException) as required:
        verifier.verify(response_token=None, operation="LOGIN")
    assert required.value.problem.code == "AUTH_CHALLENGE_REQUIRED"

    with pytest.raises(ProblemException) as unavailable:
        verifier.verify(response_token="opaque-response-token", operation="LOGIN")
    assert unavailable.value.problem.status == 503
    assert unavailable.value.problem.code == "AUTH_CHALLENGE_UNAVAILABLE"


@dataclass
class _FakeVerifier:
    configuration: PublicAuthChallengeConfiguration = PublicAuthChallengeConfiguration(
        provider=PublicAuthChallengeProvider.TURNSTILE,
        site_key="test-site-key",
    )
    accepted_tokens: set[str] = field(default_factory=lambda: {"passed-token"})
    calls: list[tuple[str | None, str]] = field(default_factory=list)

    def verify(self, *, response_token: str | None, operation: str) -> None:
        self.calls.append((response_token, operation))
        if response_token is None:
            raise problem(
                status=403,
                code="AUTH_CHALLENGE_REQUIRED",
                title="Authentication challenge required",
                detail="Complete the authentication challenge before trying again.",
            )
        if response_token not in self.accepted_tokens:
            raise problem(
                status=403,
                code="AUTH_CHALLENGE_INVALID",
                title="Authentication challenge rejected",
                detail="Complete a new authentication challenge before trying again.",
            )


def _challenge_policy() -> AuthAbusePolicy:
    return AuthAbusePolicy(
        login_source=RateLimit(100, 60),
        login_subject=RateLimit(100, 900),
        register_source=RateLimit(100, 600),
        register_subject=RateLimit(100, 3600),
        global_limit=RateLimit(100, 60),
        failure_window_seconds=900,
        challenge_after_failures=3,
        delay_after_failures=10,
        delay_initial_seconds=30,
        delay_max_seconds=600,
        lock_after_failures=20,
        lock_seconds=3600,
    )


def test_service_requires_and_audits_turnstile_only_after_failure_threshold() -> None:
    policy = InMemoryAbuseProtection(
        hmac_secret="turnstile-service-test-secret-at-least-32",
        policy=_challenge_policy(),
    )
    verifier = _FakeVerifier()
    service = AccessService(
        InMemoryAccessRepository(),
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
        abuse_protection=policy,
        challenge_verifier=verifier,
    )
    username = "challenge-user"
    password = "Challenge-protected correct passphrase 2026!"
    service.register(
        RegistrationCommand(username=username, password=password),
        source_network="198.51.100.0/24",
        request_id="register",
    )

    for index in range(3):
        with pytest.raises(ProblemException) as failed:
            service.login(
                LoginCommand(username=username, password="wrong passphrase"),
                source_network="198.51.100.0/24",
                request_id=f"wrong-{index}",
            )
        assert failed.value.problem.code == "INVALID_CREDENTIALS"

    with pytest.raises(ProblemException) as required:
        service.login(
            LoginCommand(username=username, password=password),
            source_network="198.51.100.0/24",
            request_id="challenge-missing",
        )
    assert required.value.problem.code == "AUTH_CHALLENGE_REQUIRED"
    assert verifier.calls == [(None, "LOGIN")]

    with pytest.raises(ProblemException) as rejected:
        service.login(
            LoginCommand(username=username, password=password),
            source_network="198.51.100.0/24",
            challenge_response="invalid-token",
            request_id="challenge-invalid",
        )
    assert rejected.value.problem.code == "AUTH_CHALLENGE_INVALID"
    assert verifier.calls == [(None, "LOGIN"), ("invalid-token", "LOGIN")]
    assert any(action == "auth.challenge.denied" for action, _ in policy.events)

    created = service.login(
        LoginCommand(username=username, password=password),
        source_network="198.51.100.0/24",
        challenge_response="passed-token",
        request_id="challenge-passed",
    )
    assert created.access_token.startswith("hcs_")
    policy_attempt = PublicAuthAttempt(
        operation="LOGIN",
        source_network="198.51.100.0/24",
        subject_hint=service.canonical_username(username),
    )
    assert (
        policy.check(
            policy_attempt,
            request_id="reset-check",
        )
        is False
    )
    assert policy_attempt.subject_hint not in repr(policy.events)
    assert service.public_auth_configuration().challenge == verifier.configuration
