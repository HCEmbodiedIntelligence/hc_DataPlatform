from __future__ import annotations

import asyncio
import json
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import Event, Lock
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import asyncpg
import pytest

psycopg = pytest.importorskip("psycopg")

from hc_data_platform.core import migrations as migration_module  # noqa: E402
from hc_data_platform.core.errors import ProblemException  # noqa: E402
from hc_data_platform.core.migrations import apply_migrations  # noqa: E402
from hc_data_platform.security.access_models import (  # noqa: E402
    AccessDecisionCommand,
    AccessRequestStatus,
    AccountProfileUpdate,
    CapabilityRequestCreate,
    LoginCommand,
    MembershipRequestCreate,
    PasswordChangeCommand,
    RegistrationCommand,
    SessionIssueResult,
)
from hc_data_platform.security.access_postgres import PostgresAccessRepository  # noqa: E402
from hc_data_platform.security.access_service import AccessService  # noqa: E402
from hc_data_platform.security.auth import AuthContext  # noqa: E402
from hc_data_platform.security.passwords import (  # noqa: E402
    PasswordHasher,
    PasswordPolicy,
    ScryptParameters,
)
from hc_data_platform.security.recovery import (  # noqa: E402
    AccountRecoveryService,
    PasswordRecoveryConfirmation,
    PasswordRecoveryRequest,
    RecoveryEmailConfirmation,
    RecoveryEmailVerificationRequest,
)

pytestmark = pytest.mark.integration


@dataclass(slots=True)
class _RecordingRecoveryDelivery:
    email_verifications: list[tuple[str, str]] = field(default_factory=list)
    password_recoveries: list[tuple[str, str]] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return True

    def send_recovery_email_verification(self, *, email: str, token: str) -> None:
        self.email_verifications.append((email, token))

    def send_password_recovery(self, *, email: str, token: str) -> None:
        self.password_recoveries.append((email, token))


def _database_url() -> str:
    value = os.getenv("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    return value


def _dsn_with_application_name(dsn: str, application_name: str) -> str:
    separator = "&" if "?" in dsn else "?"
    return f"{dsn}{separator}application_name={application_name}"


def _wait_for_blocked_repository_connections(
    observer: Any,
    *,
    application_name: str,
    expected: int,
    futures: tuple[Future[Any], ...],
    timeout_seconds: float = 10,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        blocked = observer.execute(
            """
            SELECT count(*)
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND application_name = %s
              AND wait_event_type = 'Lock'
            """,
            (application_name,),
        ).fetchone()[0]
        if int(blocked) >= expected:
            return
        completed = tuple(future for future in futures if future.done())
        if completed:
            for future in completed:
                future.result()
            pytest.fail("repository operation completed before all expected database lock waits")
        time.sleep(0.01)
    pytest.fail(
        f"observed fewer than {expected} blocked repository connections for {application_name}"
    )


def _admin(subject_id: str, organization_id: str, project_id: str) -> AuthContext:
    return AuthContext(
        subject_id=subject_id,
        organization_ids=frozenset({organization_id}),
        project_ids=frozenset({project_id}),
        region_codes=frozenset(),
        capabilities=frozenset({"access.manage"}),
        scope_pairs=frozenset({(project_id, None)}),
        organization_scope_triples=frozenset({(organization_id, project_id, None)}),
    )


def test_postgres_access_request_foreign_keys_map_to_their_actual_resource() -> None:
    dsn = _database_url()
    asyncio.run(apply_migrations(dsn))
    suffix = uuid4().hex
    organization_id = f"foreign-key-organization-{suffix}"
    project_id = f"foreign-key-project-{suffix}"
    missing_organization_id = f"missing-organization-{suffix}"
    missing_project_id = f"missing-project-{suffix}"
    unregistered_principal_id = str(uuid4())
    service = AccessService(PostgresAccessRepository.from_dsn(dsn))
    principal_ids: list[str] = []

    try:
        normalized = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        with psycopg.connect(normalized) as connection:
            connection.execute(
                "INSERT INTO registry.organization_projects "
                "(organization_id, project_id) VALUES (%s, %s)",
                (organization_id, project_id),
            )
            connection.commit()

        account = service.register(
            RegistrationCommand(
                username=f"foreign-key-account-{suffix}",
                password="foreign-key-integration-password",
            ),
            request_id=f"foreign-key-register-{suffix}",
        ).principal
        principal_ids.append(account.principal_id)
        token = service.login(
            LoginCommand(
                username=account.username,
                password="foreign-key-integration-password",
            ),
            request_id=f"foreign-key-login-{suffix}",
        ).access_token
        registered_auth = service.authenticate_access_token(token)
        assert registered_auth is not None
        unregistered_auth = AuthContext(
            subject_id=unregistered_principal_id,
            project_ids=frozenset(),
            region_codes=frozenset(),
        )

        membership_key = f"foreign-key-membership-{suffix}"
        with pytest.raises(ProblemException) as missing_membership_project:
            service.create_membership_request(
                auth=registered_auth,
                organization_id=missing_organization_id,
                project_id=missing_project_id,
                command=MembershipRequestCreate(reason="missing project"),
                idempotency_key=membership_key,
                request_id=f"missing-membership-project-{suffix}",
            )
        assert missing_membership_project.value.problem.status == 404
        assert missing_membership_project.value.problem.code == "PROJECT_NOT_FOUND"
        assert (
            missing_membership_project.value.problem.detail
            == "The requested project does not exist in the specified organization."
        )

        membership = service.create_membership_request(
            auth=registered_auth,
            organization_id=organization_id,
            project_id=project_id,
            command=MembershipRequestCreate(reason="valid project"),
            idempotency_key=membership_key,
            request_id=f"valid-membership-{suffix}",
        )
        replayed_membership = service.create_membership_request(
            auth=registered_auth,
            organization_id=organization_id,
            project_id=project_id,
            command=MembershipRequestCreate(reason="valid project"),
            idempotency_key=membership_key,
            request_id=f"replayed-membership-{suffix}",
        )
        assert membership == replayed_membership
        assert membership.status is AccessRequestStatus.PENDING

        with pytest.raises(ProblemException) as missing_membership_account:
            service.create_membership_request(
                auth=unregistered_auth,
                organization_id=organization_id,
                project_id=project_id,
                command=MembershipRequestCreate(reason="unregistered principal"),
                idempotency_key=f"missing-membership-account-{suffix}",
                request_id=f"missing-membership-account-{suffix}",
            )
        assert missing_membership_account.value.problem.status == 403
        assert missing_membership_account.value.problem.code == "ACCOUNT_PRINCIPAL_REQUIRED"

        capability_key = f"foreign-key-capability-{suffix}"
        capability_command = CapabilityRequestCreate(
            capability_keys=("dataset.read",),
            reason="foreign key regression",
        )
        with pytest.raises(ProblemException) as missing_capability_project:
            service.create_capability_request(
                auth=registered_auth,
                organization_id=missing_organization_id,
                project_id=missing_project_id,
                command=capability_command,
                idempotency_key=capability_key,
                request_id=f"missing-capability-project-{suffix}",
            )
        assert missing_capability_project.value.problem.status == 404
        assert missing_capability_project.value.problem.code == "PROJECT_NOT_FOUND"

        capability = service.create_capability_request(
            auth=registered_auth,
            organization_id=organization_id,
            project_id=project_id,
            command=capability_command,
            idempotency_key=capability_key,
            request_id=f"valid-capability-{suffix}",
        )
        replayed_capability = service.create_capability_request(
            auth=registered_auth,
            organization_id=organization_id,
            project_id=project_id,
            command=capability_command,
            idempotency_key=capability_key,
            request_id=f"replayed-capability-{suffix}",
        )
        assert capability == replayed_capability

        with pytest.raises(ProblemException) as missing_capability_account:
            service.create_capability_request(
                auth=unregistered_auth,
                organization_id=organization_id,
                project_id=project_id,
                command=capability_command,
                idempotency_key=f"missing-capability-account-{suffix}",
                request_id=f"missing-capability-account-{suffix}",
            )
        assert missing_capability_account.value.problem.status == 403
        assert missing_capability_account.value.problem.code == "ACCOUNT_PRINCIPAL_REQUIRED"
    finally:
        _cleanup(
            dsn,
            principal_ids=principal_ids,
            project_ids=(project_id, missing_project_id),
            actor_ids=(unregistered_principal_id, f"unused-actor-{suffix}"),
        )


def test_postgres_access_scope_revocation_and_concurrent_approval() -> None:
    dsn = _database_url()
    asyncio.run(apply_migrations(dsn))
    _assert_access_repository_boundary(dsn)
    suffix = uuid4().hex
    organization_a = f"access-organization-a-{suffix}"
    organization_b = f"access-organization-b-{suffix}"
    project_a = f"access-a-{suffix}"
    project_b = f"access-b-{suffix}"
    admin_a = _admin(f"admin-a-{suffix}", organization_a, project_a)
    admin_b = _admin(f"admin-b-{suffix}", organization_b, project_b)
    service = AccessService(PostgresAccessRepository.from_dsn(dsn))
    principal_ids: list[str] = []

    try:
        normalized = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        with psycopg.connect(normalized) as connection:
            connection.execute(
                "INSERT INTO registry.organization_projects "
                "(organization_id, project_id) VALUES (%s, %s), (%s, %s)",
                (organization_a, project_a, organization_b, project_b),
            )
            connection.commit()
        alice = service.register(
            RegistrationCommand(
                username=f"alice-{suffix}",
                password="integration-password-a",
            ),
            request_id=f"register-alice-{suffix}",
        ).principal
        bob = service.register(
            RegistrationCommand(
                username=f"bob-{suffix}",
                password="integration-password-b",
            ),
            request_id=f"register-bob-{suffix}",
        ).principal
        principal_ids.extend((alice.principal_id, bob.principal_id))
        alice_token = service.login(
            LoginCommand(
                username=alice.username,
                password="integration-password-a",
            ),
            request_id=f"login-alice-{suffix}",
        ).access_token
        bob_token = service.login(
            LoginCommand(
                username=bob.username,
                password="integration-password-b",
            ),
            request_id=f"login-bob-{suffix}",
        ).access_token
        alice_auth = service.authenticate_access_token(alice_token)
        bob_auth = service.authenticate_access_token(bob_token)
        assert alice_auth is not None and bob_auth is not None
        assert service.bootstrap(alice_token).available_scopes == ()

        alice_membership = service.create_membership_request(
            auth=alice_auth,
            organization_id=organization_a,
            project_id=project_a,
            command=MembershipRequestCreate(reason="join project a"),
            idempotency_key=f"alice-membership-{suffix}",
            request_id=f"alice-membership-{suffix}",
        )
        bob_membership = service.create_membership_request(
            auth=bob_auth,
            organization_id=organization_b,
            project_id=project_b,
            command=MembershipRequestCreate(reason="join project b"),
            idempotency_key=f"bob-membership-{suffix}",
            request_id=f"bob-membership-{suffix}",
        )

        with pytest.raises(ProblemException) as cross_project:
            service.get_membership_request(
                auth=alice_auth,
                organization_id=organization_b,
                project_id=project_b,
                access_request_id=bob_membership.request_id,
            )
        assert cross_project.value.problem.status == 404
        assert (
            service.list_membership_requests(
                auth=admin_a,
                organization_id=organization_b,
                project_id=project_b,
            ).items
            == ()
        )

        def approve(index: int) -> str:
            return service.decide_membership_request(
                auth=admin_a,
                organization_id=organization_a,
                project_id=project_a,
                access_request_id=alice_membership.request_id,
                target_status=AccessRequestStatus.APPROVED,
                command=AccessDecisionCommand(reason="approved"),
                idempotency_key=f"approve-{suffix}-{index}",
                request_id=f"approve-{suffix}-{index}",
            ).status.value

        with ThreadPoolExecutor(max_workers=16) as executor:
            assert set(executor.map(approve, range(64))) == {"APPROVED"}

        with pytest.raises(ProblemException) as conflicting_decision:
            service.decide_membership_request(
                auth=admin_a,
                organization_id=organization_a,
                project_id=project_a,
                access_request_id=alice_membership.request_id,
                target_status=AccessRequestStatus.REJECTED,
                command=AccessDecisionCommand(reason="too late"),
                idempotency_key=f"reject-after-approve-{suffix}",
                request_id=f"reject-after-approve-{suffix}",
            )
        assert conflicting_decision.value.problem.status == 409

        current_alice_auth = service.authenticate_access_token(alice_token)
        assert current_alice_auth is not None
        capability = service.create_capability_request(
            auth=current_alice_auth,
            organization_id=organization_a,
            project_id=project_a,
            command=CapabilityRequestCreate(
                capability_keys=("dataset.read",),
                reason="read project datasets",
            ),
            idempotency_key=f"capability-{suffix}",
            request_id=f"capability-{suffix}",
        )
        service.decide_capability_request(
            auth=admin_a,
            organization_id=organization_a,
            project_id=project_a,
            access_request_id=capability.request_id,
            target_status=AccessRequestStatus.APPROVED,
            command=AccessDecisionCommand(reason="approved"),
            idempotency_key=f"approve-capability-{suffix}",
            request_id=f"approve-capability-{suffix}",
        )
        before_revoke = service.bootstrap(alice_token)
        assert before_revoke.available_scopes[0].capabilities == ("dataset.read",)

        service.decide_capability_request(
            auth=admin_a,
            organization_id=organization_a,
            project_id=project_a,
            access_request_id=capability.request_id,
            target_status=AccessRequestStatus.REVOKED,
            command=AccessDecisionCommand(reason="revoked"),
            idempotency_key=f"revoke-capability-{suffix}",
            request_id=f"revoke-capability-{suffix}",
        )
        after_capability_revoke = service.bootstrap(alice_token)
        assert after_capability_revoke.available_scopes[0].capabilities == ()
        assert after_capability_revoke.capability_revision > before_revoke.capability_revision

        service.decide_membership_request(
            auth=admin_a,
            organization_id=organization_a,
            project_id=project_a,
            access_request_id=alice_membership.request_id,
            target_status=AccessRequestStatus.REVOKED,
            command=AccessDecisionCommand(reason="revoked"),
            idempotency_key=f"revoke-membership-{suffix}",
            request_id=f"revoke-membership-{suffix}",
        )
        assert service.bootstrap(alice_token).available_scopes == ()

        actions = {
            event.action
            for event in service.list_audit_events(
                auth=admin_a,
                organization_id=organization_a,
                project_id=project_a,
            ).items
        }
        assert {
            "access.membership.requested",
            "access.membership.approved",
            "access.capability.approved",
            "access.capability.revoked",
            "access.membership.revoked",
        }.issubset(actions)
        assert service.list_audit_events(
            auth=admin_b,
            organization_id=organization_b,
            project_id=project_b,
        ).items

        service.logout(alice_token, request_id=f"logout-alice-{suffix}")
        service.logout(alice_token, request_id=f"logout-alice-retry-{suffix}")
        assert service.authenticate_access_token(alice_token) is None
        assert service.authenticate_access_token(bob_token) is not None

        normalized = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        with psycopg.connect(normalized) as connection:
            core_rows = connection.execute(
                """
                SELECT core_event.action, core_event.details::text
                FROM core.audit_events core_event
                JOIN access_control.audit_events access_event
                  ON access_event.event_id = core_event.audit_id
                WHERE core_event.project_id = %s
                """,
                (project_a,),
            ).fetchall()
            logout_rows = connection.execute(
                """
                SELECT request_id, safe_details::text
                FROM access_control.audit_events
                WHERE actor_id = %s AND action = 'auth.session.revoked'
                """,
                (alice.principal_id,),
            ).fetchall()
        assert actions.issubset({str(row[0]) for row in core_rows})
        safe_core_payload = " ".join(str(row[1]) for row in core_rows).lower()
        assert "integration-password" not in safe_core_payload
        assert alice_token.lower() not in safe_core_payload
        assert len(logout_rows) == 1
        safe_logout_payload = " ".join(str(value) for row in logout_rows for value in row)
        assert alice_token not in safe_logout_payload
        assert bob_token not in safe_logout_payload
        assert "integration-password" not in safe_logout_payload
    finally:
        _cleanup(
            dsn,
            principal_ids=principal_ids,
            project_ids=(project_a, project_b),
            actor_ids=(admin_a.subject_id, admin_b.subject_id),
        )


def test_organization_access_upgrade_rejects_ambiguous_legacy_project_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dsn = _database_url()
    normalized = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    database_name = f"organization_access_upgrade_{uuid4().hex}"
    temporary_dsn = f"{dsn.rsplit('/', maxsplit=1)[0]}/{database_name}"
    migrations = migration_module.load_migrations()
    organization_migration_index = next(
        index
        for index, migration in enumerate(migrations)
        if migration.version == "security/011_organization_scoped_access.sql"
    )
    prefix = migrations[:organization_migration_index]
    original_repeatable = migration_module._REPEATABLE_SECURITY_MIGRATIONS
    prefix_versions = {migration.version for migration in prefix}
    prefix_repeatable = original_repeatable & prefix_versions

    with psycopg.connect(normalized, autocommit=True) as connection:
        connection.execute(
            psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(database_name))
        )
    try:
        monkeypatch.setattr(migration_module, "load_migrations", lambda: prefix)
        monkeypatch.setattr(
            migration_module,
            "_REPEATABLE_SECURITY_MIGRATIONS",
            prefix_repeatable,
        )
        assert asyncio.run(migration_module.apply_migrations(temporary_dsn)) == [
            migration.version for migration in prefix
        ]

        project_id = f"legacy-project-{uuid4().hex}"
        principal_id = uuid4()
        request_id = uuid4()
        with psycopg.connect(
            temporary_dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        ) as connection:
            connection.execute(
                "INSERT INTO registry.organization_projects (organization_id, project_id) "
                "VALUES (%s, %s), (%s, %s)",
                ("legacy-org-a", project_id, "legacy-org-b", project_id),
            )
            connection.execute(
                """
                INSERT INTO access_control.accounts (
                    principal_id, canonical_username, display_username, display_name,
                    password_hash, password_changed_at
                ) VALUES (%s, %s, %s, %s, %s, now())
                """,
                (
                    principal_id,
                    f"legacy-{uuid4().hex}",
                    "legacy",
                    "legacy",
                    "historical-hash",
                ),
            )
            connection.execute(
                """
                INSERT INTO access_control.membership_requests (
                    request_id, project_id, requester_id, status
                ) VALUES (%s, %s, %s, 'PENDING')
                """,
                (request_id, project_id, principal_id),
            )
            connection.commit()

        monkeypatch.setattr(migration_module, "load_migrations", lambda: migrations)
        monkeypatch.setattr(
            migration_module,
            "_REPEATABLE_SECURITY_MIGRATIONS",
            original_repeatable,
        )
        with pytest.raises(asyncpg.RaiseError, match="requires exactly one registry organization"):
            asyncio.run(migration_module.apply_migrations(temporary_dsn))

        with psycopg.connect(
            temporary_dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        ) as connection:
            applied = connection.execute(
                "SELECT count(*) FROM core.schema_migrations "
                "WHERE version = 'security/011_organization_scoped_access.sql'"
            ).fetchone()
            organization_column = connection.execute(
                """
                SELECT count(*)
                FROM pg_attribute
                WHERE attrelid = 'access_control.membership_requests'::regclass
                  AND attname = 'organization_id'
                  AND NOT attisdropped
                """
            ).fetchone()
        assert applied == (0,)
        assert organization_column == (0,)
    finally:
        with psycopg.connect(normalized, autocommit=True) as connection:
            connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            connection.execute(
                psycopg.sql.SQL("DROP DATABASE IF EXISTS {}").format(
                    psycopg.sql.Identifier(database_name)
                )
            )


def test_postgres_account_profile_and_password_change_are_atomic_under_race() -> None:
    dsn = _database_url()
    asyncio.run(apply_migrations(dsn))
    suffix = uuid4().hex
    username = f"account-security-{suffix}"
    original_password = "Postgres original phrase 2026!"
    candidates = (
        "First concurrent password phrase 2027!",
        "Second concurrent password phrase 2027!",
    )
    application_name = f"hc-password-cas-{suffix}"
    repository = PostgresAccessRepository.from_dsn(
        _dsn_with_application_name(dsn, application_name)
    )
    service = AccessService(
        repository,
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
        password_policy=PasswordPolicy(min_length=15, max_length=128),
    )
    principal_id: str | None = None
    normalized = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)

    try:
        principal = service.register(
            RegistrationCommand(username=username, password=original_password),
            request_id=f"account-register-{suffix}",
        ).principal
        principal_id = principal.principal_id
        sessions = tuple(
            service.login(
                LoginCommand(username=username, password=original_password),
                request_id=f"account-login-{suffix}-{index}",
            ).access_token
            for index in range(2)
        )
        settings = service.get_account_settings(sessions[0])
        assert settings.profile.etag == '"v1"'
        assert settings.profile.username == username
        assert settings.profile.display_name == username

        updated = service.update_account_profile(
            sessions[0],
            AccountProfileUpdate(display_name="PostgreSQL 账户值班员"),
            if_match='"v1"',
            idempotency_key=f"profile-{suffix}",
            request_id=f"profile-{suffix}",
        )
        replayed = service.update_account_profile(
            sessions[0],
            AccountProfileUpdate(display_name="PostgreSQL 账户值班员"),
            if_match='"v1"',
            idempotency_key=f"profile-{suffix}",
            request_id=f"profile-replay-{suffix}",
        )
        assert updated == replayed
        assert updated.profile.etag == '"v2"'

        def change(index: int) -> tuple[int, str, str]:
            try:
                result = service.change_password(
                    sessions[0],
                    PasswordChangeCommand(
                        current_password=original_password,
                        new_password=candidates[index],
                    ),
                    if_match='"v2"',
                    idempotency_key=f"password-{suffix}-{index}",
                    request_id=f"password-{suffix}-{index}",
                )
                return 200, candidates[index], result.account.profile.etag
            except ProblemException as exc:
                return exc.problem.status, candidates[index], exc.problem.code

        original_change_password = repository.change_password
        changes_ready = Event()
        release_changes = Event()
        ready_lock = Lock()
        ready_count = 0

        def staged_change_password(**kwargs: object) -> object:
            nonlocal ready_count
            with ready_lock:
                ready_count += 1
                if ready_count == 2:
                    changes_ready.set()
            if not release_changes.wait(timeout=10):
                raise RuntimeError("timed out staging PostgreSQL password CAS")
            return original_change_password(**kwargs)  # type: ignore[arg-type]

        with (
            psycopg.connect(normalized) as account_blocker,
            psycopg.connect(normalized, autocommit=True) as observer,
            patch.object(
                repository,
                "change_password",
                side_effect=staged_change_password,
            ),
        ):
            executor = ThreadPoolExecutor(max_workers=2)
            pending = tuple(executor.submit(change, index) for index in range(2))
            try:
                assert changes_ready.wait(timeout=10)
                account_blocker.execute(
                    """
                    SELECT principal_id
                    FROM access_control.accounts
                    WHERE principal_id = %s::uuid
                    FOR UPDATE
                    """,
                    (principal_id,),
                ).fetchone()
                release_changes.set()
                _wait_for_blocked_repository_connections(
                    observer,
                    application_name=application_name,
                    expected=2,
                    futures=pending,
                )
                account_blocker.commit()
                outcomes = [future.result(timeout=10) for future in pending]
            finally:
                release_changes.set()
                account_blocker.rollback()
                executor.shutdown(wait=True, cancel_futures=True)
        assert [status for status, _, _ in outcomes].count(200) == 1
        assert [status for status, _, _ in outcomes].count(412) == 1
        assert next(code for status, _, code in outcomes if status == 412) == "ETAG_MISMATCH"
        winner = next(password for status, password, _ in outcomes if status == 200)

        assert service.authenticate_access_token(sessions[0]) is not None
        assert service.authenticate_access_token(sessions[1]) is None
        with pytest.raises(ProblemException) as old_login:
            service.login(
                LoginCommand(username=username, password=original_password),
                request_id=f"old-login-{suffix}",
            )
        assert old_login.value.problem.code == "INVALID_CREDENTIALS"
        assert (
            service.login(
                LoginCommand(username=username, password=winner),
                request_id=f"new-login-{suffix}",
            ).principal.display_name
            == "PostgreSQL 账户值班员"
        )

        with psycopg.connect(normalized) as connection:
            account_row = connection.execute(
                """
                SELECT account_revision, credential_revision, display_name
                FROM access_control.accounts
                WHERE principal_id = %s::uuid
                """,
                (principal_id,),
            ).fetchone()
            session_rows = connection.execute(
                """
                SELECT revoked_at IS NULL AS active, credential_revision,
                       COALESCE(revocation_reason, '')
                FROM access_control.sessions
                WHERE principal_id = %s::uuid
                ORDER BY issued_at
                """,
                (principal_id,),
            ).fetchall()
            audit_rows = connection.execute(
                """
                SELECT action, safe_details::text
                FROM access_control.audit_events
                WHERE actor_id = %s
                  AND action IN ('account.profile.updated', 'auth.password.changed')
                ORDER BY occurred_at
                """,
                (principal_id,),
            ).fetchall()
        assert account_row == (3, 2, "PostgreSQL 账户值班员")
        assert sum(bool(row[0]) for row in session_rows) == 2  # winner plus new login
        assert any(row[2] == "PASSWORD_CHANGED" for row in session_rows)
        assert [str(row[0]) for row in audit_rows] == [
            "account.profile.updated",
            "auth.password.changed",
        ]
        safe_audit = " ".join(str(value) for row in audit_rows for value in row)
        assert original_password not in safe_audit
        assert all(candidate not in safe_audit for candidate in candidates)
        assert all(token not in safe_audit for token in sessions)
    finally:
        if principal_id is not None:
            _cleanup(
                dsn,
                principal_ids=[principal_id],
                project_ids=(f"unused-a-{suffix}", f"unused-b-{suffix}"),
                actor_ids=(f"unused-actor-a-{suffix}", f"unused-actor-b-{suffix}"),
            )


def test_postgres_password_replay_revalidates_session_at_repository_commit() -> None:
    dsn = _database_url()
    asyncio.run(apply_migrations(dsn))
    suffix = uuid4().hex
    original_password = "Postgres replay boundary phrase 2026!"
    new_password = "Postgres replay boundary phrase 2027!"
    repository = PostgresAccessRepository.from_dsn(
        dsn,
        session_idle_ttl_seconds=5,
        session_absolute_ttl_seconds=60,
        session_touch_interval_seconds=1,
    )
    service = AccessService(
        repository,
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
        password_policy=PasswordPolicy(min_length=15, max_length=128),
    )
    principal_id: str | None = None
    normalized = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    replay_entered = Event()
    release_replay = Event()

    try:
        principal = service.register(
            RegistrationCommand(
                username=f"password-replay-{suffix}",
                password=original_password,
            ),
            request_id=f"replay-register-{suffix}",
        ).principal
        principal_id = principal.principal_id
        token = service.login(
            LoginCommand(username=principal.username, password=original_password),
            request_id=f"replay-login-{suffix}",
        ).access_token
        command = PasswordChangeCommand(
            current_password=original_password,
            new_password=new_password,
        )
        idempotency_key = f"password-replay-key-{suffix}"
        first = service.change_password(
            token,
            command,
            if_match='"v1"',
            idempotency_key=idempotency_key,
            request_id=f"replay-first-{suffix}",
        )
        assert first.account.profile.etag == '"v2"'

        replay_request_id = f"replay-invalid-{suffix}"
        original_replay = repository.replay_account_command

        def paused_replay(**kwargs: object) -> object:
            if kwargs.get("request_id") == replay_request_id:
                replay_entered.set()
                if not release_replay.wait(timeout=10):
                    raise RuntimeError("timed out pausing PostgreSQL password replay")
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
                    idempotency_key=idempotency_key,
                    request_id=replay_request_id,
                )
                assert replay_entered.wait(timeout=10)
                with psycopg.connect(normalized) as connection:
                    connection.execute(
                        """
                        UPDATE access_control.sessions
                        SET last_seen_at = clock_timestamp() - interval '5 seconds'
                        WHERE token_hash = %s
                        """,
                        (service.token_hash(token),),
                    )
                release_replay.set()
                with pytest.raises(ProblemException) as invalid:
                    pending.result(timeout=10)
            assert invalid.value.problem.code == "SESSION_INVALID"
        finally:
            release_replay.set()
            executor.shutdown(wait=True, cancel_futures=True)

        with psycopg.connect(normalized) as connection:
            account_state = connection.execute(
                """
                SELECT account_revision, credential_revision
                FROM access_control.accounts
                WHERE principal_id = %s::uuid
                """,
                (principal_id,),
            ).fetchone()
            session_state = connection.execute(
                """
                SELECT revocation_reason
                FROM access_control.sessions
                WHERE token_hash = %s
                """,
                (service.token_hash(token),),
            ).fetchone()
            replay_audit = connection.execute(
                """
                SELECT action, safe_details->>'reason'
                FROM access_control.audit_events
                WHERE actor_id = %s AND request_id = %s
                """,
                (principal_id, replay_request_id),
            ).fetchall()
        assert account_state == (2, 2)
        assert session_state == ("EXPIRED",)
        assert replay_audit == [("auth.session.expired", "EXPIRED")]
    finally:
        release_replay.set()
        if principal_id is not None:
            _cleanup(
                dsn,
                principal_ids=[principal_id],
                project_ids=(f"replay-unused-a-{suffix}", f"replay-unused-b-{suffix}"),
                actor_ids=(f"replay-actor-a-{suffix}", f"replay-actor-b-{suffix}"),
            )


def test_postgres_profile_loses_to_password_rotation_after_service_resolve() -> None:
    dsn = _database_url()
    asyncio.run(apply_migrations(dsn))
    suffix = uuid4().hex
    original_password = "Postgres profile rotation phrase 2026!"
    repository = PostgresAccessRepository.from_dsn(dsn)
    service = AccessService(
        repository,
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
        password_policy=PasswordPolicy(min_length=15, max_length=128),
    )
    principal_id: str | None = None
    normalized = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    profile_entered = Event()
    release_profile = Event()

    try:
        principal = service.register(
            RegistrationCommand(
                username=f"profile-rotation-{suffix}",
                password=original_password,
            ),
            request_id=f"profile-rotation-register-{suffix}",
        ).principal
        principal_id = principal.principal_id
        current_token, peer_token = (
            service.login(
                LoginCommand(username=principal.username, password=original_password),
                request_id=f"profile-rotation-login-{suffix}-{index}",
            ).access_token
            for index in range(2)
        )
        profile_request_id = f"profile-after-rotation-{suffix}"
        original_update = repository.update_account_profile

        def paused_update(**kwargs: object) -> object:
            if kwargs.get("request_id") == profile_request_id:
                profile_entered.set()
                if not release_profile.wait(timeout=10):
                    raise RuntimeError("timed out pausing PostgreSQL profile mutation")
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
                    idempotency_key=profile_request_id,
                    request_id=profile_request_id,
                )
                assert profile_entered.wait(timeout=10)
                changed = service.change_password(
                    current_token,
                    PasswordChangeCommand(
                        current_password=original_password,
                        new_password="Postgres profile rotation phrase 2027!",
                    ),
                    if_match='"v1"',
                    idempotency_key=f"profile-rotation-password-{suffix}",
                    request_id=f"profile-rotation-password-{suffix}",
                )
                assert changed.other_sessions_revoked == 1
                release_profile.set()
                with pytest.raises(ProblemException) as invalid:
                    pending.result(timeout=10)
            assert invalid.value.problem.code == "SESSION_INVALID"
        finally:
            release_profile.set()
            executor.shutdown(wait=True, cancel_futures=True)

        with psycopg.connect(normalized) as connection:
            account_state = connection.execute(
                """
                SELECT account_revision, credential_revision, display_name
                FROM access_control.accounts
                WHERE principal_id = %s::uuid
                """,
                (principal_id,),
            ).fetchone()
            peer_state = connection.execute(
                """
                SELECT revocation_reason
                FROM access_control.sessions
                WHERE token_hash = %s
                """,
                (service.token_hash(peer_token),),
            ).fetchone()
            profile_artifacts = connection.execute(
                """
                SELECT
                    (SELECT count(*) FROM access_control.audit_events
                     WHERE actor_id = %s AND request_id = %s),
                    (SELECT count(*) FROM access_control.command_idempotency
                     WHERE actor_id = %s AND idempotency_key = %s)
                """,
                (principal_id, profile_request_id, principal_id, profile_request_id),
            ).fetchone()
        assert account_state == (2, 2, principal.username)
        assert peer_state == ("PASSWORD_CHANGED",)
        assert profile_artifacts == (0, 0)
    finally:
        release_profile.set()
        if principal_id is not None:
            _cleanup(
                dsn,
                principal_ids=[principal_id],
                project_ids=(f"profile-unused-a-{suffix}", f"profile-unused-b-{suffix}"),
                actor_ids=(f"profile-actor-a-{suffix}", f"profile-actor-b-{suffix}"),
            )


@pytest.mark.parametrize("operation", ("profile", "password"))
def test_postgres_account_mutation_revalidates_expiry_under_transaction_lock(
    operation: str,
) -> None:
    dsn = _database_url()
    asyncio.run(apply_migrations(dsn))
    suffix = uuid4().hex
    original_password = "Postgres commit boundary phrase 2026!"
    repository = PostgresAccessRepository.from_dsn(
        dsn,
        session_idle_ttl_seconds=5,
        session_absolute_ttl_seconds=60,
        session_touch_interval_seconds=1,
    )
    service = AccessService(
        repository,
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
        password_policy=PasswordPolicy(min_length=15, max_length=128),
    )
    principal_id: str | None = None
    mutation_entered = Event()
    release_mutation = Event()
    normalized = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)

    try:
        principal = service.register(
            RegistrationCommand(
                username=f"commit-{operation}-{suffix}",
                password=original_password,
            ),
            request_id=f"commit-register-{suffix}",
        ).principal
        principal_id = principal.principal_id
        token = service.login(
            LoginCommand(username=principal.username, password=original_password),
            request_id=f"commit-login-{suffix}",
        ).access_token

        if operation == "profile":
            original_mutation = repository.update_account_profile

            def paused_mutation(**kwargs: object) -> object:
                mutation_entered.set()
                if not release_mutation.wait(timeout=5):
                    raise RuntimeError("timed out waiting to release PostgreSQL profile mutation")
                return original_mutation(**kwargs)  # type: ignore[arg-type]

            invoke = lambda: service.update_account_profile(  # noqa: E731
                token,
                AccountProfileUpdate(display_name="Must not commit"),
                if_match='"v1"',
                idempotency_key=f"commit-profile-{suffix}",
                request_id=f"commit-profile-{suffix}",
            )
            method = "update_account_profile"
            request_id = f"commit-profile-{suffix}"
        else:
            original_mutation = repository.change_password

            def paused_mutation(**kwargs: object) -> object:
                mutation_entered.set()
                if not release_mutation.wait(timeout=5):
                    raise RuntimeError("timed out waiting to release PostgreSQL password mutation")
                return original_mutation(**kwargs)  # type: ignore[arg-type]

            invoke = lambda: service.change_password(  # noqa: E731
                token,
                PasswordChangeCommand(
                    current_password=original_password,
                    new_password="Postgres replacement boundary phrase 2027!",
                ),
                if_match='"v1"',
                idempotency_key=f"commit-password-{suffix}",
                request_id=f"commit-password-{suffix}",
            )
            method = "change_password"
            request_id = f"commit-password-{suffix}"

        try:
            with (
                patch.object(repository, method, side_effect=paused_mutation),
                ThreadPoolExecutor(max_workers=1) as executor,
            ):
                pending = executor.submit(invoke)
                assert mutation_entered.wait(timeout=5)
                with psycopg.connect(normalized) as connection:
                    connection.execute(
                        """
                        UPDATE access_control.sessions
                        SET last_seen_at = clock_timestamp() - interval '5 seconds'
                        WHERE token_hash = %s
                        """,
                        (service.token_hash(token),),
                    )
                release_mutation.set()
                with pytest.raises(ProblemException) as invalid:
                    pending.result(timeout=5)
            assert invalid.value.problem.code == "SESSION_INVALID"
        finally:
            release_mutation.set()

        with psycopg.connect(normalized) as connection:
            account_state = connection.execute(
                """
                SELECT account_revision, credential_revision, display_name
                FROM access_control.accounts
                WHERE principal_id = %s::uuid
                """,
                (principal_id,),
            ).fetchone()
            session_state = connection.execute(
                """
                SELECT revocation_reason
                FROM access_control.sessions
                WHERE token_hash = %s
                """,
                (service.token_hash(token),),
            ).fetchone()
            expiry_audit = connection.execute(
                """
                SELECT count(*)
                FROM access_control.audit_events
                WHERE actor_id = %s AND action = 'auth.session.expired'
                  AND request_id = %s
                """,
                (principal_id, request_id),
            ).fetchone()
        assert account_state == (1, 1, principal.username)
        assert session_state == ("EXPIRED",)
        assert expiry_audit == (1,)
    finally:
        release_mutation.set()
        if principal_id is not None:
            _cleanup(
                dsn,
                principal_ids=[principal_id],
                project_ids=(f"commit-unused-a-{suffix}", f"commit-unused-b-{suffix}"),
                actor_ids=(f"commit-actor-a-{suffix}", f"commit-actor-b-{suffix}"),
            )


def test_postgres_session_expiry_touch_credential_revision_and_atomic_cap() -> None:
    dsn = _database_url()
    asyncio.run(apply_migrations(dsn))
    suffix = uuid4().hex
    password = "Postgres session lifecycle phrase 2026!"
    application_name = f"hc-session-cap-{suffix}"
    repository = PostgresAccessRepository.from_dsn(
        _dsn_with_application_name(dsn, application_name),
        session_idle_ttl_seconds=5,
        session_absolute_ttl_seconds=20,
        session_touch_interval_seconds=2,
    )
    service = AccessService(
        repository,
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
        password_policy=PasswordPolicy(min_length=15, max_length=128),
    )
    principal_ids: list[str] = []
    normalized = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)

    try:
        principal = service.register(
            RegistrationCommand(
                username=f"session-lifecycle-{suffix}",
                password=password,
            ),
            request_id=f"session-register-{suffix}",
        ).principal
        principal_ids.append(principal.principal_id)
        tokens = tuple(
            service.login(
                LoginCommand(username=principal.username, password=password),
                request_id=f"session-login-{suffix}-{index}",
            ).access_token
            for index in range(5)
        )
        token_hashes = tuple(service.token_hash(token) for token in tokens)
        with pytest.raises(ProblemException) as sixth_login:
            service.login(
                LoginCommand(username=principal.username, password=password),
                request_id=f"session-login-{suffix}-rejected",
            )

        assert sixth_login.value.problem.status == 429
        assert sixth_login.value.problem.code == "SESSION_LIMIT_REACHED"
        assert sixth_login.value.problem.retryable is True
        assert 1 <= sixth_login.value.problem.retry_after_seconds <= 86_400
        assert all(service.authenticate_access_token(token) is not None for token in tokens)
        with psycopg.connect(normalized) as connection:
            capped_rows = connection.execute(
                """
                SELECT token_hash, revoked_at IS NULL AS active,
                       COALESCE(revocation_reason, '')
                FROM access_control.sessions
                WHERE principal_id = %s::uuid
                ORDER BY issued_at, session_id
                """,
                (principal.principal_id,),
            ).fetchall()
            cap_audits = connection.execute(
                """
                SELECT action, outcome, safe_details::text
                FROM access_control.audit_events
                WHERE actor_id = %s AND request_id = %s
                """,
                (principal.principal_id, f"session-login-{suffix}-rejected"),
            ).fetchall()
        capped = {str(row[0]): (bool(row[1]), str(row[2])) for row in capped_rows}
        assert len(capped) == 5
        assert set(capped) == set(token_hashes)
        assert set(capped.values()) == {(True, "")}
        cap_audit_by_action = {str(row[0]): (str(row[1]), str(row[2])) for row in cap_audits}
        assert set(cap_audit_by_action) == {
            "auth.login.succeeded",
            "auth.session.admission.denied",
        }
        assert cap_audit_by_action["auth.login.succeeded"][0] == "SUCCEEDED"
        assert cap_audit_by_action["auth.session.admission.denied"][0] == "DENIED"
        assert json.loads(cap_audit_by_action["auth.session.admission.denied"][1]) == {
            "reason": "SESSION_LIMIT_REACHED"
        }
        safe_cap_audit = " ".join(str(value) for row in cap_audits for value in row)
        assert password not in safe_cap_audit
        assert all(token not in safe_cap_audit for token in (*tokens, *token_hashes))

        touch_hash = token_hashes[4]
        with psycopg.connect(normalized) as connection:
            before_touch = connection.execute(
                """
                UPDATE access_control.sessions
                SET issued_at = clock_timestamp() - interval '10 seconds',
                    last_seen_at = clock_timestamp() - interval '3 seconds'
                WHERE token_hash = %s
                RETURNING last_seen_at
                """,
                (touch_hash,),
            ).fetchone()
            connection.commit()
        assert before_touch is not None
        assert (
            repository.resolve_session(
                touch_hash,
                request_id=f"read-only-touch-{suffix}",
                allow_session_mutation=False,
            )
            is not None
        )
        with psycopg.connect(normalized) as connection:
            after_read_only_resolve = connection.execute(
                "SELECT last_seen_at FROM access_control.sessions WHERE token_hash = %s",
                (touch_hash,),
            ).fetchone()
        assert after_read_only_resolve == before_touch
        assert repository.resolve_session(touch_hash, request_id=f"touch-{suffix}") is not None
        with psycopg.connect(normalized) as connection:
            after_touch = connection.execute(
                "SELECT last_seen_at FROM access_control.sessions WHERE token_hash = %s",
                (touch_hash,),
            ).fetchone()
        assert after_touch is not None
        assert after_touch[0] > before_touch[0]

        idle_hash = token_hashes[1]
        absolute_hash = token_hashes[2]
        revision_hash = token_hashes[3]
        with psycopg.connect(normalized) as connection:
            connection.execute(
                """
                UPDATE access_control.sessions
                SET issued_at = clock_timestamp() - interval '10 seconds',
                    last_seen_at = clock_timestamp() - interval '5 seconds'
                WHERE token_hash = %s
                """,
                (idle_hash,),
            )
            connection.execute(
                """
                UPDATE access_control.sessions
                SET issued_at = clock_timestamp() - interval '20 seconds',
                    last_seen_at = clock_timestamp()
                WHERE token_hash = %s
                """,
                (absolute_hash,),
            )
            connection.execute(
                """
                UPDATE access_control.sessions
                SET credential_revision = credential_revision + 1,
                    last_seen_at = clock_timestamp()
                WHERE token_hash = %s
                """,
                (revision_hash,),
            )
            connection.commit()

        assert (
            repository.resolve_session(
                idle_hash,
                request_id=f"read-only-idle-expired-{suffix}",
                allow_session_mutation=False,
            )
            is None
        )
        with psycopg.connect(normalized) as connection:
            read_only_idle_state = connection.execute(
                """
                SELECT revoked_at, revocation_reason
                FROM access_control.sessions
                WHERE token_hash = %s
                """,
                (idle_hash,),
            ).fetchone()
        assert read_only_idle_state == (None, None)
        assert repository.resolve_session(idle_hash, request_id=f"idle-expired-{suffix}") is None
        assert (
            repository.resolve_session(
                absolute_hash,
                request_id=f"absolute-expired-{suffix}",
            )
            is None
        )
        assert repository.resolve_session(revision_hash, request_id=f"revision-{suffix}") is None
        replacement = service.login(
            LoginCommand(username=principal.username, password=password),
            request_id=f"invalid-cleanup-login-{suffix}",
        ).access_token
        with psycopg.connect(normalized) as connection:
            lifecycle_rows = connection.execute(
                """
                SELECT token_hash, revoked_at IS NULL AS active,
                       COALESCE(revocation_reason, '')
                FROM access_control.sessions
                WHERE token_hash = ANY(%s)
                """,
                ([idle_hash, absolute_hash, revision_hash],),
            ).fetchall()
            expiry_audits = connection.execute(
                """
                SELECT request_id, action, safe_details::text
                FROM access_control.audit_events
                WHERE actor_id = %s AND action = 'auth.session.expired'
                ORDER BY occurred_at
                """,
                (principal.principal_id,),
            ).fetchall()
            revision_audit = connection.execute(
                """
                SELECT request_id, safe_details::text
                FROM access_control.audit_events
                WHERE actor_id = %s AND action = 'auth.session.revoked'
                  AND safe_details->>'reason' = 'PASSWORD_CHANGED'
                  AND request_id = %s
                """,
                (principal.principal_id, f"revision-{suffix}"),
            ).fetchone()
        lifecycle = {str(row[0]): (bool(row[1]), str(row[2])) for row in lifecycle_rows}
        assert lifecycle[idle_hash] == (False, "EXPIRED")
        assert lifecycle[absolute_hash] == (False, "EXPIRED")
        assert lifecycle[revision_hash] == (False, "PASSWORD_CHANGED")
        assert len(expiry_audits) == 2
        assert {str(row[0]) for row in expiry_audits} == {
            f"idle-expired-{suffix}",
            f"absolute-expired-{suffix}",
        }
        assert revision_audit is not None
        safe_expiry_audit = " ".join(str(value) for row in expiry_audits for value in row)
        assert all(
            token not in safe_expiry_audit for token in (*tokens, replacement, *token_hashes)
        )

        concurrent = service.register(
            RegistrationCommand(
                username=f"session-concurrent-{suffix}",
                password=password,
            ),
            request_id=f"concurrent-register-{suffix}",
        ).principal
        principal_ids.append(concurrent.principal_id)

        def login_concurrently(index: int) -> tuple[str, object]:
            try:
                token = service.login(
                    LoginCommand(username=concurrent.username, password=password),
                    request_id=f"concurrent-login-{suffix}-{index}",
                ).access_token
                return "issued", token
            except ProblemException as exc:
                return "rejected", exc.problem

        with (
            psycopg.connect(normalized) as account_blocker,
            psycopg.connect(normalized, autocommit=True) as observer,
        ):
            account_blocker.execute(
                """
                SELECT principal_id
                FROM access_control.accounts
                WHERE principal_id = %s::uuid
                FOR UPDATE
                """,
                (concurrent.principal_id,),
            ).fetchone()
            executor = ThreadPoolExecutor(max_workers=12)
            pending_logins = tuple(
                executor.submit(login_concurrently, index) for index in range(12)
            )
            try:
                _wait_for_blocked_repository_connections(
                    observer,
                    application_name=application_name,
                    expected=12,
                    futures=pending_logins,
                    timeout_seconds=15,
                )
                account_blocker.commit()
                concurrent_outcomes = tuple(future.result(timeout=15) for future in pending_logins)
            finally:
                account_blocker.rollback()
                executor.shutdown(wait=True, cancel_futures=True)
        concurrent_tokens = tuple(
            value for status, value in concurrent_outcomes if status == "issued"
        )
        concurrent_rejections = tuple(
            value for status, value in concurrent_outcomes if status == "rejected"
        )
        assert len(concurrent_tokens) == len(set(concurrent_tokens)) == 5
        assert len(concurrent_rejections) == 7
        assert all(
            problem.status == 429
            and problem.code == "SESSION_LIMIT_REACHED"
            and problem.retryable is True
            and 1 <= problem.retry_after_seconds <= 86_400
            for problem in concurrent_rejections
        )
        with psycopg.connect(normalized) as connection:
            concurrent_counts = connection.execute(
                """
                SELECT count(*), count(*) FILTER (WHERE revoked_at IS NULL),
                       count(*) FILTER (WHERE revocation_reason = 'SESSION_LIMIT')
                FROM access_control.sessions
                WHERE principal_id = %s::uuid
                """,
                (concurrent.principal_id,),
            ).fetchone()
            concurrent_audit_counts = connection.execute(
                """
                SELECT count(*) FILTER (WHERE action = 'auth.login.succeeded'),
                       count(*) FILTER (WHERE action = 'auth.login.failed'),
                       count(*) FILTER (WHERE action = 'auth.session.admission.denied')
                FROM access_control.audit_events
                WHERE actor_id = %s
                """,
                (concurrent.principal_id,),
            ).fetchone()
            concurrent_denials = connection.execute(
                """
                SELECT request_id, safe_details::text
                FROM access_control.audit_events
                WHERE actor_id = %s AND action = 'auth.session.admission.denied'
                ORDER BY request_id
                """,
                (concurrent.principal_id,),
            ).fetchall()
        assert concurrent_counts == (5, 5, 0)
        assert concurrent_audit_counts == (12, 0, 7)
        assert len(concurrent_denials) == 7
        assert all(
            json.loads(str(row[1])) == {"reason": "SESSION_LIMIT_REACHED"}
            for row in concurrent_denials
        )
        safe_concurrent_audit = " ".join(str(value) for row in concurrent_denials for value in row)
        assert password not in safe_concurrent_audit
        assert all(str(token) not in safe_concurrent_audit for token in concurrent_tokens)

        single_session_repository = PostgresAccessRepository.from_dsn(
            dsn,
            session_idle_ttl_seconds=5,
            session_absolute_ttl_seconds=20,
            session_touch_interval_seconds=2,
            max_active_sessions=1,
        )
        single_session_service = AccessService(
            single_session_repository,
            password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
            password_policy=PasswordPolicy(min_length=15, max_length=128),
        )
        single_session_principal = single_session_service.register(
            RegistrationCommand(
                username=f"session-single-{suffix}",
                password=password,
            ),
            request_id=f"single-register-{suffix}",
        ).principal
        principal_ids.append(single_session_principal.principal_id)
        session_b = single_session_service.login(
            LoginCommand(username=single_session_principal.username, password=password),
            request_id=f"single-login-b-{suffix}",
        ).access_token
        with pytest.raises(ProblemException) as late_session_a:
            single_session_service.login(
                LoginCommand(username=single_session_principal.username, password=password),
                request_id=f"single-late-login-a-{suffix}",
            )

        assert late_session_a.value.problem.status == 429
        assert late_session_a.value.problem.code == "SESSION_LIMIT_REACHED"
        assert single_session_service.authenticate_access_token(session_b) is not None
        with psycopg.connect(normalized) as connection:
            single_session_state = connection.execute(
                """
                SELECT count(*), count(*) FILTER (WHERE revoked_at IS NULL),
                       count(*) FILTER (WHERE revocation_reason = 'SESSION_LIMIT')
                FROM access_control.sessions
                WHERE principal_id = %s::uuid
                """,
                (single_session_principal.principal_id,),
            ).fetchone()
        assert single_session_state == (1, 1, 0)

        disabled = service.register(
            RegistrationCommand(
                username=f"session-disabled-{suffix}",
                password=password,
            ),
            request_id=f"disabled-register-{suffix}",
        ).principal
        principal_ids.append(disabled.principal_id)
        disabled_token = service.login(
            LoginCommand(username=disabled.username, password=password),
            request_id=f"disabled-login-{suffix}",
        ).access_token
        disabled_hash = service.token_hash(disabled_token)
        with psycopg.connect(normalized) as connection:
            connection.execute(
                """
                UPDATE access_control.accounts
                SET status = 'DISABLED', disabled_at = clock_timestamp()
                WHERE principal_id = %s::uuid
                """,
                (disabled.principal_id,),
            )
            connection.commit()
        assert (
            repository.resolve_session(
                disabled_hash,
                request_id=f"disabled-resolve-{suffix}",
            )
            is None
        )
        with psycopg.connect(normalized) as connection:
            disabled_state = connection.execute(
                """
                SELECT revoked_at IS NOT NULL, revocation_reason
                FROM access_control.sessions
                WHERE token_hash = %s
                """,
                (disabled_hash,),
            ).fetchone()
            disabled_audit = connection.execute(
                """
                SELECT safe_details::text
                FROM access_control.audit_events
                WHERE actor_id = %s AND action = 'auth.session.revoked'
                  AND safe_details->>'reason' = 'ACCOUNT_DISABLED'
                """,
                (disabled.principal_id,),
            ).fetchone()
        assert disabled_state == (True, "ACCOUNT_DISABLED")
        assert disabled_audit is not None
        assert disabled_token not in str(disabled_audit)
    finally:
        _cleanup(
            dsn,
            principal_ids=principal_ids,
            project_ids=(f"session-unused-a-{suffix}", f"session-unused-b-{suffix}"),
            actor_ids=(f"session-actor-a-{suffix}", f"session-actor-b-{suffix}"),
        )


def test_postgres_login_audit_failure_rolls_back_session_issuance() -> None:
    dsn = _database_url()
    asyncio.run(apply_migrations(dsn))
    suffix = uuid4().hex
    password = "Postgres audit atomicity phrase 2026!"
    repository = PostgresAccessRepository.from_dsn(dsn)
    service = AccessService(
        repository,
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
        password_policy=PasswordPolicy(min_length=15, max_length=128),
    )
    principal_id: str | None = None
    request_id = f"audit-rollback-login-{suffix}"
    normalized = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    original_audit = repository._audit

    def fail_login_success_audit(cursor: Any, **kwargs: Any) -> None:
        if kwargs.get("action") == "auth.login.succeeded":
            raise RuntimeError("injected login audit failure")
        original_audit(cursor, **kwargs)

    try:
        principal = service.register(
            RegistrationCommand(
                username=f"session-audit-rollback-{suffix}",
                password=password,
            ),
            request_id=f"audit-rollback-register-{suffix}",
        ).principal
        principal_id = principal.principal_id

        with (
            patch.object(repository, "_audit", side_effect=fail_login_success_audit),
            pytest.raises(RuntimeError, match="injected login audit failure"),
        ):
            service.login(
                LoginCommand(username=principal.username, password=password),
                request_id=request_id,
            )

        with psycopg.connect(normalized) as connection:
            session_count = connection.execute(
                "SELECT count(*) FROM access_control.sessions WHERE principal_id = %s::uuid",
                (principal_id,),
            ).fetchone()
            request_audit_count = connection.execute(
                "SELECT count(*) FROM access_control.audit_events WHERE request_id = %s",
                (request_id,),
            ).fetchone()
        assert session_count == (0,)
        assert request_audit_count == (0,)
    finally:
        if principal_id is not None:
            _cleanup(
                dsn,
                principal_ids=[principal_id],
                project_ids=(f"audit-unused-a-{suffix}", f"audit-unused-b-{suffix}"),
                actor_ids=(f"audit-actor-a-{suffix}", f"audit-actor-b-{suffix}"),
            )


def test_postgres_password_rotation_classifies_invalid_peers_before_counting() -> None:
    dsn = _database_url()
    asyncio.run(apply_migrations(dsn))
    suffix = uuid4().hex
    original_password = "Postgres rotation classification phrase 2026!"
    repository = PostgresAccessRepository.from_dsn(
        dsn,
        session_idle_ttl_seconds=5,
        session_absolute_ttl_seconds=60,
        session_touch_interval_seconds=1,
    )
    service = AccessService(
        repository,
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
        password_policy=PasswordPolicy(min_length=15, max_length=128),
    )
    principal_id: str | None = None
    normalized = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)

    try:
        principal = service.register(
            RegistrationCommand(
                username=f"rotation-classification-{suffix}",
                password=original_password,
            ),
            request_id=f"rotation-register-{suffix}",
        ).principal
        principal_id = principal.principal_id
        tokens = tuple(
            service.login(
                LoginCommand(username=principal.username, password=original_password),
                request_id=f"rotation-login-{suffix}-{index}",
            ).access_token
            for index in range(4)
        )
        token_hashes = tuple(service.token_hash(token) for token in tokens)
        current_hash, valid_hash, expired_hash, stale_hash = token_hashes
        with psycopg.connect(normalized) as connection:
            connection.execute(
                """
                UPDATE access_control.sessions
                SET last_seen_at = clock_timestamp() - interval '5 seconds'
                WHERE token_hash = %s
                """,
                (expired_hash,),
            )
            connection.execute(
                """
                UPDATE access_control.sessions
                SET credential_revision = credential_revision + 1
                WHERE token_hash = %s
                """,
                (stale_hash,),
            )

        rejected_request_id = f"rotation-rejected-{suffix}"
        with pytest.raises(ProblemException) as rejected:
            service.change_password(
                tokens[0],
                PasswordChangeCommand(
                    current_password=original_password,
                    new_password="Postgres rejected rotation phrase 2027!",
                ),
                if_match='"v2"',
                idempotency_key=rejected_request_id,
                request_id=rejected_request_id,
            )
        assert rejected.value.problem.code == "ETAG_MISMATCH"
        with psycopg.connect(normalized) as connection:
            rejected_peer_states = connection.execute(
                """
                SELECT token_hash, revoked_at, revocation_reason
                FROM access_control.sessions
                WHERE token_hash = ANY(%s)
                """,
                ([expired_hash, stale_hash],),
            ).fetchall()
            rejected_audits = connection.execute(
                """
                SELECT count(*)
                FROM access_control.audit_events
                WHERE actor_id = %s AND request_id = %s
                """,
                (principal_id, rejected_request_id),
            ).fetchone()
        assert all(row[1:] == (None, None) for row in rejected_peer_states)
        assert rejected_audits == (0,)

        changed = service.change_password(
            tokens[0],
            PasswordChangeCommand(
                current_password=original_password,
                new_password="Postgres rotation classification phrase 2027!",
            ),
            if_match='"v1"',
            idempotency_key=f"rotation-classify-{suffix}",
            request_id=f"rotation-classify-{suffix}",
        )
        assert changed.other_sessions_revoked == 1

        with psycopg.connect(normalized) as connection:
            rows = connection.execute(
                """
                SELECT token_hash, revoked_at IS NULL, credential_revision,
                       revocation_reason
                FROM access_control.sessions
                WHERE token_hash = ANY(%s)
                """,
                (list(token_hashes),),
            ).fetchall()
            session_audits = connection.execute(
                """
                SELECT action, safe_details->>'reason'
                FROM access_control.audit_events
                WHERE actor_id = %s AND request_id = %s
                  AND resource_type = 'session'
                ORDER BY action, resource_id
                """,
                (principal_id, f"rotation-classify-{suffix}"),
            ).fetchall()
        states = {str(row[0]): (bool(row[1]), int(row[2]), row[3]) for row in rows}
        assert states[current_hash] == (True, 2, None)
        assert states[valid_hash] == (False, 1, "PASSWORD_CHANGED")
        assert states[expired_hash] == (False, 1, "EXPIRED")
        assert states[stale_hash] == (False, 2, "PASSWORD_CHANGED")
        assert sorted((str(row[0]), str(row[1])) for row in session_audits) == [
            ("auth.session.expired", "EXPIRED"),
            ("auth.session.revoked", "PASSWORD_CHANGED"),
            ("auth.session.revoked", "PASSWORD_CHANGED"),
        ]
    finally:
        if principal_id is not None:
            _cleanup(
                dsn,
                principal_ids=[principal_id],
                project_ids=(f"rotation-unused-a-{suffix}", f"rotation-unused-b-{suffix}"),
                actor_ids=(f"rotation-actor-a-{suffix}", f"rotation-actor-b-{suffix}"),
            )


def test_postgres_lifecycle_timestamp_is_captured_after_lock_waits() -> None:
    dsn = _database_url()
    asyncio.run(apply_migrations(dsn))
    suffix = uuid4().hex
    password = "Postgres delayed lifecycle clock phrase 2026!"
    application_name = f"hc-session-clock-{suffix}"
    tagged_dsn = f"{dsn}{'&' if '?' in dsn else '?'}application_name={application_name}"
    repository = PostgresAccessRepository.from_dsn(
        tagged_dsn,
        session_idle_ttl_seconds=2,
        session_absolute_ttl_seconds=60,
        session_touch_interval_seconds=1,
    )
    service = AccessService(
        repository,
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
        password_policy=PasswordPolicy(min_length=15, max_length=128),
    )
    normalized = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    principal_id: str | None = None

    def wait_for_blocked_query(
        observer: Any,
        future: Future[Any],
    ) -> datetime:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            blocked = observer.execute(
                """
                SELECT xact_start
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND application_name = %s
                  AND wait_event_type = 'Lock'
                ORDER BY query_start DESC
                LIMIT 1
                """,
                (application_name,),
            ).fetchone()
            if blocked is not None:
                return blocked[0]
            if future.done():
                future.result()
                pytest.fail("lifecycle operation completed before reaching the expected row lock")
            time.sleep(0.01)
        pytest.fail("lifecycle operation did not reach the expected blocked query")

    try:
        principal = service.register(
            RegistrationCommand(
                username=f"delayed-lifecycle-{suffix}",
                password=password,
            ),
            request_id=f"delayed-register-{suffix}",
        ).principal
        principal_id = principal.principal_id

        with (
            psycopg.connect(normalized) as account_blocker,
            psycopg.connect(normalized, autocommit=True) as observer,
        ):
            account_blocker.execute(
                """
                SELECT principal_id
                FROM access_control.accounts
                WHERE principal_id = %s::uuid
                FOR UPDATE
                """,
                (principal_id,),
            ).fetchone()
            executor = ThreadPoolExecutor(max_workers=1)
            pending_login = executor.submit(
                service.login,
                LoginCommand(username=principal.username, password=password),
                request_id=f"delayed-login-{suffix}",
            )
            try:
                wait_for_blocked_query(observer, pending_login)
                release_timestamp = account_blocker.execute("SELECT clock_timestamp()").fetchone()[
                    0
                ]
                account_blocker.commit()
                token = pending_login.result(timeout=5).access_token
            finally:
                account_blocker.rollback()
                executor.shutdown(wait=True, cancel_futures=True)

        token_hash = service.token_hash(token)
        with psycopg.connect(normalized) as connection:
            issued_at, last_seen_at, created_audit_at = connection.execute(
                """
                SELECT session.issued_at, session.last_seen_at, audit.occurred_at
                FROM access_control.sessions AS session
                JOIN access_control.audit_events AS audit
                  ON audit.resource_id = session.session_id::text
                 AND audit.action = 'auth.session.created'
                 AND audit.request_id = %s
                WHERE session.token_hash = %s
                """,
                (f"delayed-login-{suffix}", token_hash),
            ).fetchone()
        assert issued_at >= release_timestamp
        assert issued_at == last_seen_at == created_audit_at

        with psycopg.connect(normalized) as connection:
            boundary_last_seen = connection.execute(
                """
                UPDATE access_control.sessions
                SET last_seen_at = clock_timestamp()
                WHERE token_hash = %s
                RETURNING last_seen_at
                """,
                (token_hash,),
            ).fetchone()[0]
        expiry_deadline = boundary_last_seen + timedelta(seconds=2)
        release_deadline = expiry_deadline + timedelta(milliseconds=100)
        with (
            psycopg.connect(normalized) as session_blocker,
            psycopg.connect(normalized, autocommit=True) as observer,
        ):
            session_blocker.execute(
                """
                SELECT session_id
                FROM access_control.sessions
                WHERE token_hash = %s
                FOR UPDATE
                """,
                (token_hash,),
            ).fetchone()
            executor = ThreadPoolExecutor(max_workers=1)
            pending_resolve = executor.submit(
                repository.resolve_session,
                token_hash,
                request_id=f"delayed-resolve-{suffix}",
            )
            try:
                resolve_xact_start = wait_for_blocked_query(observer, pending_resolve)
                assert resolve_xact_start < expiry_deadline
                seconds_until_release = session_blocker.execute(
                    """
                    SELECT GREATEST(
                        EXTRACT(EPOCH FROM (%s::timestamptz - clock_timestamp())),
                        0
                    )
                    """,
                    (release_deadline,),
                ).fetchone()[0]
                session_blocker.execute(
                    "SELECT pg_sleep(%s::double precision)",
                    (seconds_until_release,),
                )
                released_at = session_blocker.execute("SELECT clock_timestamp()").fetchone()[0]
                assert released_at >= release_deadline
                session_blocker.commit()
                assert pending_resolve.result(timeout=5) is None
            finally:
                session_blocker.rollback()
                executor.shutdown(wait=True, cancel_futures=True)

        with psycopg.connect(normalized) as connection:
            revoked_at, reason, expiry_audit_at = connection.execute(
                """
                SELECT session.revoked_at, session.revocation_reason, audit.occurred_at
                FROM access_control.sessions AS session
                JOIN access_control.audit_events AS audit
                  ON audit.resource_id = session.session_id::text
                 AND audit.action = 'auth.session.expired'
                 AND audit.request_id = %s
                WHERE session.token_hash = %s
                """,
                (f"delayed-resolve-{suffix}", token_hash),
            ).fetchone()
        assert reason == "EXPIRED"
        assert revoked_at >= boundary_last_seen + timedelta(seconds=2)
        assert expiry_audit_at == revoked_at
    finally:
        if principal_id is not None:
            _cleanup(
                dsn,
                principal_ids=[principal_id],
                project_ids=(f"clock-unused-a-{suffix}", f"clock-unused-b-{suffix}"),
                actor_ids=(f"clock-actor-a-{suffix}", f"clock-actor-b-{suffix}"),
            )


def test_postgres_login_issuance_cas_loses_to_completed_password_rotation() -> None:
    dsn = _database_url()
    asyncio.run(apply_migrations(dsn))
    suffix = uuid4().hex
    username = f"login-password-race-{suffix}"
    original_password = "Postgres original race phrase 2026!"
    new_password = "Postgres rotated race phrase 2027!"
    repository = PostgresAccessRepository.from_dsn(dsn)
    service = AccessService(
        repository,
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
        password_policy=PasswordPolicy(min_length=15, max_length=128),
    )
    principal_id: str | None = None
    issue_started = Event()
    release_issue = Event()
    original_issue = repository.create_session

    def paused_issue(
        *,
        principal_id: str,
        token_hash: str,
        expected_password_hash: str,
        expected_credential_revision: int,
        request_id: str,
    ) -> SessionIssueResult:
        if request_id == f"race-login-{suffix}":
            issue_started.set()
            if not release_issue.wait(timeout=5):
                raise RuntimeError("timed out waiting to release PostgreSQL session issuance")
        return original_issue(
            principal_id=principal_id,
            token_hash=token_hash,
            expected_password_hash=expected_password_hash,
            expected_credential_revision=expected_credential_revision,
            request_id=request_id,
        )

    try:
        principal = service.register(
            RegistrationCommand(username=username, password=original_password),
            request_id=f"race-register-{suffix}",
        ).principal
        principal_id = principal.principal_id
        current_token = service.login(
            LoginCommand(username=username, password=original_password),
            request_id=f"race-initial-login-{suffix}",
        ).access_token

        with (
            patch.object(repository, "create_session", side_effect=paused_issue),
            ThreadPoolExecutor(max_workers=1) as executor,
        ):
            pending_login = executor.submit(
                service.login,
                LoginCommand(username=username, password=original_password),
                request_id=f"race-login-{suffix}",
            )
            assert issue_started.wait(timeout=5)
            changed = service.change_password(
                current_token,
                PasswordChangeCommand(
                    current_password=original_password,
                    new_password=new_password,
                ),
                if_match='"v1"',
                idempotency_key=f"race-password-{suffix}",
                request_id=f"race-password-{suffix}",
            )
            assert changed.other_sessions_revoked == 0
            release_issue.set()
            with pytest.raises(ProblemException) as raced:
                pending_login.result(timeout=5)

        assert raced.value.problem.code == "INVALID_CREDENTIALS"
        assert service.authenticate_access_token(current_token) is not None
        normalized = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        with psycopg.connect(normalized) as connection:
            session_state = connection.execute(
                """
                SELECT count(*), count(*) FILTER (WHERE revoked_at IS NULL),
                       min(credential_revision), max(credential_revision)
                FROM access_control.sessions
                WHERE principal_id = %s::uuid
                """,
                (principal_id,),
            ).fetchone()
            failed_logins = connection.execute(
                """
                SELECT count(*)
                FROM access_control.audit_events
                WHERE actor_id = %s AND action = 'auth.login.failed'
                """,
                (principal_id,),
            ).fetchone()
        assert session_state == (1, 1, 2, 2)
        assert failed_logins == (1,)
    finally:
        release_issue.set()
        if principal_id is not None:
            _cleanup(
                dsn,
                principal_ids=[principal_id],
                project_ids=(f"race-unused-a-{suffix}", f"race-unused-b-{suffix}"),
                actor_ids=(f"race-actor-a-{suffix}", f"race-actor-b-{suffix}"),
            )


def test_postgres_account_notifications_are_recipient_scoped_and_read_once() -> None:
    dsn = _database_url()
    asyncio.run(apply_migrations(dsn))
    suffix = uuid4().hex
    organization_id = f"notification-organization-{suffix}"
    project_id = f"notification-project-{suffix}"
    reader_role = f"notification_reader_{suffix}"
    service = AccessService(PostgresAccessRepository.from_dsn(dsn))
    principal_ids: list[str] = []
    try:
        normalized = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        with psycopg.connect(normalized) as connection:
            connection.execute(
                "INSERT INTO registry.organization_projects "
                "(organization_id, project_id) VALUES (%s, %s)",
                (organization_id, project_id),
            )
            connection.commit()
        alice = service.register(
            RegistrationCommand(
                username=f"notification-alice-{suffix}",
                password="notification integration password",
            ),
            request_id=f"notification-register-alice-{suffix}",
        ).principal
        bob = service.register(
            RegistrationCommand(
                username=f"notification-bob-{suffix}",
                password="notification integration password",
            ),
            request_id=f"notification-register-bob-{suffix}",
        ).principal
        principal_ids.extend((alice.principal_id, bob.principal_id))
        alice_token = service.login(
            LoginCommand(username=alice.username, password="notification integration password"),
            request_id=f"notification-login-alice-{suffix}",
        ).access_token
        bob_token = service.login(
            LoginCommand(username=bob.username, password="notification integration password"),
            request_id=f"notification-login-bob-{suffix}",
        ).access_token
        alice_auth = service.authenticate_access_token(alice_token)
        bob_auth = service.authenticate_access_token(bob_token)
        assert alice_auth is not None and bob_auth is not None
        request = service.create_membership_request(
            auth=alice_auth,
            organization_id=organization_id,
            project_id=project_id,
            command=MembershipRequestCreate(reason="private request reason"),
            idempotency_key=f"notification-request-{suffix}",
            request_id=f"notification-request-{suffix}",
        )
        admin = _admin(f"notification-admin-{suffix}", organization_id, project_id)
        for index in range(2):
            service.decide_membership_request(
                auth=admin,
                organization_id=organization_id,
                project_id=project_id,
                access_request_id=request.request_id,
                target_status=AccessRequestStatus.APPROVED,
                command=AccessDecisionCommand(reason="private decision reason"),
                idempotency_key=f"notification-approve-{suffix}-{index}",
                request_id=f"notification-approve-{suffix}-{index}",
            )

        assert service.notification_unread_count(auth=alice_auth).unread_count == 1
        page = service.notifications(auth=alice_auth, state=None, cursor=None, limit=20)
        assert len(page.items) == 1
        notification = page.items[0]
        assert notification.kind.value == "MEMBERSHIP_APPROVED"
        assert notification.resource_type.value == "ACCESS_REQUEST"
        assert notification.resource_id == request.request_id
        assert service.notifications(auth=bob_auth, state=None, cursor=None, limit=20).items == ()
        with pytest.raises(ProblemException) as foreign:
            service.mark_notification_read(
                auth=bob_auth,
                notification_id=notification.notification_id,
                request_id=f"notification-read-foreign-{suffix}",
            )
        assert foreign.value.problem.status == 404
        service.mark_notification_read(
            auth=alice_auth,
            notification_id=notification.notification_id,
            request_id=f"notification-read-{suffix}",
        )
        service.mark_notification_read(
            auth=alice_auth,
            notification_id=notification.notification_id,
            request_id=f"notification-read-repeat-{suffix}",
        )
        assert service.notification_unread_count(auth=alice_auth).unread_count == 0

        normalized = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        with psycopg.connect(normalized) as connection:
            connection.execute(f"CREATE ROLE {reader_role} NOLOGIN")
            connection.execute(f"GRANT USAGE ON SCHEMA access_control TO {reader_role}")
            connection.execute(
                f"GRANT SELECT ON access_control.account_notifications TO {reader_role}"
            )
            connection.commit()
            try:
                with connection.transaction():
                    connection.execute(f"SET LOCAL ROLE {reader_role}")
                    connection.execute(
                        "SELECT set_config('app.subject_id', %s, true)", (alice.principal_id,)
                    )
                    visible_to_alice = connection.execute(
                        "SELECT count(*) FROM access_control.account_notifications"
                    ).fetchone()
                    connection.execute(
                        "SELECT set_config('app.subject_id', %s, true)", (bob.principal_id,)
                    )
                    visible_to_bob = connection.execute(
                        "SELECT count(*) FROM access_control.account_notifications"
                    ).fetchone()
                read_audits = connection.execute(
                    """
                    SELECT count(*)
                      FROM access_control.audit_events
                     WHERE actor_id = %s AND action = 'account.notification.read'
                    """,
                    (alice.principal_id,),
                ).fetchone()
            finally:
                connection.execute(f"REVOKE ALL ON SCHEMA access_control FROM {reader_role}")
                connection.execute(
                    f"REVOKE ALL ON access_control.account_notifications FROM {reader_role}"
                )
                connection.execute(f"DROP ROLE {reader_role}")
        assert visible_to_alice == (1,)
        assert visible_to_bob == (0,)
        assert read_audits == (1,)
    finally:
        _cleanup(
            dsn,
            principal_ids=principal_ids,
            project_ids=(project_id, f"notification-unused-{suffix}"),
            actor_ids=(f"notification-admin-{suffix}",),
        )


def test_postgres_password_recovery_is_single_use_and_revokes_all_sessions() -> None:
    dsn = _database_url()
    asyncio.run(apply_migrations(dsn))
    suffix = uuid4().hex
    repository = PostgresAccessRepository.from_dsn(dsn, max_active_sessions=8)
    hasher = PasswordHasher(ScryptParameters(n=2**10))
    policy = PasswordPolicy(min_length=6, max_length=128)
    access = AccessService(repository, password_hasher=hasher, password_policy=policy)
    delivery = _RecordingRecoveryDelivery()
    recovery = AccountRecoveryService(
        repository,
        delivery=delivery,
        password_hasher=hasher,
        password_policy=policy,
    )
    principal_id: str | None = None
    old_password = "postgres-recovery-old-password"
    new_password = "postgres-recovery-new-password"
    username = f"postgres-recovery-{suffix}"
    try:
        principal = access.register(
            RegistrationCommand(username=username, password=old_password),
            request_id=f"recovery-register-{suffix}",
        ).principal
        principal_id = principal.principal_id
        sessions = tuple(
            access.login(
                LoginCommand(username=username, password=old_password),
                request_id=f"recovery-login-{suffix}-{index}",
            ).access_token
            for index in range(2)
        )
        auth = access.authenticate_access_token(sessions[0])
        assert auth is not None

        recovery.request_recovery_email_verification(
            RecoveryEmailVerificationRequest(
                recovery_email=f"postgres-recovery-{suffix}@example.com"
            ),
            auth=auth,
            request_id=f"recovery-email-request-{suffix}",
        )
        verification_token = delivery.email_verifications[-1][1]
        recovery.confirm_recovery_email(
            RecoveryEmailConfirmation(token=verification_token),
            auth=auth,
            request_id=f"recovery-email-confirm-{suffix}",
        )
        recovery.request_password_recovery(
            PasswordRecoveryRequest(identifier=username),
            source_network="198.51.100.0/24",
            challenge_response=None,
            request_id=f"recovery-request-{suffix}",
        )
        recovery_token = delivery.password_recoveries[-1][1]
        completed = recovery.confirm_password_recovery(
            PasswordRecoveryConfirmation(
                token=recovery_token,
                new_password=new_password,
            ),
            request_id=f"recovery-confirm-{suffix}",
        )
        assert completed.sessions_revoked == 2
        assert all(access.authenticate_access_token(token) is None for token in sessions)
        with pytest.raises(ProblemException) as old_login:
            access.login(
                LoginCommand(username=username, password=old_password),
                request_id=f"recovery-old-login-{suffix}",
            )
        assert old_login.value.problem.code == "INVALID_CREDENTIALS"
        assert access.login(
            LoginCommand(username=username, password=new_password),
            request_id=f"recovery-new-login-{suffix}",
        ).access_token.startswith("hcs_")
        with pytest.raises(ProblemException) as replay:
            recovery.confirm_password_recovery(
                PasswordRecoveryConfirmation(
                    token=recovery_token,
                    new_password="postgres-recovery-replay-password",
                ),
                request_id=f"recovery-replay-{suffix}",
            )
        assert replay.value.problem.code == "ACCOUNT_RECOVERY_TOKEN_INVALID"

        normalized = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        with psycopg.connect(normalized) as connection:
            persisted = connection.execute(
                """
                SELECT token_hash, credential_revision
                FROM access_control.account_security_challenges
                WHERE principal_id = %s::uuid
                ORDER BY created_at
                """,
                (principal_id,),
            ).fetchall()
        assert len(persisted) == 2
        assert all(len(row[0]) == 64 and int(row[1]) >= 1 for row in persisted)
        assert recovery_token not in repr(persisted)
        assert verification_token not in repr(persisted)
    finally:
        if principal_id is not None:
            _cleanup(
                dsn,
                principal_ids=[principal_id],
                project_ids=(f"recovery-unused-a-{suffix}", f"recovery-unused-b-{suffix}"),
                actor_ids=(f"recovery-actor-a-{suffix}", f"recovery-actor-b-{suffix}"),
            )


def _assert_access_repository_boundary(dsn: str) -> None:
    normalized = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    with psycopg.connect(normalized) as connection:
        rows = connection.execute(
            """
            SELECT relation.relname, relation.relrowsecurity, relation.relforcerowsecurity
            FROM pg_class relation
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = 'access_control' AND relation.relkind = 'r'
            ORDER BY relation.relname
            """
        ).fetchall()
    assert rows
    notification_row = next(row for row in rows if row[0] == "account_notifications")
    assert notification_row == ("account_notifications", True, True)
    assert all(
        not row_security and not force_security
        for name, row_security, force_security in rows
        if name != "account_notifications"
    )


def _cleanup(
    dsn: str,
    *,
    principal_ids: list[str],
    project_ids: tuple[str, str],
    actor_ids: tuple[str, str],
) -> None:
    normalized = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    with psycopg.connect(normalized) as connection:
        try:
            connection.execute(
                "ALTER TABLE access_control.audit_events "
                "DISABLE TRIGGER access_audit_no_update_delete"
            )
            connection.execute(
                "DELETE FROM access_control.audit_events "
                "WHERE project_id = ANY(%s) OR actor_id = ANY(%s) OR resource_id = ANY(%s)",
                (list(project_ids), [*actor_ids, *principal_ids], principal_ids),
            )
            connection.execute(
                "DELETE FROM core.audit_integrity_entries WHERE project_id = ANY(%s)",
                (list(project_ids),),
            )
            connection.execute(
                "DELETE FROM core.audit_events WHERE project_id = ANY(%s)",
                (list(project_ids),),
            )
            connection.execute(
                "DELETE FROM core.audit_integrity_heads WHERE project_id = ANY(%s)",
                (list(project_ids),),
            )
            connection.execute(
                "DELETE FROM access_control.command_idempotency WHERE actor_id = ANY(%s)",
                ([*actor_ids, *principal_ids],),
            )
            if principal_ids:
                for principal_id in principal_ids:
                    connection.execute(
                        "SELECT set_config('app.subject_id', %s, true)", (principal_id,)
                    )
                    connection.execute(
                        "DELETE FROM access_control.account_notifications "
                        "WHERE recipient_id = %s::uuid",
                        (principal_id,),
                    )
                connection.execute(
                    "DELETE FROM access_control.capability_grants "
                    "WHERE principal_id = ANY(%s::uuid[])",
                    (principal_ids,),
                )
                connection.execute(
                    "DELETE FROM access_control.capability_requests "
                    "WHERE requester_id = ANY(%s::uuid[])",
                    (principal_ids,),
                )
                connection.execute(
                    "DELETE FROM access_control.memberships WHERE principal_id = ANY(%s::uuid[])",
                    (principal_ids,),
                )
                connection.execute(
                    "DELETE FROM access_control.organization_memberships "
                    "WHERE principal_id = ANY(%s::uuid[])",
                    (principal_ids,),
                )
                connection.execute(
                    "DELETE FROM access_control.organization_membership_requests "
                    "WHERE requester_id = ANY(%s::uuid[])",
                    (principal_ids,),
                )
                connection.execute(
                    "DELETE FROM access_control.membership_requests "
                    "WHERE requester_id = ANY(%s::uuid[])",
                    (principal_ids,),
                )
                connection.execute(
                    "DELETE FROM access_control.sessions WHERE principal_id = ANY(%s::uuid[])",
                    (principal_ids,),
                )
                connection.execute(
                    "DELETE FROM access_control.account_security_challenges "
                    "WHERE principal_id = ANY(%s::uuid[])",
                    (principal_ids,),
                )
                connection.execute(
                    "DELETE FROM access_control.accounts WHERE principal_id = ANY(%s::uuid[])",
                    (principal_ids,),
                )
            connection.execute(
                "DELETE FROM registry.organization_projects WHERE project_id = ANY(%s)",
                (list(project_ids),),
            )
            connection.execute(
                "ALTER TABLE access_control.audit_events "
                "ENABLE TRIGGER access_audit_no_update_delete"
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
