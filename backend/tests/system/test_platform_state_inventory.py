from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = ROOT / "docs/architecture/platform-state-inventory.yaml"
ADR_PATH = ROOT / "docs/architecture/ADR-0001-platform-state-boundaries-and-recovery-objectives.md"

REQUIRED_DOMAINS = {
    "postgresql_business",
    "object_store",
    "temporal_workflows",
    "deployment_configuration",
    "secrets_and_kms",
    "release_artifacts",
    "backup_repository",
    "runtime_logs",
    "external_dependencies",
}
FAIL_CLOSED_DOMAIN_IDS = {
    "postgresql_business",
    "object_store",
    "temporal_workflows",
    "deployment_configuration",
    "secrets_and_kms",
    "release_artifacts",
    "backup_repository",
}


def _inventory() -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(INVENTORY_PATH.read_text(encoding="utf-8")))


def test_platform_state_inventory_freezes_every_recovery_domain() -> None:
    inventory = _inventory()
    assert inventory["api_version"] == "hc-data-platform.io/v1alpha1"
    assert inventory["kind"] == "PlatformStateInventory"
    assert inventory["metadata"] == {
        "contract_version": 2,
        "effective_date": "2026-08-31",
        "adr": ("docs/architecture/ADR-0001-platform-state-boundaries-and-recovery-objectives.md"),
        "implementation_task": "DR0-01",
        "evidence_status": "DESIGN_FROZEN_CAPACITY_NOT_PASSED",
    }

    owner_roles = inventory["owner_roles"]
    domains = inventory["state_domains"]
    assert {domain["id"] for domain in domains} == REQUIRED_DOMAINS
    assert len(domains) == len(REQUIRED_DOMAINS)

    for domain in domains:
        assert domain["disposition"] in {"included", "external", "not_applicable"}
        assert domain["owner"] in owner_roles
        assert isinstance(domain["required_for_write_enable"], bool)
        assert domain["source_of_truth"]
        assert domain["rto_seconds"] > 0
        if domain["rpo_seconds"] is None:
            assert domain["disposition"] == "external"
            assert domain["rpo_basis"]
        else:
            assert domain["rpo_seconds"] >= 0
        assert domain["backup"].get("method") or domain["backup"].get("continuous")
        assert domain["restore"]["method"]
        assert domain["restore"]["order"]
        assert domain["omission_policy"]["action"]
        assert domain["omission_policy"]["error_code"]
        assert domain["verification"]

    by_id = {domain["id"]: domain for domain in domains}
    for domain_id in FAIL_CLOSED_DOMAIN_IDS:
        policy = by_id[domain_id]["omission_policy"]["action"]
        assert policy.startswith("fail_")
        assert by_id[domain_id]["required_for_write_enable"] is True

    assert by_id["runtime_logs"]["required_for_write_enable"] is False
    assert "p19_audit_events" in by_id["runtime_logs"]["exclusions"]
    assert (
        "p19_audit_events_integrity_chain_and_legal_holds"
        in by_id["postgresql_business"]["protected_components"]
    )


def test_platform_state_inventory_preserves_capacity_and_temporal_fail_closed_decisions() -> None:
    inventory = _inventory()
    scale = inventory["scale_profile"]
    assert scale["daily_ingest_bytes"] == 5_000_000_000_000
    assert scale["headroom_percent"] == 30
    assert scale["minimum_sustained_full_pipeline_bytes_per_second"] == 75_231_481.48
    assert scale["maximum_rollout_bytes"] == 20 * 1024**3
    assert scale["concurrent_upload_sessions"] == 50
    assert scale["functional_migration_reference_bytes"] == 100_000_000
    assert scale["portable_restore_capacity_basis"] == "actual_referenced_bytes"
    assert scale["current_evidence"] == "NOT_PASSED"
    assert scale["release_policy"] == "production_release_blocking"

    temporal = inventory["temporal_mode"]
    assert temporal["production"] == "external_managed"
    assert temporal["development"] == "compose_single_node_development_only"
    assert temporal["internal_database_in_business_dump"] is False
    assert temporal["self_hosted_production"]["allowed"] is False
    assert temporal["backup_contract"]["hot_internal_table_dump_allowed"] is False


def test_platform_state_adr_is_accepted_and_contract_contains_no_secret_material() -> None:
    adr = ADR_PATH.read_text(encoding="utf-8")
    inventory = INVENTORY_PATH.read_text(encoding="utf-8")

    assert "状态：已接受" in adr
    assert "DR0-01" in adr
    assert "platform-state-inventory.yaml" in adr
    for forbidden in (
        "postgresql://",
        "BEGIN PRIVATE KEY",
        "AKIA",
        "minio-local-only",
        "local-cursor-secret-change-me",
    ):
        assert forbidden not in inventory
