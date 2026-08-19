from __future__ import annotations

import pytest

from hc_data_platform.workflow import healthcheck


def test_worker_healthcheck_validates_factory_and_registrations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "HC_WORKFLOW_ACTIVITY_FACTORY",
        "hc_data_platform.runtime:activity_dependencies",
    )
    workflow_count, activity_count = healthcheck.validate_worker_configuration()
    assert workflow_count > 0
    assert activity_count > 0


def test_worker_healthcheck_rejects_invalid_factory_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HC_WORKFLOW_ACTIVITY_FACTORY", "not-a-factory-path")
    with pytest.raises(RuntimeError, match="package.module:factory"):
        healthcheck.validate_worker_configuration()
