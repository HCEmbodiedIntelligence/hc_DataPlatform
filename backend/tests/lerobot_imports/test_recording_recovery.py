from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hc_data_platform.lerobot_imports import recording_recovery


def test_recording_recovery_preserves_scope_and_reuses_failed_attempt_request(monkeypatch):
    factory = MagicMock()
    db = factory.return_value.__enter__.return_value
    receipt = {"receipt": "validated"}
    db.execute.return_value.fetchone.return_value = (receipt, "2026-10-10T00:00:00Z")
    upload = SimpleNamespace(
        target=SimpleNamespace(organization_id="org", project_id="project", region_code="region"),
        raw_source_id="raw",
        state=SimpleNamespace(value="COMMITTED"),
        source_format="CAPTURE_BUNDLE",
    )
    validate = MagicMock(return_value=upload)
    monkeypatch.setattr(recording_recovery.RobotIngestUpload, "model_validate", validate)
    monkeypatch.setattr(
        recording_recovery.recording_bridge, "eligible", lambda value: value is upload
    )
    retry = MagicMock()
    monkeypatch.setattr(recording_recovery.recording_bridge, "retry", retry)
    for _ in range(2):
        assert recording_recovery.retry_recording_source(
            "org", "project", "region", "raw", factory=factory
        )
    assert db.execute.call_args.args[1] == ("org", "project", "region", "raw")
    assert retry.call_args_list[0] == retry.call_args_list[1]
    assert retry.call_args.args[:2] == (factory, upload)
    db.execute.return_value.fetchone.return_value = (receipt, "2026-10-10T00:01:00Z")
    recording_recovery.retry_recording_source("org", "project", "region", "raw", factory=factory)
    assert retry.call_args_list[-1] != retry.call_args_list[0]
    with pytest.raises(ValueError, match="scope"):
        recording_recovery.retry_recording_source(
            "org", "project", "foreign", "raw", factory=factory
        )
    assert retry.call_count == 3
    db.execute.return_value.fetchone.return_value = None
    assert not recording_recovery.retry_recording_source(
        "org", "project", "region", "raw", factory=factory
    )
    assert retry.call_count == 3


def test_native_robot_retry_uses_robot_processor_and_same_durable_request(monkeypatch):
    factory = MagicMock()
    factory.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = (
        {},
        "2026-10-20T01:00:00Z",
    )
    upload = SimpleNamespace(
        target=SimpleNamespace(organization_id="org", project_id="p", region_code="r"),
        raw_source_id="raw",
        source_format="LEROBOT_V3",
        state=SimpleNamespace(value="COMMITTED"),
    )
    monkeypatch.setattr(recording_recovery.RobotIngestUpload, "model_validate", lambda _: upload)
    store = MagicMock()
    monkeypatch.setattr(recording_recovery.processing_store, "ProcessingStore", lambda _: store)
    for _ in range(2):
        assert recording_recovery.retry_recording_source("org", "p", "r", "raw", factory=factory)
    assert store.retry.call_count == 2
    assert store.retry.call_args_list[0] == store.retry.call_args_list[1]
