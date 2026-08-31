from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Iterator
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.context import (
    RequestContext,
    bind_platform_task_lease,
    bind_request_context,
    bind_writer_permit,
    reset_platform_task_lease,
    reset_request_context,
    reset_writer_permit,
)
from hc_data_platform.core.dbapi import normalize_postgres_dsn, psycopg_connection_factory
from hc_data_platform.core.migrations import apply_migrations
from hc_data_platform.platform_control.maintenance_contract import (
    MaintenanceCommandV1,
    MaintenanceContractError,
    MaintenanceState,
)
from hc_data_platform.platform_ops.maintenance import (
    MaintenanceOperation,
    PostgresMaintenanceRepository,
)
from hc_data_platform.platform_ops.task_leases import PostgresPlatformTaskLeaseRepository
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.capabilities import (
    CAPABILITY_PLATFORM_ADMIN,
    CAPABILITY_PLATFORM_BREAK_GLASS,
    CAPABILITY_PLATFORM_MAINTENANCE_OPERATE,
    CAPABILITY_PLATFORM_MAINTENANCE_VERIFY,
    CAPABILITY_PLATFORM_OPERATIONS_READ,
    CAPABILITY_PLATFORM_RELEASE_OPERATE,
)
from hc_data_platform.security.http import require_auth_context

pytestmark = pytest.mark.integration
ENVIRONMENT_ID = "test-cn-east-maintenance"
PLAN_DIGEST = f"sha256:{'a' * 64}"
OWNER_A = UUID("00000000-0000-4000-8000-000000000021")
OWNER_B = UUID("00000000-0000-4000-8000-000000000022")


def _source_dsn() -> str:
    value = os.getenv("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    return normalize_postgres_dsn(value)


def _database_dsn(base_dsn: str, database_name: str) -> str:
    parsed = urlsplit(base_dsn)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("HC_TEST_POSTGRES_DSN must use a PostgreSQL URI")
    return parsed._replace(path=f"/{quote(database_name, safe='')}").geturl()


@pytest.fixture(scope="module")
def maintenance_dsn() -> Iterator[str]:
    base_dsn = _source_dsn()
    database_name = f"hc_platform_maintenance_{uuid4().hex[:12]}"
    with psycopg.connect(base_dsn, autocommit=True) as admin:
        try:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        except psycopg.errors.InsufficientPrivilege:
            pytest.skip("HC_TEST_POSTGRES_DSN role cannot create an isolated database")
    isolated_dsn = _database_dsn(base_dsn, database_name)
    try:
        asyncio.run(apply_migrations(isolated_dsn))
        yield isolated_dsn
    finally:
        with psycopg.connect(base_dsn, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (database_name,),
            )
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))


def _command(
    operation: MaintenanceOperation,
    next_state: MaintenanceState,
    **updates: object,
) -> MaintenanceCommandV1:
    values: dict[str, object] = {
        "operation_id": operation.operation_id,
        "environment_id": operation.environment_id,
        "owner_instance_id": str(operation.owner_instance_id),
        "fencing_token": operation.fencing_token,
        "expected_state": operation.state,
        "expected_state_version": operation.state_version,
        "next_state": next_state,
    }
    values.update(updates)
    return MaintenanceCommandV1.model_validate(values)


def _assert_code(code: str, action: Callable[[], object]) -> None:
    with pytest.raises(MaintenanceContractError) as captured:
        action()
    assert captured.value.code == code


def test_database_clock_lease_epoch_inventory_and_takeover_fence_old_owner(
    maintenance_dsn: str,
) -> None:
    repository = PostgresMaintenanceRepository.from_dsn(maintenance_dsn)
    requested = repository.request_operation(
        operation_id="maintenance-pg-001",
        environment_id=ENVIRONMENT_ID,
        operation_kind="BACKUP",
        plan_digest=PLAN_DIGEST,
        requested_by="platform-operator-a",
    )
    assert requested.state is MaintenanceState.REQUESTED
    initial_fence = repository.current_fence(ENVIRONMENT_ID)
    assert initial_fence.mode == "READ_WRITE"

    _assert_code(
        "PLATFORM_MAINTENANCE_ALREADY_ACTIVE",
        lambda: repository.request_operation(
            operation_id="maintenance-pg-competing",
            environment_id=ENVIRONMENT_ID,
            operation_kind="RESTORE",
            plan_digest=PLAN_DIGEST,
            requested_by="platform-operator-b",
        ),
    )

    leased = repository.acquire(requested.operation_id, owner_instance_id=OWNER_A)
    assert leased.state is MaintenanceState.LEASED
    assert leased.fencing_token is not None
    assert leased.fencing_token > initial_fence.fencing_token
    permit = repository.issue_writer_permit(
        environment_id=ENVIRONMENT_ID,
        writer_id="api-request-before-fence",
        writer_kind="api_command",
    )
    repository.assert_writer_permit(permit.permit_id)
    renewed_permit = repository.renew_writer_permit(permit.permit_id)
    assert renewed_permit.lease_until >= permit.lease_until
    retained_permit = repository.retain_writer_permit(permit.permit_id, lease_seconds=120)
    assert retained_permit.writer_kind == "presigned_upload_grant"
    assert retained_permit.lease_until >= renewed_permit.lease_until

    request_token = bind_request_context(
        RequestContext(
            organization_id="maintenance-test-org",
            project_id="maintenance-test-project",
            region_code="cn-east",
            subject_id="maintenance-test-user",
        )
    )
    permit_token = bind_writer_permit(permit.permit_id)
    fenced_connection = psycopg_connection_factory(maintenance_dsn)()
    fenced_connection.execute("SELECT 1")

    read_only = repository.transition(_command(leased, MaintenanceState.READ_ONLY))
    fence = repository.current_fence(ENVIRONMENT_ID)
    assert fence.mode == "READ_ONLY_MAINTENANCE"
    assert fence.fencing_token == leased.fencing_token
    _assert_code(
        "PLATFORM_MAINTENANCE",
        lambda: repository.assert_writer_permit(permit.permit_id),
    )
    try:
        with pytest.raises(MaintenanceContractError) as commit_error:
            fenced_connection.commit()
        assert commit_error.value.code == "PLATFORM_MAINTENANCE"
    finally:
        fenced_connection.rollback()
        fenced_connection.close()
        reset_writer_permit(permit_token)
        reset_request_context(request_token)
    _assert_code(
        "PLATFORM_MAINTENANCE",
        lambda: repository.issue_writer_permit(
            environment_id=ENVIRONMENT_ID,
            writer_id="api-request-after-fence",
            writer_kind="api_command",
        ),
    )

    draining = repository.transition(_command(read_only, MaintenanceState.DRAINING))
    _assert_code(
        "PLATFORM_MAINTENANCE_WRITERS_ACTIVE",
        lambda: repository.transition(_command(draining, MaintenanceState.FENCED)),
    )
    repository.release_writer_permit(permit.permit_id)
    fenced = repository.transition(_command(draining, MaintenanceState.FENCED))
    assert fenced.state is MaintenanceState.FENCED
    assert repository.writer_inventory(ENVIRONMENT_ID) == ()

    with psycopg.connect(maintenance_dsn) as connection:
        connection.execute(
            """
            UPDATE platform.maintenance_operations
            SET lease_until = statement_timestamp() - interval '1 second'
            WHERE operation_id = %s
            """,
            (fenced.operation_id,),
        )

    taken_over = repository.takeover(fenced.operation_id, new_owner_instance_id=OWNER_B)
    assert taken_over.owner_instance_id == OWNER_B
    assert taken_over.state is MaintenanceState.FENCED
    assert taken_over.state_version == fenced.state_version
    assert taken_over.fencing_token is not None
    assert fenced.fencing_token is not None
    assert taken_over.fencing_token > fenced.fencing_token
    assert repository.current_fence(ENVIRONMENT_ID).fencing_token == taken_over.fencing_token

    _assert_code(
        "PLATFORM_MAINTENANCE_OWNER_MISMATCH",
        lambda: repository.renew(
            fenced.operation_id,
            owner_instance_id=OWNER_A,
            fencing_token=fenced.fencing_token,
        ),
    )
    _assert_code(
        "PLATFORM_MAINTENANCE_OWNER_MISMATCH",
        lambda: repository.transition(_command(fenced, MaintenanceState.EXECUTING)),
    )
    renewed = repository.renew(
        taken_over.operation_id,
        owner_instance_id=OWNER_B,
        fencing_token=taken_over.fencing_token,
    )
    assert renewed.lease_until is not None
    assert taken_over.lease_until is not None
    assert renewed.lease_until >= taken_over.lease_until


def test_maintenance_events_are_append_only(maintenance_dsn: str) -> None:
    with psycopg.connect(maintenance_dsn) as connection:
        count = connection.execute(
            "SELECT count(*) FROM platform.maintenance_events WHERE operation_id = %s",
            ("maintenance-pg-001",),
        ).fetchone()[0]
        assert count >= 5
        with pytest.raises(psycopg.errors.RaiseException, match="IMMUTABLE"):
            connection.execute(
                """
                UPDATE platform.maintenance_events
                SET event_code = 'PLATFORM_TAMPERED'
                WHERE operation_id = %s
                """,
                ("maintenance-pg-001",),
            )


def test_platform_task_singleton_takeover_fences_old_owner_commit(
    maintenance_dsn: str,
) -> None:
    repository = PostgresPlatformTaskLeaseRepository.from_dsn(maintenance_dsn)
    environment_id = "task-lease-test-cn-east"
    task_id = f"storage-inventory:sha256:{'1' * 64}"

    lease_a = repository.try_acquire(
        environment_id=environment_id,
        task_id=task_id,
        owner_instance_id=OWNER_A,
    )
    assert lease_a is not None
    assert (
        repository.try_acquire(
            environment_id=environment_id,
            task_id=task_id,
            owner_instance_id=OWNER_B,
        )
        is None
    )
    renewed_a = repository.renew(lease_a.lease_id)
    assert renewed_a.lease_version > lease_a.lease_version

    with psycopg.connect(maintenance_dsn) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS platform.task_lease_test_writes (
                write_id text PRIMARY KEY,
                owner_instance_id uuid NOT NULL
            )
            """
        )

    request_token = bind_request_context(
        RequestContext(
            organization_id="task-lease-test-org",
            project_id="task-lease-test-project",
            region_code="cn-east",
            subject_id="task-lease-test-worker-a",
            service_identity=True,
        )
    )
    lease_token = bind_platform_task_lease(lease_a.lease_id)
    old_connection = psycopg_connection_factory(maintenance_dsn)()
    old_connection.execute(
        """
        INSERT INTO platform.task_lease_test_writes (write_id, owner_instance_id)
        VALUES ('old-owner-uncommitted', %s)
        """,
        (OWNER_A,),
    )

    with psycopg.connect(maintenance_dsn) as connection:
        connection.execute(
            """
            UPDATE platform.platform_task_leases
            SET lease_until = acquired_at,
                updated_at = statement_timestamp()
            WHERE lease_id = %s
            """,
            (lease_a.lease_id,),
        )
    lease_b = repository.try_acquire(
        environment_id=environment_id,
        task_id=task_id,
        owner_instance_id=OWNER_B,
    )
    assert lease_b is not None
    assert lease_b.lease_id != lease_a.lease_id
    assert lease_b.fencing_token > lease_a.fencing_token

    try:
        with pytest.raises(MaintenanceContractError) as stale_commit:
            old_connection.commit()
        assert stale_commit.value.code == "PLATFORM_TASK_LEASE_STALE"
    finally:
        old_connection.rollback()
        old_connection.close()
        reset_platform_task_lease(lease_token)
        reset_request_context(request_token)

    request_token = bind_request_context(
        RequestContext(
            organization_id="task-lease-test-org",
            project_id="task-lease-test-project",
            region_code="cn-east",
            subject_id="task-lease-test-worker-b",
            service_identity=True,
        )
    )
    lease_token = bind_platform_task_lease(lease_b.lease_id)
    try:
        with psycopg_connection_factory(maintenance_dsn)() as new_connection:
            new_connection.execute(
                """
                INSERT INTO platform.task_lease_test_writes (write_id, owner_instance_id)
                VALUES ('new-owner-committed', %s)
                """,
                (OWNER_B,),
            )
    finally:
        reset_platform_task_lease(lease_token)
        reset_request_context(request_token)

    with psycopg.connect(maintenance_dsn) as connection:
        writes = connection.execute(
            """
            SELECT write_id, owner_instance_id
            FROM platform.task_lease_test_writes
            ORDER BY write_id
            """
        ).fetchall()
        events = connection.execute(
            """
            SELECT event_code
            FROM platform.platform_task_lease_events
            WHERE environment_id = %s AND task_id = %s
            ORDER BY event_id
            """,
            (environment_id, task_id),
        ).fetchall()
    assert writes == [("new-owner-committed", OWNER_B)]
    assert [row[0] for row in events] == [
        "PLATFORM_TASK_LEASE_ACQUIRED",
        "PLATFORM_TASK_LEASE_TAKEN_OVER",
    ]

    with (
        psycopg.connect(maintenance_dsn) as connection,
        pytest.raises(psycopg.errors.RaiseException, match="IMMUTABLE"),
    ):
        connection.execute(
            """
            UPDATE platform.platform_task_lease_events
            SET event_code = 'PLATFORM_TASK_LEASE_RELEASED'
            WHERE environment_id = %s AND task_id = %s
            """,
            (environment_id, task_id),
        )

    repository.release(lease_b.lease_id)
    with psycopg.connect(maintenance_dsn) as connection:
        connection.execute(
            """
            UPDATE platform.environment_fences
            SET mode = 'READ_ONLY_MAINTENANCE'
            WHERE environment_id = %s
            """,
            (environment_id,),
        )
    _assert_code(
        "PLATFORM_MAINTENANCE",
        lambda: repository.try_acquire(
            environment_id=environment_id,
            task_id=f"storage-inventory:sha256:{'2' * 64}",
            owner_instance_id=OWNER_A,
        ),
    )


def test_platform_operation_grants_are_mutually_exclusive_and_revoke_sessions(
    maintenance_dsn: str,
) -> None:
    principal_id = uuid4()
    session_id = uuid4()
    with psycopg.connect(maintenance_dsn) as connection:
        connection.execute(
            """
            INSERT INTO access_control.accounts (
                principal_id, canonical_username, display_username, password_hash,
                display_name, password_changed_at
            ) VALUES (%s, %s, %s, 'test-only-hash', %s, statement_timestamp())
            """,
            (principal_id, f"ops-{principal_id.hex}", "ops-user", "Ops User"),
        )
        connection.execute(
            """
            INSERT INTO access_control.sessions (session_id, principal_id, token_hash)
            VALUES (%s, %s, %s)
            """,
            (session_id, principal_id, "1" * 64),
        )
        connection.execute(
            """
            INSERT INTO access_control.platform_capability_grants (
                principal_id, capability_key, granted_by
            ) VALUES (%s, 'platform.operations.read', 'test-provisioner')
            """,
            (principal_id,),
        )

    with psycopg.connect(maintenance_dsn) as connection:
        revision, revoked_at, reason = connection.execute(
            """
            SELECT account.capability_revision, session.revoked_at,
                   session.revocation_reason
            FROM access_control.accounts account
            JOIN access_control.sessions session USING (principal_id)
            WHERE account.principal_id = %s
            """,
            (principal_id,),
        ).fetchone()
        assert revision == 1
        assert revoked_at is not None
        assert reason == "PLATFORM_CAPABILITY_CHANGED"

    with (
        psycopg.connect(maintenance_dsn) as connection,
        pytest.raises(
            psycopg.errors.RaiseException,
            match="PLATFORM_OPERATION_CAPABILITY_CONFLICT",
        ),
    ):
        connection.execute(
            """
            INSERT INTO access_control.platform_capability_grants (
                principal_id, capability_key, granted_by
            ) VALUES (%s, 'platform.release.operate', 'test-provisioner')
            """,
            (principal_id,),
        )


@pytest.mark.parametrize(
    ("operation_kind", "required_capability", "wrong_capability"),
    (
        ("BACKUP", CAPABILITY_PLATFORM_MAINTENANCE_OPERATE, CAPABILITY_PLATFORM_RELEASE_OPERATE),
        ("OTHER", CAPABILITY_PLATFORM_MAINTENANCE_OPERATE, CAPABILITY_PLATFORM_RELEASE_OPERATE),
        ("MIGRATION", CAPABILITY_PLATFORM_RELEASE_OPERATE, CAPABILITY_PLATFORM_MAINTENANCE_OPERATE),
        ("RELEASE", CAPABILITY_PLATFORM_RELEASE_OPERATE, CAPABILITY_PLATFORM_MAINTENANCE_OPERATE),
        ("RESTORE", CAPABILITY_PLATFORM_BREAK_GLASS, CAPABILITY_PLATFORM_RELEASE_OPERATE),
    ),
)
def test_operation_kind_requires_its_exact_separated_capability(
    maintenance_dsn: str,
    operation_kind: str,
    required_capability: str,
    wrong_capability: str,
) -> None:
    repository = PostgresMaintenanceRepository.from_dsn(maintenance_dsn)
    operation_id = f"capability-{operation_kind.lower()}"
    settings = Settings(
        environment="test",
        runtime_backend="memory",
        platform_environment_id=f"capability-env-{operation_kind.lower()}",
        _env_file=None,
    )
    app = create_app(settings=settings, maintenance_write_gate=repository)
    current_capability = {"value": wrong_capability}
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(
        subject_id=f"{operation_kind.lower()}-operator",
        project_ids=frozenset(),
        region_codes=frozenset(),
        roles=frozenset(),
        capabilities=frozenset({current_capability["value"]}),
    )
    body = {
        "operation_id": operation_id,
        "operation_kind": operation_kind,
        "plan_digest": PLAN_DIGEST,
    }
    with TestClient(app) as client:
        denied = client.post("/api/v1/platform/maintenance-operations", json=body)
        current_capability["value"] = required_capability
        accepted = client.post("/api/v1/platform/maintenance-operations", json=body)

    assert denied.status_code == 403
    assert denied.json()["code"] == "PLATFORM_CAPABILITY_REQUIRED"
    assert accepted.status_code == 201
    assert accepted.json()["operation_kind"] == operation_kind


def test_authenticated_maintenance_http_controls_remain_available_after_read_only(
    maintenance_dsn: str,
) -> None:
    repository = PostgresMaintenanceRepository.from_dsn(maintenance_dsn)
    foreign_operation = repository.request_operation(
        operation_id="maintenance-api-foreign",
        environment_id="api-maintenance-foreign-environment",
        operation_kind="BACKUP",
        plan_digest=PLAN_DIGEST,
        requested_by="foreign-environment-operator",
    )
    settings = Settings(
        environment="test",
        runtime_backend="memory",
        platform_environment_id="api-maintenance-test",
        _env_file=None,
    )
    app = create_app(settings=settings, maintenance_write_gate=repository)
    auth = {
        "capability": CAPABILITY_PLATFORM_OPERATIONS_READ,
    }
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(
        subject_id="platform-maintenance-operator",
        project_ids=frozenset(),
        region_codes=frozenset(),
        roles=frozenset(),
        capabilities=frozenset({auth["capability"]}),
    )
    body = {
        "operation_id": "maintenance-api-001",
        "operation_kind": "BACKUP",
        "plan_digest": PLAN_DIGEST,
    }
    with TestClient(app) as client:
        foreign_get = client.get("/api/v1/platform/maintenance-operations/maintenance-api-foreign")
        auth["capability"] = CAPABILITY_PLATFORM_MAINTENANCE_OPERATE
        foreign_acquire = client.post(
            "/api/v1/platform/maintenance-operations/maintenance-api-foreign:acquire",
            json={"owner_instance_id": str(OWNER_A)},
        )
        assert foreign_get.status_code == 404
        assert foreign_acquire.status_code == 404
        assert foreign_get.json()["detail"] == (
            "The maintenance operation was not found in this environment."
        )
        assert foreign_acquire.json()["detail"] == foreign_get.json()["detail"]
        assert "foreign" not in foreign_get.json()["detail"].lower()
        assert repository.get_operation(foreign_operation.operation_id).state is (
            MaintenanceState.REQUESTED
        )

        auth["capability"] = CAPABILITY_PLATFORM_ADMIN
        admin_denied = client.post("/api/v1/platform/maintenance-operations", json=body)
        assert admin_denied.status_code == 403
        assert admin_denied.json()["code"] == "PLATFORM_CAPABILITY_REQUIRED"
        assert admin_denied.json()["detail"] == (
            "The verified identity is not authorized for this platform operation."
        )
        assert "platform.maintenance.operate" not in admin_denied.text

        auth["capability"] = CAPABILITY_PLATFORM_MAINTENANCE_OPERATE
        requested = client.post("/api/v1/platform/maintenance-operations", json=body)
        assert requested.status_code == 201
        acquired = client.post(
            "/api/v1/platform/maintenance-operations/maintenance-api-001:acquire",
            json={"owner_instance_id": str(OWNER_A)},
        )
        assert acquired.status_code == 200
        lease = acquired.json()
        transitioned = client.post(
            "/api/v1/platform/maintenance-operations/maintenance-api-001:transition",
            json={
                "owner_instance_id": str(OWNER_A),
                "fencing_token": lease["fencing_token"],
                "expected_state": "LEASED",
                "expected_state_version": lease["state_version"],
                "next_state": "READ_ONLY",
            },
        )
        assert transitioned.status_code == 200
        assert transitioned.json()["state"] == "READ_ONLY"

        operator_write_enable = client.post(
            "/api/v1/platform/maintenance-operations/maintenance-api-001:transition",
            json={
                "owner_instance_id": str(OWNER_A),
                "fencing_token": lease["fencing_token"],
                "expected_state": "READ_ONLY",
                "expected_state_version": transitioned.json()["state_version"],
                "next_state": "SUCCEEDED",
            },
        )
        assert operator_write_enable.status_code == 403
        auth["capability"] = CAPABILITY_PLATFORM_BREAK_GLASS
        break_glass_write_enable = client.post(
            "/api/v1/platform/maintenance-operations/maintenance-api-001:transition",
            json={
                "owner_instance_id": str(OWNER_A),
                "fencing_token": lease["fencing_token"],
                "expected_state": "READ_ONLY",
                "expected_state_version": transitioned.json()["state_version"],
                "next_state": "SUCCEEDED",
            },
        )
        assert break_glass_write_enable.status_code == 409
        assert break_glass_write_enable.json()["code"] == "PLATFORM_MAINTENANCE_TRANSITION_INVALID"

        auth["capability"] = CAPABILITY_PLATFORM_MAINTENANCE_OPERATE
        operator_reconcile = client.post(
            "/api/v1/platform/maintenance-operations/maintenance-api-001:reconcile",
            json={
                "owner_instance_id": str(OWNER_A),
                "fencing_token": lease["fencing_token"],
                "expected_state_version": transitioned.json()["state_version"],
            },
        )
        assert operator_reconcile.status_code == 403
        auth["capability"] = CAPABILITY_PLATFORM_MAINTENANCE_VERIFY
        verifier_reconcile = client.post(
            "/api/v1/platform/maintenance-operations/maintenance-api-001:reconcile",
            json={
                "owner_instance_id": str(OWNER_A),
                "fencing_token": lease["fencing_token"],
                "expected_state_version": transitioned.json()["state_version"],
            },
        )
        assert verifier_reconcile.status_code == 409
        assert verifier_reconcile.json()["code"] == "PLATFORM_MAINTENANCE_STATE_CONFLICT"

        auth["capability"] = CAPABILITY_PLATFORM_OPERATIONS_READ
        visible = client.get("/api/v1/platform/maintenance-operations/maintenance-api-001")
        assert visible.status_code == 200
        blocked_business_command = client.post("/api/v1/auth/registrations", json={})
        assert blocked_business_command.status_code == 503
        assert blocked_business_command.json()["code"] == "PLATFORM_MAINTENANCE"

        auth["capability"] = CAPABILITY_PLATFORM_MAINTENANCE_OPERATE
        stale = client.post(
            "/api/v1/platform/maintenance-operations/maintenance-api-001:renew",
            json={
                "owner_instance_id": str(OWNER_A),
                "fencing_token": lease["fencing_token"] - 1,
            },
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "PLATFORM_MAINTENANCE_FENCING_TOKEN_STALE"
        assert stale.json()["detail"] == (
            "The maintenance command does not match the current operation state."
        )
        assert str(OWNER_A) not in stale.text
        assert "fencing_token" not in stale.json()

    with psycopg.connect(maintenance_dsn) as connection:
        audits = connection.execute(
            """
            SELECT action, actor_id, outcome, safe_details
            FROM access_control.audit_events
            WHERE scope_kind = 'PLATFORM'
              AND resource_type = 'maintenance_operation'
              AND resource_id = 'maintenance-api-001'
            ORDER BY occurred_at, event_id
            """
        ).fetchall()
    assert {row[0] for row in audits} >= {
        "platform.maintenance.requested",
        "platform.maintenance.acquired",
        "platform.maintenance.read.only",
        "platform.maintenance.read",
    }
    assert {row[1] for row in audits} == {"platform-maintenance-operator"}
    assert {row[2] for row in audits} == {"SUCCEEDED", "FAILED"}
    failed = {(row[0], row[3]["error_code"]) for row in audits if row[2] == "FAILED"}
    assert failed == {
        ("platform.maintenance.transition", "PLATFORM_MAINTENANCE_TRANSITION_INVALID"),
        ("platform.maintenance.reconcile", "PLATFORM_MAINTENANCE_STATE_CONFLICT"),
        ("platform.maintenance.renew", "PLATFORM_MAINTENANCE_FENCING_TOKEN_STALE"),
    }
    with psycopg.connect(maintenance_dsn) as connection:
        denied = connection.execute(
            """
            SELECT action, outcome, safe_details
            FROM access_control.audit_events
            WHERE scope_kind = 'PLATFORM'
              AND resource_type = 'platform_operation_authorization'
              AND resource_id = 'maintenance-api-001'
              AND outcome = 'DENIED'
            """
        ).fetchall()
    assert {(row[0], row[1]) for row in denied} == {
        ("platform.maintenance.request", "DENIED"),
        ("platform.maintenance.transition", "DENIED"),
        ("platform.maintenance.reconcile", "DENIED"),
    }
    assert {row[2]["reason_code"] for row in denied} == {"PLATFORM_CAPABILITY_REQUIRED"}
    assert all(
        {"plan_digest", "owner_instance_id", "fencing_token"}.isdisjoint(row[3]) for row in audits
    )
