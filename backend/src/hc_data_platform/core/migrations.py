from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import asyncpg

_LOCK_NAME = "hc-data-platform-schema-migrations-v1"
_SESSION_LIFECYCLE_VERSION = "security/006_session_lifecycle.sql"
_SESSION_LIFECYCLE_CHECKSUM = "510db7b18bc9fbfa71904bdc97a5a611c4445e7c923af1806ec2c7fbdf4bbb7d"
_SESSION_LIFECYCLE_INDEX = "access_sessions_active_lifecycle_idx"
_SESSION_LIFECYCLE_NOT_NULL_CHECK = "sessions_last_seen_at_online_not_null"
_SESSION_LIFECYCLE_BACKFILL_BATCH_SIZE = 1_000
_SESSION_LIFECYCLE_BACKFILL_NO_PROGRESS_LIMIT = 100
_SESSION_LIFECYCLE_BACKFILL_RETRY_SECONDS = 0.05
_MIGRATION_DDL_LOCK_TIMEOUT = "5s"
_REPEATABLE_SECURITY_MIGRATIONS = frozenset(
    {
        "security/001_core.sql",
        "security/002_access_control.sql",
        "security/012_organization_aware_rls.sql",
        "security/019_product_organization_scope.sql",
        "security/020_platform_super_admin.sql",
    }
)
_TRACKING_TABLE_SQL = """
CREATE SCHEMA IF NOT EXISTS core;
CREATE TABLE IF NOT EXISTS core.schema_migrations (
    version text PRIMARY KEY,
    checksum_sha256 char(64) NOT NULL CHECK (checksum_sha256 ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


@dataclass(frozen=True, slots=True)
class Migration:
    version: str
    path: Path
    checksum_sha256: str
    sql: str
    phase: Literal["expand", "contract"] = "expand"


@dataclass(frozen=True, slots=True)
class _SessionLifecycleIndexState:
    target_relation: bool
    valid: bool
    ready: bool
    unique: bool
    access_method: str
    key_count: int
    total_count: int
    columns: tuple[str, ...]
    predicate: str


def migrations_root() -> Path:
    configured = os.getenv("HC_MIGRATIONS_DIR")
    if configured:
        root = Path(configured).resolve()
        if not (root / "manifest.txt").is_file():
            raise RuntimeError(f"migration manifest not found under {root}")
        return root

    for parent in Path(__file__).resolve().parents:
        candidate = parent / "migrations"
        if (candidate / "manifest.txt").is_file():
            return candidate
    raise RuntimeError("migration manifest not found; set HC_MIGRATIONS_DIR")


def load_migrations(root: Path | None = None) -> tuple[Migration, ...]:
    resolved_root = (root or migrations_root()).resolve()
    manifest = resolved_root / "manifest.txt"
    versions = [
        line.strip()
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(versions) != len(set(versions)):
        raise RuntimeError("migration manifest contains duplicate versions")
    for version in versions:
        path = (resolved_root / version).resolve()
        if resolved_root not in path.parents or path.suffix != ".sql" or not path.is_file():
            raise RuntimeError(f"invalid migration path in manifest: {version}")

    phases_path = resolved_root / "phases.json"
    try:
        phases = json.loads(phases_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("migration phase manifest is missing or invalid") from exc
    if phases.get("format_version") != "hc-migration-phases/v1" or set(phases) != {
        "format_version",
        "contract_migrations",
    }:
        raise RuntimeError("migration phase manifest has an unsupported contract")
    raw_contract = phases["contract_migrations"]
    if not isinstance(raw_contract, list) or any(
        not isinstance(item, str) for item in raw_contract
    ):
        raise RuntimeError("contract_migrations must be a list of migration versions")
    contract_versions = set(raw_contract)
    if len(contract_versions) != len(raw_contract) or not contract_versions <= set(versions):
        raise RuntimeError("contract migration phase entries must be unique manifest versions")

    migrations: list[Migration] = []
    for version in versions:
        path = (resolved_root / version).resolve()
        if resolved_root not in path.parents or path.suffix != ".sql" or not path.is_file():
            raise RuntimeError(f"invalid migration path in manifest: {version}")
        sql = path.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=version,
                path=path,
                checksum_sha256=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
                sql=sql,
                phase="contract" if version in contract_versions else "expand",
            )
        )
    if not migrations or migrations[0].version != "security/001_core.sql":
        raise RuntimeError("security/001_core.sql must be the first migration")
    return tuple(migrations)


@lru_cache(maxsize=1)
def expected_migration_checksums() -> dict[str, str]:
    return {migration.version: migration.checksum_sha256 for migration in load_migrations()}


def _asyncpg_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)


async def applied_migration_checksums(connection: asyncpg.Connection[Any]) -> dict[str, str]:
    exists = await connection.fetchval("SELECT to_regclass('core.schema_migrations') IS NOT NULL")
    if not exists:
        return {}
    rows = await connection.fetch(
        "SELECT version, checksum_sha256 FROM core.schema_migrations ORDER BY version"
    )
    return {str(row["version"]): str(row["checksum_sha256"]) for row in rows}


async def _record_migration(connection: asyncpg.Connection[Any], migration: Migration) -> None:
    await connection.execute(
        """
        INSERT INTO core.schema_migrations (version, checksum_sha256)
        VALUES ($1, $2)
        """,
        migration.version,
        migration.checksum_sha256,
    )


async def _set_local_ddl_lock_timeout(connection: asyncpg.Connection[Any]) -> None:
    await connection.execute(
        "SELECT set_config('lock_timeout', $1, true)",
        _MIGRATION_DDL_LOCK_TIMEOUT,
    )


def _normalized_catalog_expression(expression: str) -> str:
    normalized = "".join(expression.lower().split())
    while normalized.startswith("(") and normalized.endswith(")"):
        depth = 0
        outer_pair_encloses_expression = True
        for position, character in enumerate(normalized):
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
            if depth == 0 and position != len(normalized) - 1:
                outer_pair_encloses_expression = False
                break
        if not outer_pair_encloses_expression:
            break
        normalized = normalized[1:-1]
    return normalized


def _session_lifecycle_index_matches(state: _SessionLifecycleIndexState) -> bool:
    return (
        state.target_relation
        and not state.unique
        and state.access_method == "btree"
        and state.key_count == 3
        and state.total_count == 5
        and state.columns
        == (
            "principal_id",
            "issued_at",
            "session_id",
            "last_seen_at",
            "credential_revision",
        )
        and _normalized_catalog_expression(state.predicate) == "revoked_atisnull"
    )


async def _session_lifecycle_index_state(
    connection: asyncpg.Connection[Any],
) -> _SessionLifecycleIndexState | None:
    row = await connection.fetchrow(
        """
        SELECT index_state.indisvalid,
               index_state.indrelid = 'access_control.sessions'::regclass
                   AS target_relation,
               index_state.indisready,
               index_state.indisunique,
               access_method.amname AS access_method,
               index_state.indnkeyatts,
               index_state.indnatts,
               ARRAY(
                   SELECT pg_get_indexdef(index_state.indexrelid, position, true)
                   FROM generate_series(1, index_state.indnatts) AS position
                   ORDER BY position
               ) AS columns,
               pg_get_expr(index_state.indpred, index_state.indrelid, true) AS predicate
        FROM pg_index AS index_state
        JOIN pg_class AS index_relation
          ON index_relation.oid = index_state.indexrelid
        JOIN pg_am AS access_method
          ON access_method.oid = index_relation.relam
        WHERE index_state.indexrelid = to_regclass($1)
        """,
        f"access_control.{_SESSION_LIFECYCLE_INDEX}",
    )
    if row is None:
        return None
    return _SessionLifecycleIndexState(
        target_relation=bool(row["target_relation"]),
        valid=bool(row["indisvalid"]),
        ready=bool(row["indisready"]),
        unique=bool(row["indisunique"]),
        access_method=str(row["access_method"]),
        key_count=int(row["indnkeyatts"]),
        total_count=int(row["indnatts"]),
        columns=tuple(str(column) for column in row["columns"]),
        predicate=str(row["predicate"]),
    )


async def _prepare_session_lifecycle_column(connection: asyncpg.Connection[Any]) -> None:
    async with connection.transaction():
        await _set_local_ddl_lock_timeout(connection)
        await connection.execute(
            f"""
            ALTER TABLE access_control.sessions
                ADD COLUMN IF NOT EXISTS last_seen_at timestamptz;

            ALTER TABLE access_control.sessions
                ALTER COLUMN last_seen_at SET DEFAULT now();

            ALTER TABLE access_control.sessions
                DROP CONSTRAINT IF EXISTS {_SESSION_LIFECYCLE_NOT_NULL_CHECK};
            """
        )


async def _install_session_lifecycle_not_null_check(
    connection: asyncpg.Connection[Any],
) -> None:
    async with connection.transaction():
        await _set_local_ddl_lock_timeout(connection)
        await connection.execute(
            f"""
            ALTER TABLE access_control.sessions
                ADD CONSTRAINT {_SESSION_LIFECYCLE_NOT_NULL_CHECK}
                CHECK (last_seen_at IS NOT NULL) NOT VALID;
            """
        )
        constraint = await connection.fetchrow(
            """
            SELECT constraint_state.contype = 'c' AS is_check,
                   pg_get_expr(
                       constraint_state.conbin,
                       constraint_state.conrelid,
                       true
                   ) AS expression
            FROM pg_constraint AS constraint_state
            WHERE constraint_state.conrelid = 'access_control.sessions'::regclass
              AND constraint_state.conname = $1
            """,
            _SESSION_LIFECYCLE_NOT_NULL_CHECK,
        )
        if (
            constraint is None
            or not bool(constraint["is_check"])
            or _normalized_catalog_expression(str(constraint["expression"]))
            != "last_seen_atisnotnull"
        ):
            raise RuntimeError(
                "session lifecycle online helper constraint has an unexpected definition"
            )


async def _backfill_session_lifecycle_column(connection: asyncpg.Connection[Any]) -> None:
    no_progress = 0
    while True:
        async with connection.transaction():
            updated = int(
                await connection.fetchval(
                    """
                    WITH batch AS (
                        SELECT session_id
                        FROM access_control.sessions
                        WHERE last_seen_at IS NULL
                        ORDER BY session_id
                        LIMIT $1
                        FOR UPDATE SKIP LOCKED
                    ), updated AS (
                        UPDATE access_control.sessions AS session
                        SET last_seen_at = session.issued_at
                        FROM batch
                        WHERE session.session_id = batch.session_id
                        RETURNING 1
                    )
                    SELECT count(*) FROM updated
                    """,
                    _SESSION_LIFECYCLE_BACKFILL_BATCH_SIZE,
                )
            )
        if updated > 0:
            no_progress = 0
            continue
        remaining = int(
            await connection.fetchval(
                "SELECT count(*) FROM access_control.sessions WHERE last_seen_at IS NULL"
            )
        )
        if remaining == 0:
            return
        no_progress += 1
        if no_progress >= _SESSION_LIFECYCLE_BACKFILL_NO_PROGRESS_LIMIT:
            raise RuntimeError(
                "session lifecycle backfill made no progress while NULL rows remained"
            )
        await asyncio.sleep(_SESSION_LIFECYCLE_BACKFILL_RETRY_SECONDS)


async def _create_session_lifecycle_index(connection: asyncpg.Connection[Any]) -> None:
    state = await _session_lifecycle_index_state(connection)
    if state is not None:
        if not state.target_relation:
            raise RuntimeError("session lifecycle index has an unexpected target relation")
        if state.valid and state.ready:
            if not _session_lifecycle_index_matches(state):
                raise RuntimeError("session lifecycle index has an unexpected definition")
            return
        await connection.execute(
            "SELECT set_config('lock_timeout', $1, false)",
            _MIGRATION_DDL_LOCK_TIMEOUT,
        )
        try:
            await connection.execute(
                f"DROP INDEX CONCURRENTLY IF EXISTS access_control.{_SESSION_LIFECYCLE_INDEX}"
            )
        finally:
            await connection.execute("RESET lock_timeout")

    await connection.execute(
        "SELECT set_config('lock_timeout', $1, false)",
        _MIGRATION_DDL_LOCK_TIMEOUT,
    )
    try:
        await connection.execute(
            f"""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS {_SESSION_LIFECYCLE_INDEX}
            ON access_control.sessions (principal_id, issued_at, session_id)
            INCLUDE (last_seen_at, credential_revision)
            WHERE revoked_at IS NULL
            """
        )
    finally:
        await connection.execute("RESET lock_timeout")
    state = await _session_lifecycle_index_state(connection)
    if (
        state is None
        or not state.valid
        or not state.ready
        or not _session_lifecycle_index_matches(state)
    ):
        raise RuntimeError("session lifecycle index was not created in a valid state")


async def _finalize_session_lifecycle_column(connection: asyncpg.Connection[Any]) -> None:
    async with connection.transaction():
        await _set_local_ddl_lock_timeout(connection)
        await connection.execute(
            f"""
            ALTER TABLE access_control.sessions
                VALIDATE CONSTRAINT {_SESSION_LIFECYCLE_NOT_NULL_CHECK};
            """
        )
    async with connection.transaction():
        await _set_local_ddl_lock_timeout(connection)
        await connection.execute(
            """
            ALTER TABLE access_control.sessions
                ALTER COLUMN last_seen_at SET NOT NULL;
            """
        )
    async with connection.transaction():
        await _set_local_ddl_lock_timeout(connection)
        await connection.execute(
            f"""
            ALTER TABLE access_control.sessions
                DROP CONSTRAINT IF EXISTS {_SESSION_LIFECYCLE_NOT_NULL_CHECK};
            """
        )


async def _verify_session_lifecycle_schema(connection: asyncpg.Connection[Any]) -> None:
    row = await connection.fetchrow(
        """
        SELECT attribute.attnotnull,
               format_type(attribute.atttypid, attribute.atttypmod) AS data_type,
               pg_get_expr(default_value.adbin, default_value.adrelid) AS default_expression,
               NOT EXISTS (
                   SELECT 1 FROM access_control.sessions WHERE last_seen_at IS NULL
               ) AS fully_backfilled,
               NOT EXISTS (
                   SELECT 1
                   FROM pg_constraint AS helper_constraint
                   WHERE helper_constraint.conrelid = attribute.attrelid
                     AND helper_constraint.conname = $1
               ) AS helper_absent
        FROM pg_attribute AS attribute
        LEFT JOIN pg_attrdef AS default_value
          ON default_value.adrelid = attribute.attrelid
         AND default_value.adnum = attribute.attnum
        WHERE attribute.attrelid = 'access_control.sessions'::regclass
          AND attribute.attname = 'last_seen_at'
          AND NOT attribute.attisdropped
        """,
        _SESSION_LIFECYCLE_NOT_NULL_CHECK,
    )
    index_state = await _session_lifecycle_index_state(connection)
    if (
        row is None
        or not bool(row["attnotnull"])
        or str(row["data_type"]) != "timestamp with time zone"
        or _normalized_catalog_expression(str(row["default_expression"])) != "now()"
        or not bool(row["fully_backfilled"])
        or not bool(row["helper_absent"])
        or index_state is None
        or not index_state.valid
        or not index_state.ready
        or not _session_lifecycle_index_matches(index_state)
    ):
        raise RuntimeError("session lifecycle online migration did not reach the expected schema")


async def _apply_session_lifecycle_online(
    connection: asyncpg.Connection[Any], migration: Migration
) -> None:
    if migration.checksum_sha256 != _SESSION_LIFECYCLE_CHECKSUM:
        raise RuntimeError(
            "security/006_session_lifecycle.sql does not match its online executor checksum"
        )
    await _prepare_session_lifecycle_column(connection)
    await _backfill_session_lifecycle_column(connection)
    await _create_session_lifecycle_index(connection)
    await _install_session_lifecycle_not_null_check(connection)
    await _finalize_session_lifecycle_column(connection)
    await _verify_session_lifecycle_schema(connection)
    async with connection.transaction():
        await _record_migration(connection, migration)


async def _apply_pending_migration(
    connection: asyncpg.Connection[Any], migration: Migration
) -> None:
    if migration.version == _SESSION_LIFECYCLE_VERSION:
        await _apply_session_lifecycle_online(connection, migration)
        return
    async with connection.transaction():
        await connection.execute(migration.sql)
        await _record_migration(connection, migration)


async def apply_migrations(
    dsn: str,
    *,
    phase: Literal["expand", "contract"] = "expand",
    contract_approval_digest: str | None = None,
) -> list[str]:
    migrations = tuple(migration for migration in load_migrations() if migration.phase == phase)
    if phase == "contract" and (
        contract_approval_digest is None
        or re.fullmatch(r"sha256:[0-9a-f]{64}", contract_approval_digest) is None
    ):
        raise RuntimeError("contract migrations require a signed approval digest")
    connection = await asyncpg.connect(_asyncpg_dsn(dsn))
    applied_now: list[str] = []
    lock_acquired = False
    try:
        await connection.execute("SELECT pg_advisory_lock(hashtextextended($1, 0))", _LOCK_NAME)
        lock_acquired = True
        async with connection.transaction():
            await connection.execute(_TRACKING_TABLE_SQL)
        applied = await applied_migration_checksums(connection)
        expected_versions = {migration.version for migration in load_migrations()}
        unknown = sorted(set(applied) - expected_versions)
        if unknown:
            raise RuntimeError(f"database contains migrations absent from this image: {unknown}")

        for migration in migrations:
            previous_checksum = applied.get(migration.version)
            if previous_checksum is not None:
                if previous_checksum != migration.checksum_sha256:
                    raise RuntimeError(f"applied migration checksum drift: {migration.version}")
                continue
            await _apply_pending_migration(connection, migration)
            applied[migration.version] = migration.checksum_sha256
            applied_now.append(migration.version)

        if phase == "expand":
            # Security reconciliation is intentionally repeatable during expand. The
            # separate contract command may only execute entries explicitly assigned
            # to the contract phase; it must not replay unrelated DDL as a side effect.
            repeatable = {
                migration.version: migration
                for migration in load_migrations()
                if migration.version in _REPEATABLE_SECURITY_MIGRATIONS
            }
            missing_repeatable = _REPEATABLE_SECURITY_MIGRATIONS - repeatable.keys()
            if missing_repeatable:
                raise RuntimeError(
                    f"repeatable security migrations missing: {sorted(missing_repeatable)}"
                )
            async with connection.transaction():
                for version in (
                    "security/001_core.sql",
                    "security/012_organization_aware_rls.sql",
                    "security/019_product_organization_scope.sql",
                    "security/002_access_control.sql",
                    "security/020_platform_super_admin.sql",
                ):
                    repeatable_migration = repeatable.get(version)
                    if repeatable_migration is not None:
                        await connection.execute(repeatable_migration.sql)
    finally:
        if lock_acquired:
            await connection.execute(
                "SELECT pg_advisory_unlock(hashtextextended($1, 0))", _LOCK_NAME
            )
        await connection.close()
    return applied_now


async def migration_status(
    dsn: str, *, phase: Literal["expand", "contract"] = "expand"
) -> dict[str, object]:
    selected = tuple(migration for migration in load_migrations() if migration.phase == phase)
    expected = {migration.version: migration.checksum_sha256 for migration in selected}
    all_expected = expected_migration_checksums()
    connection = await asyncpg.connect(_asyncpg_dsn(dsn))
    try:
        applied = await applied_migration_checksums(connection)
    finally:
        await connection.close()
    missing = sorted(set(expected) - set(applied))
    unknown = sorted(set(applied) - set(all_expected))
    checksum_drift = sorted(
        version
        for version in set(all_expected) & set(applied)
        if all_expected[version] != applied[version]
    )
    applied_in_phase = len(set(applied) & set(expected))
    return {
        "status": "current"
        if not missing and not unknown and not checksum_drift
        else "not_current",
        "expected": len(expected),
        "applied": applied_in_phase,
        "missing": missing,
        "unknown": unknown,
        "checksum_drift": checksum_drift,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HC Data Platform forward-only schema migrations")
    parser.add_argument(
        "command",
        choices=("upgrade", "upgrade-expand", "upgrade-contract", "status", "status-contract"),
        nargs="?",
        default="upgrade-expand",
    )
    parser.add_argument("--approval-digest")
    return parser


def main() -> None:
    args = _parser().parse_args()
    dsn = os.getenv("HC_POSTGRES_DSN")
    if not dsn:
        raise SystemExit("HC_POSTGRES_DSN is required")
    phase: Literal["expand", "contract"] = (
        "contract" if args.command in {"upgrade-contract", "status-contract"} else "expand"
    )
    if args.command in {"upgrade", "upgrade-expand", "upgrade-contract"}:
        applied = asyncio.run(
            apply_migrations(
                dsn,
                phase=phase,
                contract_approval_digest=args.approval_digest,
            )
        )
        print(
            json.dumps(
                {"status": "current", "phase": phase, "applied_now": applied}, sort_keys=True
            )
        )
        return
    status = asyncio.run(migration_status(dsn, phase=phase))
    print(json.dumps(status, sort_keys=True))
    if status["status"] != "current":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
