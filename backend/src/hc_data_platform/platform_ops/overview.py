"""Host-redacted platform operations overview and upgrade preflight."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from hc_data_platform.backup.catalog import BackupCatalogRepository, CatalogStatus
from hc_data_platform.backup.contracts import Sha256
from hc_data_platform.platform_control.release_identity import (
    PlatformReleaseIdentityV1,
    ReleaseDigest,
    ReleaseId,
)

from .instances import InstanceRole, PlatformInstanceService, ReadinessState


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlatformOperationsNode(_StrictModel):
    """An operational node projection with every host and process locator removed."""

    node_ref: str = Field(pattern=r"^id-hmac-sha256:[0-9a-f]{64}$")
    role: InstanceRole
    release_id: ReleaseId
    release_manifest_digest: ReleaseDigest
    started_at: datetime
    last_heartbeat_at: datetime
    readiness: ReadinessState
    failed_checks: tuple[Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")], ...]
    applied_config_revision: int = Field(ge=0)
    stale: bool


class PlatformOperationsBackup(_StrictModel):
    """A backup projection without repository, environment, or object locators."""

    backup_ref: str = Field(pattern=r"^id-hmac-sha256:[0-9a-f]{64}$")
    format_version: Literal["hc-platform-backup/v1"]
    mode: Literal["portable", "snapshot"]
    status: CatalogStatus
    release_manifest_sha256: Sha256
    backup_created_at: datetime
    backup_completed_at: datetime
    status_occurred_at: datetime


PreflightCheckCode = Literal[
    "RELEASE_IDENTITY",
    "NODE_CONVERGENCE",
    "NODE_READINESS",
    "CONFIG_CONVERGENCE",
    "VERIFIED_BACKUP",
    "VERIFIED_RESTORE",
    "CENTRAL_LOG_SEARCH",
]
PreflightStatus = Literal["PASS", "BLOCKED"]


class UpgradePreflightCheck(_StrictModel):
    code: PreflightCheckCode
    status: PreflightStatus
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")


class UpgradePreflight(_StrictModel):
    status: Literal["READY", "BLOCKED"]
    checks: tuple[UpgradePreflightCheck, ...] = Field(min_length=7, max_length=7)


class PlatformOperationsOverview(_StrictModel):
    format_version: Literal["hc-platform-operations-overview/v1"] = (
        "hc-platform-operations-overview/v1"
    )
    observed_at: datetime
    environment_ref: str = Field(pattern=r"^id-hmac-sha256:[0-9a-f]{64}$")
    release: PlatformReleaseIdentityV1
    node_count: int = Field(ge=0)
    nodes: tuple[PlatformOperationsNode, ...]
    backup_count: int = Field(ge=0, le=20)
    backups: tuple[PlatformOperationsBackup, ...] = Field(max_length=20)
    upgrade_preflight: UpgradePreflight

    @model_validator(mode="after")
    def counts_match_items(self) -> PlatformOperationsOverview:
        if self.node_count != len(self.nodes) or self.backup_count != len(self.backups):
            raise ValueError("platform operations overview counts differ from item counts")
        return self


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PlatformOperationsOverviewService:
    def __init__(
        self,
        *,
        instance_service: PlatformInstanceService,
        backup_repository: BackupCatalogRepository,
        release_identity: PlatformReleaseIdentityV1,
        environment_id: str,
        reference_secret: str,
        central_log_search_configured: bool,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._instance_service = instance_service
        self._backup_repository = backup_repository
        self._release = release_identity
        self._environment_id = environment_id
        self._reference_key = reference_secret.encode()
        self._central_log_search_configured = central_log_search_configured
        self._clock = clock

    def overview(self) -> PlatformOperationsOverview:
        instance_page = self._instance_service.list_instances()
        backup_page = self._backup_repository.list_page(
            source_environment_id=self._environment_id,
            status=None,
            cursor=None,
            limit=20,
        )
        nodes = tuple(
            PlatformOperationsNode(
                node_ref=self._reference(str(instance.instance_id)),
                role=instance.role,
                release_id=instance.release_id,
                release_manifest_digest=instance.release_manifest_digest,
                started_at=instance.started_at,
                last_heartbeat_at=instance.last_heartbeat_at,
                readiness=instance.readiness.status,
                failed_checks=instance.readiness.failed_checks,
                applied_config_revision=instance.applied_config_revision,
                stale=instance.stale,
            )
            for instance in instance_page.instances
        )
        backups = tuple(
            PlatformOperationsBackup(
                backup_ref=self._reference(item.backup_id),
                format_version=item.format_version,
                mode=item.mode,
                status=item.status,
                release_manifest_sha256=item.release_manifest_sha256,
                backup_created_at=item.backup_created_at,
                backup_completed_at=item.backup_completed_at,
                status_occurred_at=item.status_occurred_at,
            )
            for item in backup_page.items
        )
        preflight = self._preflight(nodes=nodes, backups=backups)
        return PlatformOperationsOverview(
            observed_at=self._clock(),
            environment_ref=self._reference(self._environment_id),
            release=self._release,
            node_count=len(nodes),
            nodes=nodes,
            backup_count=len(backups),
            backups=backups,
            upgrade_preflight=preflight,
        )

    def _preflight(
        self,
        *,
        nodes: tuple[PlatformOperationsNode, ...],
        backups: tuple[PlatformOperationsBackup, ...],
    ) -> UpgradePreflight:
        active = tuple(node for node in nodes if not node.stale)
        release_ready = (
            self._release.release_id != "unreleased"
            and self._release.git_commit != "unknown"
            and self._release.release_manifest_digest != "unreleased"
            and self._release.migration_manifest_digest != "unreleased"
        )
        expected_roles: set[InstanceRole] = {"frontend", "api", "worker", "media-worker"}
        observed_roles = {node.role for node in active}
        nodes_converged = (
            bool(active)
            and expected_roles <= observed_roles
            and all(
                node.release_id == self._release.release_id
                and node.release_manifest_digest == self._release.release_manifest_digest
                for node in active
            )
        )
        nodes_ready = bool(active) and all(node.readiness == "ready" for node in active)
        config_converged = (
            bool(active) and len({node.applied_config_revision for node in active}) == 1
        )
        verified_backup = any(
            backup.status in {"INTEGRITY_VERIFIED", "RESTORE_VERIFIED"}
            and backup.release_manifest_sha256
            == self._release.release_manifest_digest.removeprefix("sha256:")
            for backup in backups
        )
        verified_restore = any(
            backup.status == "RESTORE_VERIFIED"
            and backup.release_manifest_sha256
            == self._release.release_manifest_digest.removeprefix("sha256:")
            for backup in backups
        )
        conditions: tuple[tuple[PreflightCheckCode, bool, str, str], ...] = (
            (
                "RELEASE_IDENTITY",
                release_ready,
                "RELEASE_IDENTITY_VERIFIED",
                "RELEASE_IDENTITY_UNVERIFIED",
            ),
            (
                "NODE_CONVERGENCE",
                nodes_converged,
                "REQUIRED_ROLES_CONVERGED",
                "REQUIRED_ROLES_NOT_CONVERGED",
            ),
            (
                "NODE_READINESS",
                nodes_ready,
                "ACTIVE_NODES_READY",
                "ACTIVE_NODES_NOT_READY",
            ),
            (
                "CONFIG_CONVERGENCE",
                config_converged,
                "CONFIG_REVISION_CONVERGED",
                "CONFIG_REVISION_DIVERGED",
            ),
            (
                "VERIFIED_BACKUP",
                verified_backup,
                "CURRENT_RELEASE_BACKUP_VERIFIED",
                "CURRENT_RELEASE_BACKUP_MISSING",
            ),
            (
                "VERIFIED_RESTORE",
                verified_restore,
                "CURRENT_RELEASE_RESTORE_VERIFIED",
                "CURRENT_RELEASE_RESTORE_EVIDENCE_MISSING",
            ),
            (
                "CENTRAL_LOG_SEARCH",
                self._central_log_search_configured,
                "CENTRAL_LOG_SEARCH_CONFIGURED",
                "CENTRAL_LOG_SEARCH_UNAVAILABLE",
            ),
        )
        checks = tuple(
            UpgradePreflightCheck(
                code=code,
                status="PASS" if passed else "BLOCKED",
                reason_code=passed_reason if passed else blocked_reason,
            )
            for code, passed, passed_reason, blocked_reason in conditions
        )
        return UpgradePreflight(
            status="READY" if all(check.status == "PASS" for check in checks) else "BLOCKED",
            checks=checks,
        )

    def _reference(self, value: str) -> str:
        digest = hmac.new(self._reference_key, value.encode(), hashlib.sha256).hexdigest()
        return f"id-hmac-sha256:{digest}"


__all__ = [
    "PlatformOperationsBackup",
    "PlatformOperationsNode",
    "PlatformOperationsOverview",
    "PlatformOperationsOverviewService",
    "UpgradePreflight",
    "UpgradePreflightCheck",
]
