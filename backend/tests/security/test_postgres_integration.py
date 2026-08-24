from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
pytest.importorskip("asyncpg")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from hc_data_platform.core.errors import ProblemException  # noqa: E402
from hc_data_platform.core.events import DomainEventEnvelope  # noqa: E402
from hc_data_platform.core.migrations import apply_migrations  # noqa: E402
from hc_data_platform.security.audit import AuditRecord  # noqa: E402
from hc_data_platform.security.auth import AuthContext, Role  # noqa: E402
from hc_data_platform.security.postgres import PostgresScopedUnitOfWork  # noqa: E402

pytestmark = pytest.mark.integration


def _database_url() -> str:
    value = os.getenv("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)
    return value


async def _run_migration(engine: AsyncEngine) -> None:
    script = (Path(__file__).parents[2] / "migrations" / "security" / "001_core.sql").read_text(
        encoding="utf-8"
    )
    async with engine.connect() as connection:
        raw_connection = await connection.get_raw_connection()
        assert raw_connection.driver_connection is not None
        await raw_connection.driver_connection.execute(script)


@pytest.mark.integration
def test_postgres_rls_idempotency_and_atomic_side_effects() -> None:
    asyncio.run(_postgres_scenario())


@pytest.mark.integration
def test_platform_admin_rls_bypasses_membership_without_loosening_ordinary_scope() -> None:
    asyncio.run(_platform_admin_rls_scenario())


@pytest.mark.integration
def test_platform_admin_migration_revises_once_and_revokes_old_sessions() -> None:
    asyncio.run(_platform_admin_migration_scenario())


async def _platform_admin_migration_scenario() -> None:
    database_url = _database_url()
    await apply_migrations(database_url)
    engine = create_async_engine(database_url)
    suffix = uuid4().hex[:12]
    principal_id = str(uuid4())
    session_id = str(uuid4())
    username = f"legacy-platform-admin-{suffix}"
    migration = (
        Path(__file__).parents[2] / "migrations" / "security" / "020_platform_super_admin.sql"
    ).read_text(encoding="utf-8")
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO access_control.accounts (
                        principal_id, canonical_username, display_username, display_name,
                        password_hash, capability_revision, password_changed_at
                    ) VALUES (
                        CAST(:principal_id AS uuid), :username, :username, :username,
                        'not-used-by-this-migration-test', 7, now()
                    )
                    """
                ),
                {"principal_id": principal_id, "username": username},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO access_control.platform_capability_grants (
                        principal_id, capability_key, granted_by
                    ) VALUES (
                        CAST(:principal_id AS uuid), 'platform.account.manage', 'integration-test'
                    )
                    """
                ),
                {"principal_id": principal_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO access_control.sessions (
                        session_id, principal_id, token_hash, credential_revision,
                        issued_at, last_seen_at
                    ) VALUES (
                        CAST(:session_id AS uuid), CAST(:principal_id AS uuid),
                        repeat('a', 64), 1, now(), now()
                    )
                    """
                ),
                {"session_id": session_id, "principal_id": principal_id},
            )

        async with engine.connect() as connection:
            raw_connection = await connection.get_raw_connection()
            assert raw_connection.driver_connection is not None
            await raw_connection.driver_connection.execute(migration)
            first = await raw_connection.driver_connection.fetchrow(
                """
                SELECT account.capability_revision,
                       platform_grant.active AS marker_active,
                       session.revoked_at IS NOT NULL AS session_revoked,
                       session.revocation_reason
                FROM access_control.accounts account
                JOIN access_control.platform_capability_grants platform_grant
                  ON platform_grant.principal_id = account.principal_id
                 AND platform_grant.capability_key = 'platform.admin'
                JOIN access_control.sessions session
                  ON session.principal_id = account.principal_id
                WHERE account.principal_id = $1::uuid
                """,
                principal_id,
            )
            assert first is not None
            assert tuple(first) == (8, True, True, "ADMIN_REVOKED")

            await raw_connection.driver_connection.execute(migration)
            second_revision = await raw_connection.driver_connection.fetchval(
                """
                SELECT capability_revision
                FROM access_control.accounts
                WHERE principal_id = $1::uuid
                """,
                principal_id,
            )
            assert second_revision == 8
    finally:
        await engine.dispose()
        cleanup = create_async_engine(database_url)
        try:
            async with cleanup.begin() as connection:
                await connection.execute(
                    text(
                        "DELETE FROM access_control.sessions "
                        "WHERE principal_id = CAST(:principal_id AS uuid)"
                    ),
                    {"principal_id": principal_id},
                )
                await connection.execute(
                    text(
                        "DELETE FROM access_control.platform_capability_grants "
                        "WHERE principal_id = CAST(:principal_id AS uuid)"
                    ),
                    {"principal_id": principal_id},
                )
                await connection.execute(
                    text(
                        "DELETE FROM access_control.accounts "
                        "WHERE principal_id = CAST(:principal_id AS uuid)"
                    ),
                    {"principal_id": principal_id},
                )
        finally:
            await cleanup.dispose()


async def _platform_admin_rls_scenario() -> None:
    database_url = _database_url()
    await apply_migrations(database_url)
    engine = create_async_engine(database_url)
    suffix = uuid4().hex[:12]
    schema = f"platform_admin_rls_{suffix}"
    role = f"platform_admin_rls_role_{suffix}"
    organization_a = f"organization-a-{suffix}"
    organization_b = f"organization-b-{suffix}"
    project_a = f"project-a-{suffix}"
    project_b = f"project-b-{suffix}"
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SELECT set_config('app.platform_admin', 'true', true)"))
            await connection.execute(
                text(
                    "INSERT INTO registry.organization_projects (organization_id, project_id) "
                    "VALUES (:organization_a, :project_a), (:organization_b, :project_b)"
                ),
                {
                    "organization_a": organization_a,
                    "project_a": project_a,
                    "organization_b": organization_b,
                    "project_b": project_b,
                },
            )
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.execute(
                text(
                    f"""
                    CREATE TABLE "{schema}".resources (
                        resource_id text PRIMARY KEY,
                        organization_id text NOT NULL,
                        project_id text NOT NULL,
                        region_code text NOT NULL,
                        value text NOT NULL
                    )
                    """
                )
            )
            await connection.execute(
                text("SELECT core.apply_project_rls(CAST(:table_name AS regclass))"),
                {"table_name": f'"{schema}".resources'},
            )
            await connection.execute(
                text(
                    f"""
                    INSERT INTO "{schema}".resources
                        (resource_id, organization_id, project_id, region_code, value)
                    VALUES
                        ('a', :organization_a, :project_a, 'cn', 'a'),
                        ('b', :organization_b, :project_b, 'eu', 'b')
                    """
                ),
                {
                    "organization_a": organization_a,
                    "project_a": project_a,
                    "organization_b": organization_b,
                    "project_b": project_b,
                },
            )
            is_superuser = bool(
                await connection.scalar(
                    text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
                )
            )
            if is_superuser:
                await connection.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
                await connection.execute(
                    text(f'GRANT USAGE ON SCHEMA "{schema}", core TO "{role}"')
                )
                await connection.execute(
                    text(f'GRANT SELECT, INSERT ON "{schema}".resources TO "{role}"')
                )
                await connection.execute(text(f'SET LOCAL ROLE "{role}"'))

            await connection.execute(
                text(
                    """
                    SELECT set_config('app.platform_admin', 'false', true),
                           set_config('app.organization_id', :organization_id, true),
                           set_config('app.project_id', :project_id, true),
                           set_config('app.region_code', 'cn', true)
                    """
                ),
                {"organization_id": organization_a, "project_id": project_a},
            )
            ordinary_rows = (
                (
                    await connection.execute(
                        text(f'SELECT resource_id FROM "{schema}".resources ORDER BY resource_id')
                    )
                )
                .scalars()
                .all()
            )
            assert ordinary_rows == ["a"]
            savepoint = await connection.begin_nested()
            with pytest.raises(sqlalchemy.exc.DBAPIError):
                await connection.execute(
                    text(
                        f"""
                        INSERT INTO "{schema}".resources
                            (resource_id, organization_id, project_id, region_code, value)
                        VALUES ('ordinary-cross', :organization_id, :project_id, 'eu', 'denied')
                        """
                    ),
                    {"organization_id": organization_b, "project_id": project_b},
                )
            await savepoint.rollback()

            await connection.execute(text("SELECT set_config('app.platform_admin', 'true', true)"))
            platform_rows = (
                (
                    await connection.execute(
                        text(f'SELECT resource_id FROM "{schema}".resources ORDER BY resource_id')
                    )
                )
                .scalars()
                .all()
            )
            assert platform_rows == ["a", "b"]
            await connection.execute(
                text(
                    f"""
                    INSERT INTO "{schema}".resources
                        (resource_id, organization_id, project_id, region_code, value)
                    VALUES ('platform-cross', :organization_id, :project_id, 'eu', 'allowed')
                    """
                ),
                {"organization_id": organization_b, "project_id": project_b},
            )
            if is_superuser:
                await connection.execute(text("RESET ROLE"))
    finally:
        await engine.dispose()
        cleanup = create_async_engine(database_url)
        try:
            async with cleanup.begin() as connection:
                await connection.execute(
                    text("SELECT set_config('app.platform_admin', 'true', true)")
                )
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                await connection.execute(
                    text(
                        "DELETE FROM registry.organization_projects "
                        "WHERE organization_id IN (:organization_a, :organization_b)"
                    ),
                    {"organization_a": organization_a, "organization_b": organization_b},
                )
                if bool(
                    await connection.scalar(
                        text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :role)"),
                        {"role": role},
                    )
                ):
                    await connection.execute(text(f'REVOKE USAGE ON SCHEMA core FROM "{role}"'))
                    await connection.execute(text(f'DROP ROLE "{role}"'))
        finally:
            await cleanup.dispose()


async def _postgres_scenario() -> None:
    engine = create_async_engine(_database_url(), pool_size=12, max_overflow=12)
    suffix = uuid4().hex[:12]
    schema = f"security_it_{suffix}"
    role = f"security_it_role_{suffix}"
    project_id = f"project-{suffix}"
    other_project = f"other-{suffix}"
    organization_id = f"organization-{suffix}"
    try:
        # Repeating the module migration is itself part of the acceptance contract.
        await _run_migration(engine)
        await _run_migration(engine)
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO registry.organization_projects (organization_id, project_id) "
                    "VALUES (:organization_id, :project_id), (:organization_id, :other_project)"
                ),
                {
                    "organization_id": organization_id,
                    "project_id": project_id,
                    "other_project": other_project,
                },
            )
        await _create_test_tables(engine, schema)
        await _verify_rls(engine, schema, role, project_id, other_project)
        await _verify_postgres_idempotency(engine, schema, organization_id, project_id)
        await _verify_atomic_rollback_and_commit(engine, schema, organization_id, project_id)
    finally:
        await engine.dispose()
        cleanup = create_async_engine(_database_url())
        try:
            async with cleanup.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                await _set_scope(connection, project_id, "cn")
                await connection.execute(
                    text("SELECT set_config('app.organization_id', :organization_id, true)"),
                    {"organization_id": organization_id},
                )
                await connection.execute(
                    text("DELETE FROM core.idempotency_records WHERE project_id = :project_id"),
                    {"project_id": project_id},
                )
                await connection.execute(
                    text("DELETE FROM core.audit_events WHERE project_id = :project_id"),
                    {"project_id": project_id},
                )
                await connection.execute(
                    text("DELETE FROM core.audit_integrity_heads WHERE project_id = :project_id"),
                    {"project_id": project_id},
                )
                await connection.execute(
                    text("DELETE FROM core.outbox_events WHERE project_id = :project_id"),
                    {"project_id": project_id},
                )
                await connection.execute(
                    text(
                        "DELETE FROM registry.organization_projects "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": organization_id},
                )
                if bool(
                    await connection.scalar(
                        text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :role)"),
                        {"role": role},
                    )
                ):
                    await connection.execute(text(f'REVOKE USAGE ON SCHEMA core FROM "{role}"'))
                    await connection.execute(text(f'DROP ROLE "{role}"'))
        finally:
            await cleanup.dispose()


async def _create_test_tables(engine: AsyncEngine, schema: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await connection.execute(
            text(
                f"""
                CREATE TABLE "{schema}".resources (
                    resource_id text PRIMARY KEY,
                    project_id text NOT NULL,
                    region_code text NOT NULL,
                    version integer NOT NULL DEFAULT 1,
                    value text NOT NULL
                )
                """
            )
        )
        await connection.execute(
            text(
                f"""
                CREATE TABLE "{schema}".effects (
                    effect_id bigserial PRIMARY KEY,
                    project_id text NOT NULL,
                    region_code text NOT NULL,
                    value text NOT NULL
                )
                """
            )
        )
        await connection.execute(
            text("SELECT core.apply_project_rls(CAST(:table_name AS regclass))"),
            {"table_name": f'"{schema}".resources'},
        )
        await connection.execute(
            text("SELECT core.apply_project_rls(CAST(:table_name AS regclass))"),
            {"table_name": f'"{schema}".effects'},
        )


async def _verify_rls(
    engine: AsyncEngine,
    schema: str,
    role: str,
    project_id: str,
    other_project: str,
) -> None:
    async with engine.begin() as connection:
        await _set_scope(connection, project_id, "cn")
        await connection.execute(
            text(
                f"""
                INSERT INTO "{schema}".resources
                    (resource_id, project_id, region_code, value)
                VALUES ('visible', :project_id, 'cn', 'yes')
                """
            ),
            {"project_id": project_id},
        )
        await _set_scope(connection, other_project, "cn")
        await connection.execute(
            text(
                f"""
                INSERT INTO "{schema}".resources
                    (resource_id, project_id, region_code, value)
                VALUES ('other-project', :other_project, 'cn', 'no')
                """
            ),
            {"other_project": other_project},
        )
        await _set_scope(connection, project_id, "us")
        await connection.execute(
            text(
                f"""
                INSERT INTO "{schema}".resources
                    (resource_id, project_id, region_code, value)
                VALUES ('other-region', :project_id, 'us', 'no')
                """
            ),
            {"project_id": project_id},
        )
        is_superuser = bool(
            await connection.scalar(
                text(
                    """
                    SELECT rolsuper FROM pg_roles
                    WHERE rolname = current_user
                    """
                )
            )
        )
        if is_superuser:
            await connection.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
            await connection.execute(text(f'GRANT USAGE ON SCHEMA "{schema}", core TO "{role}"'))
            await connection.execute(
                text(f'GRANT SELECT, INSERT ON "{schema}".resources TO "{role}"')
            )
            await connection.execute(text(f'SET LOCAL ROLE "{role}"'))

        await _set_scope(connection, project_id, "cn")
        assert (
            await connection.execute(
                text(f'SELECT resource_id FROM "{schema}".resources ORDER BY resource_id')
            )
        ).scalars().all() == ["visible"]

        await _set_scope(connection, project_id, "us")
        assert (
            await connection.execute(text(f'SELECT resource_id FROM "{schema}".resources'))
        ).scalars().all() == ["other-region"]

        await _set_scope(connection, other_project, "cn")
        assert (
            await connection.execute(text(f'SELECT resource_id FROM "{schema}".resources'))
        ).scalars().all() == ["other-project"]

        await _set_scope(connection, project_id, "cn")
        savepoint = await connection.begin_nested()
        with pytest.raises(sqlalchemy.exc.DBAPIError):
            await connection.execute(
                text(
                    f"""
                    INSERT INTO "{schema}".resources
                        (resource_id, project_id, region_code, value)
                    VALUES ('cross-write', :other_project, 'cn', 'denied')
                    """
                ),
                {"other_project": other_project},
            )
        await savepoint.rollback()

        await connection.execute(
            text(
                f"""
                INSERT INTO "{schema}".resources
                    (resource_id, project_id, region_code, value)
                VALUES ('same-scope-write', :project_id, 'cn', 'allowed')
                """
            ),
            {"project_id": project_id},
        )
        if is_superuser:
            await connection.execute(text("RESET ROLE"))


async def _set_scope(connection: AsyncConnection, project_id: str, region_code: str) -> None:
    await connection.execute(
        text(
            """
            SELECT
                set_config('app.project_id', :project_id, true),
                set_config('app.region_code', :region_code, true)
            """
        ),
        {"project_id": project_id, "region_code": region_code},
    )


def _auth(organization_id: str, project_id: str) -> AuthContext:
    return AuthContext(
        subject_id="integration-worker",
        roles=frozenset({Role.ADMIN.value}),
        project_ids=frozenset({project_id}),
        region_codes=frozenset({"cn"}),
        service_identity=True,
        scope_pairs=frozenset({(project_id, "cn")}),
        organization_ids=frozenset({organization_id}),
        organization_scope_triples=frozenset({(organization_id, project_id, "cn")}),
    )


async def _verify_postgres_idempotency(
    engine: AsyncEngine, schema: str, organization_id: str, project_id: str
) -> None:
    calls = 0

    async def execute_once() -> dict[str, str]:
        nonlocal calls
        async with PostgresScopedUnitOfWork.for_worker(
            engine_session_factory(engine),
            auth=_auth(organization_id, project_id),
            organization_id=organization_id,
            project_id=project_id,
            region_code="cn",
        ) as uow:

            async def action() -> dict[str, str]:
                nonlocal calls
                calls += 1
                await uow.session.execute(
                    text(
                        f"""
                        INSERT INTO "{schema}".effects (project_id, region_code, value)
                        VALUES (:project_id, 'cn', 'created')
                        """
                    ),
                    {"project_id": project_id},
                )
                return {"status": "created"}

            result = await uow.idempotency.execute(
                scope="resource/create",
                key="shared-key",
                payload={"body": 1},
                action=action,
            )
            await uow.commit()
            return result.value

    results = await asyncio.gather(*(execute_once() for _ in range(100)))
    assert results == [{"status": "created"}] * 100
    assert calls == 1
    async with engine.connect() as connection:
        await _set_scope(connection, project_id, "cn")
        assert await connection.scalar(text(f'SELECT count(*) FROM "{schema}".effects')) == 1

    with pytest.raises(ProblemException) as captured:
        async with PostgresScopedUnitOfWork.for_worker(
            engine_session_factory(engine),
            auth=_auth(organization_id, project_id),
            organization_id=organization_id,
            project_id=project_id,
            region_code="cn",
        ) as conflict_uow:
            await conflict_uow.idempotency.execute(
                scope="resource/create",
                key="shared-key",
                payload={"body": 2},
                action=lambda: {"status": "must-not-run"},
            )
    assert captured.value.problem.status == 409
    assert captured.value.problem.code == "IDEMPOTENCY_KEY_REUSED"


async def _verify_atomic_rollback_and_commit(
    engine: AsyncEngine, schema: str, organization_id: str, project_id: str
) -> None:
    resource_id = f"atomic-{uuid4().hex}"
    async with PostgresScopedUnitOfWork.for_worker(
        engine_session_factory(engine),
        auth=_auth(organization_id, project_id),
        organization_id=organization_id,
        project_id=project_id,
        region_code="cn",
        request_id="rollback-request",
    ) as uow:
        await _stage_atomic_write(uow, schema, project_id, resource_id, "rollback-request")

    assert await _atomic_counts(engine, schema, project_id, resource_id) == (0, 0, 0)

    async with PostgresScopedUnitOfWork.for_worker(
        engine_session_factory(engine),
        auth=_auth(organization_id, project_id),
        organization_id=organization_id,
        project_id=project_id,
        region_code="cn",
        request_id="commit-request",
    ) as uow:
        await _stage_atomic_write(uow, schema, project_id, resource_id, "commit-request")
        await uow.commit()

    assert await _atomic_counts(engine, schema, project_id, resource_id) == (1, 1, 1)


async def _stage_atomic_write(
    uow: PostgresScopedUnitOfWork,
    schema: str,
    project_id: str,
    resource_id: str,
    request_id: str,
) -> None:
    await uow.session.execute(
        text(
            f"""
            INSERT INTO "{schema}".resources
                (resource_id, project_id, region_code, value)
            VALUES (:resource_id, :project_id, 'cn', 'atomic')
            """
        ),
        {"resource_id": resource_id, "project_id": project_id},
    )
    uow.audit.append(
        AuditRecord(
            actor_id="integration-worker",
            action="resource.created",
            resource_type="resource",
            resource_id=resource_id,
            project_id=project_id,
            region_code="cn",
            request_id=request_id,
        )
    )
    uow.outbox.stage(
        DomainEventEnvelope(
            event_type="ResourceCreatedV1",
            aggregate_type="resource",
            aggregate_id=resource_id,
            organization_id=uow.scope.organization_id,
            project_id=project_id,
            region_code="cn",
        )
    )


async def _atomic_counts(
    engine: AsyncEngine, schema: str, project_id: str, resource_id: str
) -> tuple[int, int, int]:
    async with engine.connect() as connection:
        await _set_scope(connection, project_id, "cn")
        business = await connection.scalar(
            text(f'SELECT count(*) FROM "{schema}".resources WHERE resource_id = :resource_id'),
            {"resource_id": resource_id},
        )
        audit = await connection.scalar(
            text(
                """
                SELECT count(*) FROM core.audit_events
                WHERE project_id = :project_id AND resource_id = :resource_id
                """
            ),
            {"project_id": project_id, "resource_id": resource_id},
        )
        outbox = await connection.scalar(
            text(
                """
                SELECT count(*) FROM core.outbox_events
                WHERE project_id = :project_id
                  AND envelope ->> 'aggregate_id' = :resource_id
                """
            ),
            {"project_id": project_id, "resource_id": resource_id},
        )
    return int(business or 0), int(audit or 0), int(outbox or 0)


def engine_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
