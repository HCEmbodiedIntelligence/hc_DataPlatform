from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.errors import ProblemException, problem
from hc_data_platform.security.abuse import PublicAuthAttempt
from hc_data_platform.security.access_models import (
    AccessDecisionCommand,
    CapabilityRequestCreate,
    LoginCommand,
    MembershipRequestCreate,
    RegistrationCommand,
)
from hc_data_platform.security.access_repository import InMemoryAccessRepository
from hc_data_platform.security.access_service import AccessService
from hc_data_platform.security.passwords import PasswordHasher, ScryptParameters


def _fast_service(*, abuse_protection: object | None = None) -> AccessService:
    return AccessService(
        InMemoryAccessRepository(),
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
        abuse_protection=abuse_protection,  # type: ignore[arg-type]
    )


def test_concurrent_duplicate_registration_creates_exactly_one_account() -> None:
    service = _fast_service()
    command = RegistrationCommand(
        username="be23-race-user", password="A durable race passphrase 2026!"
    )

    def register(index: int) -> tuple[str, str]:
        try:
            principal = service.register(command, request_id=f"register-{index}").principal
            return "created", principal.principal_id
        except ProblemException as exc:
            return "conflict", exc.problem.code

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = list(executor.map(register, range(32)))

    assert sum(outcome == "created" for outcome, _ in results) == 1
    assert sum(outcome == "conflict" for outcome, _ in results) == 31
    assert {value for outcome, value in results if outcome == "conflict"} == {
        "ACCOUNT_REGISTRATION_CONFLICT"
    }


def test_login_does_not_reveal_whether_the_username_exists() -> None:
    service = _fast_service()
    service.register(
        RegistrationCommand(username="known-user", password="correct-password"),
        request_id="register-known",
    )

    failures = []
    for command in (
        LoginCommand(username="known-user", password="wrong-password"),
        LoginCommand(username="unknown-user", password="wrong-password"),
    ):
        with pytest.raises(ProblemException) as captured:
            service.login(command, request_id="failed-login")
        failures.append(captured.value.problem.model_dump(exclude={"request_id", "instance"}))
    assert failures[0] == failures[1]
    assert failures[0]["code"] == "INVALID_CREDENTIALS"


class RecordingLimit:
    def __init__(self, blocked_operation: str) -> None:
        self.blocked_operation = blocked_operation
        self.operations: list[str] = []
        self._lock = Lock()

    def check(self, attempt: PublicAuthAttempt, *, request_id: str) -> None:
        del request_id
        operation = attempt.operation.lower()
        with self._lock:
            self.operations.append(operation)
        if operation == self.blocked_operation:
            raise problem(
                status=429,
                code="ABUSE_POLICY_LIMITED",
                title="Request rate limited",
                detail="The configured abuse policy denied this request.",
                retryable=True,
            )

    def record_login_failure(
        self,
        attempt: PublicAuthAttempt,
        *,
        principal_id: str | None,
        request_id: str,
    ) -> None:
        del attempt, principal_id, request_id

    def record_login_success(self, attempt: PublicAuthAttempt, *, request_id: str) -> None:
        del attempt, request_id

    def check_authenticated(self, *, operation: str, subject_hint: str | None) -> None:
        del operation, subject_hint


@pytest.mark.parametrize("operation", ["register", "login"])
def test_public_authentication_operations_invoke_the_abuse_policy(operation: str) -> None:
    limiter = RecordingLimit(operation)
    service = _fast_service(abuse_protection=limiter)
    if operation == "login":
        service.register(
            RegistrationCommand(username="limited-user", password="correct-password"),
            request_id="setup",
        )
        limiter.operations.clear()
        invoke = lambda: service.login(  # noqa: E731
            LoginCommand(username="limited-user", password="correct-password"),
            request_id="limited-login",
        )
    else:
        invoke = lambda: service.register(  # noqa: E731
            RegistrationCommand(username="limited-user", password="correct-password"),
            request_id="limited-register",
        )

    with pytest.raises(ProblemException) as limited:
        invoke()
    assert limited.value.problem.status == 429
    assert limiter.operations == [operation]


def test_public_access_models_keep_login_compatibility_resource_ceilings() -> None:
    # The wire model keeps the legacy login ceiling. Registration/password-change policy is
    # centralized in AccessService so deployment settings and the returned policy cannot drift.
    assert RegistrationCommand(username="u", password="x").password.get_secret_value() == "x"

    with pytest.raises(ValidationError):
        RegistrationCommand(username="u" * 129, password="valid")
    with pytest.raises(ValidationError):
        RegistrationCommand(username="valid", password="x" * 1025)
    with pytest.raises(ValidationError):
        LoginCommand(username="valid", password="x" * 1025)
    with pytest.raises(ValidationError):
        MembershipRequestCreate(reason="r" * 2001)
    with pytest.raises(ValidationError):
        AccessDecisionCommand(reason="r" * 2001)
    with pytest.raises(ValidationError):
        CapabilityRequestCreate(capability_keys=tuple(f"cap-{index}" for index in range(65)))
    with pytest.raises(ValidationError):
        CapabilityRequestCreate(capability_keys=("c" * 129,))


def test_membership_request_replay_is_exact_body_change_conflicts_and_header_is_bounded() -> None:
    service = _fast_service()
    app = create_app(
        settings=Settings(environment="test", runtime_backend="memory", _env_file=None),
        access_service=service,
    )
    with TestClient(app) as client:
        password = "Independent passphrase 2026!"
        assert (
            client.post(
                "/api/v1/auth/registrations",
                json={"username": "be23-idempotency", "password": password},
            ).status_code
            == 201
        )
        session = client.post(
            "/api/v1/auth/sessions",
            json={"username": "be23-idempotency", "password": password},
        )
        token = session.json()["access_token"]
        path = "/api/v1/organizations/organization-a/projects/project-a/membership-requests"
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "same-key"}
        first = client.post(path, json={"reason": "same"}, headers=headers)
        replay = client.post(path, json={"reason": "same"}, headers=headers)
        assert first.status_code == replay.status_code == 201
        assert first.json() == replay.json()

        conflict = client.post(path, json={"reason": "changed"}, headers=headers)
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "IDEMPOTENCY_KEY_REUSED"

        oversized = client.post(
            path,
            json={"reason": "bounded"},
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "k" * 257},
        )
        assert oversized.status_code == 422
        assert oversized.headers["content-type"].startswith("application/problem+json")
        assert token not in oversized.text
