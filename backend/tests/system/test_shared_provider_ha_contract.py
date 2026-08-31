from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = ROOT / "docs/architecture/shared-provider-ha.yaml"
RUNBOOK_PATH = ROOT / "deploy/runbooks/ha-shared-provider-failover.md"


def _contract() -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8")))


def test_shared_provider_contract_is_closed_and_requires_all_three_failovers() -> None:
    contract = _contract()
    assert contract["api_version"] == "hc-data-platform.io/v1alpha1"
    assert contract["kind"] == "SharedProviderHAContract"
    assert contract["metadata"] == {
        "contract_version": 1,
        "effective_date": "2026-08-29",
        "implementation_task": "HA4-04",
        "evidence_status": "REAL_LOCAL_PROTOCOL_FAILOVER_PASSED",
    }
    application = contract["application_contract"]
    assert application["endpoint_identity_changes_on_failover"] is False
    assert application["application_restart_required"] is False
    assert application["credential_rotation_during_provider_failover"] is False
    assert application["correlated_site_failure_claim"] is False

    providers = {item["id"]: item for item in contract["providers"]}
    assert set(providers) == {"postgresql", "object_store", "temporal"}
    for provider in providers.values():
        assert provider["stable_application_endpoint"] == "required"
        assert provider["acceptance_event"]
        assert provider["acceptance_result"]
        assert len(provider["authority_checks"]) >= 4

    acceptance = contract["acceptance"]
    assert set(acceptance["required_providers"]) == set(providers)
    assert acceptance["final_application_restarts"] == 0
    assert acceptance["database_side_effect_duplicates"] == 0
    assert acceptance["workflow_side_effect_duplicates"] == 0
    assert acceptance["capacity_claim"] == "NOT_APPLICABLE"

    evidence = contract["evidence"]
    assert evidence["scope"] == "isolated_same_host_multi_process_protocol_drill"
    assert evidence["application"]["stable_endpoints_unchanged"] is True
    assert evidence["application"]["final_restarts"] == 0
    assert evidence["postgresql"]["original_session_preserved"] is True
    assert evidence["postgresql"]["idempotency_rows"] == 1
    assert evidence["object_store"]["healthy_nodes_before"] == 4
    assert evidence["object_store"]["healthy_nodes_after"] == 3
    assert evidence["temporal"]["unique_reconciled_results"] == 300
    assert evidence["temporal"]["authoritative_running_after"] == 0
    assert evidence["side_effects"]["database_duplicates"] == 0
    assert evidence["side_effects"]["workflow_duplicates"] == 0


def test_secret_consistency_requires_one_versioned_bundle_and_no_hot_reload() -> None:
    secret = _contract()["secret_consistency"]
    assert secret["manager_mode"] == "external_versioned_secret_manager"
    assert secret["public_revision_setting"] == "HC_SECRET_BUNDLE_REVISION"
    assert secret["plaintext_fingerprint_exported"] is False
    assert set(secret["pod_sources"]) == {
        "postgres_secret_reference",
        "object_store_secret_reference",
        "application_secret_reference",
    }
    assert "in_place_partial_key_update" in secret["forbidden_methods"]
    assert "dynamic_dsn_or_credential_hot_reload" in secret["forbidden_methods"]


def test_failover_runbook_preserves_provider_and_secret_boundaries() -> None:
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")
    for required in (
        "PostgreSQL writer failover",
        "Object-store node failover",
        "Temporal frontend failover",
        "HC_SECRET_BUNDLE_REVISION",
        "Do not restart application Pods",
        "Do not rotate credentials",
        "old writer",
        "multipart",
        "workflow",
    ):
        assert required in runbook
    for forbidden in ("BEGIN PRIVATE KEY", "postgresql://", "--force"):
        assert forbidden not in runbook
