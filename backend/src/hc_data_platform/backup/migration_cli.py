"""Runtime wiring for the external RST3-04 planned-migration CLI."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from hc_data_platform.backup.contracts import canonical_json_bytes
from hc_data_platform.backup.planned_migration import (
    FileMigrationCheckpointStore,
    PlannedMigrationError,
    StaticMigrationObservationPort,
    advance_planned_migration,
    load_planned_migration_plan,
    verify_migration_cutover_approval,
    verify_migration_observation_files,
)


def validate_plan(*, plan_path: Path, validated_at: str) -> Mapping[str, object]:
    plan = load_planned_migration_plan(plan_path)
    _require_expected_object_mode(plan.object_migration_mode)
    now = _timestamp(validated_at)
    if now < plan.planned_at or now >= plan.expires_at:
        raise PlannedMigrationError(
            "MIGRATION_PLAN_EXPIRED", "planned migration is not currently valid"
        )
    plan_sha = hashlib.sha256(canonical_json_bytes(plan.model_dump(mode="json"))).hexdigest()
    return {
        "status": "MIGRATION_PLAN_VALID",
        "plan_id": plan.plan_id,
        "migration_id": plan.migration_id,
        "operation_id": plan.operation_id,
        "plan_sha256": plan_sha,
        "rpo_target_bytes": plan.rpo_target_bytes,
        "rpo_target_seconds": plan.rpo_target_seconds,
        "maximum_write_pause_seconds": plan.maximum_write_pause_seconds,
        "object_migration_mode": plan.object_migration_mode,
        "actual_referenced_bytes": plan.actual_referenced_bytes,
        "headroom_ratio": plan.headroom_ratio,
        "required_capacity_bytes": plan.required_capacity_bytes,
        "next_action": "migration_precopy_observation_required",
    }


def advance(
    *,
    plan_path: Path,
    approval_path: Path,
    approval_signature_path: Path,
    observation_path: Path,
    observation_signature_path: Path,
    checkpoint_path: Path,
    execution_owner_id: str,
    fencing_token: int,
    advanced_at: str,
) -> Mapping[str, object]:
    plan = load_planned_migration_plan(plan_path)
    _require_expected_object_mode(plan.object_migration_mode)
    now = _timestamp(advanced_at)
    fingerprint, public_key = _approval_public_key()
    if fingerprint != plan.trusted_cutover_approval_key_sha256:
        raise PlannedMigrationError(
            "MIGRATION_APPROVAL_KEY_MISMATCH", "configured approval key differs from plan"
        )
    approval = verify_migration_cutover_approval(
        plan,
        approval_path,
        approval_signature_path,
        public_key=public_key,
        now=now,
    )
    observer_fingerprint, observer_public_key = _observer_public_key()
    if observer_fingerprint != plan.trusted_observer_key_sha256:
        raise PlannedMigrationError(
            "MIGRATION_OBSERVER_KEY_MISMATCH", "configured observer key differs from plan"
        )
    observation = verify_migration_observation_files(
        plan,
        observation_path,
        observation_signature_path,
        public_key=observer_public_key,
    )
    loaded = advance_planned_migration(
        plan,
        approval,
        execution_owner_id=execution_owner_id,
        fencing_token=fencing_token,
        observation_port=StaticMigrationObservationPort(observation),
        checkpoint_store=FileMigrationCheckpointStore(checkpoint_path),
        now=now,
    )
    checkpoint = loaded.checkpoint
    return {
        "status": checkpoint.state,
        "plan_id": checkpoint.plan_id,
        "migration_id": checkpoint.migration_id,
        "operation_id": checkpoint.operation_id,
        "checkpoint_sha256": loaded.sha256,
        "completed_steps": list(checkpoint.completed_steps),
        "source_writes_enabled": checkpoint.source_writes_enabled,
        "target_writes_enabled": checkpoint.target_writes_enabled,
        "direct_rollback_allowed": checkpoint.direct_rollback_allowed,
        "reverse_sync_required": checkpoint.reverse_sync_required,
        "rpo_bytes": checkpoint.rpo_bytes,
        "rpo_seconds": checkpoint.rpo_seconds,
        "write_pause_seconds": checkpoint.write_pause_seconds,
        "restore_verified": checkpoint.restore_verified,
        "next_action": _next_action(len(checkpoint.completed_steps)),
    }


def status(*, checkpoint_path: Path) -> Mapping[str, object]:
    loaded = FileMigrationCheckpointStore(checkpoint_path).load()
    if loaded is None:
        raise PlannedMigrationError(
            "MIGRATION_CHECKPOINT_UNAVAILABLE", "migration checkpoint is unavailable"
        )
    checkpoint = loaded.checkpoint
    return {
        "status": checkpoint.state,
        "plan_id": checkpoint.plan_id,
        "migration_id": checkpoint.migration_id,
        "checkpoint_sha256": loaded.sha256,
        "completed_steps": list(checkpoint.completed_steps),
        "source_writes_enabled": checkpoint.source_writes_enabled,
        "target_writes_enabled": checkpoint.target_writes_enabled,
        "direct_rollback_allowed": checkpoint.direct_rollback_allowed,
        "reverse_sync_required": checkpoint.reverse_sync_required,
        "rpo_bytes": checkpoint.rpo_bytes,
        "rpo_seconds": checkpoint.rpo_seconds,
        "write_pause_seconds": checkpoint.write_pause_seconds,
        "restore_verified": checkpoint.restore_verified,
        "next_action": _next_action(len(checkpoint.completed_steps)),
    }


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PlannedMigrationError(
            "MIGRATION_TIME_INVALID", "migration CLI timestamp is invalid"
        ) from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise PlannedMigrationError("MIGRATION_TIME_INVALID", "migration CLI timestamp must be UTC")
    return parsed


def _approval_public_key() -> tuple[str, Ed25519PublicKey]:
    encoded = _environment("HC_MIGRATION_CUTOVER_PUBLIC_KEY_BASE64URL")
    try:
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        key = Ed25519PublicKey.from_public_bytes(raw)
    except (ValueError, binascii.Error) as exc:
        raise PlannedMigrationError(
            "MIGRATION_CONFIGURATION_INVALID", "migration approval public key is invalid"
        ) from exc
    fingerprint = hashlib.sha256(raw).hexdigest()
    if fingerprint != _environment("HC_MIGRATION_CUTOVER_KEY_SHA256"):
        raise PlannedMigrationError(
            "MIGRATION_CONFIGURATION_INVALID", "migration approval key pin differs"
        )
    return fingerprint, key


def _observer_public_key() -> tuple[str, Ed25519PublicKey]:
    encoded = _environment("HC_MIGRATION_OBSERVER_PUBLIC_KEY_BASE64URL")
    try:
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        key = Ed25519PublicKey.from_public_bytes(raw)
    except (ValueError, binascii.Error) as exc:
        raise PlannedMigrationError(
            "MIGRATION_CONFIGURATION_INVALID", "migration observer public key is invalid"
        ) from exc
    fingerprint = hashlib.sha256(raw).hexdigest()
    if fingerprint != _environment("HC_MIGRATION_OBSERVER_KEY_SHA256"):
        raise PlannedMigrationError(
            "MIGRATION_CONFIGURATION_INVALID", "migration observer key pin differs"
        )
    return fingerprint, key


def _environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value or any(character in value for character in ("\x00", "\r", "\n")):
        raise PlannedMigrationError(
            "MIGRATION_CONFIGURATION_INVALID", "required migration configuration is invalid"
        )
    return value


def _require_expected_object_mode(actual: str) -> None:
    expected = os.environ.get("HC_MIGRATION_EXPECTED_OBJECT_MODE")
    if expected is None:
        return
    if expected not in {"reuse_external", "copy_referenced", "portable"} or expected != actual:
        raise PlannedMigrationError(
            "MIGRATION_OBJECT_MODE_MISMATCH",
            "configured object migration mode differs from the signed plan",
        )


def _next_action(completed: int) -> str:
    actions = (
        "migration_source_fence_required",
        "migration_final_sync_required",
        "migration_target_verification_required",
        "migration_traffic_switch_required",
        "migration_target_write_enable_required",
        "migration_rollback_retention_required",
        "migration_completed_rollback_window_active",
    )
    return actions[completed - 1]
