from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RESULT = Path(__file__).with_name("results") / "rst3-04-compose-server-migration-summary.json"
RUNNER = ROOT / "deploy/scripts/compose_server_migration_exercise.py"
ENTRYPOINT = ROOT / "deploy/scripts/exercise-compose-server-migration.sh"


def test_real_compose_server_migration_evidence_closes_functional_contract() -> None:
    evidence = json.loads(RESULT.read_bytes())

    assert evidence["status"] == "PASS"
    assert evidence["functional_object_bytes"] >= 100_000_000
    assert evidence["object_inventory"]["bytes"] == evidence["functional_object_bytes"]
    assert evidence["object_inventory"]["count"] > 0
    assert evidence["object_migration_mode"] == "reuse_external"
    assert evidence["performance_benchmark_claimed"] is False
    assert evidence["backup"]["repository_bytes"] < evidence["functional_object_bytes"]
    assert re.fullmatch(r"[0-9a-f]{64}", evidence["backup"]["manifest_sha256"])
    assert evidence["cutover"]["rpo_zero"] is True
    assert 0 < evidence["cutover"]["write_pause_seconds"] <= 1800
    assert evidence["cutover"]["write_pause_target_seconds"] == 1800
    assert evidence["cutover"]["write_pause_within_target"] is True
    assert evidence["restore"] == {
        "object_capacity_bytes": 0,
        "plan_id": evidence["restore"]["plan_id"],
        "reconciliation_status": "RECONCILIATION_PASSED",
        "repeat_status": "READ_ONLY_READY",
        "status": "READ_ONLY_READY",
    }
    assert evidence["source_database"] == evidence["target_database"]
    assert evidence["source_login_and_query"] == "PASS"
    assert evidence["target_login_and_controlled_write"] == "PASS"
    assert evidence["source_postgresql_stopped_after_cutover"] is True
    assert evidence["source_platform_services_stopped_after_cutover"] is True
    assert evidence["source_external_oss_retained_until_cleanup"] is True
    assert evidence["post_cutover_isolation"] == {
        "controlled_write_rows_in_source": 0,
        "controlled_write_rows_in_target": 1,
        "source_business_unchanged": True,
        "source_expected_backup_catalog_audit_rows": 1,
        "source_operational_audit_delta": 1,
    }
    assert evidence["target_operational_facts"]["outbox_active_claim_count"] == 0
    assert evidence["target_operational_facts"]["running_workflow_count"] == 0
    assert evidence["target_operational_facts"]["dispatched_workflow_trigger_count"] == 0

    source_services = evidence["runtime_services"]["source"]
    target_services = evidence["runtime_services"]["target"]
    for service in ("postgres", "api", "worker", "media-worker", "temporal"):
        assert source_services[service]["running"] is False
    assert source_services["minio"]["running"] is True
    for service in ("postgres", "temporal", "api", "frontend", "gateway", "worker", "media-worker"):
        assert target_services[service]["running"] is True
    for service in ("api", "frontend", "worker", "media-worker", "gateway"):
        assert source_services[service]["image_id"] == target_services[service]["image_id"]
        assert evidence["component_image_ids"][service] == target_services[service]["image_id"]

    reconciliation = evidence["reconciliation"]
    assert reconciliation["overall_status"] == "PASS"
    assert reconciliation["check_count"] == 9
    assert [item["name"] for item in reconciliation["checks"]] == [
        "restore_checkpoint",
        "database_content",
        "object_inventory",
        "database_object_references",
        "audit_integrity",
        "outbox",
        "workflow_temporal",
        "permissions",
        "read_only_runtime",
    ]
    assert all(
        item["status"] == "PASS" and item["coverage"] == "FULL" and item["issue_count"] == 0
        for item in reconciliation["checks"]
    )
    scan = evidence["secret_artifact_scan"]
    assert scan["status"] == "PASS"
    assert scan["forbidden_match_count"] == 0
    assert scan["artifact_file_count"] > 0
    assert scan["artifact_bytes_scanned"] > 0
    assert scan["container_log_count"] > 0
    assert scan["container_log_bytes_scanned"] > 0
    assert scan["vault_dependency_log_driver"] == "none"

    commands = evidence["commands"]
    assert len(commands) >= 14
    assert all(item["result"] == "PASS" for item in commands)
    command_text = "\n".join(item["command"] for item in commands)
    for required in (
        "hc-platform backup create",
        "hc-platform restore plan",
        "hc-platform restore execute",
        "idempotent replay",
        "hc-platform restore reconcile",
        "forbidden-secret scan",
    ):
        assert required in command_text
    assert evidence["cleanup"]["status"] == "PASS"
    assert evidence["cleanup"]["temporary_work_root_removed"] is True
    before = evidence["cleanup"]["exact_targets_before"]
    assert before[evidence["source_project"]]["containers"]
    assert before[evidence["source_project"]]["networks"]
    assert before[evidence["source_project"]]["volumes"]
    assert before[evidence["target_project"]]["containers"]
    assert before[evidence["target_project"]]["networks"]
    assert before[evidence["target_project"]]["volumes"]
    vault = f"hc-migration-vault-{evidence['run_id']}"
    assert before[vault]["containers"]
    assert all(
        not resources[kind]
        for resources in evidence["cleanup"]["exact_targets_after"].values()
        for kind in ("containers", "networks", "volumes")
    )


def test_compose_server_migration_runner_is_run_scoped_and_executable() -> None:
    source = RUNNER.read_text(encoding="utf-8")

    assert ENTRYPOINT.stat().st_mode & 0o111
    assert "hc-migration-src-{run_id}" in source
    assert "hc-migration-dst-{run_id}" in source
    assert '"docker", "system", "prune"' not in source
    assert '"docker", "volume", "prune"' not in source
    assert '"docker", "network", "prune"' not in source
    assert source.count("hc-data-platform-dev") == 1
    assert '"--log-driver"' in source
    assert '"none"' in source
    assert "_scan_outputs_for_secrets" in source
    assert "_standalone_container_inventory" in source
