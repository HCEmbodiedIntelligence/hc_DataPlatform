from __future__ import annotations

from pathlib import Path

import yaml

from hc_data_platform.lance_catalog.models import StepRecord

BACKEND = Path(__file__).resolve().parents[2]


def test_openapi_exposes_project_scoped_logical_identity_apis() -> None:
    contract = yaml.safe_load((BACKEND / "openapi/lance_catalog.yaml").read_text())
    operations = {
        operation["operationId"]
        for path in contract["paths"].values()
        for operation in path.values()
    }

    assert {
        "listLanceCatalogVersions",
        "getLanceCatalogVersionSnapshot",
        "readStepWindow",
        "getRolloutLineage",
        "reconcileLanceCatalog",
    } <= operations
    serialized = (BACKEND / "openapi/lance_catalog.yaml").read_text()
    assert "physical_row" not in serialized
    assert "row_address" not in serialized


def test_step_timestamp_is_a_decimal_string_on_the_public_wire_without_losing_nanoseconds() -> None:
    contract = yaml.safe_load((BACKEND / "openapi/lance_catalog.yaml").read_text())
    assert contract["components"]["schemas"]["StepRecord"]["properties"]["timestamp_ns"] == {
        "type": "string",
        "pattern": "^(0|[1-9][0-9]*)$",
    }
    record = StepRecord(
        rollout_id="rollout-a",
        step_index=0,
        timestamp_ns=9_007_199_254_740_993,
    )
    assert record.model_dump(mode="json")["timestamp_ns"] == "9007199254740993"


def test_step_binary_modalities_are_bounded_markers_on_the_public_wire() -> None:
    record = StepRecord(
        rollout_id="rollout-a",
        step_index=0,
        timestamp_ns=0,
        modalities={
            "camera.front": b"\xff\x00\xfe",
            "joint.position": [0.1, -0.2],
        },
    )

    payload = record.model_dump(mode="json")["modalities"]

    assert payload == {
        "camera.front": {
            "$type": "binary",
            "byte_length": 3,
            "transport": "aligned_media",
        },
        "joint.position": [0.1, -0.2],
    }
    assert "base64" not in str(payload).lower()


def test_migration_persists_receipts_lineage_pending_and_idempotency() -> None:
    migration = (BACKEND / "migrations/lance_catalog/0001_lance_catalog.sql").read_text()

    assert "receipt_json jsonb NOT NULL" in migration
    assert "lance_pending_reconciliation" in migration
    assert "lance_rollout_lineage" in migration
    assert "rollout_id, source_sha256, converter_version" in migration
    assert "dataset_uri" in migration and "lance_version" in migration
