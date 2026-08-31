from __future__ import annotations

import base64
import hashlib
import json
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from hc_data_platform.backup.contracts import canonical_json_bytes
from hc_data_platform.backup.restore import (
    RestoreCapacityCheckV1,
    RestorePlanV1,
    RestorePreflightCheckV1,
)
from hc_data_platform.backup.restore_execution import (
    RESTORE_STEPS,
    FileRestoreCheckpointStore,
    LoadedRestoreCheckpoint,
    RestoreApprovalSignatureV1,
    RestoreApprovalV1,
    RestoreExecutionCheckpointV1,
    RestoreExecutionError,
    RestoreStep,
    RestoreStepResultV1,
    VerifiedRestoreApproval,
    execute_restore,
    verify_restore_approval,
)

NOW = datetime(2026, 8, 29, 4, 0, tzinfo=timezone.utc)
CHECK_NAMES = (
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
    capacities = tuple(
        RestoreCapacityCheckV1(
            domain=domain,
            source_bytes=100,
            required_bytes=130,
            available_bytes=1_000,
        )
        for domain in ("postgresql", "object_store", "staging")
    )
    checks = tuple(
        RestorePreflightCheckV1(name=name, evidence_sha256=_sha(name)) for name in CHECK_NAMES
    )
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
        capacities=capacities,
        checks=checks,
        checkpoint_sha256="6" * 64,
    )


def _approval_artifacts(
    plan: RestorePlanV1,
    *,
    private_key: Ed25519PrivateKey | None = None,
    approval_update: dict[str, object] | None = None,
) -> tuple[bytes, bytes, Ed25519PrivateKey, RestoreApprovalV1]:
    key = private_key or Ed25519PrivateKey.from_private_bytes(bytes(range(32, 64)))
    public_raw = key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    key_sha = hashlib.sha256(public_raw).hexdigest()
    approval = RestoreApprovalV1(
        approval_id="restore-approval-001",
        approval_reference="approval://control-plane/rehearsal-001",
        plan_id=plan.plan_id,
        plan_sha256=hashlib.sha256(canonical_json_bytes(plan.model_dump(mode="json"))).hexdigest(),
        plan_checkpoint_sha256=plan.checkpoint_sha256,
        operation_id=plan.operation_id,
        backup_id=plan.backup_id,
        target_instance_id=plan.target_instance_id,
        target_environment_id=plan.target_environment_id,
        approver_identity="restore-approver@example.invalid",
        approval_key_sha256=key_sha,
        approved_at=NOW - timedelta(minutes=5),
        expires_at=NOW + timedelta(hours=1),
    )
    if approval_update:
        approval = approval.model_copy(update=approval_update)
    approval_bytes = canonical_json_bytes(approval.model_dump(mode="json"))
    signature = base64.urlsafe_b64encode(key.sign(approval_bytes)).rstrip(b"=").decode()
    envelope = RestoreApprovalSignatureV1(
        approval_key_sha256=approval.approval_key_sha256,
        approval_sha256=hashlib.sha256(approval_bytes).hexdigest(),
        signature_base64url=signature,
    )
    return (
        approval_bytes,
        canonical_json_bytes(envelope.model_dump(mode="json")),
        key,
        approval,
    )


def _verified_approval(
    plan: RestorePlanV1,
    *,
    approval_update: dict[str, object] | None = None,
) -> VerifiedRestoreApproval:
    approval_bytes, signature_bytes, key, approval = _approval_artifacts(
        plan,
        approval_update=approval_update,
    )
    return verify_restore_approval(
        approval_bytes,
        signature_bytes,
        plan=plan,
        public_keys_by_sha256={approval.approval_key_sha256: key.public_key()},
        now=NOW,
    )


class _Fence:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []
        self.read_only = True

    def assert_read_only(
        self,
        plan: RestorePlanV1,
        *,
        execution_owner_id: str,
        fencing_token: int,
    ) -> str:
        del plan
        if not self.read_only:
            raise RestoreExecutionError("RESTORE_FENCE_OPEN", "write fence is open")
        self.calls.append((execution_owner_id, fencing_token))
        return _sha(f"{execution_owner_id}:{fencing_token}:{len(self.calls)}")


class _Step:
    def __init__(
        self,
        step: RestoreStep,
        calls: list[RestoreStep],
        *,
        fail: bool = False,
        result_step: RestoreStep | None = None,
    ) -> None:
        self.step = step
        self.calls = calls
        self.fail = fail
        self.result_step = result_step or step

    def execute(
        self,
        plan: RestorePlanV1,
        checkpoint: RestoreExecutionCheckpointV1 | None,
    ) -> RestoreStepResultV1:
        del plan, checkpoint
        self.calls.append(self.step)
        if self.fail:
            raise RestoreExecutionError("INJECTED_RESTORE_FAILURE", "injected step failure")
        return RestoreStepResultV1(
            step=self.result_step,
            evidence_sha256=_sha(self.step),
            executor_identity=f"test:{self.step}",
            target_mutated=self.step != "payload_verified",
        )


class _RecordingStore:
    def __init__(self, path: Path) -> None:
        self.delegate = FileRestoreCheckpointStore(path)
        self.saved: list[LoadedRestoreCheckpoint] = []

    def load(self) -> LoadedRestoreCheckpoint | None:
        return self.delegate.load()

    def save(
        self,
        checkpoint: RestoreExecutionCheckpointV1,
        *,
        expected_previous_sha256: str | None,
    ) -> LoadedRestoreCheckpoint:
        loaded = self.delegate.save(
            checkpoint,
            expected_previous_sha256=expected_previous_sha256,
        )
        self.saved.append(loaded)
        return loaded


def _steps(
    calls: list[RestoreStep],
    *,
    failing_step: RestoreStep | None = None,
) -> dict[RestoreStep, _Step]:
    return {step: _Step(step, calls, fail=step == failing_step) for step in RESTORE_STEPS}


def _execute(
    plan: RestorePlanV1,
    approval: VerifiedRestoreApproval,
    *,
    fence: _Fence,
    steps: dict[RestoreStep, _Step],
    store: _RecordingStore,
    owner: str = "restore-job-001",
    token: int = 7,
) -> LoadedRestoreCheckpoint:
    return execute_restore(
        plan,
        approval=approval,
        execution_owner_id=owner,
        fencing_token=token,
        fence=fence,
        steps=steps,
        checkpoints=store,
        clock=lambda: NOW,
    )


def _assert_execution_error(code: str, function: Any) -> None:
    with pytest.raises(RestoreExecutionError) as captured:
        function()
    assert captured.value.code == code


def test_signed_restore_approval_is_exact_and_uses_separate_duty_key() -> None:
    plan = _plan()
    approval_bytes, signature_bytes, key, approval = _approval_artifacts(plan)

    verified = verify_restore_approval(
        approval_bytes,
        signature_bytes,
        plan=plan,
        public_keys_by_sha256={approval.approval_key_sha256: key.public_key()},
        now=NOW,
    )

    assert verified.approval == approval
    assert verified.approval_sha256 == hashlib.sha256(approval_bytes).hexdigest()
    assert len(verified.signature_sha256) == 64


def test_restore_approval_rejects_tampering_wrong_plan_expiry_and_untrusted_key() -> None:
    plan = _plan()
    approval_bytes, signature_bytes, key, approval = _approval_artifacts(plan)
    tampered = json.loads(approval_bytes)
    tampered["target_environment_id"] = "another-environment"

    _assert_execution_error(
        "RESTORE_APPROVAL_SIGNATURE_INVALID",
        lambda: verify_restore_approval(
            canonical_json_bytes(tampered),
            signature_bytes,
            plan=plan,
            public_keys_by_sha256={approval.approval_key_sha256: key.public_key()},
            now=NOW,
        ),
    )
    _assert_execution_error(
        "RESTORE_APPROVAL_PLAN_MISMATCH",
        lambda: verify_restore_approval(
            approval_bytes,
            signature_bytes,
            plan=plan.model_copy(update={"operation_id": "another-operation"}),
            public_keys_by_sha256={approval.approval_key_sha256: key.public_key()},
            now=NOW,
        ),
    )
    _assert_execution_error(
        "RESTORE_APPROVAL_KEY_UNTRUSTED",
        lambda: verify_restore_approval(
            approval_bytes,
            signature_bytes,
            plan=plan,
            public_keys_by_sha256={},
            now=NOW,
        ),
    )

    expired_bytes, expired_signature, expired_key, expired = _approval_artifacts(
        plan,
        approval_update={
            "approved_at": NOW - timedelta(hours=2),
            "expires_at": NOW - timedelta(hours=1),
        },
    )
    _assert_execution_error(
        "RESTORE_APPROVAL_EXPIRED",
        lambda: verify_restore_approval(
            expired_bytes,
            expired_signature,
            plan=plan,
            public_keys_by_sha256={expired.approval_key_sha256: expired_key.public_key()},
            now=NOW,
        ),
    )


def test_restore_approval_rejects_backup_signer_duty_conflict_and_duplicate_json() -> None:
    plan = _plan()
    approval_bytes, signature_bytes, key, approval = _approval_artifacts(plan)
    conflicting_plan = plan.model_copy(
        update={"backup_signing_key_sha256": approval.approval_key_sha256}
    )
    _assert_execution_error(
        "RESTORE_APPROVAL_DUTY_CONFLICT",
        lambda: verify_restore_approval(
            approval_bytes,
            signature_bytes,
            plan=conflicting_plan,
            public_keys_by_sha256={approval.approval_key_sha256: key.public_key()},
            now=NOW,
        ),
    )

    duplicate = b'{"approval_id":"duplicate",' + approval_bytes[1:]
    _assert_execution_error(
        "RESTORE_APPROVAL_INVALID",
        lambda: verify_restore_approval(
            duplicate,
            signature_bytes,
            plan=plan,
            public_keys_by_sha256={approval.approval_key_sha256: key.public_key()},
            now=NOW,
        ),
    )


def test_restore_execution_orders_and_checkpoints_all_steps_read_only(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    plan = _plan()
    approval = _verified_approval(plan)
    calls: list[RestoreStep] = []
    fence = _Fence()
    store = _RecordingStore(tmp_path / "restore-checkpoint.json")

    completed = _execute(
        plan,
        approval,
        fence=fence,
        steps=_steps(calls),
        store=store,
    )

    assert calls == list(RESTORE_STEPS)
    assert len(store.saved) == len(RESTORE_STEPS)
    assert completed.checkpoint.state == "READ_ONLY_READY"
    assert completed.checkpoint.completed_steps == RESTORE_STEPS
    assert completed.checkpoint.approval_id == approval.approval.approval_id
    assert completed.checkpoint.approval_sha256 == approval.approval_sha256
    assert completed.checkpoint.writes_enabled is False
    assert completed.checkpoint.reconciliation_completed is False
    assert completed.checkpoint.restore_verified is False
    assert len(fence.calls) == len(RESTORE_STEPS) * 2
    assert stat.S_IMODE((tmp_path / "restore-checkpoint.json").stat().st_mode) == 0o600
    for index, saved in enumerate(store.saved):
        expected_previous = None if index == 0 else store.saved[index - 1].sha256
        assert saved.checkpoint.previous_checkpoint_sha256 == expected_previous
        assert saved.checkpoint.state_version == index + 1


@pytest.mark.parametrize("failure_index", range(len(RESTORE_STEPS)))
def test_each_step_failure_stops_at_last_safe_checkpoint_and_keeps_fence(
    tmp_path: Path,
    failure_index: int,
) -> None:
    tmp_path.chmod(0o700)
    plan = _plan()
    approval = _verified_approval(plan)
    calls: list[RestoreStep] = []
    fence = _Fence()
    store = _RecordingStore(tmp_path / f"failure-{failure_index}.json")
    failing_step = RESTORE_STEPS[failure_index]

    _assert_execution_error(
        "INJECTED_RESTORE_FAILURE",
        lambda: _execute(
            plan,
            approval,
            fence=fence,
            steps=_steps(calls, failing_step=failing_step),
            store=store,
        ),
    )

    assert calls == list(RESTORE_STEPS[: failure_index + 1])
    assert fence.read_only is True
    loaded = store.load()
    if failure_index == 0:
        assert loaded is None
    else:
        assert loaded is not None
        assert loaded.checkpoint.completed_steps == RESTORE_STEPS[:failure_index]
        assert loaded.checkpoint.writes_enabled is False
        assert loaded.checkpoint.restore_verified is False


def test_restore_resumes_from_last_safe_step_and_complete_retry_is_idempotent(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    plan = _plan()
    approval = _verified_approval(plan)
    store = _RecordingStore(tmp_path / "resume.json")
    first_calls: list[RestoreStep] = []
    fence = _Fence()

    _assert_execution_error(
        "INJECTED_RESTORE_FAILURE",
        lambda: _execute(
            plan,
            approval,
            fence=fence,
            steps=_steps(first_calls, failing_step="postgresql_restored"),
            store=store,
        ),
    )
    assert first_calls == ["payload_verified", "objects_restored", "postgresql_restored"]

    resumed_calls: list[RestoreStep] = []
    completed = _execute(
        plan,
        approval,
        fence=fence,
        steps=_steps(resumed_calls),
        store=store,
    )
    assert resumed_calls == ["postgresql_restored", "temporal_ready", "services_read_only"]
    assert completed.checkpoint.state == "READ_ONLY_READY"

    forbidden_calls: list[RestoreStep] = []
    before_fence_calls = len(fence.calls)
    repeated = _execute(
        plan,
        approval,
        fence=fence,
        steps=_steps(forbidden_calls, failing_step="payload_verified"),
        store=store,
    )
    assert repeated == completed
    assert forbidden_calls == []
    assert len(fence.calls) == before_fence_calls + 1


def test_restore_resume_rejects_changed_owner_fence_approval_and_stale_cas(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    plan = _plan()
    approval = _verified_approval(plan)
    store = _RecordingStore(tmp_path / "binding.json")
    calls: list[RestoreStep] = []
    fence = _Fence()
    _assert_execution_error(
        "INJECTED_RESTORE_FAILURE",
        lambda: _execute(
            plan,
            approval,
            fence=fence,
            steps=_steps(calls, failing_step="objects_restored"),
            store=store,
        ),
    )
    loaded = store.load()
    assert loaded is not None

    for owner, token in (("another-owner", 7), ("restore-job-001", 8)):
        _assert_execution_error(
            "RESTORE_CHECKPOINT_OWNER_MISMATCH",
            lambda owner=owner, token=token: _execute(
                plan,
                approval,
                fence=fence,
                steps=_steps([]),
                store=store,
                owner=owner,
                token=token,
            ),
        )

    another_approval = _verified_approval(
        plan,
        approval_update={"approval_id": "restore-approval-002"},
    )
    _assert_execution_error(
        "RESTORE_CHECKPOINT_APPROVAL_MISMATCH",
        lambda: _execute(
            plan,
            another_approval,
            fence=fence,
            steps=_steps([]),
            store=store,
        ),
    )
    _assert_execution_error(
        "RESTORE_CHECKPOINT_CONFLICT",
        lambda: store.delegate.save(
            loaded.checkpoint,
            expected_previous_sha256="0" * 64,
        ),
    )
    _assert_execution_error(
        "RESTORE_CHECKPOINT_CHAIN_INVALID",
        lambda: store.delegate.save(
            loaded.checkpoint,
            expected_previous_sha256=loaded.sha256,
        ),
    )


def test_restore_execution_rejects_expired_verified_approval_and_unsafe_result(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    plan = _plan()
    approval = _verified_approval(plan)
    fence = _Fence()
    store = _RecordingStore(tmp_path / "unsafe.json")

    _assert_execution_error(
        "RESTORE_APPROVAL_EXPIRED",
        lambda: execute_restore(
            plan,
            approval=approval,
            execution_owner_id="restore-job-001",
            fencing_token=7,
            fence=fence,
            steps=_steps([]),
            checkpoints=store,
            clock=lambda: NOW + timedelta(hours=2),
        ),
    )

    calls: list[RestoreStep] = []
    unsafe_steps = _steps(calls)
    unsafe_steps["payload_verified"] = _Step(
        "payload_verified",
        calls,
        result_step="objects_restored",
    )
    _assert_execution_error(
        "RESTORE_STEP_RESULT_INVALID",
        lambda: _execute(
            plan,
            approval,
            fence=fence,
            steps=unsafe_steps,
            store=store,
        ),
    )
    assert store.load() is None


def test_checkpoint_contract_and_owner_only_file_fail_closed(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    plan = _plan()
    approval = _verified_approval(plan)
    store = _RecordingStore(tmp_path / "private.json")
    _execute(
        plan,
        approval,
        fence=_Fence(),
        steps=_steps([]),
        store=store,
    )
    payload = store.saved[-1].checkpoint.model_dump(mode="json")
    payload["completed_steps"] = ["payload_verified", "postgresql_restored"]
    with pytest.raises(ValidationError):
        RestoreExecutionCheckpointV1.model_validate(payload)

    store.delegate.path.chmod(0o644)
    _assert_execution_error("RESTORE_CHECKPOINT_INVALID", store.load)

    unsafe_directory = tmp_path / "unsafe"
    unsafe_directory.mkdir(mode=0o755)
    unsafe_store = FileRestoreCheckpointStore(unsafe_directory / "checkpoint.json")
    first = store.saved[0].checkpoint
    _assert_execution_error(
        "RESTORE_CHECKPOINT_PATH_INVALID",
        lambda: unsafe_store.save(first, expected_previous_sha256=None),
    )
