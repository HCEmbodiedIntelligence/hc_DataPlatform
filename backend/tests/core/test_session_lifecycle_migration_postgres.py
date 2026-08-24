from __future__ import annotations

import asyncio
import hashlib
import os
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

import asyncpg
import psycopg
import pytest
from psycopg import sql

from hc_data_platform.core import migrations as migration_module
from hc_data_platform.core.dbapi import normalize_postgres_dsn
from hc_data_platform.core.migrations import apply_migrations, load_migrations, migration_status

pytestmark = pytest.mark.integration

_SESSION_LIFECYCLE_VERSION = "security/006_session_lifecycle.sql"
_SESSION_LIFECYCLE_CHECKSUM = "510db7b18bc9fbfa71904bdc97a5a611c4445e7c923af1806ec2c7fbdf4bbb7d"
_BACKFILL_BLOCK_LOCK = 8_260_060_001


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


@pytest.fixture
def isolated_session_migration_dsn() -> Iterator[str]:
    base_dsn = _source_dsn()
    database_name = f"hc_session_006_{uuid4().hex[:12]}"
    with psycopg.connect(base_dsn, autocommit=True) as admin:
        try:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        except psycopg.errors.InsufficientPrivilege:
            pytest.skip("HC_TEST_POSTGRES_DSN role cannot create an isolated database")
    isolated_dsn = _database_dsn(base_dsn, database_name)
    try:
        yield isolated_dsn
    finally:
        with psycopg.connect(base_dsn, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (database_name,),
            )
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))


async def _bootstrap_through_session_migration_005(dsn: str) -> None:
    migrations = load_migrations()
    lifecycle_position = next(
        index
        for index, migration in enumerate(migrations)
        if migration.version == _SESSION_LIFECYCLE_VERSION
    )
    connection = await asyncpg.connect(normalize_postgres_dsn(dsn))
    try:
        async with connection.transaction():
            await connection.execute(migration_module._TRACKING_TABLE_SQL)
        for migration in migrations[:lifecycle_position]:
            async with connection.transaction():
                await connection.execute(migration.sql)
                await connection.execute(
                    """
                    INSERT INTO core.schema_migrations (version, checksum_sha256)
                    VALUES ($1, $2)
                    """,
                    migration.version,
                    migration.checksum_sha256,
                )
    finally:
        await connection.close()


def _seed_populated_sessions(dsn: str, *, count: int = 64) -> tuple[UUID, tuple[UUID, ...]]:
    principal_id = uuid4()
    session_ids = tuple(uuid4() for _ in range(count))
    issued_at = datetime(2026, 8, 20, 8, tzinfo=timezone.utc)
    with psycopg.connect(dsn) as connection:
        connection.execute(
            """
            INSERT INTO access_control.accounts (
                principal_id, canonical_username, display_username, display_name,
                status, password_hash, credential_revision, password_changed_at
            ) VALUES (%s, %s, %s, %s, 'ACTIVE', %s, 1, %s)
            """,
            (
                principal_id,
                f"migration-{principal_id.hex}",
                f"migration-{principal_id.hex}",
                "Migration Test",
                "test-password-hash",
                issued_at,
            ),
        )
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO access_control.sessions (
                    session_id, principal_id, token_hash, issued_at, credential_revision
                ) VALUES (%s, %s, %s, %s, 1)
                """,
                (
                    (
                        session_id,
                        principal_id,
                        hashlib.sha256(session_id.bytes).hexdigest(),
                        issued_at + timedelta(seconds=offset),
                    )
                    for offset, session_id in enumerate(session_ids)
                ),
            )
    return principal_id, session_ids


def _has_lifecycle_ledger_row(dsn: str) -> bool:
    with psycopg.connect(dsn) as connection:
        return bool(
            connection.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM core.schema_migrations WHERE version = %s
                )
                """,
                (_SESSION_LIFECYCLE_VERSION,),
            ).fetchone()[0]
        )


def _install_backfill_trigger(dsn: str, body: str) -> None:
    with psycopg.connect(dsn) as connection:
        connection.execute(
            f"""
            CREATE OR REPLACE FUNCTION access_control.test_session_006_backfill_hook()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $function$
            BEGIN
                {body}
                RETURN NEW;
            END
            $function$;

            DROP TRIGGER IF EXISTS test_session_006_backfill_hook
                ON access_control.sessions;
            CREATE TRIGGER test_session_006_backfill_hook
            BEFORE UPDATE ON access_control.sessions
            FOR EACH ROW
            EXECUTE FUNCTION access_control.test_session_006_backfill_hook();
            """
        )


def _drop_backfill_trigger(dsn: str) -> None:
    with psycopg.connect(dsn) as connection:
        connection.execute(
            """
            DROP TRIGGER IF EXISTS test_session_006_backfill_hook
                ON access_control.sessions;
            DROP FUNCTION IF EXISTS access_control.test_session_006_backfill_hook();
            """
        )


def test_populated_005_to_006_upgrade_is_online_bounded_and_resumable(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_migration_dsn: str,
) -> None:
    dsn = isolated_session_migration_dsn
    asyncio.run(_bootstrap_through_session_migration_005(dsn))
    principal_id, historical_session_ids = _seed_populated_sessions(dsn)
    migrations = load_migrations()
    lifecycle_position = next(
        index
        for index, migration in enumerate(migrations)
        if migration.version == _SESSION_LIFECYCLE_VERSION
    )
    expected_pending = tuple(migration.version for migration in migrations[lifecycle_position:])
    assert expected_pending[0] == _SESSION_LIFECYCLE_VERSION
    assert asyncio.run(migration_status(dsn)) == {
        "status": "not_current",
        "expected": len(migrations),
        "applied": lifecycle_position,
        "missing": sorted(expected_pending),
        "unknown": [],
        "checksum_drift": [],
    }

    # A long reader cannot make the initial ACCESS EXCLUSIVE lock wait forever.
    monkeypatch.setattr(migration_module, "_MIGRATION_DDL_LOCK_TIMEOUT", "250ms")
    with psycopg.connect(dsn) as reader:
        reader.execute("SELECT count(*) FROM access_control.sessions").fetchone()
        with pytest.raises(asyncpg.exceptions.LockNotAvailableError):
            asyncio.run(apply_migrations(dsn))
    assert not _has_lifecycle_ledger_row(dsn)
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            """
            SELECT NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'access_control'
                  AND table_name = 'sessions'
                  AND column_name = 'last_seen_at'
            )
            """
        ).fetchone()[0]

    # A failure after the short DDL phase leaves resumable state but no ledger lie.
    monkeypatch.setattr(migration_module, "_MIGRATION_DDL_LOCK_TIMEOUT", "5s")
    _install_backfill_trigger(dsn, "RAISE EXCEPTION 'injected session 006 interruption';")
    with pytest.raises(asyncpg.exceptions.RaiseError, match="injected session 006 interruption"):
        asyncio.run(apply_migrations(dsn))
    assert not _has_lifecycle_ledger_row(dsn)
    with psycopg.connect(dsn) as connection:
        not_null, null_rows, helper_exists = connection.execute(
            """
            SELECT attribute.attnotnull,
                   (SELECT count(*) FROM access_control.sessions WHERE last_seen_at IS NULL),
                   EXISTS (
                       SELECT 1 FROM pg_constraint
                       WHERE conrelid = attribute.attrelid
                         AND conname = 'sessions_last_seen_at_online_not_null'
                   )
            FROM pg_attribute AS attribute
            WHERE attribute.attrelid = 'access_control.sessions'::regclass
              AND attribute.attname = 'last_seen_at'
            """
        ).fetchone()
    assert not_null is False
    assert null_rows == len(historical_session_ids)
    assert helper_exists is False
    _drop_backfill_trigger(dsn)

    # During the retry's batch backfill, the initial DDL lock has been committed:
    # an old-shape writer can update a historical NULL row, while an insert receives
    # the new default. The batch is one row so the maximum UUID remains unlocked.
    monkeypatch.setattr(migration_module, "_SESSION_LIFECYCLE_BACKFILL_BATCH_SIZE", 1)
    _install_backfill_trigger(
        dsn,
        (
            "IF OLD.last_seen_at IS NULL AND NEW.last_seen_at IS NOT NULL THEN "
            f"PERFORM pg_advisory_xact_lock({_BACKFILL_BLOCK_LOCK}); "
            "END IF;"
        ),
    )
    concurrent_session_id = uuid4()
    with psycopg.connect(dsn) as connection:
        old_shape_update_session_id = connection.execute(
            "SELECT session_id FROM access_control.sessions ORDER BY session_id DESC LIMIT 1"
        ).fetchone()[0]
    with psycopg.connect(dsn, autocommit=True) as blocker:
        blocker.execute("SELECT pg_advisory_lock(%s)", (_BACKFILL_BLOCK_LOCK,))
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(lambda: asyncio.run(apply_migrations(dsn)))
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                blocked = blocker.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_stat_activity
                        WHERE datname = current_database()
                          AND pid <> pg_backend_pid()
                          AND query LIKE '%SET last_seen_at = session.issued_at%'
                          AND wait_event_type = 'Lock'
                    )
                    """
                ).fetchone()[0]
                if blocked:
                    break
                if future.done():
                    future.result()
                time.sleep(0.02)
            else:
                pytest.fail("session 006 backfill did not reach the blocked batch phase")

            assert not _has_lifecycle_ledger_row(dsn)
            access_exclusive_locks = blocker.execute(
                """
                SELECT count(*)
                FROM pg_locks
                WHERE relation = 'access_control.sessions'::regclass
                  AND mode = 'AccessExclusiveLock'
                  AND granted
                """
            ).fetchone()[0]
            assert access_exclusive_locks == 0

            with psycopg.connect(dsn) as writer:
                old_update_last_seen = writer.execute(
                    """
                    UPDATE access_control.sessions
                    SET revoked_at = clock_timestamp(), revocation_reason = 'LOGOUT'
                    WHERE session_id = %s
                    RETURNING last_seen_at
                    """,
                    (old_shape_update_session_id,),
                ).fetchone()[0]
                inserted_last_seen = writer.execute(
                    """
                    INSERT INTO access_control.sessions (
                        session_id, principal_id, token_hash, credential_revision
                    ) VALUES (%s, %s, %s, 1)
                    RETURNING last_seen_at
                    """,
                    (
                        concurrent_session_id,
                        principal_id,
                        hashlib.sha256(concurrent_session_id.bytes).hexdigest(),
                    ),
                ).fetchone()[0]
            assert old_update_last_seen is None
            assert inserted_last_seen is not None
            blocker.execute("SELECT pg_advisory_unlock(%s)", (_BACKFILL_BLOCK_LOCK,))
            assert tuple(future.result(timeout=30)) == expected_pending
        finally:
            blocker.execute("SELECT pg_advisory_unlock(%s)", (_BACKFILL_BLOCK_LOCK,))
            executor.shutdown(wait=True, cancel_futures=True)

    _drop_backfill_trigger(dsn)
    with psycopg.connect(dsn) as connection:
        historical_backfilled = connection.execute(
            """
            SELECT count(*)
            FROM access_control.sessions
            WHERE session_id = ANY(%s)
              AND last_seen_at = issued_at
            """,
            (list(historical_session_ids),),
        ).fetchone()[0]
        column_state = connection.execute(
            """
            SELECT attribute.attnotnull,
                   format_type(attribute.atttypid, attribute.atttypmod),
                   pg_get_expr(default_value.adbin, default_value.adrelid)
            FROM pg_attribute AS attribute
            JOIN pg_attrdef AS default_value
              ON default_value.adrelid = attribute.attrelid
             AND default_value.adnum = attribute.attnum
            WHERE attribute.attrelid = 'access_control.sessions'::regclass
              AND attribute.attname = 'last_seen_at'
            """
        ).fetchone()
        index_state = connection.execute(
            """
            SELECT index_state.indisvalid, index_state.indisready,
                   index_state.indisunique, access_method.amname,
                   index_state.indnkeyatts, index_state.indnatts,
                   ARRAY(
                       SELECT pg_get_indexdef(index_state.indexrelid, position, true)
                       FROM generate_series(1, index_state.indnatts) AS position
                       ORDER BY position
                   ),
                   pg_get_expr(index_state.indpred, index_state.indrelid, true)
            FROM pg_index AS index_state
            JOIN pg_class AS index_relation ON index_relation.oid = index_state.indexrelid
            JOIN pg_am AS access_method ON access_method.oid = index_relation.relam
            WHERE index_state.indexrelid =
                'access_control.access_sessions_active_lifecycle_idx'::regclass
            """
        ).fetchone()
        ledger_checksum = connection.execute(
            "SELECT checksum_sha256 FROM core.schema_migrations WHERE version = %s",
            (_SESSION_LIFECYCLE_VERSION,),
        ).fetchone()[0]
        helper_exists = connection.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'access_control.sessions'::regclass
                  AND conname = 'sessions_last_seen_at_online_not_null'
            )
            """
        ).fetchone()[0]

    assert historical_backfilled == len(historical_session_ids)
    assert column_state == (True, "timestamp with time zone", "now()")
    assert index_state == (
        True,
        True,
        False,
        "btree",
        3,
        5,
        [
            "principal_id",
            "issued_at",
            "session_id",
            "last_seen_at",
            "credential_revision",
        ],
        "revoked_at IS NULL",
    )
    assert ledger_checksum == _SESSION_LIFECYCLE_CHECKSUM
    assert helper_exists is False
    assert asyncio.run(migration_status(dsn))["status"] == "current"
    assert asyncio.run(apply_migrations(dsn)) == []


@pytest.mark.parametrize("invalid_decoy", (False, True), ids=("valid-decoy", "invalid-decoy"))
def test_session_006_rejects_decoy_index_and_verifier_rejects_leftover_helper(
    isolated_session_migration_dsn: str,
    invalid_decoy: bool,
) -> None:
    dsn = isolated_session_migration_dsn
    asyncio.run(_bootstrap_through_session_migration_005(dsn))
    migrations = load_migrations()
    lifecycle_position = next(
        index
        for index, migration in enumerate(migrations)
        if migration.version == _SESSION_LIFECYCLE_VERSION
    )
    expected_pending = tuple(migration.version for migration in migrations[lifecycle_position:])

    with psycopg.connect(dsn) as connection:
        connection.execute(
            """
            CREATE TABLE access_control.session_lifecycle_decoy (
                principal_id uuid NOT NULL,
                issued_at timestamptz NOT NULL,
                session_id uuid NOT NULL,
                last_seen_at timestamptz NOT NULL,
                credential_revision bigint NOT NULL,
                revoked_at timestamptz
            );
            CREATE INDEX access_sessions_active_lifecycle_idx
            ON access_control.session_lifecycle_decoy (
                principal_id, issued_at, session_id
            )
            INCLUDE (last_seen_at, credential_revision)
            WHERE revoked_at IS NULL;
            """
        )
        if invalid_decoy:
            connection.execute(
                """
                UPDATE pg_index
                SET indisvalid = false, indisready = false
                WHERE indexrelid =
                    'access_control.access_sessions_active_lifecycle_idx'::regclass
                """
            )

    with pytest.raises(RuntimeError, match="index has an unexpected target relation"):
        asyncio.run(apply_migrations(dsn))
    assert not _has_lifecycle_ledger_row(dsn)
    with psycopg.connect(dsn) as connection:
        decoy_index_state = connection.execute(
            """
            SELECT target_relation.relname, index_state.indisvalid, index_state.indisready
            FROM pg_index AS index_state
            JOIN pg_class AS target_relation
              ON target_relation.oid = index_state.indrelid
            WHERE index_state.indexrelid =
                'access_control.access_sessions_active_lifecycle_idx'::regclass
            """
        ).fetchone()
    assert decoy_index_state == (
        "session_lifecycle_decoy",
        not invalid_decoy,
        not invalid_decoy,
    )

    with psycopg.connect(dsn) as connection:
        connection.execute(
            """
            DROP INDEX access_control.access_sessions_active_lifecycle_idx;
            DROP TABLE access_control.session_lifecycle_decoy;
            """
        )
    assert tuple(asyncio.run(apply_migrations(dsn))) == expected_pending

    with psycopg.connect(dsn) as connection:
        connection.execute(
            """
            ALTER TABLE access_control.sessions
                ADD CONSTRAINT sessions_last_seen_at_online_not_null
                CHECK (last_seen_at IS NOT NULL) NOT VALID
            """
        )

    async def verify_schema() -> None:
        connection = await asyncpg.connect(normalize_postgres_dsn(dsn))
        try:
            await migration_module._verify_session_lifecycle_schema(connection)
        finally:
            await connection.close()

    with pytest.raises(RuntimeError, match="did not reach the expected schema"):
        asyncio.run(verify_schema())
    with psycopg.connect(dsn) as connection:
        helper_still_exists = connection.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'access_control.sessions'::regclass
                  AND conname = 'sessions_last_seen_at_online_not_null'
            )
            """
        ).fetchone()[0]
    assert helper_still_exists is True
