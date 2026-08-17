from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from hc_data_platform.core import migrations as migration_module
from hc_data_platform.core.migrations import load_migrations, migration_status


def test_manifest_is_ordered_unique_and_checksum_bound() -> None:
    migrations = load_migrations()
    assert migrations[0].version == "security/001_core.sql"
    assert len({migration.version for migration in migrations}) == len(migrations)
    for migration in migrations:
        assert migration.checksum_sha256 == hashlib.sha256(migration.path.read_bytes()).hexdigest()


def test_manifest_rejects_path_escape(tmp_path: Path) -> None:
    (tmp_path / "manifest.txt").write_text("../outside.sql\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="invalid migration path"):
        load_migrations(tmp_path)


@pytest.mark.asyncio
async def test_migration_status_rejects_unknown_applied_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeConnection:
        async def fetchval(self, query: str) -> bool:
            del query
            return True

        async def fetch(self, query: str) -> list[dict[str, str]]:
            del query
            return [
                {"version": "known.sql", "checksum_sha256": "known-checksum"},
                {"version": "unknown.sql", "checksum_sha256": "unknown-checksum"},
            ]

        async def close(self) -> None:
            return None

    async def connect(dsn: str) -> FakeConnection:
        del dsn
        return FakeConnection()

    monkeypatch.setattr(migration_module.asyncpg, "connect", connect)
    monkeypatch.setattr(
        migration_module,
        "expected_migration_checksums",
        lambda: {"known.sql": "known-checksum"},
    )

    status = await migration_status("postgresql://localhost/test")

    assert status["status"] == "not_current"
    assert status["unknown"] == ["unknown.sql"]
