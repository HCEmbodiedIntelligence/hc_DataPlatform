from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from hc_data_platform.core import migrations as migration_module
from hc_data_platform.core.migrations import (
    Migration,
    apply_migrations,
    load_migrations,
    migration_status,
)

HISTORICAL_CHECKSUMS = {
    "annotation/0002_tag_schema_revisions.sql": (
        "3f033d4af5fbb53140c8e98e30b5c3d88e4c9ddf71157ecbc28d678a8a8a1ccd"
    ),
    "storage/0001_storage_governance.sql": (
        "4f07ef2d609958203a2af30a7ed37cebff3eb5cb8d21755a4c22b90fb36f5e8f"
    ),
}

EXPECTED_MANIFEST = (
    "security/001_core.sql",
    "security/002_access_control.sql",
    "ingest/001_ingest.sql",
    "ingest/002_package_manifest_uploads.sql",
    "workflow/001_jobs.sql",
    "workflow/002_job_recovery.sql",
    "verification/0001_verification_reports.sql",
    "verification/0002_runtime_scope.sql",
    "quality/0001_quality_reports.sql",
    "alignment/0001_alignment_fragments.sql",
    "alignment/0002_runtime_manifest.sql",
    "lance_catalog/0001_lance_catalog.sql",
    "annotation/0001_annotation.sql",
    "publishing/0001_publishing.sql",
    "verification/0003_tenant_report_identity.sql",
    "quality/0002_tenant_report_identity.sql",
    "alignment/0003_ready_attempt_immutable.sql",
    "collection_tasks/0001_collection_tasks.sql",
    "storage/0001_storage_governance.sql",
    "annotation/0002_tag_schema_revisions.sql",
    "dashboard/0001_dashboard_query_indexes.sql",
    "security/003_outbox_dispatch.sql",
    "ingest/003_automatic_workflow_trigger.sql",
    "annotation/0003_automatic_tasks.sql",
    "publishing/0002_rollout_publication_region_lineage.sql",
    "dashboard/0002_dashboard_business_feed_indexes.sql",
    "storage/0002_seal_inventory_snapshots.sql",
    "annotation/0004_backfill_task_base_step_count.sql",
)


def test_manifest_is_ordered_unique_and_checksum_bound() -> None:
    migrations = load_migrations()
    assert tuple(migration.version for migration in migrations) == EXPECTED_MANIFEST
    assert len({migration.version for migration in migrations}) == len(migrations)
    for migration in migrations:
        assert migration.checksum_sha256 == hashlib.sha256(migration.path.read_bytes()).hexdigest()


def test_applied_history_bytes_are_immutable() -> None:
    migrations = {migration.version: migration for migration in load_migrations()}
    for version, checksum in HISTORICAL_CHECKSUMS.items():
        assert migrations[version].checksum_sha256 == checksum


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("applied", "error"),
    (
        (
            {"security/001_core.sql": "wrong-checksum"},
            "applied migration checksum drift: security/001_core.sql",
        ),
        ({"unknown.sql": "unknown-checksum"}, "database contains migrations absent"),
    ),
)
async def test_upgrade_rejects_checksum_drift_and_unknown_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    applied: dict[str, str],
    error: str,
) -> None:
    class FakeTransaction:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *args: object) -> None:
            del args

    class FakeConnection:
        def transaction(self) -> FakeTransaction:
            return FakeTransaction()

        async def execute(self, query: str, *args: object) -> None:
            del query, args

        async def fetchval(self, query: str) -> bool:
            del query
            return True

        async def fetch(self, query: str) -> list[dict[str, str]]:
            del query
            return [
                {"version": version, "checksum_sha256": checksum}
                for version, checksum in applied.items()
            ]

        async def close(self) -> None:
            return None

    async def connect(dsn: str) -> FakeConnection:
        del dsn
        return FakeConnection()

    migration = Migration(
        version="security/001_core.sql",
        path=tmp_path / "security-001.sql",
        checksum_sha256="expected-checksum",
        sql="SELECT 1",
    )
    monkeypatch.setattr(migration_module.asyncpg, "connect", connect)
    monkeypatch.setattr(migration_module, "load_migrations", lambda: (migration,))

    with pytest.raises(RuntimeError, match=error):
        await apply_migrations("postgresql://localhost/test")
