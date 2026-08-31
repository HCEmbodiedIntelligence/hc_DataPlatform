"""Fail-closed adjacent-release compatibility and rollback contracts."""

from __future__ import annotations

import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CompatibilityResult(str, Enum):
    PASS = "PASS"
    NOT_PASSED = "NOT_PASSED"


class DatabaseCompatibilityV1(_StrictModel):
    change_mode: Literal["no_schema_change", "expand_only", "expand_then_contract"]
    source_migration_count: int = Field(gt=0)
    source_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_migration_count: int = Field(gt=0)
    target_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expand_migrations: tuple[str, ...]
    data_migration: Literal["none", "online_checkpointed", "maintenance_window"]
    contract_migrations: tuple[str, ...]
    old_app_on_target_schema: CompatibilityResult
    new_app_on_source_schema: CompatibilityResult
    database_downgrade_allowed: Literal[False] = False


class TemporalCompatibilityV1(_StrictModel):
    source_patch_ids: tuple[str, ...]
    target_patch_ids: tuple[str, ...]
    added_patch_ids: tuple[str, ...]
    removed_patch_ids: tuple[str, ...]
    existing_history_replay: CompatibilityResult
    source_worker_on_target_history: CompatibilityResult
    target_worker_on_source_history: CompatibilityResult
    worker_build_id_routing: Literal["ENABLED", "NOT_IMPLEMENTED_RELEASE_BLOCKER"]
    production_server_version_range: str = Field(min_length=1, max_length=127)


class APICompatibilityV1(_StrictModel):
    source_openapi_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_openapi_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    old_client_on_target_server: CompatibilityResult
    target_client_on_source_server: CompatibilityResult
    removed_operations: tuple[str, ...]
    removed_schema_fields: tuple[str, ...]
    generated_client_drift_check: Literal["required"] = "required"


class ConfigurationCompatibilityV1(_StrictModel):
    source_values_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_values_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    added_required_keys_without_defaults: tuple[str, ...]
    removed_keys: tuple[str, ...]
    old_app_with_target_config: CompatibilityResult
    target_app_with_source_config: CompatibilityResult
    secret_key_removal_allowed: Literal[False] = False


class RollbackContractV1(_StrictModel):
    before_contract_application: Literal["application_digest_rollback_allowed"]
    after_contract_application: Literal[
        "application_rollback_if_schema_compatible",
        "prohibited_restore_or_forward_fix_required",
        "not_applicable_no_contract_migration",
    ]
    exact_source_artifacts_required: Literal[True] = True
    database_down_migration_allowed: Literal[False] = False
    temporal_source_build_must_remain_available: Literal[True] = True


class AdjacentReleaseEdgeV1(_StrictModel):
    schema_version: Literal["release-compatibility-edge/v1"] = "release-compatibility-edge/v1"
    edge_id: str = Field(min_length=1, max_length=128)
    source_version: str
    target_version: str
    release_type: Literal["patch", "minor", "major"]
    status: Literal[
        "DESIGN_ONLY_TARGET_ARTIFACTS_NOT_BUILT",
        "BLOCKED",
        "READY_FOR_CANARY",
        "RELEASED",
    ]
    target_artifacts_built_and_signed: bool
    database: DatabaseCompatibilityV1
    temporal: TemporalCompatibilityV1
    api: APICompatibilityV1
    configuration: ConfigurationCompatibilityV1
    rollback: RollbackContractV1
    release_blockers: tuple[str, ...]

    @model_validator(mode="after")
    def validate_adjacent_edge(self) -> AdjacentReleaseEdgeV1:
        source = _parse_semver(self.source_version)
        target = _parse_semver(self.target_version)
        if target <= source:
            raise ValueError("target release must be newer than source release")
        expected_type = (
            "major" if target[0] != source[0] else "minor" if target[1] != source[1] else "patch"
        )
        if self.release_type != expected_type:
            raise ValueError("release_type does not match the semantic-version edge")

        source_patches = set(self.temporal.source_patch_ids)
        target_patches = set(self.temporal.target_patch_ids)
        if set(self.temporal.added_patch_ids) != target_patches - source_patches:
            raise ValueError("Temporal added_patch_ids do not match the patch sets")
        if set(self.temporal.removed_patch_ids) != source_patches - target_patches:
            raise ValueError("Temporal removed_patch_ids do not match the patch sets")

        if self.database.change_mode == "no_schema_change":
            if (
                self.database.source_migration_count != self.database.target_migration_count
                or self.database.source_manifest_sha256 != self.database.target_manifest_sha256
                or self.database.expand_migrations
                or self.database.contract_migrations
                or self.database.data_migration != "none"
            ):
                raise ValueError("no_schema_change must preserve the exact migration boundary")
        elif self.database.target_migration_count <= self.database.source_migration_count:
            raise ValueError("schema-changing releases must append migrations")
        if self.database.change_mode == "expand_only" and self.database.contract_migrations:
            raise ValueError("expand_only edges cannot contain contract migrations")
        if (
            self.database.change_mode == "expand_then_contract"
            and not self.database.contract_migrations
        ):
            raise ValueError("expand_then_contract requires explicit contract migrations")

        if self.status in {"READY_FOR_CANARY", "RELEASED"}:
            required_results = (
                self.database.old_app_on_target_schema,
                self.database.new_app_on_source_schema,
                self.temporal.existing_history_replay,
                self.temporal.source_worker_on_target_history,
                self.temporal.target_worker_on_source_history,
                self.api.old_client_on_target_server,
                self.api.target_client_on_source_server,
                self.configuration.old_app_with_target_config,
                self.configuration.target_app_with_source_config,
            )
            if any(result is not CompatibilityResult.PASS for result in required_results):
                raise ValueError(
                    "ready/released edges require every compatibility direction to pass"
                )
            if not self.target_artifacts_built_and_signed:
                raise ValueError("ready/released edges require signed immutable target artifacts")
            if self.temporal.worker_build_id_routing != "ENABLED":
                raise ValueError("ready/released edges require Temporal worker build routing")
            if self.release_blockers:
                raise ValueError("ready/released edges cannot retain release blockers")
        return self


class UpgradePhase(str, Enum):
    PREFLIGHT = "PREFLIGHT"
    EXPAND_APPLIED = "EXPAND_APPLIED"
    CANARY = "CANARY"
    ROLLED_OUT = "ROLLED_OUT"
    CONTRACT_APPLIED = "CONTRACT_APPLIED"


class RollbackDecisionV1(_StrictModel):
    allowed: bool
    application_action: Literal["none", "deploy_exact_source_digests", "forward_fix_or_restore"]
    database_action: Literal["none", "keep_forward_schema", "restore_verified_backup"]
    reason_code: str = Field(min_length=1, max_length=127)


class ReleaseContractError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _parse_semver(value: str) -> tuple[int, int, int]:
    match = _SEMVER.fullmatch(value)
    if match is None:
        raise ValueError("release versions must be stable semantic versions")
    return tuple(int(component) for component in match.groups())  # type: ignore[return-value]


def evaluate_rollback(edge: AdjacentReleaseEdgeV1, phase: UpgradePhase) -> RollbackDecisionV1:
    """Return the only safe rollback action at an exact upgrade phase."""

    if phase is UpgradePhase.PREFLIGHT:
        return RollbackDecisionV1(
            allowed=True,
            application_action="none",
            database_action="none",
            reason_code="RELEASE_NOT_APPLIED",
        )
    if phase is UpgradePhase.CONTRACT_APPLIED:
        if not edge.database.contract_migrations:
            raise ReleaseContractError(
                "RELEASE_PHASE_INVALID", "this edge has no contract migration phase"
            )
        if edge.rollback.after_contract_application == "prohibited_restore_or_forward_fix_required":
            return RollbackDecisionV1(
                allowed=False,
                application_action="forward_fix_or_restore",
                database_action="restore_verified_backup",
                reason_code="RELEASE_ROLLBACK_BLOCKED_AFTER_CONTRACT",
            )
        if edge.rollback.after_contract_application == "application_rollback_if_schema_compatible":
            return RollbackDecisionV1(
                allowed=True,
                application_action="deploy_exact_source_digests",
                database_action="keep_forward_schema",
                reason_code="RELEASE_SOURCE_SCHEMA_COMPATIBLE_AFTER_CONTRACT",
            )
        raise ReleaseContractError(
            "RELEASE_ROLLBACK_POLICY_INVALID", "contract phase lacks an applicable rollback rule"
        )
    if (
        edge.database.old_app_on_target_schema is not CompatibilityResult.PASS
        or edge.temporal.source_worker_on_target_history is not CompatibilityResult.PASS
        or edge.api.target_client_on_source_server is not CompatibilityResult.PASS
        or edge.configuration.old_app_with_target_config is not CompatibilityResult.PASS
    ):
        return RollbackDecisionV1(
            allowed=False,
            application_action="forward_fix_or_restore",
            database_action="restore_verified_backup",
            reason_code="RELEASE_SOURCE_NOT_COMPATIBLE_WITH_FORWARD_STATE",
        )
    return RollbackDecisionV1(
        allowed=True,
        application_action="deploy_exact_source_digests",
        database_action="keep_forward_schema",
        reason_code="RELEASE_APPLICATION_ROLLBACK_BEFORE_CONTRACT",
    )
