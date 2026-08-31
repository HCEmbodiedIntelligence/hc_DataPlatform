from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from hc_data_platform.backup.contracts import canonical_json_bytes
from hc_data_platform.backup.migration_cli import validate_plan
from hc_data_platform.backup.planned_migration import (
    MIGRATION_STEPS,
    FileMigrationCheckpointStore,
    MigrationCapacityEvidenceV1,
    MigrationCutoverApprovalSignatureV1,
    MigrationCutoverApprovalV1,
    MigrationEndpointV1,
    MigrationObjectMode,
    MigrationObservationSignatureV1,
    MigrationObservationV1,
    PlannedMigrationError,
    PlannedMigrationPlanV1,
    StaticMigrationObservationPort,
    advance_planned_migration,
    calculate_required_capacity_bytes,
    verify_migration_cutover_approval,
    verify_migration_observation_files,
)
from hc_data_platform.backup.whole_cli import run as run_whole_cli

NOW = datetime(2026, 8, 29, 1, 0, tzinfo=timezone.utc)
REFERENCED_OBJECT_BYTES = 100_000_000
DATABASE_REQUIRED_BYTES = 20_000_000
TEMPORARY_REQUIRED_BYTES = 10_000_000
LOG_REQUIRED_BYTES = 5_000_000


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _endpoint(
    name: str,
    *,
    release: str,
    object_location: str | None = None,
) -> MigrationEndpointV1:
    objects = object_location or name
    return MigrationEndpointV1(
        platform_id="platform-main",
        environment_id=f"environment-{name}",
        instance_id=f"instance-{name}",
        release_identity_sha256=release,
        postgresql_database=f"database-{name}",
        object_store_endpoint=f"https://objects-{objects}.example.invalid",
        object_bucket_reference=f"s3://bucket-{objects}",
        object_prefix=f"platform/{objects}",
        temporal_cluster_reference=f"temporal://cluster-{name}",
        temporal_namespace="default",
    )


def _plan(
    key: Ed25519PrivateKey,
    observer_key: Ed25519PrivateKey | None = None,
    *,
    object_migration_mode: MigrationObjectMode = "reuse_external",
    actual_referenced_bytes: int = REFERENCED_OBJECT_BYTES,
    headroom_ratio: float = 0.30,
) -> PlannedMigrationPlanV1:
    release = _sha("release")
    fingerprint = hashlib.sha256(key.public_key().public_bytes_raw()).hexdigest()
    required_capacity_bytes = calculate_required_capacity_bytes(
        object_migration_mode=object_migration_mode,
        actual_referenced_bytes=actual_referenced_bytes,
        database_required_bytes=DATABASE_REQUIRED_BYTES,
        temporary_required_bytes=TEMPORARY_REQUIRED_BYTES,
        log_required_bytes=LOG_REQUIRED_BYTES,
        headroom_ratio=headroom_ratio,
    )
    return PlannedMigrationPlanV1(
        plan_id="migration-plan-1",
        migration_id="migration-1",
        operation_id="migration-operation-1",
        source=_endpoint("source", release=release),
        target=_endpoint(
            "target",
            release=release,
            object_location=("source" if object_migration_mode == "reuse_external" else "target"),
        ),
        expected_postgresql_major=16,
        trusted_cutover_approval_key_sha256=fingerprint,
        trusted_observer_key_sha256=(
            hashlib.sha256(observer_key.public_key().public_bytes_raw()).hexdigest()
            if observer_key is not None
            else _sha("test-observer-public-key")
        ),
        planned_at=NOW - timedelta(minutes=10),
        expires_at=NOW + timedelta(hours=6),
        maximum_write_pause_seconds=1800,
        maximum_dns_ttl_seconds=60,
        maximum_observation_age_seconds=60,
        minimum_rollback_window_seconds=3600,
        object_migration_mode=object_migration_mode,
        actual_referenced_bytes=actual_referenced_bytes,
        database_required_bytes=DATABASE_REQUIRED_BYTES,
        temporary_required_bytes=TEMPORARY_REQUIRED_BYTES,
        log_required_bytes=LOG_REQUIRED_BYTES,
        headroom_ratio=headroom_ratio,
        required_capacity_bytes=required_capacity_bytes,
        portable_encryption_recipient_sha256=(
            _sha("portable-recipient") if object_migration_mode == "portable" else None
        ),
        portable_max_part_bytes=(64 * 1024 * 1024 if object_migration_mode == "portable" else None),
        maximum_precopy_seconds=86_400,
        maximum_precopy_postgresql_lag_bytes=1024,
    )


def test_distinct_postgresql_instances_may_reuse_the_same_database_name() -> None:
    key = Ed25519PrivateKey.generate()
    plan = _plan(key)
    document = plan.model_dump(mode="json")
    document["target"]["postgresql_database"] = plan.source.postgresql_database

    validated = PlannedMigrationPlanV1.model_validate(document)

    assert validated.source.instance_id != validated.target.instance_id
    assert validated.source.postgresql_database == validated.target.postgresql_database


def _approval(
    tmp_path: Path,
    plan: PlannedMigrationPlanV1,
    key: Ed25519PrivateKey,
) -> tuple[Path, Path]:
    plan_sha = _sha_bytes(canonical_json_bytes(plan.model_dump(mode="json")))
    fingerprint = hashlib.sha256(key.public_key().public_bytes_raw()).hexdigest()
    approval = MigrationCutoverApprovalV1(
        approval_id="cutover-approval-1",
        plan_id=plan.plan_id,
        plan_sha256=plan_sha,
        migration_id=plan.migration_id,
        operation_id=plan.operation_id,
        source_environment_id=plan.source.environment_id,
        target_environment_id=plan.target.environment_id,
        approver_identity="independent-change-approver",
        approval_key_sha256=fingerprint,
        approved_at=NOW - timedelta(minutes=5),
        expires_at=NOW + timedelta(hours=4),
    )
    approval_raw = canonical_json_bytes(approval.model_dump(mode="json"))
    signature = key.sign(approval_raw)
    envelope = MigrationCutoverApprovalSignatureV1(
        approval_key_sha256=fingerprint,
        approval_sha256=_sha_bytes(approval_raw),
        signature_base64url=base64.urlsafe_b64encode(signature).rstrip(b"=").decode(),
    )
    approval_path = tmp_path / "approval.json"
    signature_path = tmp_path / "approval-signature.json"
    _private_write(approval_path, approval_raw)
    _private_write(signature_path, canonical_json_bytes(envelope.model_dump(mode="json")))
    return approval_path, signature_path


def _verified(
    tmp_path: Path,
    plan: PlannedMigrationPlanV1,
    key: Ed25519PrivateKey,
):
    approval, signature = _approval(tmp_path, plan, key)
    return verify_migration_cutover_approval(
        plan,
        approval,
        signature,
        public_key=key.public_key(),
        now=NOW,
    )


def _capacity(
    plan: PlannedMigrationPlanV1,
    **updates: object,
) -> MigrationCapacityEvidenceV1:
    values: dict[str, object] = {
        "provider_reference": "capacity://rst304/functional-migration",
        "evidence_sha256": _sha("capacity"),
        "target_usable_capacity_bytes": plan.required_capacity_bytes,
        "measured_precopy_bytes": (
            0 if plan.object_migration_mode == "reuse_external" else plan.actual_referenced_bytes
        ),
        "measured_precopy_seconds": (0 if plan.object_migration_mode == "reuse_external" else 80),
        "measured_final_delta_bytes": 10_000_000,
        "measured_final_sync_seconds": 60,
        "measurement_started_at": NOW - timedelta(minutes=5),
        "measurement_completed_at": NOW,
        "production_equivalent_storage": False,
        "production_equivalent_network": False,
        "synthetic_or_sparse_bytes": False,
    }
    values.update(updates)
    return MigrationCapacityEvidenceV1.model_validate(values)


def _observation(
    plan: PlannedMigrationPlanV1,
    step: str,
    **updates: object,
) -> MigrationObservationV1:
    fenced = step != "precopy_verified"
    target_writable = step in {"target_writes_enabled", "rollback_window_retained"}
    final = step not in {"precopy_verified", "source_fenced"}
    values: dict[str, object] = {
        "migration_id": plan.migration_id,
        "plan_id": plan.plan_id,
        "step": step,
        "observed_at": NOW,
        "observer_identity": "migration-verifier",
        "observer_key_sha256": plan.trusted_observer_key_sha256,
        "source_release_identity_sha256": plan.source.release_identity_sha256,
        "target_release_identity_sha256": plan.target.release_identity_sha256,
        "postgresql_major": 16,
        "source_postgresql_system_identifier": 123456789,
        "target_postgresql_system_identifier": 123456789,
        "source_postgresql_timeline": 1,
        "target_postgresql_timeline": 1 if not target_writable else 2,
        "source_postgresql_lsn": "0/100",
        "target_postgresql_replay_lsn": "0/100" if final else "0/F0",
        "target_postgresql_in_recovery": not target_writable,
        "source_object_count": 3,
        "target_object_count": 3,
        "source_object_bytes": plan.actual_referenced_bytes,
        "target_object_bytes": plan.actual_referenced_bytes,
        "source_object_inventory_sha256": _sha("objects"),
        "target_object_inventory_sha256": _sha("objects"),
        "object_endpoint_bucket_prefix_verified": True,
        "object_key_version_size_hash_verified": True,
        "source_object_read_permission_verified": True,
        "target_object_read_permission_verified": True,
        "object_replication_pending_count": 0,
        "portable_package_encrypted": plan.object_migration_mode == "portable",
        "portable_package_part_count": (2 if plan.object_migration_mode == "portable" else 0),
        "portable_package_inventory_sha256": (
            _sha("objects") if plan.object_migration_mode == "portable" else None
        ),
        "source_temporal_inventory_sha256": _sha("temporal"),
        "target_temporal_inventory_sha256": _sha("temporal"),
        "source_writes_enabled": not fenced,
        "target_writes_enabled": target_writable,
        "source_worker_replicas": 2 if not fenced else 0,
        "target_worker_replicas": 2 if target_writable else 0,
        "active_source_writer_count": 2 if not fenced else 0,
        "unexpired_source_upload_grant_count": 1 if not fenced else 0,
        "source_read_only_fence": fenced,
        "target_read_only_fence": not target_writable,
        "target_verification_passed": step
        in {
            "target_verified",
            "traffic_switched",
            "target_writes_enabled",
            "rollback_window_retained",
        },
        "target_verification_sha256": (
            _sha("target-verification")
            if step
            in {
                "target_verified",
                "traffic_switched",
                "target_writes_enabled",
                "rollback_window_retained",
            }
            else None
        ),
        "traffic_routes_to_target": step
        in {"traffic_switched", "target_writes_enabled", "rollback_window_retained"},
        "observed_dns_ttl_seconds": (
            60
            if step in {"traffic_switched", "target_writes_enabled", "rollback_window_retained"}
            else None
        ),
        "write_pause_seconds": 600 if target_writable else None,
        "source_retained_until": (
            NOW + timedelta(hours=2) if step == "rollback_window_retained" else None
        ),
        "capacity": _capacity(plan) if step == "precopy_verified" else None,
        "evidence_sha256": _sha(f"evidence:{step}"),
    }
    values.update(updates)
    return MigrationObservationV1.model_validate(values)


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _private_write(path: Path, raw: bytes) -> None:
    path.write_bytes(raw)
    path.chmod(0o600)


def _observation_signature(
    tmp_path: Path,
    observation_path: Path,
    key: Ed25519PrivateKey,
) -> Path:
    raw = observation_path.read_bytes()
    fingerprint = hashlib.sha256(key.public_key().public_bytes_raw()).hexdigest()
    signature = MigrationObservationSignatureV1(
        observer_key_sha256=fingerprint,
        observation_sha256=_sha_bytes(raw),
        signature_base64url=base64.urlsafe_b64encode(key.sign(raw)).rstrip(b"=").decode(),
    )
    path = tmp_path / "observation-signature.json"
    _private_write(path, canonical_json_bytes(signature.model_dump(mode="json")))
    return path


def test_all_phases_reach_one_way_rollback_window_with_exact_replay(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    plan = _plan(key)
    approval = _verified(tmp_path, plan, key)
    store = FileMigrationCheckpointStore(tmp_path / "checkpoint.json")
    loaded = None
    for index, step in enumerate(MIGRATION_STEPS, start=1):
        observation = _observation(plan, step)
        loaded = advance_planned_migration(
            plan,
            approval,
            execution_owner_id="migration-owner-1",
            fencing_token=17,
            observation_port=StaticMigrationObservationPort(observation),
            checkpoint_store=store,
            now=NOW,
        )
        assert loaded.checkpoint.state_version == index
        assert loaded.checkpoint.completed_steps == MIGRATION_STEPS[:index]
        replay = advance_planned_migration(
            plan,
            approval,
            execution_owner_id="migration-owner-1",
            fencing_token=17,
            observation_port=StaticMigrationObservationPort(observation),
            checkpoint_store=store,
            now=NOW,
        )
        assert replay.sha256 == loaded.sha256
    assert loaded is not None
    final = loaded.checkpoint
    assert final.state == "ROLLBACK_WINDOW"
    assert final.rpo_bytes == final.rpo_seconds == 0
    assert final.source_writes_enabled is False
    assert final.target_writes_enabled is True
    assert final.direct_rollback_allowed is False
    assert final.reverse_sync_required is True
    assert final.write_pause_seconds == 600
    assert final.restore_verified is False
    assert (tmp_path / "checkpoint.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("step", "updates", "code"),
    (
        (
            "precopy_verified",
            {"target_object_inventory_sha256": _sha("different")},
            "MIGRATION_PRECOPY_NOT_READY",
        ),
        (
            "source_fenced",
            {"active_source_writer_count": 1},
            "MIGRATION_SOURCE_NOT_FENCED",
        ),
        (
            "final_sync_verified",
            {"target_postgresql_replay_lsn": "0/FF"},
            "MIGRATION_RPO_ZERO_NOT_MET",
        ),
        (
            "final_sync_verified",
            {"object_replication_pending_count": 1},
            "MIGRATION_RPO_ZERO_NOT_MET",
        ),
        (
            "target_verified",
            {"target_verification_passed": False, "target_verification_sha256": None},
            "MIGRATION_TARGET_VERIFICATION_FAILED",
        ),
        (
            "target_verified",
            {"object_replication_pending_count": 1},
            "MIGRATION_TARGET_VERIFICATION_FAILED",
        ),
        (
            "traffic_switched",
            {"observed_dns_ttl_seconds": 61},
            "MIGRATION_TRAFFIC_SWITCH_UNVERIFIED",
        ),
        (
            "traffic_switched",
            {"target_object_inventory_sha256": _sha("changed-after-verification")},
            "MIGRATION_TRAFFIC_SWITCH_UNVERIFIED",
        ),
        (
            "target_writes_enabled",
            {"write_pause_seconds": 1801},
            "MIGRATION_WRITE_ENABLE_UNSAFE",
        ),
        (
            "target_writes_enabled",
            {"target_postgresql_timeline": 1},
            "MIGRATION_POSTGRESQL_TIMELINE_INVALID",
        ),
        (
            "rollback_window_retained",
            {"source_retained_until": NOW + timedelta(minutes=59)},
            "MIGRATION_ROLLBACK_WINDOW_INVALID",
        ),
        (
            "rollback_window_retained",
            {"target_read_only_fence": True},
            "MIGRATION_ROLLBACK_WINDOW_INVALID",
        ),
    ),
)
def test_phase_observations_fail_closed(
    tmp_path: Path,
    step: str,
    updates: dict[str, object],
    code: str,
) -> None:
    key = Ed25519PrivateKey.generate()
    plan = _plan(key)
    approval = _verified(tmp_path, plan, key)
    store = FileMigrationCheckpointStore(tmp_path / "checkpoint.json")
    target_index = MIGRATION_STEPS.index(step)
    for prior in MIGRATION_STEPS[:target_index]:
        advance_planned_migration(
            plan,
            approval,
            execution_owner_id="migration-owner-1",
            fencing_token=17,
            observation_port=StaticMigrationObservationPort(_observation(plan, prior)),
            checkpoint_store=store,
            now=NOW,
        )
    with pytest.raises(PlannedMigrationError) as caught:
        advance_planned_migration(
            plan,
            approval,
            execution_owner_id="migration-owner-1",
            fencing_token=17,
            observation_port=StaticMigrationObservationPort(_observation(plan, step, **updates)),
            checkpoint_store=store,
            now=NOW,
        )
    assert caught.value.code == code
    loaded = store.load()
    completed_count = len(loaded.checkpoint.completed_steps) if loaded else 0
    assert completed_count == target_index


@pytest.mark.parametrize(
    ("mode", "expected_capacity"),
    (
        (
            "reuse_external",
            DATABASE_REQUIRED_BYTES + TEMPORARY_REQUIRED_BYTES + LOG_REQUIRED_BYTES,
        ),
        ("copy_referenced", 130_000_000),
        ("portable", 130_000_000),
    ),
)
def test_capacity_contract_is_dynamic_per_object_mode(
    mode: MigrationObjectMode,
    expected_capacity: int,
) -> None:
    plan = _plan(Ed25519PrivateKey.generate(), object_migration_mode=mode)
    assert plan.actual_referenced_bytes == REFERENCED_OBJECT_BYTES
    assert plan.required_capacity_bytes == expected_capacity
    if mode == "reuse_external":
        assert plan.source.object_store_endpoint == plan.target.object_store_endpoint
        assert plan.source.object_bucket_reference == plan.target.object_bucket_reference
        assert plan.source.object_prefix == plan.target.object_prefix
        assert _capacity(plan).measured_precopy_bytes == 0
    else:
        assert plan.source.object_store_endpoint != plan.target.object_store_endpoint
        assert _capacity(plan).measured_precopy_bytes == REFERENCED_OBJECT_BYTES


def test_copy_referenced_has_no_fixed_minimum_and_capacity_rounds_up() -> None:
    plan = _plan(
        Ed25519PrivateKey.generate(),
        object_migration_mode="copy_referenced",
        actual_referenced_bytes=1,
    )
    assert plan.required_capacity_bytes == 2


def test_capacity_evidence_rejects_sparse_claims() -> None:
    plan = _plan(Ed25519PrivateKey.generate())
    with pytest.raises(ValidationError):
        _capacity(plan, synthetic_or_sparse_bytes=True)


def test_precopy_rejects_less_than_mode_specific_capacity(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    plan = _plan(key, object_migration_mode="copy_referenced")
    approval = _verified(tmp_path, plan, key)
    observation = _observation(
        plan,
        "precopy_verified",
        capacity=_capacity(plan, target_usable_capacity_bytes=plan.required_capacity_bytes - 1),
    )
    with pytest.raises(PlannedMigrationError) as caught:
        advance_planned_migration(
            plan,
            approval,
            execution_owner_id="migration-owner-1",
            fencing_token=17,
            observation_port=StaticMigrationObservationPort(observation),
            checkpoint_store=FileMigrationCheckpointStore(tmp_path / "checkpoint.json"),
            now=NOW,
        )
    assert caught.value.code == "MIGRATION_PRECOPY_NOT_READY"


def test_portable_requires_encrypted_parts_and_reuse_rejects_object_copy(
    tmp_path: Path,
) -> None:
    key = Ed25519PrivateKey.generate()
    portable = _plan(key, object_migration_mode="portable")
    approval = _verified(tmp_path, portable, key)
    bad_portable = _observation(
        portable,
        "precopy_verified",
        portable_package_encrypted=False,
        portable_package_part_count=0,
        portable_package_inventory_sha256=None,
    )
    with pytest.raises(PlannedMigrationError) as portable_error:
        advance_planned_migration(
            portable,
            approval,
            execution_owner_id="migration-owner-1",
            fencing_token=17,
            observation_port=StaticMigrationObservationPort(bad_portable),
            checkpoint_store=FileMigrationCheckpointStore(tmp_path / "portable-checkpoint.json"),
            now=NOW,
        )
    assert portable_error.value.code == "MIGRATION_PRECOPY_NOT_READY"

    reuse = _plan(key)
    reuse_approval = _verified(tmp_path, reuse, key)
    copied = _observation(
        reuse,
        "precopy_verified",
        capacity=_capacity(reuse, measured_precopy_bytes=1),
    )
    with pytest.raises(PlannedMigrationError) as reuse_error:
        advance_planned_migration(
            reuse,
            reuse_approval,
            execution_owner_id="migration-owner-1",
            fencing_token=17,
            observation_port=StaticMigrationObservationPort(copied),
            checkpoint_store=FileMigrationCheckpointStore(tmp_path / "reuse-checkpoint.json"),
            now=NOW,
        )
    assert reuse_error.value.code == "MIGRATION_PRECOPY_NOT_READY"


def test_plan_separates_observer_and_approver_keys() -> None:
    key = Ed25519PrivateKey.generate()
    with pytest.raises(ValidationError):
        _plan(key, key)


def test_cli_rejects_object_mode_different_from_helm_expectation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(Ed25519PrivateKey.generate())
    plan_path = tmp_path / "plan.json"
    _private_write(plan_path, canonical_json_bytes(plan.model_dump(mode="json")))
    monkeypatch.setenv("HC_MIGRATION_EXPECTED_OBJECT_MODE", "portable")
    with pytest.raises(PlannedMigrationError) as caught:
        validate_plan(plan_path=plan_path, validated_at=NOW.isoformat())
    assert caught.value.code == "MIGRATION_OBJECT_MODE_MISMATCH"


def test_stale_observation_is_rejected_before_first_checkpoint(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    plan = _plan(key)
    approval = _verified(tmp_path, plan, key)
    store = FileMigrationCheckpointStore(tmp_path / "checkpoint.json")
    with pytest.raises(PlannedMigrationError) as caught:
        advance_planned_migration(
            plan,
            approval,
            execution_owner_id="migration-owner-1",
            fencing_token=17,
            observation_port=StaticMigrationObservationPort(
                _observation(
                    plan,
                    "precopy_verified",
                    observed_at=NOW - timedelta(seconds=61),
                )
            ),
            checkpoint_store=store,
            now=NOW,
        )
    assert caught.value.code == "MIGRATION_OBSERVATION_TIME_INVALID"
    assert store.load() is None


def test_approval_signature_tamper_wrong_key_and_expiry_fail_closed(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    plan = _plan(key)
    approval_path, signature_path = _approval(tmp_path, plan, key)
    raw = bytearray(approval_path.read_bytes())
    raw[-2] = ord(" ")
    _private_write(approval_path, bytes(raw))
    with pytest.raises(PlannedMigrationError) as tampered:
        verify_migration_cutover_approval(
            plan,
            approval_path,
            signature_path,
            public_key=key.public_key(),
            now=NOW,
        )
    assert tampered.value.code in {
        "MIGRATION_APPROVAL_INVALID",
        "MIGRATION_APPROVAL_SIGNATURE_INVALID",
        "MIGRATION_APPROVAL_KEY_MISMATCH",
    }

    approval_path, signature_path = _approval(tmp_path, plan, key)
    with pytest.raises(PlannedMigrationError) as wrong_key:
        verify_migration_cutover_approval(
            plan,
            approval_path,
            signature_path,
            public_key=Ed25519PrivateKey.generate().public_key(),
            now=NOW,
        )
    assert wrong_key.value.code == "MIGRATION_APPROVAL_KEY_MISMATCH"

    with pytest.raises(PlannedMigrationError) as expired:
        verify_migration_cutover_approval(
            plan,
            approval_path,
            signature_path,
            public_key=key.public_key(),
            now=NOW + timedelta(hours=5),
        )
    assert expired.value.code == "MIGRATION_APPROVAL_EXPIRED"


def test_observation_requires_independent_detached_signature(tmp_path: Path) -> None:
    approval_key = Ed25519PrivateKey.generate()
    observer_key = Ed25519PrivateKey.generate()
    plan = _plan(approval_key, observer_key)
    observation_path = tmp_path / "observation.json"
    _private_write(
        observation_path,
        canonical_json_bytes(_observation(plan, "precopy_verified").model_dump(mode="json")),
    )
    signature_path = _observation_signature(tmp_path, observation_path, observer_key)
    verified = verify_migration_observation_files(
        plan,
        observation_path,
        signature_path,
        public_key=observer_key.public_key(),
    )
    assert verified.step == "precopy_verified"

    raw = bytearray(observation_path.read_bytes())
    raw[-2] = ord(" ")
    _private_write(observation_path, bytes(raw))
    with pytest.raises(PlannedMigrationError) as tampered:
        verify_migration_observation_files(
            plan,
            observation_path,
            signature_path,
            public_key=observer_key.public_key(),
        )
    assert tampered.value.code in {
        "MIGRATION_OBSERVATION_INVALID",
        "MIGRATION_OBSERVATION_SIGNATURE_INVALID",
        "MIGRATION_OBSERVER_KEY_MISMATCH",
    }


def test_wrong_step_owner_and_replayed_bytes_are_rejected(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    plan = _plan(key)
    approval = _verified(tmp_path, plan, key)
    store = FileMigrationCheckpointStore(tmp_path / "checkpoint.json")
    with pytest.raises(PlannedMigrationError) as wrong_step:
        advance_planned_migration(
            plan,
            approval,
            execution_owner_id="migration-owner-1",
            fencing_token=17,
            observation_port=StaticMigrationObservationPort(_observation(plan, "source_fenced")),
            checkpoint_store=store,
            now=NOW,
        )
    assert wrong_step.value.code == "MIGRATION_STEP_ORDER_INVALID"

    first = _observation(plan, "precopy_verified")
    advance_planned_migration(
        plan,
        approval,
        execution_owner_id="migration-owner-1",
        fencing_token=17,
        observation_port=StaticMigrationObservationPort(first),
        checkpoint_store=store,
        now=NOW,
    )
    with pytest.raises(PlannedMigrationError) as owner:
        advance_planned_migration(
            plan,
            approval,
            execution_owner_id="other-owner",
            fencing_token=17,
            observation_port=StaticMigrationObservationPort(first),
            checkpoint_store=store,
            now=NOW,
        )
    assert owner.value.code == "MIGRATION_CHECKPOINT_MISMATCH"
    changed = first.model_copy(update={"evidence_sha256": _sha("changed")})
    with pytest.raises(PlannedMigrationError) as replay:
        advance_planned_migration(
            plan,
            approval,
            execution_owner_id="migration-owner-1",
            fencing_token=17,
            observation_port=StaticMigrationObservationPort(changed),
            checkpoint_store=store,
            now=NOW,
        )
    assert replay.value.code == "MIGRATION_OBSERVATION_CONFLICT"


def test_checkpoint_parent_and_document_modes_are_owner_only(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    plan = _plan(key)
    approval = _verified(tmp_path, plan, key)
    tmp_path.chmod(0o755)
    with pytest.raises(PlannedMigrationError) as unsafe:
        advance_planned_migration(
            plan,
            approval,
            execution_owner_id="migration-owner-1",
            fencing_token=17,
            observation_port=StaticMigrationObservationPort(_observation(plan, "precopy_verified")),
            checkpoint_store=FileMigrationCheckpointStore(tmp_path / "checkpoint.json"),
            now=NOW,
        )
    assert unsafe.value.code == "MIGRATION_PRIVATE_PATH_INVALID"


def test_whole_cli_validates_advances_and_reads_migration_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    key = Ed25519PrivateKey.generate()
    observer_key = Ed25519PrivateKey.generate()
    plan = _plan(key, observer_key)
    plan_path = tmp_path / "plan.json"
    observation_path = tmp_path / "observation.json"
    checkpoint_path = tmp_path / "checkpoint.json"
    _private_write(plan_path, canonical_json_bytes(plan.model_dump(mode="json")))
    approval_path, signature_path = _approval(tmp_path, plan, key)
    _private_write(
        observation_path,
        canonical_json_bytes(_observation(plan, "precopy_verified").model_dump(mode="json")),
    )
    observation_signature_path = _observation_signature(tmp_path, observation_path, observer_key)
    public_raw = key.public_key().public_bytes_raw()
    monkeypatch.setenv(
        "HC_MIGRATION_CUTOVER_PUBLIC_KEY_BASE64URL",
        base64.urlsafe_b64encode(public_raw).rstrip(b"=").decode(),
    )
    monkeypatch.setenv("HC_MIGRATION_CUTOVER_KEY_SHA256", hashlib.sha256(public_raw).hexdigest())
    observer_raw = observer_key.public_key().public_bytes_raw()
    monkeypatch.setenv(
        "HC_MIGRATION_OBSERVER_PUBLIC_KEY_BASE64URL",
        base64.urlsafe_b64encode(observer_raw).rstrip(b"=").decode(),
    )
    monkeypatch.setenv("HC_MIGRATION_OBSERVER_KEY_SHA256", hashlib.sha256(observer_raw).hexdigest())

    assert (
        run_whole_cli(
            [
                "migration",
                "plan",
                "--document",
                str(plan_path),
                "--validated-at",
                NOW.isoformat(),
            ]
        )
        == 0
    )
    planned = json.loads(capsys.readouterr().out)
    assert planned["status"] == "MIGRATION_PLAN_VALID"
    assert planned["object_migration_mode"] == "reuse_external"
    assert planned["actual_referenced_bytes"] == REFERENCED_OBJECT_BYTES
    assert planned["required_capacity_bytes"] == (
        DATABASE_REQUIRED_BYTES + TEMPORARY_REQUIRED_BYTES + LOG_REQUIRED_BYTES
    )

    assert (
        run_whole_cli(
            [
                "migration",
                "advance",
                "--plan",
                str(plan_path),
                "--approval",
                str(approval_path),
                "--approval-signature",
                str(signature_path),
                "--observation",
                str(observation_path),
                "--observation-signature",
                str(observation_signature_path),
                "--checkpoint",
                str(checkpoint_path),
                "--execution-owner-id",
                "migration-owner-1",
                "--fencing-token",
                "17",
                "--advanced-at",
                NOW.isoformat(),
            ]
        )
        == 0
    )
    advanced = json.loads(capsys.readouterr().out)
    assert advanced["status"] == "PRECOPY_READY"
    assert advanced["source_writes_enabled"] is True
    assert advanced["target_writes_enabled"] is False
    assert advanced["next_action"] == "migration_source_fence_required"

    assert run_whole_cli(["migration", "status", "--checkpoint", str(checkpoint_path)]) == 0
    current = json.loads(capsys.readouterr().out)
    assert current["checkpoint_sha256"] == advanced["checkpoint_sha256"]
    assert current["restore_verified"] is False
