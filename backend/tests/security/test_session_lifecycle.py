from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.security.access_models import (
    AccountProfileUpdate,
    AccountStatus,
    LoginCommand,
    PasswordChangeCommand,
    RegistrationCommand,
    ResolvedSession,
    SessionIssueCapacityRejected,
    SessionIssued,
    SessionIssueResult,
)
from hc_data_platform.security.access_repository import InMemoryAccessRepository
from hc_data_platform.security.access_service import AccessService
from hc_data_platform.security.passwords import PasswordHasher, PasswordPolicy, ScryptParameters


class MutableClock:
    def __init__(self, timestamp: datetime) -> None:
        self.timestamp = timestamp

    def __call__(self) -> datetime:
        return self.timestamp

    def advance(self, seconds: int) -> None:
        self.timestamp += timedelta(seconds=seconds)


class PausingIssueRepository(InMemoryAccessRepository):
    def __init__(self) -> None:
        super().__init__()
        self.issue_started = Event()
        self.release_issue = Event()

    def create_session(
        self,
        *,
        principal_id: str,
        token_hash: str,
        expected_password_hash: str,
        expected_credential_revision: int,
        request_id: str,
    ) -> SessionIssueResult:
        if request_id == "race-login":
            self.issue_started.set()
            if not self.release_issue.wait(timeout=5):
                raise RuntimeError("timed out waiting to release the login race")
        return super().create_session(
            principal_id=principal_id,
            token_hash=token_hash,
            expected_password_hash=expected_password_hash,
            expected_credential_revision=expected_credential_revision,
            request_id=request_id,
        )


class AdvanceAfterFirstResolveRepository(InMemoryAccessRepository):
    def __init__(self, clock: MutableClock) -> None:
        super().__init__(
            session_idle_ttl_seconds=5,
            session_absolute_ttl_seconds=60,
            session_touch_interval_seconds=1,
            clock=clock,
        )
        self._test_clock = clock
        self._advance_after_resolve = False

    def arm_boundary_crossing(self) -> None:
        self._advance_after_resolve = True

    def resolve_session(
        self, token_hash: str, *, request_id: str | None = None
    ) -> ResolvedSession | None:
        resolved = super().resolve_session(token_hash, request_id=request_id)
        if self._advance_after_resolve and resolved is not None:
            self._advance_after_resolve = False
            self._test_clock.advance(5)
        return resolved


def _token_hash(index: int) -> str:
    return hashlib.sha256(f"session-{index}".encode()).hexdigest()


def _account(repository: InMemoryAccessRepository, username: str = "session-owner") -> str:
    return repository.register_account(
        canonical_username=username,
        display_username=username,
        password_hash="test-only-hash",
        request_id=f"register-{username}",
    ).principal_id


def _create_session(
    repository: InMemoryAccessRepository,
    *,
    principal_id: str,
    token_hash: str,
    request_id: str,
) -> str:
    credential = repository.credential_for_principal(principal_id)
    assert credential is not None
    issue = repository.create_session(
        principal_id=principal_id,
        token_hash=token_hash,
        expected_password_hash=credential.password_hash,
        expected_credential_revision=credential.credential_revision,
        request_id=request_id,
    )
    assert isinstance(issue, SessionIssued)
    return issue.session_id


def test_session_settings_have_bounded_defaults_and_ordered_ttls() -> None:
    settings = Settings(_env_file=None)
    assert settings.password_min_length == 6
    assert settings.session_idle_ttl_seconds == 1_800
    assert settings.session_absolute_ttl_seconds == 86_400
    assert settings.session_touch_interval_seconds == 60
    assert settings.max_active_sessions == 5

    with pytest.raises(ValidationError):
        Settings(password_min_length=5, _env_file=None)

    with pytest.raises(ValidationError, match="HC_SESSION_TOUCH_INTERVAL_SECONDS"):
        Settings(
            session_touch_interval_seconds=10,
            session_idle_ttl_seconds=10,
            session_absolute_ttl_seconds=20,
            _env_file=None,
        )
    with pytest.raises(ValueError, match="greater than the touch interval"):
        InMemoryAccessRepository(
            session_touch_interval_seconds=10,
            session_idle_ttl_seconds=10,
            session_absolute_ttl_seconds=20,
        )
    with pytest.raises(ValidationError, match="HC_SESSION_IDLE_TTL_SECONDS"):
        Settings(
            session_touch_interval_seconds=5,
            session_idle_ttl_seconds=10,
            session_absolute_ttl_seconds=9,
            _env_file=None,
        )


def test_idle_expiry_and_touch_use_exact_utc_boundaries() -> None:
    clock = MutableClock(datetime(2026, 8, 20, tzinfo=timezone.utc))
    repository = InMemoryAccessRepository(
        session_idle_ttl_seconds=10,
        session_absolute_ttl_seconds=30,
        session_touch_interval_seconds=3,
        clock=clock,
    )
    principal_id = _account(repository)
    token_hash = _token_hash(1)
    _create_session(
        repository,
        principal_id=principal_id,
        token_hash=token_hash,
        request_id="login-1",
    )
    issued_at = repository._sessions[token_hash].issued_at

    clock.advance(2)
    assert repository.resolve_session(token_hash) is not None
    assert repository._sessions[token_hash].last_seen_at == issued_at

    clock.advance(1)
    assert repository.resolve_session(token_hash) is not None
    assert repository._sessions[token_hash].last_seen_at == issued_at + timedelta(seconds=3)

    clock.advance(9)
    assert repository.resolve_session(token_hash) is not None
    assert repository._sessions[token_hash].last_seen_at == issued_at + timedelta(seconds=12)

    clock.advance(10)
    assert repository.resolve_session(token_hash, request_id="expired-idle") is None
    expired = repository._sessions[token_hash]
    assert expired.revoked_at == issued_at + timedelta(seconds=22)
    assert expired.revocation_reason == "EXPIRED"
    expiry_audit = [event for event in repository._audit if event.action == "auth.session.expired"]
    assert len(expiry_audit) == 1
    assert expiry_audit[0].request_id == "expired-idle"
    assert expiry_audit[0].safe_details == {"reason": "EXPIRED"}
    assert expiry_audit[0].occurred_at == expired.revoked_at


def test_absolute_expiry_wins_even_when_idle_touch_is_current() -> None:
    clock = MutableClock(datetime(2026, 8, 20, tzinfo=timezone.utc))
    repository = InMemoryAccessRepository(
        session_idle_ttl_seconds=10,
        session_absolute_ttl_seconds=20,
        session_touch_interval_seconds=4,
        clock=clock,
    )
    principal_id = _account(repository, "absolute-owner")
    token_hash = _token_hash(2)
    _create_session(
        repository,
        principal_id=principal_id,
        token_hash=token_hash,
        request_id="login-absolute",
    )

    for _ in range(4):
        clock.advance(4)
        assert repository.resolve_session(token_hash) is not None
    clock.advance(4)
    assert repository.resolve_session(token_hash, request_id="expired-absolute") is None
    assert repository._sessions[token_hash].revocation_reason == "EXPIRED"


def test_sixth_login_is_rejected_without_revoking_existing_sessions() -> None:
    clock = MutableClock(datetime(2026, 8, 20, tzinfo=timezone.utc))
    repository = InMemoryAccessRepository(clock=clock)
    principal_id = _account(repository, "limit-owner")
    token_hashes: list[str] = []

    for index in range(5):
        token_hash = _token_hash(10 + index)
        token_hashes.append(token_hash)
        _create_session(
            repository,
            principal_id=principal_id,
            token_hash=token_hash,
            request_id=f"login-limit-{index}",
        )
        clock.advance(1)

    credential = repository.credential_for_principal(principal_id)
    assert credential is not None
    rejected_hash = _token_hash(15)
    issue = repository.create_session(
        principal_id=principal_id,
        token_hash=rejected_hash,
        expected_password_hash=credential.password_hash,
        expected_credential_revision=credential.credential_revision,
        request_id="login-limit-rejected",
    )
    assert isinstance(issue, SessionIssueCapacityRejected)
    assert issue.retry_after_seconds == 1_795
    assert rejected_hash not in repository._sessions
    assert all(repository.resolve_session(token_hash) is not None for token_hash in token_hashes)
    assert all(
        session.revoked_at is None
        for session in repository._sessions.values()
        if session.principal_id == principal_id
    )
    admission_events = [
        event for event in repository._audit if event.action == "auth.session.admission.denied"
    ]
    assert len(admission_events) == 1
    assert admission_events[0].request_id == "login-limit-rejected"
    assert admission_events[0].outcome == "DENIED"
    assert admission_events[0].safe_details == {"reason": "SESSION_LIMIT_REACHED"}
    assert all(
        token_hash not in repr(admission_events) for token_hash in (*token_hashes, rejected_hash)
    )
    assert not any(
        event.action == "auth.session.revoked"
        and event.safe_details.get("reason") == "SESSION_LIMIT"
        for event in repository._audit
    )


def test_late_login_at_capacity_preserves_the_existing_session() -> None:
    clock = MutableClock(datetime(2026, 8, 20, tzinfo=timezone.utc))
    repository = InMemoryAccessRepository(max_active_sessions=1, clock=clock)
    service = AccessService(
        repository,
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
        password_policy=PasswordPolicy(min_length=15, max_length=128),
    )
    password = "Capacity-safe login phrase 2026!"
    principal = service.register(
        RegistrationCommand(username="capacity-owner", password=password),
        request_id="capacity-register",
    ).principal
    session_b = service.login(
        LoginCommand(username=principal.username, password=password),
        request_id="login-b",
    ).access_token

    with pytest.raises(ProblemException) as rejected_a:
        service.login(
            LoginCommand(username=principal.username, password=password),
            request_id="late-login-a",
        )

    assert rejected_a.value.problem.status == 429
    assert rejected_a.value.problem.code == "SESSION_LIMIT_REACHED"
    assert rejected_a.value.problem.retryable is True
    assert rejected_a.value.problem.retry_after_seconds == 1_800
    assert service.authenticate_access_token(session_b) is not None
    assert len(repository._sessions) == 1
    assert all(session.revoked_at is None for session in repository._sessions.values())
    assert [
        event.request_id
        for event in repository._audit
        if event.action == "auth.session.admission.denied"
    ] == ["late-login-a"]
    assert not any(event.action == "auth.login.failed" for event in repository._audit)


def test_overlapping_valid_logins_admit_only_the_configured_capacity() -> None:
    repository = InMemoryAccessRepository(max_active_sessions=5)
    service = AccessService(
        repository,
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
        password_policy=PasswordPolicy(min_length=15, max_length=128),
    )
    password = "Concurrent capacity phrase 2026!"
    principal = service.register(
        RegistrationCommand(username="concurrent-capacity", password=password),
        request_id="concurrent-capacity-register",
    ).principal
    barrier = Barrier(12)

    def login(index: int) -> tuple[str, object]:
        barrier.wait(timeout=5)
        try:
            session = service.login(
                LoginCommand(username=principal.username, password=password),
                request_id=f"concurrent-capacity-{index}",
            )
            return "issued", session.access_token
        except ProblemException as exc:
            return "rejected", exc.problem

    with ThreadPoolExecutor(max_workers=12) as executor:
        outcomes = tuple(executor.map(login, range(12)))

    issued = [value for status, value in outcomes if status == "issued"]
    rejected = [value for status, value in outcomes if status == "rejected"]
    assert len(issued) == 5
    assert len(set(issued)) == 5
    assert len(rejected) == 7
    assert all(
        problem.status == 429
        and problem.code == "SESSION_LIMIT_REACHED"
        and 1 <= problem.retry_after_seconds <= 86_400
        for problem in rejected
    )
    assert len(repository._sessions) == 5
    assert all(session.revoked_at is None for session in repository._sessions.values())
    assert sum(event.action == "auth.session.admission.denied" for event in repository._audit) == 7
    assert sum(event.action == "auth.login.succeeded" for event in repository._audit) == 12
    assert not any(event.action == "auth.login.failed" for event in repository._audit)
    assert not any(
        event.safe_details.get("reason") == "SESSION_LIMIT" for event in repository._audit
    )


def test_success_audit_failure_does_not_leave_an_orphan_session() -> None:
    repository = InMemoryAccessRepository()
    service = AccessService(
        repository,
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
        password_policy=PasswordPolicy(min_length=15, max_length=128),
    )
    password = "Audit atomicity phrase 2026!"
    principal = service.register(
        RegistrationCommand(username="audit-atomicity", password=password),
        request_id="audit-atomicity-register",
    ).principal
    original_append = repository._append_audit

    def fail_success_audit(**kwargs: object) -> None:
        if kwargs.get("action") == "auth.login.succeeded":
            raise RuntimeError("injected audit failure")
        original_append(**kwargs)  # type: ignore[arg-type]

    with (
        patch.object(repository, "_append_audit", side_effect=fail_success_audit),
        pytest.raises(RuntimeError, match="injected audit failure"),
    ):
        service.login(
            LoginCommand(username=principal.username, password=password),
            request_id="audit-atomicity-login",
        )

    assert repository._sessions == {}
    assert not any(event.request_id == "audit-atomicity-login" for event in repository._audit)


def test_resolve_revokes_and_audits_credential_or_status_invalid_sessions() -> None:
    clock = MutableClock(datetime(2026, 8, 20, tzinfo=timezone.utc))
    repository = InMemoryAccessRepository(clock=clock)
    principal_id = _account(repository, "revision-owner")
    token_hash = _token_hash(30)
    _create_session(
        repository,
        principal_id=principal_id,
        token_hash=token_hash,
        request_id="login-revision",
    )
    credential = repository._accounts[principal_id]
    repository._accounts[principal_id] = replace(
        credential,
        credential_revision=credential.credential_revision + 1,
    )

    assert repository.resolve_session(token_hash, request_id="revision-invalid") is None
    revision_session = repository._sessions[token_hash]
    assert revision_session.revoked_at is not None
    assert revision_session.revocation_reason == "PASSWORD_CHANGED"

    disabled_id = _account(repository, "disabled-owner")
    disabled_hash = _token_hash(31)
    _create_session(
        repository,
        principal_id=disabled_id,
        token_hash=disabled_hash,
        request_id="login-disabled",
    )
    disabled = repository._accounts[disabled_id]
    repository._accounts[disabled_id] = replace(
        disabled,
        principal=disabled.principal.model_copy(update={"status": AccountStatus.DISABLED}),
    )
    assert repository.resolve_session(disabled_hash, request_id="status-invalid") is None
    disabled_session = repository._sessions[disabled_hash]
    assert disabled_session.revoked_at is not None
    assert disabled_session.revocation_reason == "ACCOUNT_DISABLED"
    invalid_events = [
        event
        for event in repository._audit
        if event.action == "auth.session.revoked"
        and event.safe_details.get("reason") in {"PASSWORD_CHANGED", "ACCOUNT_DISABLED"}
    ]
    assert [event.safe_details for event in invalid_events] == [
        {"reason": "PASSWORD_CHANGED"},
        {"reason": "ACCOUNT_DISABLED"},
    ]


def test_login_issuance_cas_rejects_password_verified_before_rotation() -> None:
    repository = PausingIssueRepository()
    service = AccessService(
        repository,
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
        password_policy=PasswordPolicy(min_length=15, max_length=128),
    )
    original_password = "Original race-resistant phrase 2026!"
    new_password = "Rotated race-resistant phrase 2027!"
    principal = service.register(
        RegistrationCommand(username="race-owner", password=original_password),
        request_id="race-register",
    ).principal
    current_token = service.login(
        LoginCommand(username=principal.username, password=original_password),
        request_id="initial-login",
    ).access_token

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending_login = executor.submit(
            service.login,
            LoginCommand(username=principal.username, password=original_password),
            request_id="race-login",
        )
        assert repository.issue_started.wait(timeout=5)
        changed = service.change_password(
            current_token,
            PasswordChangeCommand(
                current_password=original_password,
                new_password=new_password,
            ),
            if_match='"v1"',
            idempotency_key="race-password-change",
            request_id="race-password-change",
        )
        assert changed.other_sessions_revoked == 0
        repository.release_issue.set()
        with pytest.raises(ProblemException) as raced:
            pending_login.result(timeout=5)

    assert raced.value.problem.code == "INVALID_CREDENTIALS"
    assert service.authenticate_access_token(current_token) is not None
    assert (
        sum(
            session.revoked_at is None
            for session in repository._sessions.values()
            if session.principal_id == principal.principal_id
        )
        == 1
    )
    assert (
        service.login(
            LoginCommand(username=principal.username, password=new_password),
            request_id="new-password-login",
        ).principal.principal_id
        == principal.principal_id
    )


@pytest.mark.parametrize("operation", ("profile", "password"))
def test_account_mutation_revalidates_session_at_repository_commit(operation: str) -> None:
    clock = MutableClock(datetime(2026, 8, 20, tzinfo=timezone.utc))
    repository = InMemoryAccessRepository(
        session_idle_ttl_seconds=5,
        session_absolute_ttl_seconds=60,
        session_touch_interval_seconds=1,
        clock=clock,
    )
    hasher = PasswordHasher(ScryptParameters(n=2**10))
    service = AccessService(
        repository,
        password_hasher=hasher,
        password_policy=PasswordPolicy(min_length=15, max_length=128),
    )
    original_password = "Commit boundary phrase 2026!"
    principal = service.register(
        RegistrationCommand(username=f"{operation}-commit-owner", password=original_password),
        request_id=f"{operation}-register",
    ).principal
    token = service.login(
        LoginCommand(username=principal.username, password=original_password),
        request_id=f"{operation}-login",
    ).access_token
    mutation_entered = Event()
    release_mutation = Event()

    if operation == "profile":
        original_mutation = repository.update_account_profile

        def paused_mutation(**kwargs: object) -> object:
            mutation_entered.set()
            if not release_mutation.wait(timeout=5):
                raise RuntimeError("timed out waiting to release profile mutation")
            return original_mutation(**kwargs)  # type: ignore[arg-type]

        invoke = lambda: service.update_account_profile(  # noqa: E731
            token,
            AccountProfileUpdate(display_name="Must not commit"),
            if_match='"v1"',
            idempotency_key="commit-profile",
            request_id="commit-profile-request",
        )
        request_id = "commit-profile-request"
        method = "update_account_profile"
    else:
        original_mutation = repository.change_password

        def paused_mutation(**kwargs: object) -> object:
            mutation_entered.set()
            if not release_mutation.wait(timeout=5):
                raise RuntimeError("timed out waiting to release password mutation")
            return original_mutation(**kwargs)  # type: ignore[arg-type]

        invoke = lambda: service.change_password(  # noqa: E731
            token,
            PasswordChangeCommand(
                current_password=original_password,
                new_password="Replacement boundary phrase 2027!",
            ),
            if_match='"v1"',
            idempotency_key="commit-password",
            request_id="commit-password-request",
        )
        request_id = "commit-password-request"
        method = "change_password"

    try:
        with (
            patch.object(repository, method, side_effect=paused_mutation),
            ThreadPoolExecutor(max_workers=1) as executor,
        ):
            pending = executor.submit(invoke)
            assert mutation_entered.wait(timeout=5)
            clock.advance(5)
            release_mutation.set()
            with pytest.raises(ProblemException) as invalid:
                pending.result(timeout=5)
        assert invalid.value.problem.code == "SESSION_INVALID"
    finally:
        release_mutation.set()

    credential = repository._accounts[principal.principal_id]
    assert credential.account_revision == 1
    assert credential.credential_revision == 1
    assert credential.principal.display_name == principal.username
    assert hasher.verify(original_password, credential.password_hash)
    session = repository._sessions[service.token_hash(token)]
    assert session.revocation_reason == "EXPIRED"
    expiry_events = [event for event in repository._audit if event.action == "auth.session.expired"]
    assert len(expiry_events) == 1
    assert expiry_events[0].request_id == request_id


def test_password_replay_revalidates_session_at_repository_commit() -> None:
    clock = MutableClock(datetime(2026, 8, 20, tzinfo=timezone.utc))
    repository = InMemoryAccessRepository(
        session_idle_ttl_seconds=5,
        session_absolute_ttl_seconds=60,
        session_touch_interval_seconds=1,
        clock=clock,
    )
    service = AccessService(
        repository,
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
        password_policy=PasswordPolicy(min_length=15, max_length=128),
    )
    original_password = "Replay boundary phrase 2026!"
    command = PasswordChangeCommand(
        current_password=original_password,
        new_password="Replay boundary phrase 2027!",
    )
    principal = service.register(
        RegistrationCommand(username="replay-boundary", password=original_password),
        request_id="replay-register",
    ).principal
    token = service.login(
        LoginCommand(username=principal.username, password=original_password),
        request_id="replay-login",
    ).access_token
    first = service.change_password(
        token,
        command,
        if_match='"v1"',
        idempotency_key="replay-key",
        request_id="replay-first",
    )
    assert first.account.profile.etag == '"v2"'

    replay_entered = Event()
    release_replay = Event()
    original_replay = repository.replay_account_command

    def paused_replay(**kwargs: object) -> object:
        if kwargs.get("request_id") == "replay-invalid":
            replay_entered.set()
            if not release_replay.wait(timeout=5):
                raise RuntimeError("timed out pausing in-memory password replay")
        return original_replay(**kwargs)  # type: ignore[arg-type]

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        with patch.object(
            repository,
            "replay_account_command",
            side_effect=paused_replay,
        ):
            pending = executor.submit(
                service.change_password,
                token,
                command,
                if_match='"v1"',
                idempotency_key="replay-key",
                request_id="replay-invalid",
            )
            assert replay_entered.wait(timeout=5)
            clock.advance(5)
            release_replay.set()
            with pytest.raises(ProblemException) as invalid:
                pending.result(timeout=5)
        assert invalid.value.problem.code == "SESSION_INVALID"
    finally:
        release_replay.set()
        executor.shutdown(wait=True, cancel_futures=True)

    credential = repository._accounts[principal.principal_id]
    assert (credential.account_revision, credential.credential_revision) == (2, 2)
    session = repository._sessions[service.token_hash(token)]
    assert session.revocation_reason == "EXPIRED"
    replay_audits = [event for event in repository._audit if event.request_id == "replay-invalid"]
    assert [(event.action, event.safe_details) for event in replay_audits] == [
        ("auth.session.expired", {"reason": "EXPIRED"})
    ]
    assert replay_audits[0].occurred_at == session.revoked_at


def test_profile_mutation_loses_to_password_rotation_after_service_resolve() -> None:
    repository = InMemoryAccessRepository()
    service = AccessService(
        repository,
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
        password_policy=PasswordPolicy(min_length=15, max_length=128),
    )
    original_password = "Profile rotation phrase 2026!"
    principal = service.register(
        RegistrationCommand(username="profile-rotation", password=original_password),
        request_id="profile-rotation-register",
    ).principal
    current_token, peer_token = (
        service.login(
            LoginCommand(username=principal.username, password=original_password),
            request_id=f"profile-rotation-login-{index}",
        ).access_token
        for index in range(2)
    )
    profile_entered = Event()
    release_profile = Event()
    original_update = repository.update_account_profile

    def paused_update(**kwargs: object) -> object:
        profile_entered.set()
        if not release_profile.wait(timeout=5):
            raise RuntimeError("timed out pausing in-memory profile mutation")
        return original_update(**kwargs)  # type: ignore[arg-type]

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        with patch.object(
            repository,
            "update_account_profile",
            side_effect=paused_update,
        ):
            pending = executor.submit(
                service.update_account_profile,
                peer_token,
                AccountProfileUpdate(display_name="Must not survive rotation"),
                if_match='"v1"',
                idempotency_key="profile-after-rotation",
                request_id="profile-after-rotation",
            )
            assert profile_entered.wait(timeout=5)
            changed = service.change_password(
                current_token,
                PasswordChangeCommand(
                    current_password=original_password,
                    new_password="Profile rotation phrase 2027!",
                ),
                if_match='"v1"',
                idempotency_key="profile-rotation-password",
                request_id="profile-rotation-password",
            )
            assert changed.other_sessions_revoked == 1
            release_profile.set()
            with pytest.raises(ProblemException) as invalid:
                pending.result(timeout=5)
        assert invalid.value.problem.code == "SESSION_INVALID"
    finally:
        release_profile.set()
        executor.shutdown(wait=True, cancel_futures=True)

    credential = repository._accounts[principal.principal_id]
    assert credential.principal.display_name == principal.username
    assert (credential.account_revision, credential.credential_revision) == (2, 2)
    assert repository._sessions[service.token_hash(peer_token)].revocation_reason == (
        "PASSWORD_CHANGED"
    )
    assert not any(event.request_id == "profile-after-rotation" for event in repository._audit)


def test_password_rotation_classifies_expired_and_stale_other_sessions_before_counting() -> None:
    clock = MutableClock(datetime(2026, 8, 20, tzinfo=timezone.utc))
    repository = InMemoryAccessRepository(
        session_idle_ttl_seconds=10,
        session_absolute_ttl_seconds=60,
        session_touch_interval_seconds=2,
        clock=clock,
    )
    service = AccessService(
        repository,
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
        password_policy=PasswordPolicy(min_length=15, max_length=128),
    )
    original_password = "Rotation classification phrase 2026!"
    principal = service.register(
        RegistrationCommand(username="rotation-classifier", password=original_password),
        request_id="rotation-register",
    ).principal
    tokens = tuple(
        service.login(
            LoginCommand(username=principal.username, password=original_password),
            request_id=f"rotation-login-{index}",
        ).access_token
        for index in range(4)
    )
    hashes = tuple(service.token_hash(token) for token in tokens)
    current_hash, valid_hash, expired_hash, stale_hash = hashes
    repository._sessions[expired_hash].last_seen_at = clock.timestamp - timedelta(seconds=10)
    repository._sessions[stale_hash].credential_revision += 1

    with pytest.raises(ProblemException) as rejected:
        service.change_password(
            tokens[0],
            PasswordChangeCommand(
                current_password=original_password,
                new_password="Rejected rotation classification phrase 2027!",
            ),
            if_match='"v2"',
            idempotency_key="rotation-rejected",
            request_id="rotation-rejected",
        )
    assert rejected.value.problem.code == "ETAG_MISMATCH"
    assert repository._sessions[expired_hash].revoked_at is None
    assert repository._sessions[stale_hash].revoked_at is None
    assert not any(event.request_id == "rotation-rejected" for event in repository._audit)

    changed = service.change_password(
        tokens[0],
        PasswordChangeCommand(
            current_password=original_password,
            new_password="Rotation classification phrase 2027!",
        ),
        if_match='"v1"',
        idempotency_key="rotation-classify",
        request_id="rotation-classify",
    )

    assert changed.other_sessions_revoked == 1
    assert repository._sessions[current_hash].revoked_at is None
    assert repository._sessions[current_hash].credential_revision == 2
    assert repository._sessions[valid_hash].revocation_reason == "PASSWORD_CHANGED"
    assert repository._sessions[expired_hash].revocation_reason == "EXPIRED"
    assert repository._sessions[stale_hash].revocation_reason == "PASSWORD_CHANGED"
    classified_events = [
        (event.resource_id, event.action, event.safe_details)
        for event in repository._audit
        if event.request_id == "rotation-classify" and event.resource_type == "session"
    ]
    assert sorted(action for _, action, _ in classified_events) == [
        "auth.session.expired",
        "auth.session.revoked",
        "auth.session.revoked",
    ]
    assert {details["reason"] for _, _, details in classified_events} == {
        "EXPIRED",
        "PASSWORD_CHANGED",
    }
    revoked_at_by_id = {
        session.session_id: session.revoked_at
        for session in repository._sessions.values()
        if session.revoked_at is not None
    }
    assert all(
        event.occurred_at == revoked_at_by_id[event.resource_id]
        for event in repository._audit
        if event.request_id == "rotation-classify" and event.resource_type == "session"
    )


def test_expired_api_token_returns_session_invalid_and_safe_audit() -> None:
    clock = MutableClock(datetime(2026, 8, 20, tzinfo=timezone.utc))
    repository = InMemoryAccessRepository(
        session_idle_ttl_seconds=5,
        session_absolute_ttl_seconds=60,
        session_touch_interval_seconds=1,
        clock=clock,
    )
    service = AccessService(
        repository,
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
        password_policy=PasswordPolicy(min_length=15, max_length=128),
    )
    app = create_app(
        settings=Settings(environment="test", runtime_backend="memory", _env_file=None),
        access_service=service,
    )

    with TestClient(app) as client:
        registration = client.post(
            "/api/v1/auth/registrations",
            json={"username": "api-expiry-owner", "password": "Durable phrase for 2026!"},
        )
        assert registration.status_code == 201
        login = client.post(
            "/api/v1/auth/sessions",
            json={"username": "api-expiry-owner", "password": "Durable phrase for 2026!"},
        )
        assert login.status_code == 201
        token = str(login.json()["access_token"])
        clock.advance(5)
        expired = client.get(
            "/api/v1/auth/session/bootstrap",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert expired.status_code == 401
    assert expired.json()["code"] == "SESSION_INVALID"
    assert expired.headers["cache-control"] == "no-store"
    record = repository._sessions[service.token_hash(token)]
    assert record.revocation_reason == "EXPIRED"
    expiry_events = [event for event in repository._audit if event.action == "auth.session.expired"]
    assert len(expiry_events) == 1
    assert expiry_events[0].request_id == expired.headers["x-request-id"]
    assert expiry_events[0].safe_details == {"reason": "EXPIRED"}
    assert token not in repr(expiry_events)


@pytest.mark.parametrize(
    "path",
    ("/api/v1/auth/session/bootstrap", "/api/v1/account/profile"),
)
def test_second_service_resolve_audits_ttl_crossing_with_request_id(path: str) -> None:
    clock = MutableClock(datetime(2026, 8, 20, tzinfo=timezone.utc))
    repository = AdvanceAfterFirstResolveRepository(clock)
    service = AccessService(
        repository,
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
        password_policy=PasswordPolicy(min_length=15, max_length=128),
    )
    app = create_app(
        settings=Settings(environment="test", runtime_backend="memory", _env_file=None),
        access_service=service,
    )

    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v1/auth/registrations",
                json={"username": "crossing-owner", "password": "Boundary phrase for 2026!"},
            ).status_code
            == 201
        )
        login = client.post(
            "/api/v1/auth/sessions",
            json={"username": "crossing-owner", "password": "Boundary phrase for 2026!"},
        )
        token = str(login.json()["access_token"])
        repository.arm_boundary_crossing()
        response = client.get(path, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["code"] == "SESSION_INVALID"
    expiry_events = [event for event in repository._audit if event.action == "auth.session.expired"]
    assert len(expiry_events) == 1
    assert expiry_events[0].request_id == response.headers["x-request-id"]
