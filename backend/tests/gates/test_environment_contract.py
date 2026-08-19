from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def load_yaml(relative_path: str) -> dict[str, object]:
    return yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))


def test_real_api_overlay_forces_browser_mock_off_and_waits_for_migrations() -> None:
    overlay = load_yaml("compose.real-api.yaml")
    services = overlay["services"]
    assert isinstance(services, dict)
    frontend = services["frontend"]
    assert frontend["environment"]["VITE_MOCK_MODE"] == "off"
    for service_name in ("api", "worker"):
        dependency = services[service_name]["depends_on"]["migration-check"]
        assert dependency["condition"] == "service_completed_successfully"
    assert services["migration-check"]["command"] == ["hc-data-migrate", "status"]


def test_isolated_test_compose_has_repeatable_dependency_and_worker_health_contracts() -> None:
    compose = load_yaml("compose.test.yaml")
    services = compose["services"]
    assert isinstance(services, dict)
    for service_name in ("postgres", "minio", "temporal"):
        assert "healthcheck" in services[service_name]
    assert services["migration"]["command"] == ["hc-data-migrate", "upgrade"]
    assert services["migration-check"]["command"] == ["hc-data-migrate", "status"]
    assert services["worker"]["depends_on"]["migration-check"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["api"]["depends_on"]["migration-check"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["gateway"]["depends_on"]["api"]["condition"] == "service_healthy"
    assert services["minio"]["labels"]["hc.test.disposable"] == "true"
    assert services["worker"]["environment"]["HC_OUTBOX_SCOPES"]
    assert services["be22-runner"]["depends_on"]["worker"]["condition"] == "service_healthy"
    gate_dependencies = services["gate-runner"]["depends_on"]
    assert "worker" not in gate_dependencies
    assert gate_dependencies["temporal"]["condition"] == "service_healthy"
    assert gate_dependencies["minio-init"]["condition"] == ("service_completed_successfully")
    assert gate_dependencies["migration-check"]["condition"] == ("service_completed_successfully")


def test_production_frontend_has_three_independent_mock_off_guards() -> None:
    dockerfile = (ROOT / "frontend/Dockerfile").read_text(encoding="utf-8")
    entrypoint = (ROOT / "frontend/container/docker-entrypoint.sh").read_text(encoding="utf-8")
    values = load_yaml("deploy/helm/hc-data-platform/values.yaml")
    mock_mode = values["frontend"]["runtimeConfig"]["mockMode"]
    assert "VITE_MOCK_MODE=off" in dockerfile
    assert 'if [ "$mock_mode" != off ]' in entrypoint
    assert mock_mode == "off"
