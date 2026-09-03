from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from hc_data_platform.platform_control.maintenance_contract import (
    ALLOWED_MAINTENANCE_TRANSITIONS,
    EnvironmentFenceV1,
    MaintenanceCommandV1,
    MaintenanceContractError,
    MaintenanceLeaseV1,
    MaintenanceState,
    WriterPermitV1,
    apply_maintenance_transition,
    assert_writer_commit_allowed,
)

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = ROOT / "docs/architecture/platform-maintenance-ownership.yaml"
ADR_PATH = ROOT / "docs/architecture/ADR-0003-platform-maintenance-fencing-and-task-ownership.md"

REQUIRED_TASK_OWNERS = {
    "ingest_rollout_pipeline": "temporal_workflow",
    "annotation_review_pipeline": "temporal_workflow",
    "aligned_media_generation_pipeline": "temporal_workflow",
    "temporal_workflow_dispatch": "outbox_dispatcher",
    "helm_schema_migration": "kubernetes_job",
    "offline_restore_execution": "kubernetes_job",
    "platform_maintenance_operation": "database_lease",
    "aligned_media_orphan_reconciliation": "database_lease",
    "storage_inventory_scan": "database_lease",
}


def _contract() -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8")))


def _lease(*, state: MaintenanceState = MaintenanceState.LEASED) -> MaintenanceLeaseV1:
    return MaintenanceLeaseV1(
        operation_id="maintenance-001",
        environment_id="production-cn-east",
        state=state,
        owner_instance_id="pod-uid-owner-a",
        fencing_token=41,
        lease_until=datetime(2026, 8, 28, 8, 0, 30, tzinfo=timezone.utc),
        state_version=3,
    )


def _command(
    lease: MaintenanceLeaseV1,
    next_state: MaintenanceState,
    **updates: object,
) -> MaintenanceCommandV1:
    values: dict[str, object] = {
        "operation_id": lease.operation_id,
        "environment_id": lease.environment_id,
        "owner_instance_id": lease.owner_instance_id,
        "fencing_token": lease.fencing_token,
        "expected_state": lease.state,
        "expected_state_version": lease.state_version,
        "next_state": next_state,
    }
    values.update(updates)
    return MaintenanceCommandV1.model_validate(values)


def _assert_error(code: str, action: Any) -> None:
    with pytest.raises(MaintenanceContractError) as captured:
        action()
    assert captured.value.code == code


def test_machine_contract_and_accepted_adr_freeze_the_runtime_state_graph() -> None:
    contract = _contract()
    assert contract["api_version"] == "hc-data-platform.io/v1alpha1"
    assert contract["kind"] == "PlatformMaintenanceOwnershipContract"
    assert contract["metadata"] == {
        "contract_version": 1,
        "effective_date": "2026-08-28",
        "adr": "docs/architecture/ADR-0003-platform-maintenance-fencing-and-task-ownership.md",
        "implementation_task": "DR0-03",
        "persistence_implementation_task": "SYS1-03",
        "evidence_status": "IMPLEMENTED_KUBERNETES_FAULT_ACCEPTANCE_PASSED",
    }
    assert "状态：已接受" in ADR_PATH.read_text(encoding="utf-8")

    documented_edges = {
        (MaintenanceState(item["from"]), MaintenanceState(item["to"]))
        for item in contract["transitions"]
    }
    runtime_edges = {
        (source, target)
        for source, targets in ALLOWED_MAINTENANCE_TRANSITIONS.items()
        for target in targets
    }
    assert documented_edges == runtime_edges
    assert set(contract["maintenance_operation"]["active_states"]) == {
        MaintenanceState.REQUESTED.value,
        MaintenanceState.LEASED.value,
        MaintenanceState.READ_ONLY.value,
        MaintenanceState.DRAINING.value,
        MaintenanceState.FENCED.value,
        MaintenanceState.EXECUTING.value,
        MaintenanceState.VERIFYING.value,
        MaintenanceState.RELEASING.value,
        MaintenanceState.WRITE_ENABLE_PENDING.value,
        MaintenanceState.FAILED_READ_ONLY.value,
    }
    assert contract["clock"]["authority"] == "postgresql_database_clock"
    assert contract["clock"]["application_wall_clock_allowed_for_lease_decisions"] is False
    task_leases = contract["platform_task_leases"]
    assert task_leases["authority"] == "postgresql_row_and_database_clock"
    assert task_leases["current_row_key"] == ["environment_id", "task_id"]
    assert task_leases["lease_identity_changes_on_takeover"] is True
    assert task_leases["initial_lease_seconds"] == 30
    assert task_leases["renewal_interval_seconds"] == 10
    assert task_leases["takeover_allowed_only_after_database_lease_expiry"] is True
    assert task_leases["acquisition_during_read_only_allowed"] is False
    assert set(task_leases["stable_rejections"]) == {
        "PLATFORM_TASK_LEASE_STALE",
        "PLATFORM_TASK_LEASE_EXPIRED",
        "PLATFORM_MAINTENANCE",
    }


def test_every_task_class_has_one_nonoverlapping_coordination_authority() -> None:
    ownership = _contract()["task_ownership"]
    authorities = set(ownership["authority_kinds"])
    support_roles = set(ownership["support_roles"])
    tasks = ownership["tasks"]
    assert {task["id"]: task["coordination_authority"] for task in tasks} == (REQUIRED_TASK_OWNERS)
    assert len(tasks) == len({task["id"] for task in tasks})
    assert {task["coordination_authority"] for task in tasks} == authorities

    for task in tasks:
        authority = task["coordination_authority"]
        assert isinstance(authority, str) and authority in authorities
        assert set(task["forbidden_parallel_authorities"]) == authorities - {authority}
        assert task["durable_identity"]
        for mechanism in task["supporting_mechanisms"]:
            assert mechanism["role"] in support_roles
            assert mechanism["role"] not in {"coordination_authority", "scheduler"}
        for evidence in task["evidence"]:
            assert (ROOT / evidence).is_file(), evidence

    semantics = _contract()["ownership_semantics"]
    assert semantics
    assert all(value is False for value in semantics.values())


def test_exact_owner_token_lease_state_and_version_allow_one_transition() -> None:
    now = datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc)
    lease = _lease()
    updated = apply_maintenance_transition(
        lease,
        _command(lease, MaintenanceState.READ_ONLY),
        database_now=now,
    )
    assert updated.state is MaintenanceState.READ_ONLY
    assert updated.state_version == lease.state_version + 1
    assert updated.owner_instance_id == lease.owner_instance_id
    assert updated.fencing_token == lease.fencing_token


@pytest.mark.parametrize(
    ("updates", "code"),
    [
        ({"owner_instance_id": "pod-uid-old-owner"}, "PLATFORM_MAINTENANCE_OWNER_MISMATCH"),
        ({"fencing_token": 40}, "PLATFORM_MAINTENANCE_FENCING_TOKEN_STALE"),
        ({"expected_state_version": 2}, "PLATFORM_MAINTENANCE_STATE_CONFLICT"),
        (
            {"environment_id": "staging-cn-east"},
            "PLATFORM_MAINTENANCE_OPERATION_MISMATCH",
        ),
    ],
)
def test_stale_or_cross_environment_maintenance_commands_fail_closed(
    updates: dict[str, object], code: str
) -> None:
    now = datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc)
    lease = _lease()
    command = _command(lease, MaintenanceState.READ_ONLY, **updates)
    _assert_error(
        code,
        lambda: apply_maintenance_transition(lease, command, database_now=now),
    )


def test_expired_illegal_and_unapproved_failure_recovery_fail_closed() -> None:
    expired = _lease().model_copy(
        update={"lease_until": datetime(2026, 8, 28, 7, 59, 59, tzinfo=timezone.utc)}
    )
    now = datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc)
    _assert_error(
        "PLATFORM_MAINTENANCE_LEASE_EXPIRED",
        lambda: apply_maintenance_transition(
            expired,
            _command(expired, MaintenanceState.READ_ONLY),
            database_now=now,
        ),
    )

    leased = _lease()
    _assert_error(
        "PLATFORM_MAINTENANCE_TRANSITION_INVALID",
        lambda: apply_maintenance_transition(
            leased,
            _command(leased, MaintenanceState.EXECUTING),
            database_now=now,
        ),
    )

    failed = _lease(state=MaintenanceState.FAILED_READ_ONLY)
    _assert_error(
        "PLATFORM_MAINTENANCE_MANUAL_APPROVAL_REQUIRED",
        lambda: apply_maintenance_transition(
            failed,
            _command(failed, MaintenanceState.RELEASING),
            database_now=now,
        ),
    )
    approved = apply_maintenance_transition(
        failed,
        _command(
            failed,
            MaintenanceState.RELEASING,
            manual_approval_id="approval-20260828-001",
        ),
        database_now=now,
    )
    assert approved.state is MaintenanceState.RELEASING


def test_read_only_stale_expired_and_cross_environment_writer_permits_fail_closed() -> None:
    now = datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc)
    permit = WriterPermitV1(
        environment_id="production-cn-east",
        writer_id="temporal-activity-001",
        writer_kind="temporal_activity",
        fencing_token=40,
        lease_until=now + timedelta(seconds=30),
    )
    current = EnvironmentFenceV1(
        environment_id="production-cn-east", mode="READ_WRITE", fencing_token=40
    )
    assert_writer_commit_allowed(permit, current, database_now=now)

    read_only = current.model_copy(update={"mode": "READ_ONLY_MAINTENANCE", "fencing_token": 41})
    _assert_error(
        "PLATFORM_MAINTENANCE",
        lambda: assert_writer_commit_allowed(permit, read_only, database_now=now),
    )
    stale = current.model_copy(update={"fencing_token": 41})
    _assert_error(
        "PLATFORM_WRITER_FENCING_TOKEN_STALE",
        lambda: assert_writer_commit_allowed(permit, stale, database_now=now),
    )
    expired = permit.model_copy(update={"lease_until": now})
    _assert_error(
        "PLATFORM_WRITER_PERMIT_EXPIRED",
        lambda: assert_writer_commit_allowed(expired, current, database_now=now),
    )
    other_environment = current.model_copy(update={"environment_id": "staging-cn-east"})
    _assert_error(
        "PLATFORM_WRITER_ENVIRONMENT_MISMATCH",
        lambda: assert_writer_commit_allowed(permit, other_environment, database_now=now),
    )


def test_writer_inventory_is_complete_and_unknown_writers_hold_read_only() -> None:
    inventory = _contract()["writer_inventory"]
    assert inventory["zero_required_before_state"] == MaintenanceState.FENCED.value
    assert {source["writer_kind"] for source in inventory["sources"]} == {
        "api_command",
        "presigned_upload_grant",
        "outbox_claim",
        "temporal_activity",
        "aligned_media_attempt",
        "maintenance_controller",
        "kubernetes_job",
    }
    assert inventory["unknown_writer_policy"] == "fail_closed_remain_read_only"
    write_enable = _contract()["maintenance_operation"]["write_enable"]
    assert write_enable["transition"] == "WRITE_ENABLE_PENDING -> SUCCEEDED"
    assert "writer_inventory_zero" in write_enable["preconditions"]
