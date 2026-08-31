from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RUNBOOKS = ROOT / "deploy" / "runbooks"


def _read(name: str) -> str:
    return (RUNBOOKS / name).read_text(encoding="utf-8")


def test_restore_operator_packet_has_exact_safe_terminal_contract() -> None:
    evidence = _read("restore-operator-evidence.md")
    assert all(
        value in evidence
        for value in (
            '"READ_ONLY_READY"',
            '"RECONCILIATION_PASSED"',
            '"restore_smoke_and_write_enable_approval_required"',
            ".writes_enabled == false",
            ".workers_enabled == false",
            ".restore_verified == false",
            "RST3-05 operator drill complete",
            "DR7-01 complete",
        )
    )
    expected_checks = (
        "restore_checkpoint",
        "database_content",
        "object_inventory",
        "database_object_references",
        "audit_integrity",
        "outbox",
        "workflow_temporal",
        "permissions",
        "read_only_runtime",
    )
    positions = [evidence.index(f"`{name}`") for name in expected_checks]
    assert positions == sorted(positions)
    assert "no write-enable or `RESTORE_VERIFIED` path" in evidence


def test_kubernetes_runbook_tracks_product_cli_and_helm_contract() -> None:
    runbook = _read("restore-kubernetes.md")
    assert "hc-platform restore plan" in runbook
    assert '--dry-run > "$RESTORE_WORK/restore-plan.json"' in runbook
    assert '"restore_execute_requires_signed_approval"' in runbook
    assert all(
        command in runbook
        for command in (
            "operation: execute",
            "operation: reconcile",
            "--for=condition=complete",
            "backend.restoreJob",
            "automount",
            "backoffLimit: 0",
            "hc-restore-reconcile-input",
            "RESTORE_EXECUTE_JOB",
            "RESTORE_RECONCILE_JOB",
        )
    )
    assert '"allow_restore_mutation": true' in runbook
    assert '"allow_write_enable": false' in runbook
    assert '"allow_restore_verified": false' in runbook
    assert "cluster-admin" in runbook
    assert "Secret check is `no`" in runbook
    assert "exact replay" in runbook.lower()
    assert "There is no supported command here to open writes" in runbook
    assert "restore_execution_requires_signed_approval" not in runbook


def test_new_server_runbook_bootstraps_supported_dormant_release() -> None:
    runbook = _read("restore-new-bare-metal-server.md")
    assert all(
        value in runbook
        for value in (
            "Direct Docker Compose",
            "findmnt --json",
            "kubectl version",
            "frontend:\n  replicaCount: 0",
            "ingress:\n  enabled: false",
            "migration:\n    enabled: false",
            "restoreJob:\n    enabled: false",
            "restore-kubernetes.md",
            "does not prove HA4",
        )
    )
    assert "curl-to-shell" in runbook


def test_runbook_index_links_every_operator_document() -> None:
    index = _read("README.md")
    for name in (
        "restore-operator-evidence.md",
        "restore-new-bare-metal-server.md",
        "restore-kubernetes.md",
        "restore-environment-reference.md",
        "planned-migration-cutover.md",
    ):
        assert name in index


def test_restore_environment_reference_separates_provider_identities() -> None:
    reference = _read("restore-environment-reference.md")
    for name in (
        "AWS_ACCESS_KEY_ID",
        "HC_BACKUP_SIGNER_VAULT_TOKEN",
        "HC_RESTORE_POSTGRES_PASSWORD",
        "HC_RESTORE_OBJECT_STORE_ACCESS_KEY",
        "HC_RESTORE_SOURCE_OBJECT_STORE_ACCESS_KEY",
        "HC_RESTORE_KUBERNETES_TOKEN_FILE",
        "HC_RESTORE_AGE_IDENTITY_FILE",
        "HC_RESTORE_TEMPORAL_TLS_KEY_FILE",
        "HC_RESTORE_RECONCILIATION_VERIFIER_IDENTITY",
    ):
        assert f"`{name}`" in reference
    assert (
        "Repository, source-object, and target-object identities are distinct duties" in reference
    )
    assert "no approval document/key is mounted or consumed" in reference
