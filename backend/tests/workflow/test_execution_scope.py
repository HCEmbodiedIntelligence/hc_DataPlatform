"""Injected scopes remain isolated, fenced and restored through cancellation."""

import asyncio

import pytest

from hc_data_platform.continuous_recordings.registration import PostgresRecordingRegistration
from hc_data_platform.core.context import current_request_context, current_writer_permit
from hc_data_platform.platform_control.maintenance_contract import MaintenanceContractError
from hc_data_platform.platform_ops.maintenance import InMemoryMaintenanceWriteGate
from hc_data_platform.robot_ingest.recording_bridge import RecordingOutboxHandler
from hc_data_platform.workflow.execution import ActivityExecutionScope


def test_independent_nested_activity_scopes_restore_after_cancellation():
    with pytest.raises(RuntimeError, match="no RequestContext"):
        current_request_context()
    gate = InMemoryMaintenanceWriteGate()
    left = ActivityExecutionScope(gate, "left-env", "left-worker")
    right = ActivityExecutionScope(gate, "right-env", "right-worker")
    with left("left", "r", "raw", organization_id="org-left"):
        outer = current_request_context()
        permit = current_writer_permit()
        assert permit is not None and outer.organization_id == "org-left"
        with (
            pytest.raises(asyncio.CancelledError),
            right("right", "r", "raw", organization_id="org-right"),
        ):
            assert current_request_context().organization_id == "org-right"
            assert current_writer_permit() != permit
            raise asyncio.CancelledError()
        assert current_request_context() == outer and current_writer_permit() == permit
        gate.set_mode("right-env", "READ_ONLY_MAINTENANCE")
        with (
            pytest.raises(MaintenanceContractError),
            right("right", "r", "blocked", organization_id="org-right"),
        ):
            pytest.fail("fenced body executed")
        assert current_request_context() == outer and current_writer_permit() == permit
    with pytest.raises(RuntimeError, match="no RequestContext"):
        current_request_context()
    assert current_writer_permit() is None
    assert gate.writer_inventory("left-env") == ()
    assert gate.writer_inventory("right-env") == ()


@pytest.mark.parametrize("method", ["prepare", "failed"])
def test_api_only_recording_handler_cannot_execute_unconfigured_activity(method):
    handler = RecordingOutboxHandler(None, None, registrations=PostgresRecordingRegistration())
    with pytest.raises(RuntimeError, match="execution scope is not configured"):
        getattr(handler, method)({})
