from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")

from hc_data_platform.core.errors import ProblemException  # noqa: E402
from hc_data_platform.core.migrations import apply_migrations  # noqa: E402
from hc_data_platform.security.access_models import (  # noqa: E402
    AccessDecisionCommand,
    AccessRequestStatus,
    CapabilityRequestCreate,
    LoginCommand,
    MembershipRequestCreate,
    RegistrationCommand,
)
from hc_data_platform.security.access_postgres import PostgresAccessRepository  # noqa: E402
from hc_data_platform.security.access_service import AccessService  # noqa: E402
from hc_data_platform.security.auth import AuthContext, Role  # noqa: E402

pytestmark = pytest.mark.integration


def _database_url() -> str:
    value = os.getenv("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    return value


def _admin(subject_id: str, project_id: str) -> AuthContext:
    return AuthContext(
        subject_id=subject_id,
        project_ids=frozenset({project_id}),
        region_codes=frozenset(),
        roles=frozenset({Role.ADMIN.value}),
        scope_pairs=frozenset({(project_id, None)}),
    )


def test_postgres_access_scope_revocation_and_concurrent_approval() -> None:
    dsn = _database_url()
    asyncio.run(apply_migrations(dsn))
    _assert_access_repository_boundary(dsn)
    suffix = uuid4().hex
    project_a = f"access-a-{suffix}"
    project_b = f"access-b-{suffix}"
    admin_a = _admin(f"admin-a-{suffix}", project_a)
    admin_b = _admin(f"admin-b-{suffix}", project_b)
    service = AccessService(PostgresAccessRepository.from_dsn(dsn))
    principal_ids: list[str] = []

    try:
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
            project_id=project_a,
            command=MembershipRequestCreate(reason="join project a"),
            idempotency_key=f"alice-membership-{suffix}",
            request_id=f"alice-membership-{suffix}",
        )
        bob_membership = service.create_membership_request(
            auth=bob_auth,
            project_id=project_b,
            command=MembershipRequestCreate(reason="join project b"),
            idempotency_key=f"bob-membership-{suffix}",
            request_id=f"bob-membership-{suffix}",
        )

        with pytest.raises(ProblemException) as cross_project:
            service.get_membership_request(
                auth=alice_auth,
                project_id=project_b,
                access_request_id=bob_membership.request_id,
            )
        assert cross_project.value.problem.status == 404
        assert service.list_membership_requests(auth=admin_a, project_id=project_b).items == ()

        def approve(index: int) -> str:
            return service.decide_membership_request(
                auth=admin_a,
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
            project_id=project_a,
            command=CapabilityRequestCreate(
                capability_keys=("datasets.read",),
                reason="read project datasets",
            ),
            idempotency_key=f"capability-{suffix}",
            request_id=f"capability-{suffix}",
        )
        service.decide_capability_request(
            auth=admin_a,
            project_id=project_a,
            access_request_id=capability.request_id,
            target_status=AccessRequestStatus.APPROVED,
            command=AccessDecisionCommand(reason="approved"),
            idempotency_key=f"approve-capability-{suffix}",
            request_id=f"approve-capability-{suffix}",
        )
        before_revoke = service.bootstrap(alice_token)
        assert before_revoke.available_scopes[0].capabilities == ("datasets.read",)

        service.decide_capability_request(
            auth=admin_a,
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
            for event in service.list_audit_events(auth=admin_a, project_id=project_a).items
        }
        assert {
            "access.membership.requested",
            "access.membership.approved",
            "access.capability.approved",
            "access.capability.revoked",
            "access.membership.revoked",
        }.issubset(actions)
        assert service.list_audit_events(auth=admin_b, project_id=project_b).items
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
        assert actions.issubset({str(row[0]) for row in core_rows})
        safe_core_payload = " ".join(str(row[1]) for row in core_rows).lower()
        assert "integration-password" not in safe_core_payload
        assert alice_token.lower() not in safe_core_payload
    finally:
        _cleanup(
            dsn,
            principal_ids=principal_ids,
            project_ids=(project_a, project_b),
            actor_ids=(admin_a.subject_id, admin_b.subject_id),
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
    assert all(not row_security and not force_security for _, row_security, force_security in rows)


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
                "DELETE FROM core.audit_events WHERE project_id = ANY(%s)",
                (list(project_ids),),
            )
            connection.execute(
                "DELETE FROM access_control.command_idempotency WHERE actor_id = ANY(%s)",
                ([*actor_ids, *principal_ids],),
            )
            if principal_ids:
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
                    "DELETE FROM access_control.membership_requests "
                    "WHERE requester_id = ANY(%s::uuid[])",
                    (principal_ids,),
                )
                connection.execute(
                    "DELETE FROM access_control.sessions WHERE principal_id = ANY(%s::uuid[])",
                    (principal_ids,),
                )
                connection.execute(
                    "DELETE FROM access_control.accounts WHERE principal_id = ANY(%s::uuid[])",
                    (principal_ids,),
                )
            connection.execute(
                "ALTER TABLE access_control.audit_events "
                "ENABLE TRIGGER access_audit_no_update_delete"
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
