from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).resolve().parents[3]
AUDIT_PATH = ROOT / "docs/architecture/platform-runtime-state-audit.yaml"
RUNTIME_PATH = ROOT / "backend/src/hc_data_platform/runtime.py"
DEV_COMPOSE_PATH = ROOT / "compose.dev.yaml"
TEST_COMPOSE_PATH = ROOT / "compose.test.yaml"

REQUIRED_DURABLE_SURFACES = {
    "authenticated_sessions",
    "idempotency_results",
    "upload_control_and_payload",
    "workflow_and_task_ownership",
}
REQUIRED_EPHEMERAL_SURFACES = {
    "api_tmp",
    "worker_alignment_and_projection_staging",
    "aligned_media_staging",
    "worker_readiness_sentinel",
    "frontend_tmp",
    "quality_export_scratch",
}


def _audit() -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(AUDIT_PATH.read_text(encoding="utf-8")))


def test_runtime_state_audit_classifies_every_restart_acceptance_surface() -> None:
    audit = _audit()
    assert audit["api_version"] == "hc-data-platform.io/v1alpha1"
    assert audit["kind"] == "PlatformRuntimeStateAudit"
    assert audit["metadata"] == {
        "contract_version": 1,
        "effective_date": "2026-08-29",
        "implementation_task": "HA4-03",
        "evidence_status": "REAL_KUBERNETES_RESTART_PASSED",
    }
    contract = audit["production_contract"]
    assert contract["runtime_backend"] == "production"
    assert contract["process_memory_is_source_of_truth"] is False
    assert contract["pod_files_are_source_of_truth"] is False
    assert contract["local_staging_prefix"] == "/tmp/hc-data"
    assert contract["local_staging_storage"] == "bounded_emptyDir"

    durable = {item["id"]: item for item in audit["durable_surfaces"]}
    assert set(durable) == REQUIRED_DURABLE_SURFACES
    for item in durable.values():
        assert item["source_of_truth"]
        assert item["production_adapter"]
        assert item["restart_invariant"]
        assert "InMemory" not in item["production_adapter"]

    ephemeral = {item["id"]: item for item in audit["ephemeral_surfaces"]}
    assert set(ephemeral) == REQUIRED_EPHEMERAL_SURFACES
    for item in ephemeral.values():
        assert item["correctness_dependency"] is False
        assert item["size_limit"]
        assert item["rebuild_strategy"]

    acceptance = audit["acceptance"]
    assert set(acceptance["required_surfaces"]) == REQUIRED_DURABLE_SURFACES
    assert acceptance["capacity_claim"] == "NOT_APPLICABLE"

    evidence = audit["evidence"]
    assert evidence["runtime_backend"] == "production"
    assert evidence["database_migrations"] == 105
    assert evidence["kubernetes"]["node_drains"] >= 1
    assert evidence["kubernetes"]["forced_eviction"] is False
    assert evidence["kubernetes"]["pdb_bypass"] is False
    assert evidence["kubernetes"]["final_pod_restarts"] == 0
    assert evidence["session_and_idempotency"]["original_session_reused"] is True
    assert evidence["session_and_idempotency"]["replacement_login_required"] is False
    assert evidence["session_and_idempotency"]["durable_idempotency_rows"] == 1
    assert evidence["upload"]["provider_multipart_identity_preserved"] is True
    assert evidence["upload"]["uploaded_part_preserved_after_replacement"] is True
    assert evidence["temporal"]["accepted_workflows"] == 500
    assert evidence["temporal"]["completed_workflows"] == 500
    assert evidence["temporal"]["unique_workflow_ids"] == 500
    assert evidence["temporal"]["processed_instance_ids"] == 0
    assert evidence["pod_local_state"]["files_copied_between_pods"] is False


def test_production_composition_has_no_in_memory_adapter() -> None:
    source = RUNTIME_PATH.read_text(encoding="utf-8")
    build_runtime = source.split("def build_runtime(", maxsplit=1)[1].split(
        "\ndef configure_api(", maxsplit=1
    )[0]
    assert "InMemory" not in build_runtime
    for durable_adapter in (
        "PostgresAccessRepository",
        "PsycopgIdempotencyStore",
        "PostgresIngestPersistence",
        "PostgresWorkflowJobRepository",
        "PostgresAlignedMediaRepository",
        "S3AlignedMediaArtifactStore",
    ):
        assert durable_adapter in build_runtime
    assert "_object_store_clients(resolved)" in build_runtime
    assert "OssObjectStorage(" in source


def test_runtime_state_audit_contains_no_secret_or_host_persistence_material() -> None:
    content = AUDIT_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "postgresql://",
        "BEGIN PRIVATE KEY",
        "AKIA",
        "/var/lib",
        "hostPath",
        "PersistentVolumeClaim",
    ):
        assert forbidden not in content


def test_compose_workers_mount_the_bounded_runtime_sentinel_tmpfs() -> None:
    expected = ["/tmp/hc-runtime:rw,noexec,nosuid,size=16m,uid=65532,gid=65532,mode=0750"]
    dev = cast(dict[str, Any], yaml.safe_load(DEV_COMPOSE_PATH.read_text(encoding="utf-8")))
    test = cast(dict[str, Any], yaml.safe_load(TEST_COMPOSE_PATH.read_text(encoding="utf-8")))
    assert dev["services"]["worker"]["tmpfs"] == expected
    assert dev["services"]["media-worker"]["tmpfs"] == expected
    assert test["services"]["worker"]["tmpfs"] == expected
