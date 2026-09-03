from __future__ import annotations

import pytest

from hc_data_platform.core.context import current_writer_permit
from hc_data_platform.platform_control.maintenance_contract import MaintenanceContractError
from hc_data_platform.platform_ops.maintenance import InMemoryMaintenanceWriteGate
from hc_data_platform.workflow.activities import (
    _media_attempt_scope,
    _worker_scope,
    configure_activity_maintenance,
)


def test_temporal_activity_scope_binds_permit_and_rejects_read_only_claims() -> None:
    gate = InMemoryMaintenanceWriteGate()
    environment_id = "activity-maintenance-test"
    configure_activity_maintenance(
        gate,
        environment_id=environment_id,
        instance_id="worker-instance-test",
    )
    try:
        with _worker_scope("project-a", "cn-east", "resource-a"):
            assert current_writer_permit() is not None
            with _media_attempt_scope("dataset-a/rollout-a"):
                assert current_writer_permit() is not None
                assert {item.writer_kind for item in gate.writer_inventory(environment_id)} == {
                    "temporal_activity",
                    "aligned_media_attempt",
                }
        assert current_writer_permit() is None

        gate.set_mode(environment_id, "READ_ONLY_MAINTENANCE")
        with (
            pytest.raises(MaintenanceContractError) as captured,
            _worker_scope("project-a", "cn-east", "resource-b"),
        ):
            raise AssertionError("read-only activity body must not run")
        assert captured.value.code == "PLATFORM_MAINTENANCE"
        assert current_writer_permit() is None
    finally:
        configure_activity_maintenance(None)
