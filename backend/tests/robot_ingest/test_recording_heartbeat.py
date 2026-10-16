import time
from contextlib import nullcontext
from threading import Event

import pytest
from temporalio.exceptions import ApplicationError, CancelledError
from temporalio.testing import ActivityEnvironment

from hc_data_platform.robot_ingest import recording_heartbeat as heartbeat_module
from hc_data_platform.robot_ingest.recording_bridge import RecordingOutboxHandler


def test_blocking_adapter_keeps_heartbeating_and_stops_on_exit(monkeypatch):
    monkeypatch.setattr(heartbeat_module, "HEARTBEAT_INTERVAL_SECONDS", 0.02)
    env = ActivityEnvironment()
    pulses = []
    observed = Event()

    def heartbeat(value):
        pulses.append(value)
        if len(pulses) >= 3:
            observed.set()

    env.on_heartbeat = heartbeat

    def operation():
        with heartbeat_module.recording_heartbeat():
            assert observed.wait(2), "blocking adapters must not lose their heartbeat"

    env.run(operation)
    count = len(pulses)
    time.sleep(0.06)
    assert len(pulses) == count
    assert all(p["attempt"] == 1 for p in pulses)


def test_shutdown_checkpoint_rolls_back_adapter_before_retry():
    env = ActivityEnvironment()
    events = []

    def operation():
        with heartbeat_module.recording_heartbeat() as checkpoint:
            try:
                env.worker_shutdown()
                checkpoint()
                events.append("commit")
            finally:
                events.append("adapter_exited")

    with pytest.raises(ApplicationError) as caught:
        env.run(operation)
    assert caught.value.type == "RECORDING_WORKER_SHUTDOWN"
    assert not caught.value.non_retryable
    assert events == ["adapter_exited"]


def test_explicit_cancellation_does_not_become_a_shutdown_retry():
    env = ActivityEnvironment()
    env.cancel()
    with pytest.raises(CancelledError):
        env.run(lambda: heartbeat_module.recording_heartbeat().__enter__())


def test_production_prepare_wraps_the_recording_adapter(monkeypatch):
    env = ActivityEnvironment()
    pulses = []
    env.on_heartbeat = pulses.append
    handler = RecordingOutboxHandler(
        None, None, registrations=None, execution_scope=lambda *a, **kw: nullcontext()
    )
    called = []

    def adopt(event, progress):
        progress()
        called.append(event.aggregate_id)

    monkeypatch.setattr(handler, "adopt", adopt)
    env.run(handler.prepare, {
        "event_id": "recovery-test", "event_type": "robot.recording.register.requested.v1",
        "aggregate_type": "raw_source", "aggregate_id": "raw-test",
        "organization_id": "01", "project_id": "01", "region_code": "global",
        "payload": {"upload_id": "upload-test"},
    })
    assert called == ["raw-test"] and pulses
