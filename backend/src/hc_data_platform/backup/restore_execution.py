"""Checkpointed RST3-02 restore execution that always ends read-only.

The orchestrator owns ordering and checkpoint safety, not domain mutation details.
Each concrete step adapter must be independently idempotent.  No transition in
this module can enable platform writes or produce ``RESTORE_VERIFIED``.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import json
import os
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal, Protocol, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
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
from hc_data_platform.backup.restore import RestorePlanV1

RESTORE_APPROVAL_FORMAT: Literal["hc-platform-restore-approval/v1"] = (
    "hc-platform-restore-approval/v1"
)
RESTORE_APPROVAL_SIGNATURE_FORMAT: Literal["hc-platform-restore-approval-signature/v1"] = (
    "hc-platform-restore-approval-signature/v1"
)
RESTORE_CHECKPOINT_FORMAT: Literal["hc-platform-restore-checkpoint/v1"] = (
    "hc-platform-restore-checkpoint/v1"
)
MAX_RESTORE_APPROVAL_BYTES = 256 * 1024
MAX_RESTORE_APPROVAL_SIGNATURE_BYTES = 16 * 1024
MAX_RESTORE_CHECKPOINT_BYTES = 1024 * 1024

RestoreStep = Literal[
    "payload_verified",
    "objects_restored",
    "postgresql_restored",
    "temporal_ready",
    "services_read_only",
]
RESTORE_STEPS: tuple[RestoreStep, ...] = (
    "payload_verified",
    "objects_restored",
    "postgresql_restored",
    "temporal_ready",
    "services_read_only",
)
ApprovalReference = Annotated[
    str,
    StringConstraints(pattern=r"^approval://[A-Za-z0-9._:-]+(?:/[A-Za-z0-9._:-]+)+$"),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RestoreExecutionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class RestoreApprovalV1(_StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        title="hc-platform-restore-approval/v1",
        json_schema_extra={
            "$id": (
                "https://hc-data-platform.invalid/contracts/"
                "hc-platform-restore-approval-v1.schema.json"
            ),
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    format_version: Literal["hc-platform-restore-approval/v1"] = RESTORE_APPROVAL_FORMAT
    approval_id: Identifier
    approval_reference: ApprovalReference
    plan_id: Identifier
    plan_sha256: Sha256
    plan_checkpoint_sha256: Sha256
    operation_id: Identifier
    backup_id: Identifier
    target_instance_id: Identifier
    target_environment_id: Identifier
    approver_identity: str = Field(min_length=1, max_length=255)
    approver_role: Literal["restore_approver"] = "restore_approver"
    approval_key_sha256: Sha256
    approved_at: AwareDatetime
    expires_at: AwareDatetime
    allow_restore_mutation: Literal[True] = True
    allow_write_enable: Literal[False] = False
    allow_restore_verified: Literal[False] = False

    @field_validator("approved_at", "expires_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("restore approval timestamps must be UTC")
        return value

    @model_validator(mode="after")
    def require_nonempty_validity(self) -> RestoreApprovalV1:
        if self.expires_at <= self.approved_at:
            raise ValueError("restore approval validity is empty")
        return self


class RestoreApprovalSignatureV1(_StrictModel):
    format_version: Literal["hc-platform-restore-approval-signature/v1"] = (
        RESTORE_APPROVAL_SIGNATURE_FORMAT
    )
    algorithm: Literal["Ed25519"] = "Ed25519"
    approval_key_sha256: Sha256
    approval_sha256: Sha256
    signature_base64url: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


class RestoreStepResultV1(_StrictModel):
    step: RestoreStep
    evidence_sha256: Sha256
    executor_identity: str = Field(min_length=1, max_length=255)
    target_mutated: bool
    writes_enabled: Literal[False] = False
    safe_checkpoint: Literal[True] = True


class RestoreStepReceiptV1(RestoreStepResultV1):
    completed_at: AwareDatetime
    fence_evidence_before_sha256: Sha256
    fence_evidence_after_sha256: Sha256

    @field_validator("completed_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("restore receipt timestamp must be UTC")
        return value


class RestoreExecutionCheckpointV1(_StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        title="hc-platform-restore-checkpoint/v1",
        json_schema_extra={
            "$id": (
                "https://hc-data-platform.invalid/contracts/"
                "hc-platform-restore-checkpoint-v1.schema.json"
            ),
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    format_version: Literal["hc-platform-restore-checkpoint/v1"] = RESTORE_CHECKPOINT_FORMAT
    plan_id: Identifier
    plan_checkpoint_sha256: Sha256
    operation_id: Identifier
    backup_id: Identifier
    target_instance_id: Identifier
    target_environment_id: Identifier
    approval_id: Identifier
    approval_reference: ApprovalReference
    approval_sha256: Sha256
    execution_owner_id: Identifier
    fencing_token: int = Field(gt=0)
    state_version: int = Field(gt=0)
    previous_checkpoint_sha256: Sha256 | None = None
    completed_steps: tuple[RestoreStep, ...]
    receipts: tuple[RestoreStepReceiptV1, ...]
    state: Literal[
        "PAYLOAD_VERIFIED",
        "OBJECTS_RESTORED",
        "POSTGRESQL_RESTORED",
        "TEMPORAL_READY",
        "READ_ONLY_READY",
    ]
    updated_at: AwareDatetime
    writes_enabled: Literal[False] = False
    reconciliation_completed: Literal[False] = False
    restore_verified: Literal[False] = False

    @field_validator("updated_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("restore checkpoint timestamp must be UTC")
        return value

    @model_validator(mode="after")
    def require_prefix_state(self) -> RestoreExecutionCheckpointV1:
        count = len(self.completed_steps)
        if count < 1 or count > len(RESTORE_STEPS):
            raise ValueError("restore checkpoint step count is invalid")
        if self.completed_steps != RESTORE_STEPS[:count]:
            raise ValueError("restore checkpoint steps are not an exact prefix")
        if tuple(item.step for item in self.receipts) != self.completed_steps:
            raise ValueError("restore checkpoint receipts do not match completed steps")
        states = (
            "PAYLOAD_VERIFIED",
            "OBJECTS_RESTORED",
            "POSTGRESQL_RESTORED",
            "TEMPORAL_READY",
            "READ_ONLY_READY",
        )
        if self.state != states[count - 1] or self.state_version != count:
            raise ValueError("restore checkpoint state/version differs from its step prefix")
        if count == 1 and self.previous_checkpoint_sha256 is not None:
            raise ValueError("the first restore checkpoint cannot have a predecessor")
        if count > 1 and self.previous_checkpoint_sha256 is None:
            raise ValueError("a resumed restore checkpoint must bind its predecessor")
        return self


@dataclass(frozen=True)
class LoadedRestoreCheckpoint:
    checkpoint: RestoreExecutionCheckpointV1
    sha256: str


@dataclass(frozen=True)
class VerifiedRestoreApproval:
    approval: RestoreApprovalV1
    approval_sha256: str
    signature_sha256: str


class RestoreExecutionStepPort(Protocol):
    def execute(
        self,
        plan: RestorePlanV1,
        checkpoint: RestoreExecutionCheckpointV1 | None,
    ) -> RestoreStepResultV1: ...


class RestoreExecutionFencePort(Protocol):
    def assert_read_only(
        self,
        plan: RestorePlanV1,
        *,
        execution_owner_id: str,
        fencing_token: int,
    ) -> str: ...


class RestoreCheckpointStore(Protocol):
    def load(self) -> LoadedRestoreCheckpoint | None: ...

    def save(
        self,
        checkpoint: RestoreExecutionCheckpointV1,
        *,
        expected_previous_sha256: str | None,
    ) -> LoadedRestoreCheckpoint: ...


class FileRestoreCheckpointStore:
    """Owner-only atomic current checkpoint with predecessor CAS."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> LoadedRestoreCheckpoint | None:
        if not self.path.exists() and not self.path.is_symlink():
            return None
        raw = _read_private(self.path, maximum_bytes=MAX_RESTORE_CHECKPOINT_BYTES)
        try:
            value = _json_object(raw, "RESTORE_CHECKPOINT")
            checkpoint = RestoreExecutionCheckpointV1.model_validate(value)
        except RestoreExecutionError:
            raise
        except ValidationError as exc:
            raise RestoreExecutionError(
                "RESTORE_CHECKPOINT_INVALID", "the restore checkpoint is invalid"
            ) from exc
        return LoadedRestoreCheckpoint(checkpoint, hashlib.sha256(raw).hexdigest())

    def save(
        self,
        checkpoint: RestoreExecutionCheckpointV1,
        *,
        expected_previous_sha256: str | None,
    ) -> LoadedRestoreCheckpoint:
        current = self.load()
        current_sha = current.sha256 if current is not None else None
        if current_sha != expected_previous_sha256:
            raise RestoreExecutionError(
                "RESTORE_CHECKPOINT_CONFLICT", "the restore checkpoint changed concurrently"
            )
        if checkpoint.previous_checkpoint_sha256 != expected_previous_sha256:
            raise RestoreExecutionError(
                "RESTORE_CHECKPOINT_CHAIN_INVALID",
                "the restore checkpoint predecessor binding differs",
            )
        expected_version = 1 if current is None else current.checkpoint.state_version + 1
        if checkpoint.state_version != expected_version:
            raise RestoreExecutionError(
                "RESTORE_CHECKPOINT_CHAIN_INVALID",
                "the restore checkpoint does not advance exactly one state",
            )
        if current is not None:
            previous = current.checkpoint
            if (
                checkpoint.completed_steps[:-1] != previous.completed_steps
                or checkpoint.receipts[:-1] != previous.receipts
                or checkpoint.plan_id != previous.plan_id
                or checkpoint.plan_checkpoint_sha256 != previous.plan_checkpoint_sha256
                or checkpoint.operation_id != previous.operation_id
                or checkpoint.backup_id != previous.backup_id
                or checkpoint.target_instance_id != previous.target_instance_id
                or checkpoint.target_environment_id != previous.target_environment_id
                or checkpoint.approval_id != previous.approval_id
                or checkpoint.approval_reference != previous.approval_reference
                or checkpoint.approval_sha256 != previous.approval_sha256
                or checkpoint.execution_owner_id != previous.execution_owner_id
                or checkpoint.fencing_token != previous.fencing_token
            ):
                raise RestoreExecutionError(
                    "RESTORE_CHECKPOINT_CHAIN_INVALID",
                    "the restore checkpoint changed immutable chain evidence",
                )
        raw = canonical_json_bytes(checkpoint.model_dump(mode="json"))
        parent = _private_directory(self.path.parent)
        temporary = parent / f".{self.path.name}.{os.getpid()}.tmp"
        if temporary.exists() or temporary.is_symlink():
            raise RestoreExecutionError(
                "RESTORE_CHECKPOINT_CONFLICT", "a restore checkpoint write is already active"
            )
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", buffering=0) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
            directory_descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()
            raise
        return LoadedRestoreCheckpoint(checkpoint, hashlib.sha256(raw).hexdigest())


def verify_restore_approval(
    approval_bytes: bytes,
    signature_bytes: bytes,
    *,
    plan: RestorePlanV1,
    public_keys_by_sha256: Mapping[str, Ed25519PublicKey],
    now: datetime,
) -> VerifiedRestoreApproval:
    if len(approval_bytes) > MAX_RESTORE_APPROVAL_BYTES:
        raise RestoreExecutionError("RESTORE_APPROVAL_INVALID", "restore approval is oversized")
    if len(signature_bytes) > MAX_RESTORE_APPROVAL_SIGNATURE_BYTES:
        raise RestoreExecutionError(
            "RESTORE_APPROVAL_SIGNATURE_INVALID", "restore approval signature is oversized"
        )
    try:
        approval = RestoreApprovalV1.model_validate(
            _json_object(approval_bytes, "RESTORE_APPROVAL")
        )
        signature = RestoreApprovalSignatureV1.model_validate(
            _json_object(signature_bytes, "RESTORE_APPROVAL_SIGNATURE")
        )
    except RestoreExecutionError:
        raise
    except ValidationError as exc:
        raise RestoreExecutionError(
            "RESTORE_APPROVAL_INVALID", "restore approval violates its strict contract"
        ) from exc
    canonical = canonical_json_bytes(_json_object(approval_bytes, "RESTORE_APPROVAL"))
    approval_sha = hashlib.sha256(canonical).hexdigest()
    if (
        signature.approval_key_sha256 != approval.approval_key_sha256
        or signature.approval_sha256 != approval_sha
    ):
        raise RestoreExecutionError(
            "RESTORE_APPROVAL_SIGNATURE_INVALID", "restore approval signature binding differs"
        )
    public_key = public_keys_by_sha256.get(approval.approval_key_sha256)
    if public_key is None:
        raise RestoreExecutionError(
            "RESTORE_APPROVAL_KEY_UNTRUSTED", "restore approval key is not trusted"
        )
    raw_key = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    if hashlib.sha256(raw_key).hexdigest() != approval.approval_key_sha256:
        raise RestoreExecutionError(
            "RESTORE_APPROVAL_KEY_UNTRUSTED", "restore approval key fingerprint differs"
        )
    try:
        padding = "=" * (-len(signature.signature_base64url) % 4)
        signed = base64.b64decode(
            signature.signature_base64url + padding,
            altchars=b"-_",
            validate=True,
        )
        public_key.verify(signed, canonical)
    except (ValueError, binascii.Error, InvalidSignature) as exc:
        raise RestoreExecutionError(
            "RESTORE_APPROVAL_SIGNATURE_INVALID", "restore approval signature is invalid"
        ) from exc
    if approval.approval_key_sha256 == plan.backup_signing_key_sha256:
        raise RestoreExecutionError(
            "RESTORE_APPROVAL_DUTY_CONFLICT",
            "the backup signing key cannot approve restore mutation",
        )
    if (
        approval.plan_id != plan.plan_id
        or approval.plan_sha256
        != hashlib.sha256(canonical_json_bytes(plan.model_dump(mode="json"))).hexdigest()
        or approval.plan_checkpoint_sha256 != plan.checkpoint_sha256
        or approval.operation_id != plan.operation_id
        or approval.backup_id != plan.backup_id
        or approval.target_instance_id != plan.target_instance_id
        or approval.target_environment_id != plan.target_environment_id
    ):
        raise RestoreExecutionError(
            "RESTORE_APPROVAL_PLAN_MISMATCH", "restore approval is for another exact plan"
        )
    current = now.astimezone(timezone.utc)
    if not approval.approved_at <= current < approval.expires_at:
        raise RestoreExecutionError(
            "RESTORE_APPROVAL_EXPIRED", "restore approval is not valid at execution time"
        )
    canonical_signature = canonical_json_bytes(
        _json_object(signature_bytes, "RESTORE_APPROVAL_SIGNATURE")
    )
    return VerifiedRestoreApproval(
        approval=approval,
        approval_sha256=approval_sha,
        signature_sha256=hashlib.sha256(canonical_signature).hexdigest(),
    )


def verify_restore_approval_files(
    approval_path: Path,
    signature_path: Path,
    *,
    plan: RestorePlanV1,
    public_keys_by_sha256: Mapping[str, Ed25519PublicKey],
    now: datetime,
) -> VerifiedRestoreApproval:
    approval_bytes = _read_owner_only_input(
        approval_path,
        maximum_bytes=MAX_RESTORE_APPROVAL_BYTES,
        code="RESTORE_APPROVAL_INVALID",
    )
    signature_bytes = _read_owner_only_input(
        signature_path,
        maximum_bytes=MAX_RESTORE_APPROVAL_SIGNATURE_BYTES,
        code="RESTORE_APPROVAL_SIGNATURE_INVALID",
    )
    return verify_restore_approval(
        approval_bytes,
        signature_bytes,
        plan=plan,
        public_keys_by_sha256=public_keys_by_sha256,
        now=now,
    )


def execute_restore(
    plan: RestorePlanV1,
    *,
    approval: VerifiedRestoreApproval,
    execution_owner_id: str,
    fencing_token: int,
    fence: RestoreExecutionFencePort,
    steps: Mapping[RestoreStep, RestoreExecutionStepPort],
    checkpoints: RestoreCheckpointStore,
    clock: Callable[[], datetime] | None = None,
) -> LoadedRestoreCheckpoint:
    """Run or resume the exact step prefix; successful completion remains read-only."""

    if plan.writes_enabled or not plan.dry_run or plan.payloads_downloaded:
        raise RestoreExecutionError(
            "RESTORE_PLAN_UNSAFE", "restore execution requires an unmodified read-only plan"
        )
    authorization = approval.approval
    if (
        authorization.plan_id != plan.plan_id
        or authorization.plan_sha256
        != hashlib.sha256(canonical_json_bytes(plan.model_dump(mode="json"))).hexdigest()
        or authorization.plan_checkpoint_sha256 != plan.checkpoint_sha256
        or not authorization.allow_restore_mutation
        or authorization.allow_write_enable
        or authorization.allow_restore_verified
    ):
        raise RestoreExecutionError(
            "RESTORE_APPROVAL_PLAN_MISMATCH", "restore approval does not authorize this plan"
        )
    missing = [step for step in RESTORE_STEPS if step not in steps]
    if missing:
        raise RestoreExecutionError(
            "RESTORE_EXECUTION_STEP_MISSING", "one or more restore execution steps are absent"
        )
    now = clock or (lambda: datetime.now(timezone.utc))
    started_at = now().astimezone(timezone.utc)
    if not authorization.approved_at <= started_at < authorization.expires_at:
        raise RestoreExecutionError(
            "RESTORE_APPROVAL_EXPIRED", "restore approval is not valid at execution time"
        )
    loaded = checkpoints.load()
    if loaded is not None:
        _bind_checkpoint(
            loaded.checkpoint,
            plan=plan,
            approval=approval,
            execution_owner_id=execution_owner_id,
            fencing_token=fencing_token,
        )
    completed = len(loaded.checkpoint.completed_steps) if loaded is not None else 0
    if completed == len(RESTORE_STEPS):
        fence.assert_read_only(
            plan,
            execution_owner_id=execution_owner_id,
            fencing_token=fencing_token,
        )
        assert loaded is not None
        return loaded

    for step in RESTORE_STEPS[completed:]:
        before = fence.assert_read_only(
            plan,
            execution_owner_id=execution_owner_id,
            fencing_token=fencing_token,
        )
        _require_sha(before, code="RESTORE_FENCE_EVIDENCE_INVALID")
        previous = loaded.checkpoint if loaded is not None else None
        result = steps[step].execute(plan, previous)
        if result.step != step or result.writes_enabled or not result.safe_checkpoint:
            raise RestoreExecutionError(
                "RESTORE_STEP_RESULT_INVALID", "a restore step returned an unsafe result"
            )
        after = fence.assert_read_only(
            plan,
            execution_owner_id=execution_owner_id,
            fencing_token=fencing_token,
        )
        _require_sha(after, code="RESTORE_FENCE_EVIDENCE_INVALID")
        timestamp = now().astimezone(timezone.utc)
        receipt = RestoreStepReceiptV1(
            **result.model_dump(mode="python"),
            completed_at=timestamp,
            fence_evidence_before_sha256=before,
            fence_evidence_after_sha256=after,
        )
        prior_receipts = previous.receipts if previous is not None else ()
        new_receipts = (*prior_receipts, receipt)
        count = len(new_receipts)
        checkpoint = RestoreExecutionCheckpointV1(
            plan_id=plan.plan_id,
            plan_checkpoint_sha256=plan.checkpoint_sha256,
            operation_id=plan.operation_id,
            backup_id=plan.backup_id,
            target_instance_id=plan.target_instance_id,
            target_environment_id=plan.target_environment_id,
            approval_id=authorization.approval_id,
            approval_reference=authorization.approval_reference,
            approval_sha256=approval.approval_sha256,
            execution_owner_id=execution_owner_id,
            fencing_token=fencing_token,
            state_version=count,
            previous_checkpoint_sha256=loaded.sha256 if loaded is not None else None,
            completed_steps=RESTORE_STEPS[:count],
            receipts=new_receipts,
            state=(
                "PAYLOAD_VERIFIED",
                "OBJECTS_RESTORED",
                "POSTGRESQL_RESTORED",
                "TEMPORAL_READY",
                "READ_ONLY_READY",
            )[count - 1],
            updated_at=timestamp,
        )
        loaded = checkpoints.save(
            checkpoint,
            expected_previous_sha256=loaded.sha256 if loaded is not None else None,
        )
    assert loaded is not None
    return loaded


def load_restore_execution_checkpoint(path: Path) -> LoadedRestoreCheckpoint:
    loaded = FileRestoreCheckpointStore(path).load()
    if loaded is None:
        raise RestoreExecutionError(
            "RESTORE_CHECKPOINT_MISSING", "the restore checkpoint is absent"
        )
    return loaded


def _bind_checkpoint(
    checkpoint: RestoreExecutionCheckpointV1,
    *,
    plan: RestorePlanV1,
    approval: VerifiedRestoreApproval,
    execution_owner_id: str,
    fencing_token: int,
) -> None:
    if (
        checkpoint.plan_id != plan.plan_id
        or checkpoint.plan_checkpoint_sha256 != plan.checkpoint_sha256
        or checkpoint.operation_id != plan.operation_id
        or checkpoint.backup_id != plan.backup_id
        or checkpoint.target_instance_id != plan.target_instance_id
        or checkpoint.target_environment_id != plan.target_environment_id
    ):
        raise RestoreExecutionError(
            "RESTORE_CHECKPOINT_PLAN_MISMATCH", "restore checkpoint belongs to another plan"
        )
    if (
        checkpoint.approval_id != approval.approval.approval_id
        or checkpoint.approval_reference != approval.approval.approval_reference
        or checkpoint.approval_sha256 != approval.approval_sha256
    ):
        raise RestoreExecutionError(
            "RESTORE_CHECKPOINT_APPROVAL_MISMATCH",
            "restore checkpoint belongs to another signed approval",
        )
    if (
        checkpoint.execution_owner_id != execution_owner_id
        or checkpoint.fencing_token != fencing_token
    ):
        raise RestoreExecutionError(
            "RESTORE_CHECKPOINT_OWNER_MISMATCH", "restore checkpoint owner or fence differs"
        )


def _require_sha(value: str, *, code: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise RestoreExecutionError(code, "restore evidence digest is invalid")


def _private_directory(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except (FileNotFoundError, OSError) as exc:
        raise RestoreExecutionError(
            "RESTORE_CHECKPOINT_PATH_INVALID", "checkpoint directory is unavailable"
        ) from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise RestoreExecutionError(
            "RESTORE_CHECKPOINT_PATH_INVALID", "checkpoint directory must be owner-only"
        )
    return resolved


def _read_private(path: Path, *, maximum_bytes: int) -> bytes:
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except (FileNotFoundError, OSError) as exc:
        raise RestoreExecutionError(
            "RESTORE_CHECKPOINT_INVALID", "restore checkpoint is unavailable"
        ) from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
        or not 0 < info.st_size <= maximum_bytes
    ):
        raise RestoreExecutionError(
            "RESTORE_CHECKPOINT_INVALID", "restore checkpoint file is unsafe"
        )
    return resolved.read_bytes()


def _read_owner_only_input(path: Path, *, maximum_bytes: int, code: str) -> bytes:
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except (FileNotFoundError, OSError) as exc:
        raise RestoreExecutionError(code, "restore authorization input is unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
        or not 0 < info.st_size <= maximum_bytes
    ):
        raise RestoreExecutionError(code, "restore authorization input is unsafe")
    return resolved.read_bytes()


def _json_object(raw: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except RestoreExecutionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RestoreExecutionError(f"{label}_INVALID", "restore JSON input is invalid") from exc
    if not isinstance(value, dict):
        raise RestoreExecutionError(f"{label}_INVALID", "restore JSON input must be an object")
    return cast(dict[str, object], value)


def _unique_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result
