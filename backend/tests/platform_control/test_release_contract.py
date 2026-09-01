from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from pydantic import ValidationError

import hc_data_platform
from hc_data_platform.platform_control.release_contract import (
    AdjacentReleaseEdgeV1,
    CompatibilityResult,
    ReleaseContractError,
    UpgradePhase,
    evaluate_rollback,
)

ROOT = Path(__file__).resolve().parents[3]
MATRIX_PATH = ROOT / "docs/architecture/release-compatibility-matrix.yaml"
ADR_PATH = ROOT / "docs/architecture/ADR-0004-release-compatibility-and-expand-contract.md"


def _matrix() -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(MATRIX_PATH.read_text(encoding="utf-8")))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project_version() -> str:
    pyproject = (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"$', pyproject, flags=re.MULTILINE)
    assert match is not None
    return match.group(1)


def _workflow_patch_ids() -> tuple[str, ...]:
    workflow_source = (
        ROOT / "backend/src/hc_data_platform/workflow/temporal_workflows.py"
    ).read_text(encoding="utf-8")
    return tuple(sorted(set(re.findall(r'workflow\.patched\(\s*"([^"]+)"', workflow_source))))


def test_current_release_baseline_fingerprints_are_exact_and_honestly_blocked() -> None:
    matrix = _matrix()
    assert matrix["api_version"] == "hc-data-platform.io/v1alpha1"
    assert matrix["kind"] == "PlatformReleaseCompatibilityMatrix"
    assert matrix["metadata"] == {
        "contract_version": 1,
        "effective_date": "2026-08-29",
        "adr": "docs/architecture/ADR-0004-release-compatibility-and-expand-contract.md",
        "implementation_task": "REL0-01",
        "evidence_status": "RELEASE_PIPELINE_IMPLEMENTED_NO_TARGET_RELEASE_EXECUTED",
    }
    assert "状态：已接受" in ADR_PATH.read_text(encoding="utf-8")

    baseline = matrix["current_development_baseline"]
    frontend = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    chart = yaml.safe_load(
        (ROOT / "deploy/helm/hc-data-platform/Chart.yaml").read_text(encoding="utf-8")
    )
    assert baseline["semantic_version"] == "0.1.0"
    assert baseline["components"] == {
        "backend_package": _project_version(),
        "backend_runtime": hc_data_platform.__version__,
        "frontend_package": frontend["version"],
        "helm_chart": chart["version"],
        "helm_app_version": chart["appVersion"],
    }
    assert baseline["release_id"] == "unreleased"
    assert baseline["signed_release_manifest"] is False
    assert baseline["production_release_eligible"] is False

    manifest_path = ROOT / "backend/migrations/manifest.txt"
    migrations = [
        line
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert baseline["database"]["migration_count"] == len(migrations) == 121
    assert baseline["database"]["manifest_sha256"] == _sha256(manifest_path)
    assert baseline["api"]["openapi_sha256"] == _sha256(ROOT / "backend/openapi.generated.yaml")
    assert baseline["configuration"]["helm_values_sha256"] == _sha256(
        ROOT / "deploy/helm/hc-data-platform/values.yaml"
    )
    assert baseline["configuration"]["runtime_config_hot_reload_allowlist"] == [
        "scheduling.media_maintenance_interval_seconds",
        "scheduling.storage_inventory_interval_seconds",
        "ui.maintenance_banner_enabled",
    ]
    assert baseline["configuration"]["unknown_hot_reload_keys_allowed"] is False

    patch_ids = _workflow_patch_ids()
    assert tuple(baseline["temporal"]["patch_ids"]) == patch_ids
    patch_hash = hashlib.sha256(("\n".join(patch_ids) + "\n").encode()).hexdigest()
    assert baseline["temporal"]["patch_set_sha256"] == patch_hash
    assert baseline["temporal"]["worker_build_id_routing"] == "ENABLED"
    assert baseline["temporal"]["production_server_version"] == ("UNSPECIFIED_RELEASE_BLOCKER")


def test_every_adjacent_edge_explicitly_covers_all_compatibility_and_rollback_domains() -> None:
    documents = _matrix()["adjacent_release_edges"]
    edges = tuple(AdjacentReleaseEdgeV1.model_validate(document) for document in documents)
    assert [edge.edge_id for edge in edges] == ["0.1.0-to-0.1.1-contract-only"]
    edge = edges[0]
    assert edge.status == "DESIGN_ONLY_TARGET_ARTIFACTS_NOT_BUILT"
    assert edge.target_artifacts_built_and_signed is False
    assert edge.database.change_mode == "no_schema_change"
    assert edge.database.old_app_on_target_schema is CompatibilityResult.PASS
    assert edge.database.new_app_on_source_schema is CompatibilityResult.PASS
    assert edge.temporal.source_patch_ids == edge.temporal.target_patch_ids
    assert edge.api.old_client_on_target_server is CompatibilityResult.PASS
    assert edge.api.target_client_on_source_server is CompatibilityResult.PASS
    assert edge.configuration.old_app_with_target_config is CompatibilityResult.PASS
    assert edge.configuration.target_app_with_source_config is CompatibilityResult.PASS
    assert edge.rollback.database_down_migration_allowed is False
    assert {
        "TARGET_0_1_1_ARTIFACTS_NOT_BUILT_OR_SIGNED",
        "PRODUCTION_TEMPORAL_SERVER_VERSION_UNSPECIFIED",
        "PRODUCTION_CAPACITY_5TB_DAY_PLUS_30_PERCENT_NOT_PASSED",
        "VERIFIED_BACKUP_AND_RESTORE_DRILL_EVIDENCE_MISSING",
        "SIGNED_DR7_PRODUCTION_EVIDENCE_BUNDLE_MISSING",
        "ADJACENT_0_1_0_0_1_1_BIDIRECTIONAL_EXECUTION_NOT_PASSED",
    } == set(edge.release_blockers)


def test_false_no_schema_claim_patch_differences_and_ready_with_blockers_are_rejected() -> None:
    original = _matrix()["adjacent_release_edges"][0]

    wrong_database = cast(dict[str, Any], _deep_copy(original))
    wrong_database["database"]["target_manifest_sha256"] = "f" * 64
    with pytest.raises(ValidationError, match="no_schema_change"):
        AdjacentReleaseEdgeV1.model_validate(wrong_database)

    wrong_patches = cast(dict[str, Any], _deep_copy(original))
    wrong_patches["temporal"]["target_patch_ids"].append("new-patch-v1")
    with pytest.raises(ValidationError, match="added_patch_ids"):
        AdjacentReleaseEdgeV1.model_validate(wrong_patches)

    false_ready = cast(dict[str, Any], _deep_copy(original))
    false_ready["status"] = "READY_FOR_CANARY"
    with pytest.raises(ValidationError, match="signed immutable target artifacts"):
        AdjacentReleaseEdgeV1.model_validate(false_ready)

    missing_api = cast(dict[str, Any], _deep_copy(original))
    del missing_api["api"]
    with pytest.raises(ValidationError):
        AdjacentReleaseEdgeV1.model_validate(missing_api)


def test_rollback_before_contract_keeps_forward_schema_and_requires_source_compatibility() -> None:
    edge = AdjacentReleaseEdgeV1.model_validate(_matrix()["adjacent_release_edges"][0])
    decision = evaluate_rollback(edge, UpgradePhase.CANARY)
    assert decision.model_dump() == {
        "allowed": True,
        "application_action": "deploy_exact_source_digests",
        "database_action": "keep_forward_schema",
        "reason_code": "RELEASE_APPLICATION_ROLLBACK_BEFORE_CONTRACT",
    }
    with pytest.raises(ReleaseContractError) as captured:
        evaluate_rollback(edge, UpgradePhase.CONTRACT_APPLIED)
    assert captured.value.code == "RELEASE_PHASE_INVALID"

    unsafe_document = cast(dict[str, Any], _deep_copy(_matrix()["adjacent_release_edges"][0]))
    unsafe_document["database"]["old_app_on_target_schema"] = "NOT_PASSED"
    unsafe_edge = AdjacentReleaseEdgeV1.model_validate(unsafe_document)
    blocked = evaluate_rollback(unsafe_edge, UpgradePhase.ROLLED_OUT)
    assert blocked.allowed is False
    assert blocked.reason_code == "RELEASE_SOURCE_NOT_COMPATIBLE_WITH_FORWARD_STATE"
    assert blocked.database_action == "restore_verified_backup"


def test_contract_phase_never_blindly_downgrades_the_database() -> None:
    document = cast(dict[str, Any], _deep_copy(_matrix()["adjacent_release_edges"][0]))
    database = document["database"]
    database.update(
        {
            "change_mode": "expand_then_contract",
            "target_migration_count": 122,
            "target_manifest_sha256": "a" * 64,
            "expand_migrations": ["platform/001_expand.sql"],
            "data_migration": "online_checkpointed",
            "contract_migrations": ["platform/002_contract.sql"],
        }
    )
    document["rollback"]["after_contract_application"] = (
        "prohibited_restore_or_forward_fix_required"
    )
    edge = AdjacentReleaseEdgeV1.model_validate(document)
    decision = evaluate_rollback(edge, UpgradePhase.CONTRACT_APPLIED)
    assert decision.allowed is False
    assert decision.application_action == "forward_fix_or_restore"
    assert decision.database_action == "restore_verified_backup"
    assert decision.reason_code == "RELEASE_ROLLBACK_BLOCKED_AFTER_CONTRACT"


def test_global_rules_require_bidirectional_canary_and_a_separate_contract_gate() -> None:
    rules = _matrix()["global_rules"]
    assert rules["supported_application_window"]["minimum_adjacent_versions"] == 2
    assert set(rules["supported_application_window"]["direction"]) == {
        "source_application_on_target_expand_schema",
        "target_application_on_source_schema_during_canary",
        "source_frontend_on_target_api",
        "target_frontend_on_source_api_during_rollback",
    }
    assert rules["contract_phase"]["separate_release_gate"] is True
    assert rules["contract_phase"]["database_down_migration_allowed"] is False
    assert rules["temporal"]["unknown_or_unrouted_history_policy"] == "stop_rollout"
    assert rules["api_and_client"]["both_canary_directions_must_pass"] is True
    assert rules["configuration"]["hot_reload_default"] == "forbidden"
    assert rules["rollback"]["after_incompatible_contract_use_forward_fix_or_verified_restore"]


def _deep_copy(value: object) -> object:
    return json.loads(json.dumps(value))
