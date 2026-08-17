from __future__ import annotations

from pathlib import Path

import yaml

BACKEND = Path(__file__).resolve().parents[2]


def test_openapi_exposes_project_scoped_logical_identity_apis() -> None:
    contract = yaml.safe_load((BACKEND / "openapi/lance_catalog.yaml").read_text())
    operations = {
        operation["operationId"]
        for path in contract["paths"].values()
        for operation in path.values()
    }

    assert {
        "listDatasetVersions",
        "getDatasetVersionSnapshot",
        "readStepWindow",
        "getRolloutLineage",
        "reconcileLanceCatalog",
    } <= operations
    serialized = (BACKEND / "openapi/lance_catalog.yaml").read_text()
    assert "physical_row" not in serialized
    assert "row_address" not in serialized


def test_migration_persists_receipts_lineage_pending_and_idempotency() -> None:
    migration = (BACKEND / "migrations/lance_catalog/0001_lance_catalog.sql").read_text()

    assert "receipt_json jsonb NOT NULL" in migration
    assert "lance_pending_reconciliation" in migration
    assert "lance_rollout_lineage" in migration
    assert "rollout_id, source_sha256, converter_version" in migration
    assert "dataset_uri" in migration and "lance_version" in migration
