from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import asyncpg

_LOCK_NAME = "hc-data-platform-schema-migrations-v1"
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


async def apply_migrations(dsn: str) -> list[str]:
    migrations = load_migrations()
    connection = await asyncpg.connect(_asyncpg_dsn(dsn))
    applied_now: list[str] = []
    try:
        async with connection.transaction():
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", _LOCK_NAME
            )
            await connection.execute(_TRACKING_TABLE_SQL)
            applied = await applied_migration_checksums(connection)
            expected_versions = {migration.version for migration in migrations}
            unknown = sorted(set(applied) - expected_versions)
            if unknown:
                raise RuntimeError(
                    f"database contains migrations absent from this image: {unknown}"
                )

            for migration in migrations:
                previous_checksum = applied.get(migration.version)
                if previous_checksum is not None:
                    if previous_checksum != migration.checksum_sha256:
                        raise RuntimeError(f"applied migration checksum drift: {migration.version}")
                    continue
                await connection.execute(migration.sql)
                await connection.execute(
                    """
                    INSERT INTO core.schema_migrations (version, checksum_sha256)
                    VALUES ($1, $2)
                    """,
                    migration.version,
                    migration.checksum_sha256,
                )
                applied_now.append(migration.version)

            # The security migration is intentionally repeatable. Running it after every
            # module migration installs FORCE RLS policies on newly introduced tenant tables.
            await connection.execute(migrations[0].sql)
    finally:
        await connection.close()
    return applied_now


async def migration_status(dsn: str) -> dict[str, object]:
    expected = expected_migration_checksums()
    connection = await asyncpg.connect(_asyncpg_dsn(dsn))
    try:
        applied = await applied_migration_checksums(connection)
    finally:
        await connection.close()
    missing = sorted(set(expected) - set(applied))
    unknown = sorted(set(applied) - set(expected))
    checksum_drift = sorted(
        version for version in set(expected) & set(applied) if expected[version] != applied[version]
    )
    return {
        "status": "current"
        if not missing and not unknown and not checksum_drift
        else "not_current",
        "expected": len(expected),
        "applied": len(applied),
        "missing": missing,
        "unknown": unknown,
        "checksum_drift": checksum_drift,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HC Data Platform forward-only schema migrations")
    parser.add_argument("command", choices=("upgrade", "status"), nargs="?", default="upgrade")
    return parser


def main() -> None:
    args = _parser().parse_args()
    dsn = os.getenv("HC_POSTGRES_DSN")
    if not dsn:
        raise SystemExit("HC_POSTGRES_DSN is required")
    if args.command == "upgrade":
        applied = asyncio.run(apply_migrations(dsn))
        print(json.dumps({"status": "current", "applied_now": applied}, sort_keys=True))
        return
    status = asyncio.run(migration_status(dsn))
    print(json.dumps(status, sort_keys=True))
    if status["status"] != "current":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
