from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

psycopg = pytest.importorskip("psycopg")

from hc_data_platform.core.app import create_app  # noqa: E402
from hc_data_platform.core.config import Settings  # noqa: E402
from hc_data_platform.core.dbapi import normalize_postgres_dsn  # noqa: E402
from hc_data_platform.core.errors import ProblemException  # noqa: E402
from hc_data_platform.core.migrations import apply_migrations  # noqa: E402
from hc_data_platform.security.abuse import (  # noqa: E402
    AuthAbusePolicy,
    PostgresAbuseProtection,
    PublicAuthAttempt,
    RateLimit,
)
from hc_data_platform.security.access_models import (  # noqa: E402
    LoginCommand,
    PublicAuthChallengeConfiguration,
    PublicAuthChallengeProvider,
    RegistrationCommand,
)
from hc_data_platform.security.access_postgres import PostgresAccessRepository  # noqa: E402
from hc_data_platform.security.access_service import AccessService  # noqa: E402
from hc_data_platform.security.passwords import (  # noqa: E402
    PasswordHasher,
    ScryptParameters,
)

pytestmark = pytest.mark.integration


class _AcceptingChallengeVerifier:
    configuration = PublicAuthChallengeConfiguration(
        provider=PublicAuthChallengeProvider.TURNSTILE,
        site_key="test-site-key",
    )

    def verify(self, *, response_token: str | None, operation: str) -> None:
        assert response_token == "test-challenge-response"
        assert operation == "LOGIN"


def _database_url() -> str:
    value = os.getenv("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    return value


def _policy() -> AuthAbusePolicy:
    return AuthAbusePolicy(
        login_source=RateLimit(5, 60),
        login_subject=RateLimit(100, 900),
        register_source=RateLimit(100, 60),
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


def test_postgres_auth_abuse_is_cross_connection_atomic_private_and_audited() -> None:
    dsn = _database_url()
    asyncio.run(apply_migrations(dsn))
    suffix = uuid4().hex
    secret = f"postgres-auth-abuse-secret-{suffix}-at-least-32"
    policy = _policy()
    attempt = PublicAuthAttempt(
        operation="LOGIN",
        source_network="198.51.100.0/24",
        subject_hint=f"person-{suffix}@example.test",
    )
    protections = tuple(
        PostgresAbuseProtection.from_dsn(dsn, hmac_secret=secret, policy=policy) for _ in range(2)
    )

    def admit(index: int) -> str:
        try:
            protections[index % len(protections)].check(
                attempt, request_id=f"rate-{suffix}-{index}"
            )
            return "allowed"
        except ProblemException as exc:
            assert exc.problem.code == "AUTH_RATE_LIMITED"
            return "limited"

    with ThreadPoolExecutor(max_workers=12) as executor:
        outcomes = tuple(executor.map(admit, range(12)))
    assert outcomes.count("allowed") == 5
    assert outcomes.count("limited") == 7

    login_policy = AuthAbusePolicy(
        login_source=RateLimit(100, 60),
        login_subject=RateLimit(100, 900),
        register_source=RateLimit(100, 60),
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
    login_protection = PostgresAbuseProtection.from_dsn(
        dsn,
        hmac_secret=f"postgres-login-state-secret-{suffix}-at-least-32",
        policy=login_policy,
    )
    repository = PostgresAccessRepository.from_dsn(dsn)
    service = AccessService(
        repository,
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
        abuse_protection=login_protection,
        challenge_verifier=_AcceptingChallengeVerifier(),
    )
    username = f"auth-abuse-{suffix}"
    password = "Postgres auth abuse correct phrase 2026!"
    principal = service.register(
        RegistrationCommand(username=username, password=password),
        source_network="203.0.113.0/24",
        request_id=f"register-{suffix}",
    ).principal
    login_attempt_source = "203.0.113.0/24"
    for index in range(3):
        with pytest.raises(ProblemException) as failed:
            service.login(
                LoginCommand(username=username, password="wrong password phrase"),
                source_network=login_attempt_source,
                request_id=f"failure-{suffix}-{index}",
            )
        assert failed.value.problem.code == "INVALID_CREDENTIALS"
    challenge_attempt = PublicAuthAttempt(
        operation="LOGIN",
        source_network=login_attempt_source,
        subject_hint=service.canonical_username(username),
    )
    assert login_protection.check(challenge_attempt, request_id=f"challenge-{suffix}") is True
    for index in range(3, 5):
        with pytest.raises(ProblemException) as failed:
            service.login(
                LoginCommand(username=username, password="wrong password phrase"),
                source_network=login_attempt_source,
                challenge_response="test-challenge-response",
                request_id=f"failure-{suffix}-{index}",
            )
        assert failed.value.problem.code == "INVALID_CREDENTIALS"
    with pytest.raises(ProblemException) as delayed:
        service.login(
            LoginCommand(username=username, password=password),
            source_network=login_attempt_source,
            challenge_response="test-challenge-response",
            request_id=f"delay-{suffix}",
        )
    assert delayed.value.problem.code == "AUTH_ACCOUNT_TEMPORARILY_LOCKED"
    assert delayed.value.problem.retry_after_seconds is not None

    # The policy uses the database clock. Move only the test's delay boundary so a valid
    # credential can prove failure-state reset without sleeping for a production delay.
    subject_key = login_protection._subject_key(  # noqa: SLF001
        PublicAuthAttempt(
            operation="LOGIN",
            source_network=login_attempt_source,
            subject_hint=service.canonical_username(username),
        )
    )
    with psycopg.connect(normalize_postgres_dsn(dsn)) as connection:
        connection.execute(
            """
            UPDATE access_control.auth_login_states
            SET next_allowed_at = clock_timestamp() - interval '1 second'
            WHERE subject_key_hash = %s
            """,
            (subject_key,),
        )
    created = service.login(
        LoginCommand(username=username, password=password),
        source_network=login_attempt_source,
        challenge_response="test-challenge-response",
        request_id=f"success-{suffix}",
    )
    assert created.principal.principal_id == principal.principal_id

    with psycopg.connect(normalize_postgres_dsn(dsn)) as connection:
        bucket_rows = connection.execute(
            """
            SELECT bucket_key_hash
            FROM access_control.auth_rate_limit_buckets
            WHERE operation = 'LOGIN'
            """
        ).fetchall()
        state_rows = connection.execute(
            """
            SELECT subject_key_hash
            FROM access_control.auth_login_states
            WHERE subject_key_hash = %s
            """,
            (subject_key,),
        ).fetchall()
        audit_rows = connection.execute(
            """
            SELECT action, resource_id, safe_details::text
            FROM access_control.audit_events
            WHERE request_id LIKE %s
            ORDER BY occurred_at
            """,
            (f"%{suffix}%",),
        ).fetchall()
    assert bucket_rows
    assert state_rows == []
    persisted = repr(bucket_rows) + repr(audit_rows)
    assert attempt.source_network not in persisted
    assert attempt.subject_hint not in persisted
    assert password not in persisted
    actions = {row[0] for row in audit_rows}
    assert "auth.rate_limit.denied" in actions
    assert "auth.login.delay.applied" in actions


def test_production_runtime_public_route_enforces_database_admission_policy() -> None:
    dsn = _database_url()
    asyncio.run(apply_migrations(dsn))
    suffix = uuid4().hex
    app = create_app(
        settings=Settings(
            environment="test",
            runtime_backend="production",
            postgres_dsn=dsn,
            auth_abuse_enabled=True,
            auth_abuse_hmac_secret=f"http-auth-abuse-secret-{suffix}-at-least-32",
            auth_registration_source_rate_limit=1,
            auth_registration_source_rate_window_seconds=60,
            _env_file=None,
        )
    )
    with TestClient(app, client=("198.51.100.11", 50_000)) as client:
        first = client.post(
            "/api/v1/auth/registrations",
            json={
                "username": f"http-first-{suffix}",
                "password": "Production route security phrase 2026!",
            },
        )
        limited = client.post(
            "/api/v1/auth/registrations",
            json={
                "username": f"http-second-{suffix}",
                "password": "Production route security phrase 2026!",
            },
        )
    assert first.status_code == 201, first.text
    assert limited.status_code == 429
    assert limited.headers["Retry-After"].isdigit()
    assert limited.json()["code"] == "AUTH_RATE_LIMITED"
    assert suffix not in limited.text
    assert "198.51.100.11" not in limited.text


def test_postgres_admin_unlock_is_atomic_idempotent_and_audited() -> None:
    dsn = _database_url()
    asyncio.run(apply_migrations(dsn))
    suffix = uuid4().hex
    actor_id = str(uuid4())
    protection = PostgresAbuseProtection.from_dsn(
        dsn,
        hmac_secret=f"postgres-admin-unlock-secret-{suffix}-at-least-32",
        policy=AuthAbusePolicy(
            login_source=RateLimit(100, 60),
            login_subject=RateLimit(100, 900),
            register_source=RateLimit(100, 60),
            register_subject=RateLimit(100, 900),
            global_limit=RateLimit(100, 60),
            failure_window_seconds=900,
            challenge_after_failures=3,
            delay_after_failures=3,
            delay_initial_seconds=30,
            delay_max_seconds=600,
            lock_after_failures=3,
            lock_seconds=3600,
        ),
    )
    attempt = PublicAuthAttempt(
        operation="LOGIN",
        source_network="203.0.113.0/24",
        subject_hint=f"locked-{suffix}@example.test",
    )
    service = AccessService(
        PostgresAccessRepository.from_dsn(dsn),
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
        abuse_protection=protection,
    )
    principal_id = service.register(
        RegistrationCommand(
            username=f"locked-{suffix}",
            password="Postgres administrative unlock passphrase 2026!",
        ),
        source_network="203.0.113.0/24",
        request_id=f"register-{suffix}",
    ).principal.principal_id
    for index in range(3):
        protection.record_login_failure(
            attempt,
            principal_id=principal_id,
            request_id=f"lock-{suffix}-{index}",
        )
    with pytest.raises(ProblemException) as locked:
        protection.check(attempt, request_id=f"before-unlock-{suffix}")
    assert locked.value.problem.code == "AUTH_ACCOUNT_TEMPORARILY_LOCKED"

    assert protection.unlock_account(
        principal_id=principal_id,
        actor_id=actor_id,
        request_id=f"unlock-{suffix}",
    )
    assert not protection.unlock_account(
        principal_id=principal_id,
        actor_id=actor_id,
        request_id=f"unlock-replay-{suffix}",
    )
    assert protection.check(attempt, request_id=f"after-unlock-{suffix}") is False

    with psycopg.connect(normalize_postgres_dsn(dsn)) as connection:
        states = connection.execute(
            """
            SELECT subject_key_hash
            FROM access_control.auth_login_states
            WHERE principal_id = %s::uuid
            """,
            (principal_id,),
        ).fetchall()
        audits = connection.execute(
            """
            SELECT actor_id::text, resource_type, resource_id, outcome, safe_details::text
            FROM access_control.audit_events
            WHERE action = 'auth.login.unlocked' AND request_id = %s
            """,
            (f"unlock-{suffix}",),
        ).fetchall()
    assert states == []
    assert audits == [
        (actor_id, "account", principal_id, "SUCCEEDED", '{"reason": "ADMIN_UNLOCK"}')
    ]
    persisted = repr(audits)
    assert attempt.source_network not in persisted
    assert attempt.subject_hint not in persisted
