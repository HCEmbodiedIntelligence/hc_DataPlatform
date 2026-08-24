from __future__ import annotations

from dataclasses import dataclass, field
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.errors import ProblemException, problem
from hc_data_platform.security.access_models import (
    PublicAuthChallengeConfiguration,
    PublicAuthChallengeProvider,
)
from hc_data_platform.security.access_repository import InMemoryAccessRepository
from hc_data_platform.security.access_service import AccessService
from hc_data_platform.security.passwords import PasswordHasher, PasswordPolicy, ScryptParameters
from hc_data_platform.security.recovery import (
    AccountRecoveryService,
    PasswordRecoveryRequest,
    SmtpAccountRecoveryDelivery,
)

_OLD_PASSWORD = "old-password-2026"
_NEW_PASSWORD = "new-password-2027"


@dataclass(slots=True)
class RecordingRecoveryDelivery:
    email_verifications: list[tuple[str, str]] = field(default_factory=list)
    password_recoveries: list[tuple[str, str]] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return True

    def send_recovery_email_verification(self, *, email: str, token: str) -> None:
        self.email_verifications.append((email, token))

    def send_password_recovery(self, *, email: str, token: str) -> None:
        self.password_recoveries.append((email, token))


@dataclass(slots=True)
class RecordingChallengeVerifier:
    configuration: PublicAuthChallengeConfiguration = PublicAuthChallengeConfiguration(
        provider=PublicAuthChallengeProvider.TURNSTILE,
        site_key="recovery-test-site-key",
    )
    calls: list[tuple[str | None, str]] = field(default_factory=list)

    def verify(self, *, response_token: str | None, operation: str) -> None:
        self.calls.append((response_token, operation))
        if response_token != "passed-recovery-challenge":
            raise problem(
                status=403,
                code=(
                    "AUTH_CHALLENGE_REQUIRED"
                    if response_token is None
                    else "AUTH_CHALLENGE_INVALID"
                ),
                title="Authentication challenge required",
                detail="Complete the authentication challenge before trying again.",
            )


def _client() -> tuple[TestClient, InMemoryAccessRepository, RecordingRecoveryDelivery]:
    repository = InMemoryAccessRepository(max_active_sessions=8)
    hasher = PasswordHasher(ScryptParameters(n=2**10))
    policy = PasswordPolicy(min_length=6, max_length=128)
    access = AccessService(
        repository,
        password_hasher=hasher,
        password_policy=policy,
    )
    delivery = RecordingRecoveryDelivery()
    recovery = AccountRecoveryService(
        repository,
        delivery=delivery,
        password_hasher=hasher,
        password_policy=policy,
    )
    app = create_app(
        settings=Settings(environment="test", runtime_backend="memory", _env_file=None),
        access_service=access,
        account_recovery_service=recovery,
    )
    return TestClient(app), repository, delivery


def _register_and_login_twice(client: TestClient) -> tuple[str, str]:
    registration = client.post(
        "/api/v1/auth/registrations",
        json={"username": "recovery-owner", "password": _OLD_PASSWORD},
    )
    assert registration.status_code == 201
    first = client.post(
        "/api/v1/auth/sessions",
        json={"username": "recovery-owner", "password": _OLD_PASSWORD},
    )
    second = client.post(
        "/api/v1/auth/sessions",
        json={"username": "recovery-owner", "password": _OLD_PASSWORD},
    )
    assert first.status_code == second.status_code == 201
    return first.json()["access_token"], second.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_forgot_password_uses_email_token_without_original_password() -> None:
    client, repository, delivery = _client()
    with client:
        first_token, second_token = _register_and_login_twice(client)

        verification_request = client.post(
            "/api/v1/account/recovery-email-verifications",
            headers=_auth(first_token),
            json={"recovery_email": "Owner@Example.com"},
        )
        assert verification_request.status_code == 202
        assert verification_request.json() == {"accepted": True}
        assert len(delivery.email_verifications) == 1
        verification_email, verification_token = delivery.email_verifications[0]
        assert verification_email == "owner@example.com"

        configured = client.post(
            "/api/v1/account/recovery-email:confirm",
            headers=_auth(first_token),
            json={"token": verification_token},
        )
        assert configured.status_code == 200
        assert configured.json() == {"recovery_email_hint": "o****@example.com"}

        known = client.post(
            "/api/v1/auth/password-recovery-requests",
            json={"identifier": "recovery-owner"},
        )
        unknown = client.post(
            "/api/v1/auth/password-recovery-requests",
            json={"identifier": "missing@example.com"},
        )
        assert known.status_code == unknown.status_code == 202
        assert known.json() == unknown.json() == {"accepted": True}
        assert known.headers["cache-control"] == "private, no-store"
        assert len(delivery.password_recoveries) == 1
        recovery_email, recovery_token = delivery.password_recoveries[0]
        assert recovery_email == "owner@example.com"

        serialized_challenges = repr(repository._account_security_challenges)
        assert verification_token not in serialized_challenges
        assert recovery_token not in serialized_challenges

        # Forgot-password confirmation has no current/original-password field.
        recovered = client.post(
            "/api/v1/auth/password-recovery-confirmations",
            json={"token": recovery_token, "new_password": _NEW_PASSWORD},
        )
        assert recovered.status_code == 200
        assert recovered.json() == {"sessions_revoked": 2}

        for old_session in (first_token, second_token):
            expired = client.get(
                "/api/v1/auth/session/bootstrap",
                headers=_auth(old_session),
            )
            assert expired.status_code == 401

        old_login = client.post(
            "/api/v1/auth/sessions",
            json={"username": "recovery-owner", "password": _OLD_PASSWORD},
        )
        new_login = client.post(
            "/api/v1/auth/sessions",
            json={"username": "recovery-owner", "password": _NEW_PASSWORD},
        )
        assert old_login.status_code == 401
        assert new_login.status_code == 201

        replay = client.post(
            "/api/v1/auth/password-recovery-confirmations",
            json={"token": recovery_token, "new_password": "another-password-2028"},
        )
        assert replay.status_code == 422
        assert replay.json()["code"] == "ACCOUNT_RECOVERY_TOKEN_INVALID"

    audit_text = repr(repository._audit)
    assert _OLD_PASSWORD not in audit_text
    assert _NEW_PASSWORD not in audit_text
    assert recovery_token not in audit_text
    assert sum(event.action == "auth.password.recovered" for event in repository._audit) == 1


def test_account_recovery_migration_stores_only_token_hashes() -> None:
    migration = (
        __import__("pathlib").Path(__file__).parents[2]
        / "migrations/security/014_account_recovery.sql"
    ).read_text(encoding="utf-8")
    manifest = (
        __import__("pathlib").Path(__file__).parents[2] / "migrations/manifest.txt"
    ).read_text(encoding="utf-8")

    assert "token_hash char(64)" in migration
    assert "credential_revision bigint NOT NULL" in migration
    assert "PASSWORD_RECOVERED" in migration
    assert "PASSWORD_RECOVERY" in migration
    assert "security/014_account_recovery.sql" in manifest


def test_password_recovery_request_requires_turnstile_when_configured() -> None:
    verifier = RecordingChallengeVerifier()
    service = AccountRecoveryService(
        InMemoryAccessRepository(),
        challenge_verifier=verifier,
    )
    command = PasswordRecoveryRequest(identifier="unknown-user")

    with pytest.raises(ProblemException) as missing:
        service.request_password_recovery(
            command,
            source_network="198.51.100.0/24",
            challenge_response=None,
            request_id="recovery-challenge-missing",
        )
    assert missing.value.problem.code == "AUTH_CHALLENGE_REQUIRED"

    accepted = service.request_password_recovery(
        command,
        source_network="198.51.100.0/24",
        challenge_response="passed-recovery-challenge",
        request_id="recovery-challenge-passed",
    )
    assert accepted.accepted is True
    assert verifier.calls == [
        (None, "PASSWORD_RECOVERY"),
        ("passed-recovery-challenge", "PASSWORD_RECOVERY"),
    ]


def test_password_change_invalidates_an_older_recovery_token() -> None:
    client, _, delivery = _client()
    with client:
        token, _ = _register_and_login_twice(client)
        auth = _auth(token)
        requested_email = client.post(
            "/api/v1/account/recovery-email-verifications",
            headers=auth,
            json={"recovery_email": "owner@example.com"},
        )
        assert requested_email.status_code == 202
        verification_token = delivery.email_verifications[-1][1]
        assert (
            client.post(
                "/api/v1/account/recovery-email:confirm",
                headers=auth,
                json={"token": verification_token},
            ).status_code
            == 200
        )

        assert (
            client.post(
                "/api/v1/auth/password-recovery-requests",
                json={"identifier": "recovery-owner"},
            ).status_code
            == 202
        )
        stale_recovery_token = delivery.password_recoveries[-1][1]

        profile = client.get("/api/v1/account/profile", headers=auth)
        changed = client.post(
            "/api/v1/account/password:change",
            headers={
                **auth,
                "If-Match": profile.headers["etag"],
                "Idempotency-Key": "invalidate-recovery-token",
            },
            json={
                "current_password": _OLD_PASSWORD,
                "new_password": "manual-password-2028",
            },
        )
        assert changed.status_code == 200

        stale = client.post(
            "/api/v1/auth/password-recovery-confirmations",
            json={
                "token": stale_recovery_token,
                "new_password": "should-not-be-accepted-2029",
            },
        )
        assert stale.status_code == 422
        assert stale.json()["code"] == "ACCOUNT_RECOVERY_TOKEN_INVALID"


def test_smtp_recovery_link_keeps_the_token_out_of_the_http_request_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_messages: list[EmailMessage] = []

    class FakeSmtp:
        def __init__(self, host: str, port: int, *, timeout: float) -> None:
            assert (host, port, timeout) == ("smtp.example.com", 587, 4)

        def __enter__(self) -> FakeSmtp:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def ehlo(self) -> None:
            return None

        def starttls(self, *, context: object) -> None:
            assert context is not None

        def login(self, username: str, password: str) -> None:
            assert (username, password) == ("mailer", "smtp-secret")

        def send_message(self, message: EmailMessage) -> None:
            sent_messages.append(message)

    monkeypatch.setattr("hc_data_platform.security.recovery.smtplib.SMTP", FakeSmtp)
    delivery = SmtpAccountRecoveryDelivery(
        host="smtp.example.com",
        port=587,
        from_address="no-reply@example.com",
        public_base_url="https://app.example.com/",
        username="mailer",
        password="smtp-secret",
        timeout_seconds=4,
    )
    delivery.send_password_recovery(
        email="owner@example.com",
        token="hcpr_fragment-only-token",
    )

    assert len(sent_messages) == 1
    message = sent_messages[0]
    body = message.get_content()
    assert "/auth/reset-password#token=hcpr_fragment-only-token" in body
    assert "?token=" not in body
