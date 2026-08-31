"""Whole-platform maintenance and high-availability control contracts."""

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
from hc_data_platform.platform_control.release_contract import (
    AdjacentReleaseEdgeV1,
    ReleaseContractError,
    RollbackDecisionV1,
    UpgradePhase,
    evaluate_rollback,
)
from hc_data_platform.platform_control.release_identity import (
    ComponentRole,
    PlatformReleaseIdentityV1,
    release_identity_from_settings,
)

__all__ = [
    "ALLOWED_MAINTENANCE_TRANSITIONS",
    "EnvironmentFenceV1",
    "MaintenanceCommandV1",
    "MaintenanceContractError",
    "MaintenanceLeaseV1",
    "MaintenanceState",
    "WriterPermitV1",
    "AdjacentReleaseEdgeV1",
    "ComponentRole",
    "PlatformReleaseIdentityV1",
    "ReleaseContractError",
    "RollbackDecisionV1",
    "UpgradePhase",
    "apply_maintenance_transition",
    "assert_writer_commit_allowed",
    "evaluate_rollback",
    "release_identity_from_settings",
]
