"""Read-only RST3-03 reconciliation report bound to one completed restore.

This module deliberately has no write-enable or backup-catalog port.  A passed
report is evidence for later approval; it is never itself permission to run a
worker, mutate business state, or append ``RESTORE_VERIFIED``.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Mapping, Sequence
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from hc_data_platform.backup.contracts import Identifier, Sha256, canonical_json_bytes
from hc_data_platform.backup.restore import RestorePlanV1
from hc_data_platform.backup.restore_execution import LoadedRestoreCheckpoint

RESTORE_RECONCILIATION_REPORT_FORMAT: Literal["hc-platform-restore-reconciliation-report/v1"] = (
    "hc-platform-restore-reconciliation-report/v1"
)
MAX_RESTORE_RECONCILIATION_REPORT_BYTES = 2 * 1024 * 1024

ReconciliationCheckName = Literal[
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
RESTORE_RECONCILIATION_CHECKS: tuple[ReconciliationCheckName, ...] = (
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


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RestoreReconciliationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class RestoreReconciliationCheckV1(_StrictModel):
    name: ReconciliationCheckName
    status: Literal["PASS", "FAIL"]
    coverage: Literal["FULL", "SAMPLED"] = "FULL"
    total_count: int = Field(ge=0)
    checked_count: int = Field(ge=0)
    issue_count: int = Field(ge=0)
    issue_codes: tuple[Identifier, ...] = ()
    evidence_sha256: Sha256

    @model_validator(mode="after")
    def require_consistent_result(self) -> RestoreReconciliationCheckV1:
        if self.checked_count > self.total_count:
            raise ValueError("reconciliation checked count exceeds total count")
        if self.coverage == "FULL" and self.checked_count != self.total_count:
            raise ValueError("full reconciliation coverage must check every fact")
        if self.status == "PASS" and (self.issue_count or self.issue_codes):
            raise ValueError("passed reconciliation check cannot contain issues")
        if self.status == "FAIL" and (self.issue_count < 1 or not self.issue_codes):
            raise ValueError("failed reconciliation check must contain safe issue codes")
        if len(self.issue_codes) != len(set(self.issue_codes)) or self.issue_codes != tuple(
            sorted(self.issue_codes)
        ):
            raise ValueError("reconciliation issue codes must be sorted and unique")
        return self


class RestoreReconciliationReportV1(_StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        title="hc-platform-restore-reconciliation-report/v1",
        json_schema_extra={
            "$id": (
                "https://hc-data-platform.invalid/contracts/"
                "hc-platform-restore-reconciliation-report-v1.schema.json"
            ),
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    format_version: Literal["hc-platform-restore-reconciliation-report/v1"] = (
        RESTORE_RECONCILIATION_REPORT_FORMAT
    )
    report_id: Identifier
    plan_id: Identifier
    plan_checkpoint_sha256: Sha256
    restore_checkpoint_sha256: Sha256
    operation_id: Identifier
    backup_id: Identifier
    target_instance_id: Identifier
    target_environment_id: Identifier
    verifier_identity: str = Field(min_length=1, max_length=255)
    reconciled_at: AwareDatetime
    checks: tuple[RestoreReconciliationCheckV1, ...]
    overall_status: Literal["PASS", "FAIL"]
    reconciliation_passed: bool
    writes_enabled: Literal[False] = False
    workers_enabled: Literal[False] = False
    restore_verified: Literal[False] = False
    next_action: Literal["restore_smoke_and_write_enable_approval_required"] = (
        "restore_smoke_and_write_enable_approval_required"
    )

    @field_validator("reconciled_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("restore reconciliation timestamp must be UTC")
        return value

    @model_validator(mode="after")
    def require_exact_checks(self) -> RestoreReconciliationReportV1:
        if tuple(item.name for item in self.checks) != RESTORE_RECONCILIATION_CHECKS:
            raise ValueError("restore reconciliation checks differ from the V1 gate")
        passed = all(item.status == "PASS" for item in self.checks)
        if self.reconciliation_passed != passed or self.overall_status != (
            "PASS" if passed else "FAIL"
        ):
            raise ValueError("restore reconciliation aggregate status differs")
        return self


class RestoreReconciliationInspector(Protocol):
    def inspect(
        self,
        plan: RestorePlanV1,
        checkpoint: LoadedRestoreCheckpoint,
    ) -> RestoreReconciliationCheckV1: ...


def reconcile_restore(
    plan: RestorePlanV1,
    checkpoint: LoadedRestoreCheckpoint,
    *,
    inspectors: Mapping[ReconciliationCheckName, RestoreReconciliationInspector],
    verifier_identity: str,
    reconciled_at: datetime,
) -> RestoreReconciliationReportV1:
    """Run every fixed read-only check and return a non-authorizing report."""

    _bind_completed_checkpoint(plan, checkpoint)
    if reconciled_at.utcoffset() != timezone.utc.utcoffset(reconciled_at):
        raise RestoreReconciliationError(
            "RESTORE_RECONCILIATION_TIME_INVALID",
            "the restore reconciliation timestamp must be UTC",
        )
    if (
        not verifier_identity
        or len(verifier_identity) > 255
        or any(char in verifier_identity for char in ("\x00", "\r", "\n"))
    ):
        raise RestoreReconciliationError(
            "RESTORE_RECONCILIATION_VERIFIER_INVALID",
            "the restore reconciliation verifier identity is invalid",
        )
    expected_external = RESTORE_RECONCILIATION_CHECKS[1:]
    if set(inspectors) != set(expected_external):
        raise RestoreReconciliationError(
            "RESTORE_RECONCILIATION_CHECK_MISSING",
            "the exact V1 reconciliation inspector set is required",
        )

    checks: list[RestoreReconciliationCheckV1] = [_checkpoint_check(plan, checkpoint)]
    for name in expected_external:
        try:
            result = inspectors[name].inspect(plan, checkpoint)
        except Exception as exc:
            result = reconciliation_check(
                name,
                total_count=1,
                issue_count=1,
                issue_codes=("RESTORE_RECONCILIATION_INSPECTOR_ERROR",),
                evidence={"check": name, "exception_type": type(exc).__name__},
            )
        if result.name != name:
            raise RestoreReconciliationError(
                "RESTORE_RECONCILIATION_CHECK_INVALID",
                "a reconciliation inspector returned another check identity",
            )
        checks.append(result)
    passed = all(item.status == "PASS" for item in checks)
    report_material = {
        "plan_id": plan.plan_id,
        "restore_checkpoint_sha256": checkpoint.sha256,
        "verifier_identity": verifier_identity,
        "reconciled_at": reconciled_at.isoformat(),
        "checks": [item.model_dump(mode="json") for item in checks],
    }
    report_id = (
        "restore-reconciliation-"
        + hashlib.sha256(canonical_json_bytes(report_material)).hexdigest()[:24]
    )
    return RestoreReconciliationReportV1(
        report_id=report_id,
        plan_id=plan.plan_id,
        plan_checkpoint_sha256=plan.checkpoint_sha256,
        restore_checkpoint_sha256=checkpoint.sha256,
        operation_id=plan.operation_id,
        backup_id=plan.backup_id,
        target_instance_id=plan.target_instance_id,
        target_environment_id=plan.target_environment_id,
        verifier_identity=verifier_identity,
        reconciled_at=reconciled_at,
        checks=tuple(checks),
        overall_status="PASS" if passed else "FAIL",
        reconciliation_passed=passed,
    )


def publish_restore_reconciliation_report(
    report: RestoreReconciliationReportV1,
    path: Path,
) -> str:
    """Publish one canonical owner-only report, allowing only exact replay."""

    raw = canonical_json_bytes(report.model_dump(mode="json"))
    if len(raw) > MAX_RESTORE_RECONCILIATION_REPORT_BYTES:
        raise RestoreReconciliationError(
            "RESTORE_RECONCILIATION_REPORT_INVALID",
            "the restore reconciliation report exceeds its size bound",
        )
    parent = _private_directory(path.parent)
    if path.exists() or path.is_symlink():
        existing = _read_private_report(path)
        if existing != raw:
            raise RestoreReconciliationError(
                "RESTORE_RECONCILIATION_REPORT_CONFLICT",
                "an existing restore reconciliation report differs",
            )
        return hashlib.sha256(existing).hexdigest()
    temporary = parent / f".{path.name}.{os.getpid()}.tmp"
    if temporary.exists() or temporary.is_symlink():
        raise RestoreReconciliationError(
            "RESTORE_RECONCILIATION_REPORT_CONFLICT",
            "a restore reconciliation report publish is already active",
        )
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", buffering=0) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        directory_descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        with suppress(FileNotFoundError):
            temporary.unlink()
        raise
    return hashlib.sha256(raw).hexdigest()


def load_restore_reconciliation_report(path: Path) -> RestoreReconciliationReportV1:
    raw = _read_private_report(path)
    try:
        return RestoreReconciliationReportV1.model_validate_json(raw)
    except ValidationError as exc:
        raise RestoreReconciliationError(
            "RESTORE_RECONCILIATION_REPORT_INVALID",
            "the restore reconciliation report violates its strict contract",
        ) from exc


def reconciliation_check(
    name: ReconciliationCheckName,
    *,
    evidence: object,
    total_count: int,
    checked_count: int | None = None,
    coverage: Literal["FULL", "SAMPLED"] = "FULL",
    issue_codes: Sequence[str] = (),
    issue_count: int | None = None,
) -> RestoreReconciliationCheckV1:
    """Build one safe check without carrying raw identifiers or payloads."""

    codes = tuple(sorted(set(issue_codes)))
    issues = len(codes) if issue_count is None else issue_count
    checked = total_count if checked_count is None else checked_count
    return RestoreReconciliationCheckV1(
        name=name,
        status="PASS" if issues == 0 else "FAIL",
        coverage=coverage,
        total_count=total_count,
        checked_count=checked,
        issue_count=issues,
        issue_codes=codes,
        evidence_sha256=hashlib.sha256(canonical_json_bytes(evidence)).hexdigest(),
    )


def _bind_completed_checkpoint(
    plan: RestorePlanV1,
    loaded: LoadedRestoreCheckpoint,
) -> None:
    checkpoint = loaded.checkpoint
    if (
        checkpoint.plan_id != plan.plan_id
        or checkpoint.plan_checkpoint_sha256 != plan.checkpoint_sha256
        or checkpoint.operation_id != plan.operation_id
        or checkpoint.backup_id != plan.backup_id
        or checkpoint.target_instance_id != plan.target_instance_id
        or checkpoint.target_environment_id != plan.target_environment_id
    ):
        raise RestoreReconciliationError(
            "RESTORE_RECONCILIATION_CHECKPOINT_MISMATCH",
            "the restore checkpoint belongs to another plan",
        )
    if (
        checkpoint.state != "READ_ONLY_READY"
        or checkpoint.completed_steps
        != (
            "payload_verified",
            "objects_restored",
            "postgresql_restored",
            "temporal_ready",
            "services_read_only",
        )
        or checkpoint.writes_enabled
        or checkpoint.reconciliation_completed
        or checkpoint.restore_verified
    ):
        raise RestoreReconciliationError(
            "RESTORE_RECONCILIATION_CHECKPOINT_INCOMPLETE",
            "reconciliation requires a complete read-only restore checkpoint",
        )


def _checkpoint_check(
    plan: RestorePlanV1,
    loaded: LoadedRestoreCheckpoint,
) -> RestoreReconciliationCheckV1:
    checkpoint = loaded.checkpoint
    return reconciliation_check(
        "restore_checkpoint",
        total_count=len(checkpoint.receipts),
        evidence={
            "plan_id": plan.plan_id,
            "checkpoint_sha256": loaded.sha256,
            "state": checkpoint.state,
            "completed_steps": list(checkpoint.completed_steps),
            "writes_enabled": checkpoint.writes_enabled,
            "reconciliation_completed": checkpoint.reconciliation_completed,
            "restore_verified": checkpoint.restore_verified,
        },
    )


def _private_directory(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except (FileNotFoundError, OSError) as exc:
        raise RestoreReconciliationError(
            "RESTORE_RECONCILIATION_REPORT_PATH_INVALID",
            "the report directory is unavailable",
        ) from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise RestoreReconciliationError(
            "RESTORE_RECONCILIATION_REPORT_PATH_INVALID",
            "the report directory must be owner-only",
        )
    return resolved


def _read_private_report(path: Path) -> bytes:
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except (FileNotFoundError, OSError) as exc:
        raise RestoreReconciliationError(
            "RESTORE_RECONCILIATION_REPORT_INVALID",
            "the restore reconciliation report is unavailable",
        ) from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
        or not 0 < info.st_size <= MAX_RESTORE_RECONCILIATION_REPORT_BYTES
    ):
        raise RestoreReconciliationError(
            "RESTORE_RECONCILIATION_REPORT_INVALID",
            "the restore reconciliation report file is unsafe",
        )
    return resolved.read_bytes()
