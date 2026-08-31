from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from hc_data_platform.backup.contracts import canonical_json_bytes
from hc_data_platform.backup.restore import (
    RestoreCapacityCheckV1,
    RestorePlanV1,
    RestorePreflightCheckV1,
)
from hc_data_platform.backup.restore_execution import (
    RESTORE_STEPS,
    LoadedRestoreCheckpoint,
    RestoreExecutionCheckpointV1,
    RestoreStepReceiptV1,
)
from hc_data_platform.backup.restore_reconciliation import (
    RESTORE_RECONCILIATION_CHECKS,
    RestoreReconciliationCheckV1,
    RestoreReconciliationError,
    load_restore_reconciliation_report,
    publish_restore_reconciliation_report,
    reconcile_restore,
    reconciliation_check,
)

NOW = datetime(2026, 8, 29, 6, 0, tzinfo=timezone.utc)
PLAN_CHECKS = (
    "backup_signature",
    "source_target_identity",
    "target_fence",
    "exact_release",
    "kms_versions",
    "postgresql_empty",
    "object_store_mode_ready",
    "object_store_versioning",
    "capacity",
    "temporal_identity",
    "external_dependencies",
    "domains_and_certificates",
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _plan() -> RestorePlanV1:
    return RestorePlanV1(
        plan_id="restore-plan-001",
        request_id="restore-request-001",
        operation_id="restore-operation-001",
        planned_at=NOW,
        backup_id="backup-001",
        source_platform_id="source-platform-001",
        source_environment_id="production",
        target_instance_id="isolated-target-001",
        target_environment_id="rehearsal-001",
        target_postgresql_database="restore-target-db",
        object_restore_mode="copy_referenced",
        target_object_store_bucket_reference="restore-target-bucket",
        target_object_store_prefix="rehearsal/restore-001",
        manifest_sha256="1" * 64,
        signature_sha256="2" * 64,
        backup_signing_key_sha256="3" * 64,
        exact_release_sha256="4" * 64,
        readiness_evidence_sha256="5" * 64,
        capacities=tuple(
            RestoreCapacityCheckV1(
                domain=domain,
                source_bytes=100,
                required_bytes=130,
                available_bytes=1_000,
            )
            for domain in ("postgresql", "object_store", "staging")
        ),
        checks=tuple(
            RestorePreflightCheckV1(name=name, evidence_sha256=_sha(name)) for name in PLAN_CHECKS
        ),
        checkpoint_sha256="6" * 64,
    )


def _checkpoint(
    plan: RestorePlanV1,
    *,
    completed_steps: tuple[str, ...] = RESTORE_STEPS,
) -> LoadedRestoreCheckpoint:
    receipts = tuple(
        RestoreStepReceiptV1(
            step=step,
            evidence_sha256=_sha(f"receipt:{step}"),
            executor_identity=f"test:{step}",
            target_mutated=step != "payload_verified",
            completed_at=NOW,
            fence_evidence_before_sha256=_sha(f"before:{step}"),
            fence_evidence_after_sha256=_sha(f"after:{step}"),
        )
        for step in completed_steps
    )
    states = (
        "PAYLOAD_VERIFIED",
        "OBJECTS_RESTORED",
        "POSTGRESQL_RESTORED",
        "TEMPORAL_READY",
        "READ_ONLY_READY",
    )
    value = RestoreExecutionCheckpointV1(
        plan_id=plan.plan_id,
        plan_checkpoint_sha256=plan.checkpoint_sha256,
        operation_id=plan.operation_id,
        backup_id=plan.backup_id,
        target_instance_id=plan.target_instance_id,
        target_environment_id=plan.target_environment_id,
        approval_id="approval-001",
        approval_reference="approval://restore/rehearsal-001",
        approval_sha256="7" * 64,
        execution_owner_id="restore-job-001",
        fencing_token=7,
        state_version=len(completed_steps),
        previous_checkpoint_sha256=(None if len(completed_steps) == 1 else "8" * 64),
        completed_steps=completed_steps,
        receipts=receipts,
        state=states[len(completed_steps) - 1],
        updated_at=NOW,
    )
    raw = canonical_json_bytes(value.model_dump(mode="json"))
    return LoadedRestoreCheckpoint(value, hashlib.sha256(raw).hexdigest())


class _Inspector:
    def __init__(
        self,
        name: str,
        *,
        issues: tuple[str, ...] = (),
        failure: Exception | None = None,
    ) -> None:
        self.name = name
        self.issues = issues
        self.failure = failure

    def inspect(
        self,
        plan: RestorePlanV1,
        checkpoint: LoadedRestoreCheckpoint,
    ) -> RestoreReconciliationCheckV1:
        if self.failure is not None:
            raise self.failure
        return reconciliation_check(
            self.name,  # type: ignore[arg-type]
            total_count=2,
            issue_codes=self.issues,
            evidence={"plan": plan.plan_id, "checkpoint": checkpoint.sha256, "name": self.name},
        )


def _inspectors(**updates: _Inspector) -> dict[str, _Inspector]:
    result: dict[str, _Inspector] = {
        name: _Inspector(name) for name in RESTORE_RECONCILIATION_CHECKS[1:]
    }
    result.update(updates)
    return result


def test_reconciliation_report_is_exactly_bound_and_never_authorizes_writes() -> None:
    plan = _plan()
    checkpoint = _checkpoint(plan)

    report = reconcile_restore(
        plan,
        checkpoint,
        inspectors=_inspectors(),  # type: ignore[arg-type]
        verifier_identity="restore-verifier@example.invalid",
        reconciled_at=NOW,
    )

    assert tuple(item.name for item in report.checks) == RESTORE_RECONCILIATION_CHECKS
    assert report.overall_status == "PASS"
    assert report.reconciliation_passed is True
    assert report.restore_checkpoint_sha256 == checkpoint.sha256
    assert report.writes_enabled is False
    assert report.workers_enabled is False
    assert report.restore_verified is False
    assert report.next_action == "restore_smoke_and_write_enable_approval_required"


def test_failed_and_faulting_inspectors_produce_redacted_failed_report() -> None:
    plan = _plan()
    report = reconcile_restore(
        plan,
        _checkpoint(plan),
        inspectors=_inspectors(
            outbox=_Inspector("outbox", issues=("OUTBOX_CLAIM_ACTIVE",)),
            permissions=_Inspector("permissions", failure=RuntimeError("secret detail")),
        ),  # type: ignore[arg-type]
        verifier_identity="restore-verifier@example.invalid",
        reconciled_at=NOW,
    )

    assert report.overall_status == "FAIL"
    assert report.reconciliation_passed is False
    serialized = canonical_json_bytes(report.model_dump(mode="json"))
    assert b"secret detail" not in serialized
    assert report.checks[5].issue_codes == ("OUTBOX_CLAIM_ACTIVE",)
    assert report.checks[7].issue_codes == ("RESTORE_RECONCILIATION_INSPECTOR_ERROR",)


def test_reconciliation_rejects_missing_inspector_and_incomplete_or_foreign_checkpoint() -> None:
    plan = _plan()
    missing = _inspectors()
    del missing["permissions"]

    with pytest.raises(RestoreReconciliationError, match="CHECK_MISSING"):
        reconcile_restore(
            plan,
            _checkpoint(plan),
            inspectors=missing,  # type: ignore[arg-type]
            verifier_identity="verifier",
            reconciled_at=NOW,
        )
    with pytest.raises(RestoreReconciliationError, match="CHECKPOINT_INCOMPLETE"):
        reconcile_restore(
            plan,
            _checkpoint(plan, completed_steps=RESTORE_STEPS[:-1]),
            inspectors=_inspectors(),  # type: ignore[arg-type]
            verifier_identity="verifier",
            reconciled_at=NOW,
        )
    foreign = _checkpoint(plan)
    foreign_value = foreign.checkpoint.model_copy(update={"operation_id": "foreign-operation"})
    with pytest.raises(RestoreReconciliationError, match="CHECKPOINT_MISMATCH"):
        reconcile_restore(
            plan,
            LoadedRestoreCheckpoint(foreign_value, foreign.sha256),
            inspectors=_inspectors(),  # type: ignore[arg-type]
            verifier_identity="verifier",
            reconciled_at=NOW,
        )


def test_report_contract_rejects_partial_checks_and_invalid_aggregate() -> None:
    plan = _plan()
    report = reconcile_restore(
        plan,
        _checkpoint(plan),
        inspectors=_inspectors(),  # type: ignore[arg-type]
        verifier_identity="verifier",
        reconciled_at=NOW,
    )
    with pytest.raises(ValidationError):
        type(report).model_validate(
            {**report.model_dump(mode="python"), "checks": report.checks[:-1]}
        )
    with pytest.raises(ValidationError):
        type(report).model_validate({**report.model_dump(mode="python"), "overall_status": "FAIL"})


def test_owner_only_report_publish_is_atomic_idempotent_and_conflict_safe(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    plan = _plan()
    report = reconcile_restore(
        plan,
        _checkpoint(plan),
        inspectors=_inspectors(),  # type: ignore[arg-type]
        verifier_identity="verifier",
        reconciled_at=NOW,
    )
    path = tmp_path / "restore-reconciliation.json"

    first = publish_restore_reconciliation_report(report, path)
    second = publish_restore_reconciliation_report(report, path)

    assert first == second
    assert path.stat().st_mode & 0o777 == 0o600
    assert load_restore_reconciliation_report(path) == report
    with pytest.raises(RestoreReconciliationError, match="REPORT_CONFLICT"):
        publish_restore_reconciliation_report(
            report.model_copy(update={"verifier_identity": "another-verifier"}),
            path,
        )
    path.chmod(0o644)
    with pytest.raises(RestoreReconciliationError, match="REPORT_INVALID"):
        load_restore_reconciliation_report(path)


def test_report_publish_rejects_non_private_directory(tmp_path: Path) -> None:
    tmp_path.chmod(0o755)
    plan = _plan()
    report = reconcile_restore(
        plan,
        _checkpoint(plan),
        inspectors=_inspectors(),  # type: ignore[arg-type]
        verifier_identity="verifier",
        reconciled_at=NOW,
    )
    with pytest.raises(RestoreReconciliationError, match="REPORT_PATH_INVALID"):
        publish_restore_reconciliation_report(report, tmp_path / "report.json")
