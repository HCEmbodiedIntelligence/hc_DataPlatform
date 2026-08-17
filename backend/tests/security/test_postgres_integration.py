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


async def _postgres_scenario() -> None:
    engine = create_async_engine(_database_url(), pool_size=12, max_overflow=12)
    suffix = uuid4().hex[:12]
    schema = f"security_it_{suffix}"
    role = f"security_it_role_{suffix}"
    project_id = f"project-{suffix}"
    other_project = f"other-{suffix}"
    try:
        # Repeating the module migration is itself part of the acceptance contract.
        await _run_migration(engine)
        await _run_migration(engine)
        await _create_test_tables(engine, schema)
        await _verify_rls(engine, schema, role, project_id, other_project)
        await _verify_postgres_idempotency(engine, schema, project_id)
        await _verify_atomic_rollback_and_commit(engine, schema, project_id)
    finally:
        await engine.dispose()
        cleanup = create_async_engine(_database_url())
        try:
            async with cleanup.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                await _set_scope(connection, project_id, "cn")
                await connection.execute(
                    text("DELETE FROM core.idempotency_records WHERE project_id = :project_id"),
                    {"project_id": project_id},
                )
                await connection.execute(
                    text("DELETE FROM core.audit_events WHERE project_id = :project_id"),
                    {"project_id": project_id},
                )
                await connection.execute(
                    text("DELETE FROM core.outbox_events WHERE project_id = :project_id"),
                    {"project_id": project_id},
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


def _auth(project_id: str) -> AuthContext:
    return AuthContext.service(
        subject_id="integration-worker",
        roles=[Role.ADMIN],
        project_ids=[project_id],
        region_codes=["cn"],
    )


async def _verify_postgres_idempotency(engine: AsyncEngine, schema: str, project_id: str) -> None:
    calls = 0

    async def execute_once() -> dict[str, str]:
        nonlocal calls
        async with PostgresScopedUnitOfWork.for_worker(
            engine_session_factory(engine),
            auth=_auth(project_id),
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
            auth=_auth(project_id),
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
    engine: AsyncEngine, schema: str, project_id: str
) -> None:
    resource_id = f"atomic-{uuid4().hex}"
    async with PostgresScopedUnitOfWork.for_worker(
        engine_session_factory(engine),
        auth=_auth(project_id),
        project_id=project_id,
        region_code="cn",
        request_id="rollback-request",
    ) as uow:
        await _stage_atomic_write(uow, schema, project_id, resource_id, "rollback-request")

    assert await _atomic_counts(engine, schema, project_id, resource_id) == (0, 0, 0)

    async with PostgresScopedUnitOfWork.for_worker(
        engine_session_factory(engine),
        auth=_auth(project_id),
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
