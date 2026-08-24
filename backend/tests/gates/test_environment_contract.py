from __future__ import annotations

import json
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
        assert services[service_name]["environment"]["HC_ENVIRONMENT"] == "test"
        dependency = services[service_name]["depends_on"]["migration-check"]
        assert dependency["condition"] == "service_completed_successfully"
    assert services["api"]["environment"]["HC_JWT_ISSUER"] == "https://be22.test.invalid/"
    assert services["api"]["environment"]["HC_JWT_AUDIENCE"] == "hc-data-platform"
    worker_environment = services["worker"]["environment"]
    outbox_scope_template = worker_environment["HC_OUTBOX_SCOPES"]
    assert "HC_REAL_API_E2E_RUN_ID" in outbox_scope_template
    assert outbox_scope_template.count("/") == 2
    assert "-org/" in outbox_scope_template
    assert "HC_MCAP_DECODER_FACTORY" not in worker_environment
    assert "PYTHONPATH" not in worker_environment
    assert "volumes" not in services["worker"]
    gateway_volumes = services["gateway"]["volumes"]
    assert any("gateway.real-api-rate-limit.conf" in volume for volume in gateway_volumes)
    assert any("gateway.real-api-auth-rate-limit.inc" in volume for volume in gateway_volumes)
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
    outbox_scopes = json.loads(services["worker"]["environment"]["HC_OUTBOX_SCOPES"])
    assert outbox_scopes
    assert all(len(scope.split("/")) == 3 for scope in outbox_scopes)
    assert services["be22-runner"]["depends_on"]["worker"]["condition"] == "service_healthy"
    gate_dependencies = services["gate-runner"]["depends_on"]
    assert "worker" not in gate_dependencies
    assert gate_dependencies["temporal"]["condition"] == "service_healthy"
    assert gate_dependencies["minio-init"]["condition"] == ("service_completed_successfully")
    assert gate_dependencies["migration-check"]["condition"] == ("service_completed_successfully")
    assert "./:/workspace:ro" in services["gate-runner"]["volumes"]
    assert services["gate-runner"]["working_dir"] == "/workspace/backend"
    assert services["gate-runner"]["environment"]["HC_MIGRATIONS_DIR"] == (
        "/workspace/backend/migrations"
    )


def test_production_frontend_has_three_independent_mock_off_guards() -> None:
    dockerfile = (ROOT / "frontend/Dockerfile").read_text(encoding="utf-8")
    entrypoint = (ROOT / "frontend/container/docker-entrypoint.sh").read_text(encoding="utf-8")
    values = load_yaml("deploy/helm/hc-data-platform/values.yaml")
    mock_mode = values["frontend"]["runtimeConfig"]["mockMode"]
    assert "VITE_MOCK_MODE=off" in dockerfile
    assert 'if [ "$mock_mode" != off ]' in entrypoint
    assert mock_mode == "off"


def test_preview_serving_api_images_include_the_media_runtime() -> None:
    dockerfile = (ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
    assert "FROM runtime-media AS api\n" in dockerfile
    assert "FROM runtime-media AS api-dev\n" in dockerfile
    assert "apt-get install --yes --no-install-recommends ffmpeg" in dockerfile
    assert "grep --quiet libvpx-vp9" in dockerfile
    assert "ffprobe -version >/dev/null" in dockerfile


def test_worker_images_install_and_import_the_production_ros2_decoder() -> None:
    pyproject = (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
    assert '"mcap-ros2-support>=0.5.7,<0.6"' in pyproject
    assert dockerfile.count("mcap_ros2.decoder") == 2
