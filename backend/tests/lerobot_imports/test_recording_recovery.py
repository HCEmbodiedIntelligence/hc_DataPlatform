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
