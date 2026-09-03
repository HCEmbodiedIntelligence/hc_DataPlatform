"""Fail-closed RST3-04 planned-migration contracts and checkpoint state machine.

Provider operators establish PostgreSQL streaming/WAL and object replication.
This module owns the portable evidence boundary: no phase may be acknowledged
unless one exact live observation proves its safety invariants.  In particular,
target writes are impossible before zero-lag final sync, full target verification,
traffic switching, and a separately signed cutover approval.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Annotated, Literal, Protocol, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from hc_data_platform.backup.contracts import Identifier, Sha256, canonical_json_bytes

PLANNED_MIGRATION_FORMAT: Literal["hc-platform-planned-migration/v1"] = (
    "hc-platform-planned-migration/v1"
)
MIGRATION_CUTOVER_APPROVAL_FORMAT: Literal["hc-platform-migration-cutover-approval/v1"] = (
    "hc-platform-migration-cutover-approval/v1"
)
MIGRATION_CUTOVER_SIGNATURE_FORMAT: Literal[
    "hc-platform-migration-cutover-approval-signature/v1"
] = "hc-platform-migration-cutover-approval-signature/v1"
MIGRATION_OBSERVATION_FORMAT: Literal["hc-platform-migration-observation/v1"] = (
    "hc-platform-migration-observation/v1"
)
MIGRATION_OBSERVATION_SIGNATURE_FORMAT: Literal[
    "hc-platform-migration-observation-signature/v1"
] = "hc-platform-migration-observation-signature/v1"
MIGRATION_CHECKPOINT_FORMAT: Literal["hc-platform-migration-checkpoint/v1"] = (
    "hc-platform-migration-checkpoint/v1"
)

DEFAULT_MIGRATION_HEADROOM_RATIO = 0.30
MAX_WRITE_PAUSE_SECONDS = 30 * 60
MAX_PRECOPY_SECONDS = 24 * 60 * 60
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024

MigrationStep = Literal[
    "precopy_verified",
    "source_fenced",
    "final_sync_verified",
    "target_verified",
    "traffic_switched",
    "target_writes_enabled",
    "rollback_window_retained",
]
MigrationObjectMode = Literal["reuse_external", "copy_referenced", "portable"]
MIGRATION_STEPS: tuple[MigrationStep, ...] = (
    "precopy_verified",
    "source_fenced",
    "final_sync_verified",
    "target_verified",
    "traffic_switched",
    "target_writes_enabled",
    "rollback_window_retained",
)
MIGRATION_STATES = (
    "PRECOPY_READY",
    "SOURCE_FENCED",
    "FINAL_SYNCED",
    "TARGET_VERIFIED",
    "TRAFFIC_SWITCHED",
    "TARGET_WRITABLE",
    "ROLLBACK_WINDOW",
)

PostgresLsn = Annotated[str, StringConstraints(pattern=r"^[0-9A-F]+/[0-9A-F]+$")]
ResourceReference = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9+.-]*://[^\x00\r\n]+$", max_length=2048),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlannedMigrationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class MigrationEndpointV1(_StrictModel):
    platform_id: Identifier
    environment_id: Identifier
    instance_id: Identifier
    release_identity_sha256: Sha256
    postgresql_database: Identifier
    object_store_endpoint: ResourceReference
    object_bucket_reference: ResourceReference
    object_prefix: str = Field(min_length=1, max_length=1024)
    temporal_cluster_reference: ResourceReference
    temporal_namespace: Identifier

    @field_validator("object_prefix")
    @classmethod
    def require_safe_prefix(cls, value: str) -> str:
        if (
            value.startswith("/")
            or value.endswith("/")
            or "//" in value
            or ".." in value.split("/")
        ):
            raise ValueError("migration object prefix is not a safe non-root prefix")
        return value


class PlannedMigrationPlanV1(_StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        title="hc-platform-planned-migration/v1",
        json_schema_extra={
            "$id": (
                "https://hc-data-platform.invalid/contracts/"
                "hc-platform-planned-migration-v1.schema.json"
            ),
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    format_version: Literal["hc-platform-planned-migration/v1"] = PLANNED_MIGRATION_FORMAT
    plan_id: Identifier
    migration_id: Identifier
    operation_id: Identifier
    source: MigrationEndpointV1
    target: MigrationEndpointV1
    expected_postgresql_major: int = Field(ge=12, le=99)
    trusted_cutover_approval_key_sha256: Sha256
    trusted_observer_key_sha256: Sha256
    planned_at: AwareDatetime
    expires_at: AwareDatetime
    rpo_target_bytes: Literal[0] = 0
    rpo_target_seconds: Literal[0] = 0
    maximum_write_pause_seconds: int = Field(gt=0, le=MAX_WRITE_PAUSE_SECONDS)
    maximum_dns_ttl_seconds: int = Field(gt=0, le=300)
    maximum_observation_age_seconds: int = Field(gt=0, le=300)
    minimum_rollback_window_seconds: int = Field(ge=3600, le=7 * 24 * 60 * 60)
    object_migration_mode: MigrationObjectMode = "reuse_external"
    actual_referenced_bytes: int = Field(ge=0)
    database_required_bytes: int = Field(ge=0)
    temporary_required_bytes: int = Field(ge=0)
    log_required_bytes: int = Field(ge=0)
    headroom_ratio: float = Field(default=DEFAULT_MIGRATION_HEADROOM_RATIO, ge=0, le=10)
    required_capacity_bytes: int = Field(ge=0)
    portable_encryption_recipient_sha256: Sha256 | None = None
    portable_max_part_bytes: int | None = Field(default=None, gt=0)
    maximum_precopy_seconds: int = Field(gt=0, le=MAX_PRECOPY_SECONDS)
    maximum_precopy_postgresql_lag_bytes: int = Field(ge=0)
    capacity_evidence_required: Literal[True] = True
    source_remains_read_only_during_rollback_window: Literal[True] = True
    direct_rollback_after_target_writes_allowed: Literal[False] = False
    reverse_sync_required_after_target_writes: Literal[True] = True

    @field_validator("planned_at", "expires_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def require_distinct_compatible_endpoints(self) -> PlannedMigrationPlanV1:
        if self.expires_at <= self.planned_at:
            raise ValueError("planned migration validity is empty")
        if (
            self.source.platform_id != self.target.platform_id
            or self.source.environment_id == self.target.environment_id
            or self.source.instance_id == self.target.instance_id
        ):
            raise ValueError("planned migration endpoints are not distinct one-platform targets")
        if self.source.release_identity_sha256 != self.target.release_identity_sha256:
            raise ValueError("planned migration must start on the exact same release identity")
        if self.trusted_cutover_approval_key_sha256 == self.trusted_observer_key_sha256:
            raise ValueError("migration observer and cutover approver duties must be separated")
        source_objects = (
            self.source.object_store_endpoint,
            self.source.object_bucket_reference,
            self.source.object_prefix,
        )
        target_objects = (
            self.target.object_store_endpoint,
            self.target.object_bucket_reference,
            self.target.object_prefix,
        )
        if self.object_migration_mode == "reuse_external":
            if source_objects != target_objects:
                raise ValueError("reuse_external must retain the exact external object location")
        elif source_objects == target_objects:
            raise ValueError("object-copy migration requires a distinct target object location")
        portable_fields_present = (
            self.portable_encryption_recipient_sha256 is not None
            and self.portable_max_part_bytes is not None
        )
        if self.object_migration_mode == "portable" and not portable_fields_present:
            raise ValueError("portable migration requires encrypted object-package configuration")
        if self.object_migration_mode != "portable" and (
            self.portable_encryption_recipient_sha256 is not None
            or self.portable_max_part_bytes is not None
        ):
            raise ValueError("only portable migration may configure an encrypted object package")
        required = calculate_required_capacity_bytes(
            object_migration_mode=self.object_migration_mode,
            actual_referenced_bytes=self.actual_referenced_bytes,
            database_required_bytes=self.database_required_bytes,
            temporary_required_bytes=self.temporary_required_bytes,
            log_required_bytes=self.log_required_bytes,
            headroom_ratio=self.headroom_ratio,
        )
        if self.required_capacity_bytes != required:
            raise ValueError(
                "required migration capacity differs from the dynamic capacity contract"
            )
        return self


class MigrationCutoverApprovalV1(_StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        title="hc-platform-migration-cutover-approval/v1",
        json_schema_extra={
            "$id": (
                "https://hc-data-platform.invalid/contracts/"
                "hc-platform-migration-cutover-approval-v1.schema.json"
            ),
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    format_version: Literal["hc-platform-migration-cutover-approval/v1"] = (
        MIGRATION_CUTOVER_APPROVAL_FORMAT
    )
    approval_id: Identifier
    plan_id: Identifier
    plan_sha256: Sha256
    migration_id: Identifier
    operation_id: Identifier
    source_environment_id: Identifier
    target_environment_id: Identifier
    approver_identity: str = Field(min_length=1, max_length=255)
    approver_role: Literal["migration_cutover_approver"] = "migration_cutover_approver"
    approval_key_sha256: Sha256
    approved_at: AwareDatetime
    expires_at: AwareDatetime
    allow_source_fence: Literal[True] = True
    allow_traffic_switch: Literal[True] = True
    allow_target_write_enable: Literal[True] = True
    allow_source_write_enable: Literal[False] = False
    allow_dual_write: Literal[False] = False
    allow_direct_rollback_after_target_writes: Literal[False] = False

    @field_validator("approved_at", "expires_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def require_nonempty_validity(self) -> MigrationCutoverApprovalV1:
        if self.expires_at <= self.approved_at:
            raise ValueError("migration approval validity is empty")
        return self


class MigrationCutoverApprovalSignatureV1(_StrictModel):
    format_version: Literal["hc-platform-migration-cutover-approval-signature/v1"] = (
        MIGRATION_CUTOVER_SIGNATURE_FORMAT
    )
    algorithm: Literal["Ed25519"] = "Ed25519"
    approval_key_sha256: Sha256
    approval_sha256: Sha256
    signature_base64url: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


class MigrationCapacityEvidenceV1(_StrictModel):
    provider_reference: ResourceReference
    evidence_sha256: Sha256
    target_usable_capacity_bytes: int = Field(ge=0)
    measured_precopy_bytes: int = Field(ge=0)
    measured_precopy_seconds: int = Field(ge=0, le=MAX_PRECOPY_SECONDS)
    measured_final_delta_bytes: int = Field(ge=0)
    measured_final_sync_seconds: int = Field(ge=0, le=MAX_WRITE_PAUSE_SECONDS)
    measurement_started_at: AwareDatetime
    measurement_completed_at: AwareDatetime
    production_equivalent_storage: bool = False
    production_equivalent_network: bool = False
    synthetic_or_sparse_bytes: Literal[False] = False

    @field_validator("measurement_started_at", "measurement_completed_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def require_consistent_measurement(self) -> MigrationCapacityEvidenceV1:
        if self.measurement_completed_at <= self.measurement_started_at:
            raise ValueError("migration capacity measurement window is empty")
        elapsed = int((self.measurement_completed_at - self.measurement_started_at).total_seconds())
        if elapsed < self.measured_precopy_seconds:
            raise ValueError("migration capacity measurement duration is inconsistent")
        return self


class MigrationObservationV1(_StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        title="hc-platform-migration-observation/v1",
        json_schema_extra={
            "$id": (
                "https://hc-data-platform.invalid/contracts/"
                "hc-platform-migration-observation-v1.schema.json"
            ),
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    format_version: Literal["hc-platform-migration-observation/v1"] = MIGRATION_OBSERVATION_FORMAT
    migration_id: Identifier
    plan_id: Identifier
    step: MigrationStep
    observed_at: AwareDatetime
    observer_identity: str = Field(min_length=1, max_length=255)
    observer_key_sha256: Sha256
    source_release_identity_sha256: Sha256
    target_release_identity_sha256: Sha256
    postgresql_major: int = Field(ge=12, le=99)
    source_postgresql_system_identifier: int = Field(gt=0)
    target_postgresql_system_identifier: int = Field(gt=0)
    source_postgresql_timeline: int = Field(gt=0)
    target_postgresql_timeline: int = Field(gt=0)
    source_postgresql_lsn: PostgresLsn
    target_postgresql_replay_lsn: PostgresLsn
    target_postgresql_in_recovery: bool
    source_object_count: int = Field(ge=0)
    target_object_count: int = Field(ge=0)
    source_object_bytes: int = Field(ge=0)
    target_object_bytes: int = Field(ge=0)
    source_object_inventory_sha256: Sha256
    target_object_inventory_sha256: Sha256
    object_endpoint_bucket_prefix_verified: Literal[True] = True
    object_key_version_size_hash_verified: Literal[True] = True
    source_object_read_permission_verified: Literal[True] = True
    target_object_read_permission_verified: Literal[True] = True
    object_replication_pending_count: int = Field(ge=0)
    portable_package_encrypted: bool = False
    portable_package_part_count: int = Field(default=0, ge=0)
    portable_package_inventory_sha256: Sha256 | None = None
    source_temporal_inventory_sha256: Sha256
    target_temporal_inventory_sha256: Sha256
    source_writes_enabled: bool
    target_writes_enabled: bool
    source_worker_replicas: int = Field(ge=0)
    target_worker_replicas: int = Field(ge=0)
    active_source_writer_count: int = Field(ge=0)
    unexpired_source_upload_grant_count: int = Field(ge=0)
    source_read_only_fence: bool
    target_read_only_fence: bool
    target_verification_passed: bool = False
    target_verification_sha256: Sha256 | None = None
    traffic_routes_to_target: bool = False
    observed_dns_ttl_seconds: int | None = Field(default=None, gt=0)
    write_pause_seconds: int | None = Field(default=None, ge=0)
    source_retained_until: AwareDatetime | None = None
    capacity: MigrationCapacityEvidenceV1 | None = None
    evidence_sha256: Sha256

    @field_validator("observed_at", "source_retained_until")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_utc(value)

    @model_validator(mode="after")
    def reject_dual_write(self) -> MigrationObservationV1:
        if self.source_writes_enabled and self.target_writes_enabled:
            raise ValueError("planned migration can never observe dual writes")
        if (self.target_verification_sha256 is None) != (not self.target_verification_passed):
            raise ValueError("target verification status and evidence differ")
        return self


class MigrationObservationSignatureV1(_StrictModel):
    format_version: Literal["hc-platform-migration-observation-signature/v1"] = (
        MIGRATION_OBSERVATION_SIGNATURE_FORMAT
    )
    algorithm: Literal["Ed25519"] = "Ed25519"
    observer_key_sha256: Sha256
    observation_sha256: Sha256
    signature_base64url: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


class MigrationStepReceiptV1(_StrictModel):
    step: MigrationStep
    completed_at: AwareDatetime
    observer_identity: str = Field(min_length=1, max_length=255)
    observation_sha256: Sha256
    evidence_sha256: Sha256
    postgresql_cutover_lsn: PostgresLsn | None = None
    object_inventory_sha256: Sha256 | None = None
    write_pause_seconds: int | None = Field(default=None, ge=0)
    source_writes_enabled: bool
    target_writes_enabled: bool
    direct_rollback_allowed: bool
    reverse_sync_required: bool

    @field_validator("completed_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class MigrationCheckpointV1(_StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        title="hc-platform-migration-checkpoint/v1",
        json_schema_extra={
            "$id": (
                "https://hc-data-platform.invalid/contracts/"
                "hc-platform-migration-checkpoint-v1.schema.json"
            ),
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    format_version: Literal["hc-platform-migration-checkpoint/v1"] = MIGRATION_CHECKPOINT_FORMAT
    plan_id: Identifier
    plan_sha256: Sha256
    migration_id: Identifier
    operation_id: Identifier
    approval_id: Identifier
    approval_sha256: Sha256
    execution_owner_id: Identifier
    fencing_token: int = Field(gt=0)
    state_version: int = Field(gt=0)
    previous_checkpoint_sha256: Sha256 | None = None
    completed_steps: tuple[MigrationStep, ...]
    receipts: tuple[MigrationStepReceiptV1, ...]
    state: Literal[
        "PRECOPY_READY",
        "SOURCE_FENCED",
        "FINAL_SYNCED",
        "TARGET_VERIFIED",
        "TRAFFIC_SWITCHED",
        "TARGET_WRITABLE",
        "ROLLBACK_WINDOW",
    ]
    updated_at: AwareDatetime
    source_writes_enabled: bool
    target_writes_enabled: bool
    direct_rollback_allowed: bool
    reverse_sync_required: bool
    rpo_bytes: int = Field(ge=0)
    rpo_seconds: int = Field(ge=0)
    write_pause_seconds: int | None = Field(default=None, ge=0)
    restore_verified: Literal[False] = False

    @field_validator("updated_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def require_prefix_and_safety(self) -> MigrationCheckpointV1:
        count = len(self.completed_steps)
        if count < 1 or count > len(MIGRATION_STEPS):
            raise ValueError("migration checkpoint step count is invalid")
        if self.completed_steps != MIGRATION_STEPS[:count]:
            raise ValueError("migration checkpoint steps are not an exact prefix")
        if tuple(item.step for item in self.receipts) != self.completed_steps:
            raise ValueError("migration checkpoint receipts differ from completed steps")
        if self.state != MIGRATION_STATES[count - 1] or self.state_version != count:
            raise ValueError("migration checkpoint state/version differs from its prefix")
        if count == 1 and self.previous_checkpoint_sha256 is not None:
            raise ValueError("first migration checkpoint cannot have a predecessor")
        if count > 1 and self.previous_checkpoint_sha256 is None:
            raise ValueError("advanced migration checkpoint must bind its predecessor")
        if self.source_writes_enabled and self.target_writes_enabled:
            raise ValueError("migration checkpoint cannot allow dual writes")
        target_writable = count >= MIGRATION_STEPS.index("target_writes_enabled") + 1
        if target_writable:
            if (
                self.source_writes_enabled
                or not self.target_writes_enabled
                or self.direct_rollback_allowed
                or not self.reverse_sync_required
                or self.rpo_bytes
                or self.rpo_seconds
                or self.write_pause_seconds is None
                or self.write_pause_seconds > MAX_WRITE_PAUSE_SECONDS
            ):
                raise ValueError("writable target checkpoint violates cutover invariants")
        elif count >= 2 and (self.source_writes_enabled or self.target_writes_enabled):
            raise ValueError("fenced migration checkpoint unexpectedly enables writes")
        return self


@dataclass(frozen=True)
class VerifiedMigrationApproval:
    approval: MigrationCutoverApprovalV1
    approval_sha256: str
    signature_sha256: str


@dataclass(frozen=True)
class LoadedMigrationCheckpoint:
    checkpoint: MigrationCheckpointV1
    sha256: str


class MigrationStepPort(Protocol):
    def observe(
        self,
        plan: PlannedMigrationPlanV1,
        checkpoint: MigrationCheckpointV1 | None,
    ) -> MigrationObservationV1: ...


class StaticMigrationObservationPort:
    def __init__(self, observation: MigrationObservationV1) -> None:
        self._observation = observation

    def observe(
        self,
        plan: PlannedMigrationPlanV1,
        checkpoint: MigrationCheckpointV1 | None,
    ) -> MigrationObservationV1:
        del plan, checkpoint
        return self._observation


class FileMigrationCheckpointStore:
    """Owner-only atomic current checkpoint with predecessor CAS and exact replay."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> LoadedMigrationCheckpoint | None:
        if not self.path.exists() and not self.path.is_symlink():
            return None
        raw = _read_private(self.path, maximum_bytes=MAX_DOCUMENT_BYTES)
        try:
            checkpoint = MigrationCheckpointV1.model_validate(
                _json_object(raw, "MIGRATION_CHECKPOINT")
            )
        except PlannedMigrationError:
            raise
        except ValidationError as exc:
            raise PlannedMigrationError(
                "MIGRATION_CHECKPOINT_INVALID", "the migration checkpoint is invalid"
            ) from exc
        return LoadedMigrationCheckpoint(checkpoint, hashlib.sha256(raw).hexdigest())

    def save(
        self,
        checkpoint: MigrationCheckpointV1,
        *,
        expected_previous_sha256: str | None,
    ) -> LoadedMigrationCheckpoint:
        current = self.load()
        current_sha = current.sha256 if current else None
        if current_sha != expected_previous_sha256:
            raise PlannedMigrationError(
                "MIGRATION_CHECKPOINT_CONFLICT", "the migration checkpoint changed concurrently"
            )
        if checkpoint.previous_checkpoint_sha256 != expected_previous_sha256:
            raise PlannedMigrationError(
                "MIGRATION_CHECKPOINT_CHAIN_INVALID",
                "the migration checkpoint predecessor differs",
            )
        expected_version = 1 if current is None else current.checkpoint.state_version + 1
        if checkpoint.state_version != expected_version:
            raise PlannedMigrationError(
                "MIGRATION_CHECKPOINT_CHAIN_INVALID",
                "the migration checkpoint does not advance exactly one phase",
            )
        if current and not _same_checkpoint_identity(current.checkpoint, checkpoint):
            raise PlannedMigrationError(
                "MIGRATION_CHECKPOINT_CHAIN_INVALID",
                "immutable migration checkpoint identity changed",
            )
        raw = canonical_json_bytes(checkpoint.model_dump(mode="json"))
        parent = _private_directory(self.path.parent)
        temporary = parent / f".{self.path.name}.{os.getpid()}.tmp"
        if temporary.exists() or temporary.is_symlink():
            raise PlannedMigrationError(
                "MIGRATION_CHECKPOINT_CONFLICT", "a checkpoint write is already active"
            )
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", buffering=0) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
            os.chmod(self.path, 0o600)
            directory_descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            if temporary.exists() and not temporary.is_symlink():
                temporary.unlink()
        return LoadedMigrationCheckpoint(checkpoint, hashlib.sha256(raw).hexdigest())


def advance_planned_migration(
    plan: PlannedMigrationPlanV1,
    approval: VerifiedMigrationApproval,
    *,
    execution_owner_id: str,
    fencing_token: int,
    observation_port: MigrationStepPort,
    checkpoint_store: FileMigrationCheckpointStore,
    now: datetime,
) -> LoadedMigrationCheckpoint:
    """Validate and persist exactly one safe migration phase."""

    _require_utc(now)
    plan_sha256 = hashlib.sha256(canonical_json_bytes(plan.model_dump(mode="json"))).hexdigest()
    _bind_approval(plan, plan_sha256, approval, now=now)
    if not execution_owner_id or fencing_token < 1:
        raise PlannedMigrationError(
            "MIGRATION_EXECUTION_IDENTITY_INVALID", "migration owner or fencing token is invalid"
        )
    loaded = checkpoint_store.load()
    checkpoint = loaded.checkpoint if loaded else None
    if checkpoint is not None:
        if (
            checkpoint.plan_id != plan.plan_id
            or checkpoint.plan_sha256 != plan_sha256
            or checkpoint.migration_id != plan.migration_id
            or checkpoint.operation_id != plan.operation_id
            or checkpoint.approval_id != approval.approval.approval_id
            or checkpoint.approval_sha256 != approval.approval_sha256
            or checkpoint.execution_owner_id != execution_owner_id
            or checkpoint.fencing_token != fencing_token
        ):
            raise PlannedMigrationError(
                "MIGRATION_CHECKPOINT_MISMATCH", "migration checkpoint belongs to another execution"
            )
        if len(checkpoint.completed_steps) == len(MIGRATION_STEPS):
            return cast(LoadedMigrationCheckpoint, loaded)
    expected_step = MIGRATION_STEPS[0 if checkpoint is None else len(checkpoint.completed_steps)]
    observation = observation_port.observe(plan, checkpoint)
    observation_sha = hashlib.sha256(
        canonical_json_bytes(observation.model_dump(mode="json"))
    ).hexdigest()
    if checkpoint and observation.step == checkpoint.completed_steps[-1]:
        if checkpoint.receipts[-1].observation_sha256 == observation_sha:
            return cast(LoadedMigrationCheckpoint, loaded)
        raise PlannedMigrationError(
            "MIGRATION_OBSERVATION_CONFLICT", "replayed observation bytes differ"
        )
    if observation.step != expected_step:
        raise PlannedMigrationError(
            "MIGRATION_STEP_ORDER_INVALID", "migration observation is not the next exact phase"
        )
    receipt = _validate_observation(plan, observation, observation_sha, now=now)
    previous_steps = checkpoint.completed_steps if checkpoint else ()
    previous_receipts = checkpoint.receipts if checkpoint else ()
    completed_steps = (*previous_steps, expected_step)
    receipts = (*previous_receipts, receipt)
    target_writable = expected_step in {"target_writes_enabled", "rollback_window_retained"}
    source_writable = expected_step == "precopy_verified"
    final_sync_done = len(completed_steps) >= MIGRATION_STEPS.index("final_sync_verified") + 1
    write_pause = observation.write_pause_seconds if target_writable else None
    next_checkpoint = MigrationCheckpointV1(
        plan_id=plan.plan_id,
        plan_sha256=plan_sha256,
        migration_id=plan.migration_id,
        operation_id=plan.operation_id,
        approval_id=approval.approval.approval_id,
        approval_sha256=approval.approval_sha256,
        execution_owner_id=execution_owner_id,
        fencing_token=fencing_token,
        state_version=len(completed_steps),
        previous_checkpoint_sha256=loaded.sha256 if loaded else None,
        completed_steps=completed_steps,
        receipts=receipts,
        state=cast(AnyMigrationState, MIGRATION_STATES[len(completed_steps) - 1]),
        updated_at=observation.observed_at,
        source_writes_enabled=source_writable,
        target_writes_enabled=target_writable,
        direct_rollback_allowed=not target_writable,
        reverse_sync_required=target_writable,
        rpo_bytes=0 if final_sync_done else _lsn_lag(observation),
        rpo_seconds=0 if final_sync_done else 1,
        write_pause_seconds=write_pause,
    )
    return checkpoint_store.save(
        next_checkpoint,
        expected_previous_sha256=loaded.sha256 if loaded else None,
    )


AnyMigrationState = Literal[
    "PRECOPY_READY",
    "SOURCE_FENCED",
    "FINAL_SYNCED",
    "TARGET_VERIFIED",
    "TRAFFIC_SWITCHED",
    "TARGET_WRITABLE",
    "ROLLBACK_WINDOW",
]


def verify_migration_cutover_approval(
    plan: PlannedMigrationPlanV1,
    approval_path: Path,
    signature_path: Path,
    *,
    public_key: Ed25519PublicKey,
    now: datetime,
) -> VerifiedMigrationApproval:
    _require_utc(now)
    approval_raw = _read_private(approval_path, maximum_bytes=MAX_DOCUMENT_BYTES)
    signature_raw = _read_private(signature_path, maximum_bytes=64 * 1024)
    try:
        approval = MigrationCutoverApprovalV1.model_validate(
            _json_object(approval_raw, "MIGRATION_APPROVAL")
        )
        signature = MigrationCutoverApprovalSignatureV1.model_validate(
            _json_object(signature_raw, "MIGRATION_APPROVAL_SIGNATURE")
        )
    except PlannedMigrationError:
        raise
    except ValidationError as exc:
        raise PlannedMigrationError(
            "MIGRATION_APPROVAL_INVALID", "the migration approval is invalid"
        ) from exc
    approval_sha = hashlib.sha256(approval_raw).hexdigest()
    public_raw = public_key.public_bytes_raw()
    fingerprint = hashlib.sha256(public_raw).hexdigest()
    if (
        fingerprint != plan.trusted_cutover_approval_key_sha256
        or approval.approval_key_sha256 != fingerprint
        or signature.approval_key_sha256 != fingerprint
        or signature.approval_sha256 != approval_sha
    ):
        raise PlannedMigrationError(
            "MIGRATION_APPROVAL_KEY_MISMATCH", "migration approval trust binding differs"
        )
    try:
        encoded = signature.signature_base64url
        decoded = base64.urlsafe_b64decode(encoded + "==")
        if len(decoded) != 64:
            raise ValueError("invalid signature length")
        public_key.verify(decoded, approval_raw)
    except (InvalidSignature, ValueError, binascii.Error) as exc:
        raise PlannedMigrationError(
            "MIGRATION_APPROVAL_SIGNATURE_INVALID", "migration approval signature is invalid"
        ) from exc
    verified = VerifiedMigrationApproval(
        approval=approval,
        approval_sha256=approval_sha,
        signature_sha256=hashlib.sha256(signature_raw).hexdigest(),
    )
    plan_sha = hashlib.sha256(canonical_json_bytes(plan.model_dump(mode="json"))).hexdigest()
    _bind_approval(plan, plan_sha, verified, now=now)
    return verified


def load_planned_migration_plan(path: Path) -> PlannedMigrationPlanV1:
    raw = _read_private(path, maximum_bytes=MAX_DOCUMENT_BYTES)
    try:
        return PlannedMigrationPlanV1.model_validate(_json_object(raw, "MIGRATION_PLAN"))
    except PlannedMigrationError:
        raise
    except ValidationError as exc:
        raise PlannedMigrationError(
            "MIGRATION_PLAN_INVALID", "the planned migration document is invalid"
        ) from exc


def load_migration_observation(path: Path) -> MigrationObservationV1:
    raw = _read_private(path, maximum_bytes=MAX_DOCUMENT_BYTES)
    try:
        return MigrationObservationV1.model_validate(_json_object(raw, "MIGRATION_OBSERVATION"))
    except PlannedMigrationError:
        raise
    except ValidationError as exc:
        raise PlannedMigrationError(
            "MIGRATION_OBSERVATION_INVALID", "the migration observation is invalid"
        ) from exc


def verify_migration_observation_files(
    plan: PlannedMigrationPlanV1,
    observation_path: Path,
    signature_path: Path,
    *,
    public_key: Ed25519PublicKey,
) -> MigrationObservationV1:
    observation_raw = _read_private(observation_path, maximum_bytes=MAX_DOCUMENT_BYTES)
    signature_raw = _read_private(signature_path, maximum_bytes=64 * 1024)
    try:
        observation = MigrationObservationV1.model_validate(
            _json_object(observation_raw, "MIGRATION_OBSERVATION")
        )
        signature = MigrationObservationSignatureV1.model_validate(
            _json_object(signature_raw, "MIGRATION_OBSERVATION_SIGNATURE")
        )
    except PlannedMigrationError:
        raise
    except ValidationError as exc:
        raise PlannedMigrationError(
            "MIGRATION_OBSERVATION_INVALID", "the migration observation is invalid"
        ) from exc
    fingerprint = hashlib.sha256(public_key.public_bytes_raw()).hexdigest()
    observation_sha = hashlib.sha256(observation_raw).hexdigest()
    if (
        fingerprint != plan.trusted_observer_key_sha256
        or observation.observer_key_sha256 != fingerprint
        or signature.observer_key_sha256 != fingerprint
        or signature.observation_sha256 != observation_sha
    ):
        raise PlannedMigrationError(
            "MIGRATION_OBSERVER_KEY_MISMATCH", "migration observer trust binding differs"
        )
    try:
        decoded = base64.urlsafe_b64decode(signature.signature_base64url + "==")
        if len(decoded) != 64:
            raise ValueError("invalid signature length")
        public_key.verify(decoded, observation_raw)
    except (InvalidSignature, ValueError, binascii.Error) as exc:
        raise PlannedMigrationError(
            "MIGRATION_OBSERVATION_SIGNATURE_INVALID",
            "migration observation signature is invalid",
        ) from exc
    return observation


def _validate_observation(
    plan: PlannedMigrationPlanV1,
    observation: MigrationObservationV1,
    observation_sha: str,
    *,
    now: datetime,
) -> MigrationStepReceiptV1:
    if (
        observation.observed_at > now
        or now - observation.observed_at > _seconds(plan.maximum_observation_age_seconds)
        or now >= plan.expires_at
    ):
        raise PlannedMigrationError(
            "MIGRATION_OBSERVATION_TIME_INVALID",
            "migration observation is future, stale, or the plan expired",
        )
    if (
        observation.migration_id != plan.migration_id
        or observation.plan_id != plan.plan_id
        or observation.source_release_identity_sha256 != plan.source.release_identity_sha256
        or observation.target_release_identity_sha256 != plan.target.release_identity_sha256
        or observation.observer_key_sha256 != plan.trusted_observer_key_sha256
        or observation.postgresql_major != plan.expected_postgresql_major
        or observation.source_postgresql_system_identifier
        != observation.target_postgresql_system_identifier
    ):
        raise PlannedMigrationError(
            "MIGRATION_OBSERVATION_IDENTITY_MISMATCH",
            "migration observation identities differ from the exact plan",
        )
    step = observation.step
    lag = _lsn_lag(observation)
    objects_equal = (
        observation.source_object_count == observation.target_object_count
        and observation.source_object_bytes == observation.target_object_bytes
        and observation.source_object_inventory_sha256 == observation.target_object_inventory_sha256
    )
    object_inventory_matches_plan = (
        objects_equal and observation.source_object_bytes == plan.actual_referenced_bytes
    )
    temporal_equal = (
        observation.source_temporal_inventory_sha256 == observation.target_temporal_inventory_sha256
    )
    target_writable_step = step in {"target_writes_enabled", "rollback_window_retained"}
    if (
        not target_writable_step
        and observation.target_postgresql_timeline != observation.source_postgresql_timeline
    ) or (
        target_writable_step
        and observation.target_postgresql_timeline <= observation.source_postgresql_timeline
    ):
        raise PlannedMigrationError(
            "MIGRATION_POSTGRESQL_TIMELINE_INVALID",
            "PostgreSQL timelines do not prove replica continuity and target promotion",
        )
    if step == "precopy_verified":
        capacity = observation.capacity
        required_precopy_bytes = (
            0 if plan.object_migration_mode == "reuse_external" else plan.actual_referenced_bytes
        )
        portable_evidence_valid = (
            observation.portable_package_encrypted
            and (
                (plan.actual_referenced_bytes == 0 and observation.portable_package_part_count == 0)
                or (
                    plan.actual_referenced_bytes > 0 and observation.portable_package_part_count > 0
                )
            )
            and observation.portable_package_inventory_sha256
            == observation.source_object_inventory_sha256
        )
        if plan.object_migration_mode != "portable":
            portable_evidence_valid = (
                not observation.portable_package_encrypted
                and observation.portable_package_part_count == 0
                and observation.portable_package_inventory_sha256 is None
            )
        if (
            not observation.source_writes_enabled
            or observation.target_writes_enabled
            or observation.target_read_only_fence is not True
            or observation.target_postgresql_in_recovery is not True
            or lag > plan.maximum_precopy_postgresql_lag_bytes
            or not object_inventory_matches_plan
            or observation.object_replication_pending_count
            or capacity is None
            or capacity.target_usable_capacity_bytes < plan.required_capacity_bytes
            or capacity.measured_precopy_bytes != required_precopy_bytes
            or capacity.measured_precopy_seconds > plan.maximum_precopy_seconds
            or not portable_evidence_valid
        ):
            raise PlannedMigrationError(
                "MIGRATION_PRECOPY_NOT_READY", "online pre-copy evidence does not satisfy the plan"
            )
    elif step == "source_fenced":
        _require_fenced(observation)
        if not observation.target_postgresql_in_recovery:
            raise PlannedMigrationError(
                "MIGRATION_SOURCE_FENCE_INVALID", "target must remain a read-only replica"
            )
    elif step == "final_sync_verified":
        _require_fenced(observation)
        if (
            lag
            or not object_inventory_matches_plan
            or observation.object_replication_pending_count
            or not temporal_equal
            or not observation.target_postgresql_in_recovery
        ):
            raise PlannedMigrationError(
                "MIGRATION_RPO_ZERO_NOT_MET", "final synchronization has non-zero divergence"
            )
    elif step == "target_verified":
        _require_fenced(observation)
        if (
            lag
            or not object_inventory_matches_plan
            or observation.object_replication_pending_count
            or not temporal_equal
            or not observation.target_verification_passed
            or observation.target_verification_sha256 is None
        ):
            raise PlannedMigrationError(
                "MIGRATION_TARGET_VERIFICATION_FAILED", "target read-only verification failed"
            )
    elif step == "traffic_switched":
        _require_fenced(observation)
        if (
            lag
            or not object_inventory_matches_plan
            or observation.object_replication_pending_count
            or not temporal_equal
            or not observation.target_postgresql_in_recovery
            or not observation.target_verification_passed
            or observation.target_verification_sha256 is None
            or not observation.traffic_routes_to_target
            or observation.observed_dns_ttl_seconds is None
            or observation.observed_dns_ttl_seconds > plan.maximum_dns_ttl_seconds
        ):
            raise PlannedMigrationError(
                "MIGRATION_TRAFFIC_SWITCH_UNVERIFIED", "traffic does not resolve to the target"
            )
    elif step == "target_writes_enabled":
        if (
            observation.source_writes_enabled
            or not observation.target_writes_enabled
            or not observation.source_read_only_fence
            or observation.target_read_only_fence
            or observation.source_worker_replicas
            or observation.target_worker_replicas < 1
            or observation.write_pause_seconds is None
            or observation.write_pause_seconds > plan.maximum_write_pause_seconds
            or lag
            or not object_inventory_matches_plan
            or observation.object_replication_pending_count
            or not temporal_equal
            or observation.target_postgresql_in_recovery
            or not observation.traffic_routes_to_target
        ):
            raise PlannedMigrationError(
                "MIGRATION_WRITE_ENABLE_UNSAFE", "target write-enable evidence is unsafe"
            )
    else:
        if (
            observation.source_writes_enabled
            or not observation.target_writes_enabled
            or not observation.source_read_only_fence
            or observation.target_read_only_fence
            or observation.source_worker_replicas
            or observation.target_worker_replicas < 1
            or observation.source_retained_until is None
            or observation.source_retained_until
            < observation.observed_at + _seconds(plan.minimum_rollback_window_seconds)
            or observation.write_pause_seconds is None
            or observation.write_pause_seconds > plan.maximum_write_pause_seconds
            or observation.target_postgresql_in_recovery
            or lag
            or not object_inventory_matches_plan
            or observation.object_replication_pending_count
            or not temporal_equal
            or not observation.traffic_routes_to_target
        ):
            raise PlannedMigrationError(
                "MIGRATION_ROLLBACK_WINDOW_INVALID", "source rollback retention is insufficient"
            )
    target_writable = step in {"target_writes_enabled", "rollback_window_retained"}
    return MigrationStepReceiptV1(
        step=step,
        completed_at=observation.observed_at,
        observer_identity=observation.observer_identity,
        observation_sha256=observation_sha,
        evidence_sha256=observation.evidence_sha256,
        postgresql_cutover_lsn=(
            observation.source_postgresql_lsn
            if step in MIGRATION_STEPS[MIGRATION_STEPS.index("final_sync_verified") :]
            else None
        ),
        object_inventory_sha256=(
            observation.source_object_inventory_sha256 if objects_equal else None
        ),
        write_pause_seconds=observation.write_pause_seconds if target_writable else None,
        source_writes_enabled=step == "precopy_verified",
        target_writes_enabled=target_writable,
        direct_rollback_allowed=not target_writable,
        reverse_sync_required=target_writable,
    )


def _require_fenced(observation: MigrationObservationV1) -> None:
    if (
        observation.source_writes_enabled
        or observation.target_writes_enabled
        or not observation.source_read_only_fence
        or not observation.target_read_only_fence
        or observation.source_worker_replicas
        or observation.target_worker_replicas
        or observation.active_source_writer_count
        or observation.unexpired_source_upload_grant_count
    ):
        raise PlannedMigrationError(
            "MIGRATION_SOURCE_NOT_FENCED", "source or target writer inventory is not fully fenced"
        )


def _bind_approval(
    plan: PlannedMigrationPlanV1,
    plan_sha256: str,
    verified: VerifiedMigrationApproval,
    *,
    now: datetime,
) -> None:
    approval = verified.approval
    if (
        approval.plan_id != plan.plan_id
        or approval.plan_sha256 != plan_sha256
        or approval.migration_id != plan.migration_id
        or approval.operation_id != plan.operation_id
        or approval.source_environment_id != plan.source.environment_id
        or approval.target_environment_id != plan.target.environment_id
        or approval.approval_key_sha256 != plan.trusted_cutover_approval_key_sha256
    ):
        raise PlannedMigrationError(
            "MIGRATION_APPROVAL_MISMATCH", "migration approval differs from the exact plan"
        )
    if now < approval.approved_at or now >= approval.expires_at:
        raise PlannedMigrationError(
            "MIGRATION_APPROVAL_EXPIRED", "migration approval is not currently valid"
        )


def _same_checkpoint_identity(
    previous: MigrationCheckpointV1, current: MigrationCheckpointV1
) -> bool:
    return (
        current.completed_steps[:-1] == previous.completed_steps
        and current.receipts[:-1] == previous.receipts
        and current.plan_id == previous.plan_id
        and current.plan_sha256 == previous.plan_sha256
        and current.migration_id == previous.migration_id
        and current.operation_id == previous.operation_id
        and current.approval_id == previous.approval_id
        and current.approval_sha256 == previous.approval_sha256
        and current.execution_owner_id == previous.execution_owner_id
        and current.fencing_token == previous.fencing_token
    )


def _lsn_integer(value: str) -> int:
    high, low = value.split("/", 1)
    return (int(high, 16) << 32) + int(low, 16)


def _lsn_lag(observation: MigrationObservationV1) -> int:
    source = _lsn_integer(observation.source_postgresql_lsn)
    target = _lsn_integer(observation.target_postgresql_replay_lsn)
    if target > source:
        raise PlannedMigrationError(
            "MIGRATION_POSTGRESQL_LSN_INVALID", "target replay LSN is ahead of source coordinate"
        )
    return source - target


def _require_utc(value: datetime) -> datetime:
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError("planned migration timestamps must be UTC")
    return value


def _seconds(value: int) -> timedelta:
    return timedelta(seconds=value)


def _private_directory(path: Path) -> Path:
    try:
        absolute = path.absolute()
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except (FileNotFoundError, OSError) as exc:
        raise PlannedMigrationError(
            "MIGRATION_DOCUMENT_UNAVAILABLE", "migration directory is unavailable"
        ) from exc
    if (
        resolved != absolute
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_mode & 0o077
        or metadata.st_uid != os.geteuid()
    ):
        raise PlannedMigrationError(
            "MIGRATION_PRIVATE_PATH_INVALID", "migration directory must be owner-only"
        )
    return resolved


def _read_private(path: Path, *, maximum_bytes: int) -> bytes:
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except (FileNotFoundError, OSError) as exc:
        raise PlannedMigrationError(
            "MIGRATION_DOCUMENT_UNAVAILABLE", "migration document is unavailable"
        ) from exc
    if (
        resolved != path.absolute()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_mode & 0o077
        or metadata.st_uid != os.geteuid()
        or metadata.st_size < 2
        or metadata.st_size > maximum_bytes
    ):
        raise PlannedMigrationError(
            "MIGRATION_PRIVATE_PATH_INVALID", "migration document is not owner-only regular data"
        )
    return resolved.read_bytes()


def _json_object(raw: bytes, label: str) -> Mapping[str, object]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PlannedMigrationError(f"{label}_INVALID", "migration document is malformed") from exc
    if not isinstance(value, Mapping):
        raise PlannedMigrationError(f"{label}_INVALID", "migration document must be an object")
    return cast(Mapping[str, object], value)


def calculate_required_capacity_bytes(
    *,
    object_migration_mode: MigrationObjectMode,
    actual_referenced_bytes: int,
    database_required_bytes: int,
    temporary_required_bytes: int,
    log_required_bytes: int,
    headroom_ratio: float,
) -> int:
    """Return the exact mode-specific target capacity without a fixed data floor."""

    if object_migration_mode == "reuse_external":
        return database_required_bytes + temporary_required_bytes + log_required_bytes
    multiplier = Decimal("1") + Decimal(str(headroom_ratio))
    return int(
        (Decimal(actual_referenced_bytes) * multiplier).to_integral_value(rounding=ROUND_CEILING)
    )


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result
