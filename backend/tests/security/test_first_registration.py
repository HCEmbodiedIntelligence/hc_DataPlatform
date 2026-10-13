"""First registration is an atomic, one-time bootstrap on a fresh database."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from hc_data_platform.core.migrations import apply_migrations
from hc_data_platform.security.access_models import LoginCommand, RegistrationCommand
from hc_data_platform.security.access_postgres import PostgresAccessRepository
from hc_data_platform.security.access_repository import InMemoryAccessRepository
from hc_data_platform.security.access_service import AccessService
from hc_data_platform.security.capabilities import PLATFORM_ADMIN_CAPABILITIES


def register(repository, username):
    return repository.register_account(
        canonical_username=username,
        display_username=username,
        password_hash="unused-test-hash",
        request_id="registration-" + username,
    )


def test_in_memory_only_first_registration_gets_admin_role():
    repository = InMemoryAccessRepository()
    service = AccessService(repository)
    password = "First registration test passphrase 2026!"
    for username, expected in (("owner", PLATFORM_ADMIN_CAPABILITIES), ("member", set())):
        service.register(
            RegistrationCommand(username=username, password=password), request_id=username
        )
        session = service.login(
            LoginCommand(username=username, password=password), request_id=username
        )
        bootstrap = service.bootstrap(session.access_token)
        assert set(bootstrap.platform_capabilities) == set(expected)


@pytest.fixture
def database():
    dsn = os.environ.get("HC_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("HC_TEST_POSTGRES_DSN required for an isolated disposable database")
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    database_name = "registration_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database_name))
        )
    options = psycopg.conninfo.conninfo_to_dict(dsn)
    options["dbname"] = database_name
    isolated = psycopg.conninfo.make_conninfo(**options)
    # asyncpg accepts URI DSNs, not libpq's key=value representation.
    parsed = urlsplit(dsn)
    migration_dsn = urlunsplit(parsed._replace(path="/" + database_name))
    try:
        asyncio.run(apply_migrations(migration_dsn))
        yield isolated, migration_dsn
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name))
            )


def grants(connection):
    return connection.execute("""SELECT principal_id::text,capability_key
        FROM access_control.platform_capability_grants WHERE active""").fetchall()


@pytest.mark.integration
def test_fresh_migrations_leave_registration_open_and_first_account_can_administer(database):
    dsn, migration_dsn = database
    asyncio.run(apply_migrations(migration_dsn))
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT count(*) FROM access_control.accounts").fetchone()[0] == 0
        assert not grants(connection)
    service = AccessService(PostgresAccessRepository(lambda: psycopg.connect(dsn)))
    password = "First database registration passphrase!"
    owner = service.register(
        RegistrationCommand(username="chosen-owner", password=password), request_id="owner"
    )
    session = service.login(
        LoginCommand(username="chosen-owner", password=password), request_id="login"
    )
    assert (
        set(service.bootstrap(session.access_token).platform_capabilities)
        == PLATFORM_ADMIN_CAPABILITIES
    )
    member = service.register(
        RegistrationCommand(username="second-member", password=password), request_id="member"
    )
    with psycopg.connect(dsn) as connection:
        rows = grants(connection)
        assert {row[0] for row in rows} == {owner.principal.principal_id}
        assert member.principal.principal_id != owner.principal.principal_id
        assert {row[1] for row in rows} == PLATFORM_ADMIN_CAPABILITIES


@pytest.mark.integration
def test_concurrent_first_registrations_grant_exactly_one_admin(database):
    dsn, _ = database
    barrier = Barrier(6)

    def contender(index):
        repository = PostgresAccessRepository(lambda: psycopg.connect(dsn))
        barrier.wait(timeout=10)
        return register(repository, f"contender-{index}")

    with ThreadPoolExecutor(max_workers=6) as pool:
        accounts = tuple(pool.map(contender, range(6)))
    with psycopg.connect(dsn) as connection:
        rows = grants(connection)
        assert len({row[0] for row in rows}) == 1
        assert {row[1] for row in rows} == PLATFORM_ADMIN_CAPABILITIES
        assert connection.execute("SELECT count(*) FROM access_control.accounts").fetchone()[
            0
        ] == len(accounts)
        assert (
            connection.execute(
                "SELECT count(*) FROM access_control.accounts WHERE capability_revision=1"
            ).fetchone()[0]
            == 1
        )


@pytest.mark.integration
def test_failed_first_registration_rolls_back_admin_and_allows_retry(database):
    dsn, _ = database

    class FailedAudit(PostgresAccessRepository):
        def _audit(self, *args, **kwargs):
            raise RuntimeError("injected audit failure")

    with pytest.raises(RuntimeError, match="injected audit failure"):
        register(FailedAudit(lambda: psycopg.connect(dsn)), "failed-owner")
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT count(*) FROM access_control.accounts").fetchone()[0] == 0
        assert not grants(connection)
    owner = register(PostgresAccessRepository(lambda: psycopg.connect(dsn)), "retry-owner")
    with psycopg.connect(dsn) as connection:
        assert {row[0] for row in grants(connection)} == {owner.principal_id}


@pytest.mark.integration
def test_existing_accounts_without_active_admin_never_promote_later_registrations(database):
    dsn, _ = database
    repository = PostgresAccessRepository(lambda: psycopg.connect(dsn))
    register(repository, "previous-owner")
    with psycopg.connect(dsn) as connection:
        connection.execute("UPDATE access_control.accounts SET status='DISABLED'")
        connection.execute("""UPDATE access_control.platform_capability_grants
            SET active=false, revoked_at=now(), revoked_by='fixture'""")
    register(repository, "later-member")
    with psycopg.connect(dsn) as connection:
        assert not grants(connection)


@pytest.mark.integration
def test_historical_seeded_admin_is_preserved_on_upgrade(database):
    dsn, migration_dsn = database
    old_seed = Path(__file__).parents[2] / "migrations/security/023_default_admin.sql"
    with psycopg.connect(dsn) as connection:
        connection.execute(old_seed.read_text())
        before = sorted(grants(connection))
        assert before
    asyncio.run(apply_migrations(migration_dsn))
    register(PostgresAccessRepository(lambda: psycopg.connect(dsn)), "later-member")
    with psycopg.connect(dsn) as connection:
        assert sorted(grants(connection)) == before
