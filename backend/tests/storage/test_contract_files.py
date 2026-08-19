from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.migrations import load_migrations
from hc_data_platform.core.openapi import aggregate_fragments

BACKEND = Path(__file__).resolve().parents[2]
FRAGMENT = BACKEND / "openapi/storage.yaml"
HISTORICAL_MIGRATION = BACKEND / "migrations/storage/0001_storage_governance.sql"
SEAL_MIGRATION = BACKEND / "migrations/storage/0002_seal_inventory_snapshots.sql"


def _operations(document: dict[str, Any]) -> set[tuple[str, str]]:
    methods = {"get", "post", "put", "patch", "delete"}
    return {
        (method, path)
        for path, item in document["paths"].items()
        if "/storage/" in path
        for method in item
        if method in methods
    }


def test_storage_fragment_matches_runtime_and_has_no_preview_or_execution_path() -> None:
    fragment = yaml.safe_load(FRAGMENT.read_text(encoding="utf-8"))
    runtime = create_app(settings=Settings(environment="test", runtime_backend="memory")).openapi()
    assert _operations(fragment) == _operations(runtime)

    source = FRAGMENT.read_text(encoding="utf-8").lower()
    assert "simulat" not in source
    assert "impact-preview" not in source
    assert "dry-run" not in source
    assert re.search(r"\b(hot|cold|warm|archive)\b", source) is None
    assert "delete_object" not in source
    assert "abort_multipart" not in source
    assert all("execution" not in path for path in fragment["paths"])

    category = fragment["components"]["schemas"]["BusinessCapacityCategory"]
    assert category["enum"] == [
        "RAW",
        "ANNOTATION_COMPLETE",
        "PENDING_ANNOTATION",
        "ISSUE_DATA",
    ]
    assert aggregate_fragments(BACKEND / "openapi")["paths"].keys() >= fragment["paths"].keys()


def test_storage_migration_freezes_reconciliation_protection_and_open_10_gate() -> None:
    historical = HISTORICAL_MIGRATION.read_text(encoding="utf-8")
    seal = SEAL_MIGRATION.read_text(encoding="utf-8")
    source = historical + seal
    assert "physical_total_bytes\n        = candidate_business_total_bytes" in source
    assert "PRIMARY KEY (project_id, snapshot_id, physical_instance_id)" in source
    assert "business_category IN" in source
    for category in (
        "RAW",
        "ANNOTATION_COMPLETE",
        "PENDING_ANNOTATION",
        "ISSUE_DATA",
    ):
        assert f"'{category}'" in source
    for protected in ("RAW", "MANIFEST", "PUBLISHED_MANIFEST"):
        assert f"'{protected}'" in source
    assert "action <> 'CLEAN_REBUILDABLE_CACHE'" in source
    assert "CHECK (production = false)" in source
    assert "CHECK (production_execution_approved = false)" in source
    assert "storage lifecycle audit events are append-only" in source
    assert "sealed storage inventory snapshots are immutable" in source
    assert "sealed storage inventory snapshots reject new facts" in source
    assert historical.count("SELECT core.apply_project_rls") == 6
    assert "sealed boolean" not in historical
    assert "ALTER COLUMN sealed SET NOT NULL" in seal
    assert seal.index("SET sealed = true") < seal.index("ALTER COLUMN sealed SET NOT NULL")

    versions = [migration.version for migration in load_migrations(BACKEND / "migrations")]
    assert versions[-2:] == [
        "storage/0002_seal_inventory_snapshots.sql",
        "annotation/0004_backfill_task_base_step_count.sql",
    ]
