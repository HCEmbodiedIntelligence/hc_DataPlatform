from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RUNBOOKS = ROOT / "deploy" / "runbooks"
RESULTS = ROOT / "backend" / "tests" / "system" / "results"


def _read(name: str) -> str:
    return (RUNBOOKS / name).read_text(encoding="utf-8")


def test_rolling_upgrade_runbook_has_safe_controller_and_rollback_boundaries() -> None:
    runbook = _read("rolling-upgrade.md")
    for required in (
        "hc-dr-evidence-gate",
        "POST /api/v1/platform/releases:preflight",
        "AWAITING_APPROVAL",
        "requester",
        "approver",
        "hc-data-migrate upgrade-expand",
        "hc-release-controller temporal-route",
        "hc-release-controller canary",
        "exit 20 / `HOLD`",
        "exit 30 / `ROLLBACK`",
        "rollback_images",
        "keep the forward expand schema",
        "hc-data-migrate upgrade-contract --approval-digest",
        "database down migration",
        "DR7-05",
    ):
        assert required in runbook
    assert "browser" in runbook
    assert "cluster-admin" in runbook
    assert "exact source" in runbook.lower()


def test_dr_gate_runbook_covers_every_external_scenario_without_claiming_local_pass() -> None:
    runbook = _read("disaster-recovery-gates.md")
    for scenario in ("DR7-01", "DR7-02", "DR7-03", "DR7-04", "DR7-05", "DR7-06"):
        assert f"## {scenario}" in runbook
    for required in (
        "RESTORE_VERIFIED",
        "RPO 0",
        "5 TB",
        "five minutes",
        "PostgreSQL writer fencing",
        "exact source image rollback",
        "must fail preflight before writes",
        "does not turn a synthetic/local run into production evidence",
        "supply_chain.dr_evidence_bundle_sha256",
    ):
        assert required in runbook


def test_runbook_index_links_release_and_dr_procedures() -> None:
    index = _read("README.md")
    assert "rolling-upgrade.md" in index
    assert "disaster-recovery-gates.md" in index


def test_release_and_dr_summaries_preserve_historical_fingerprints_and_keep_blockers() -> None:
    release = json.loads((RESULTS / "rel6-safe-rolling-release-summary.json").read_text())
    dr = json.loads((RESULTS / "dr7-drill-readiness-summary.json").read_text())

    assert release["status"] == "IMPLEMENTATION_PASSED_REAL_TARGET_RELEASE_BLOCKED"
    assert re.fullmatch(r"[0-9a-f]{64}", release["fingerprints"]["migration_manifest_sha256"])
    assert re.fullmatch(r"[0-9a-f]{64}", release["fingerprints"]["openapi_sha256"])
    superseding = json.loads(
        (RESULTS / "rst3-04-fixed-capacity-superseded-summary.json").read_text()
    )
    related = {item["path"]: item["sha256"] for item in superseding["related_historical_blockers"]}
    assert related["backend/tests/system/results/rel6-safe-rolling-release-summary.json"] == (
        hashlib.sha256(
            (RESULTS / "rel6-safe-rolling-release-summary.json").read_bytes()
        ).hexdigest()
    )
    assert related["backend/tests/system/results/dr7-drill-readiness-summary.json"] == (
        hashlib.sha256((RESULTS / "dr7-drill-readiness-summary.json").read_bytes()).hexdigest()
    )
    assert "TARGET_0_1_1_ARTIFACTS_NOT_BUILT_OR_SIGNED" in release["release_blockers"]
    assert "SIGNED_DR7_PRODUCTION_EVIDENCE_BUNDLE_MISSING" in release["release_blockers"]

    assert dr["status"] == "EVIDENCE_GATE_READY_EXTERNAL_EXERCISES_INCOMPLETE"
    assert dr["machine_gate"]["valid_production_bundle_exists"] is False
    assert re.fullmatch(r"[0-9a-f]{64}", dr["fingerprints"]["dr_evidence_implementation_sha256"])
    assert dr["scenario_status"]["DR7-03"] == "PASSED_FOR_CURRENT_RELEASE_ONLY"
    assert all(dr["scenario_status"][scenario] != "PASS" for scenario in dr["scenario_status"])


def test_rst304_fixed_capacity_blocker_is_preserved_and_explicitly_superseded() -> None:
    readiness = json.loads(
        (RESULTS / "rst3-04-external-readiness-summary.json").read_text(encoding="utf-8")
    )
    superseding_path = RESULTS / "rst3-04-fixed-capacity-superseded-summary.json"
    superseding = json.loads(superseding_path.read_text(encoding="utf-8"))

    assert readiness["status"] == "IMPLEMENTATION_PASSED_EXTERNAL_CAPACITY_BLOCKED"
    assert superseding["status"] == "SUPERSEDED_BY_PRODUCT_DECISION"
    assert superseding["reason"] == "FIXED_5TB_NOT_REQUIRED_FOR_FUNCTIONAL_MIGRATION"
    assert (
        superseding["supersedes"]["sha256"]
        == hashlib.sha256(
            (RESULTS / "rst3-04-external-readiness-summary.json").read_bytes()
        ).hexdigest()
    )
    assert superseding["historical_evidence_mutated"] is False
    functional = superseding["functional_contract"]
    assert functional["default_object_migration_mode"] == "reuse_external"
    assert functional["supported_object_migration_modes"] == [
        "reuse_external",
        "copy_referenced",
        "portable",
    ]
    assert functional["capacity_basis"] == "actual_referenced_bytes"
    assert functional["fixed_6500000000000_target_bytes_required"] is False
    assert superseding["performance_boundary"] == {
        "five_tb_per_day_benchmark_preserved": True,
        "blocks_foundational_backup_restore_or_migration": False,
        "functional_100mb_evidence_counts_as_performance_benchmark": False,
    }
    assert readiness["required_acceptance"] == {
        "reference_object_scale_bytes": 5_000_000_000_000,
        "required_target_capacity_bytes": 6_500_000_000_000,
        "rpo_target_bytes": 0,
        "rpo_target_seconds": 0,
        "maximum_write_pause_seconds": 1_800,
        "synthetic_or_sparse_bytes_allowed": False,
    }
    assert "cannot be converted into a PASS" in readiness["claim_boundary"]
