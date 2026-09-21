from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "compose.dev.yaml"
MINIO_TEST_OVERLAY = ROOT / "deploy/compose/compose.minio-test.yaml"

PORT_VARIABLES = (
    "HC_POSTGRES_HOST_PORT",
    "HC_MINIO_API_HOST_PORT",
    "HC_MINIO_CONSOLE_HOST_PORT",
    "HC_OBJECT_EDGE_HOST_PORT",
    "HC_TEMPORAL_HOST_PORT",
    "HC_TEMPORAL_UI_HOST_PORT",
    "HC_API_HOST_PORT",
    "HC_FRONTEND_HOST_PORT",
    "HC_GATEWAY_HOST_PORT",
)


def _write_env(path: Path, values: dict[str, str]) -> None:
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in sorted(values.items())),
        encoding="utf-8",
    )


def _render(project: str, env_file: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            project,
            "--env-file",
            str(env_file),
            "-f",
            str(COMPOSE),
            "-f",
            str(MINIO_TEST_OVERLAY),
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return cast(dict[str, Any], json.loads(completed.stdout))


def _published_ports(config: dict[str, Any]) -> set[tuple[str, int]]:
    return {
        (str(port.get("host_ip", "0.0.0.0")), int(port["published"]))
        for service in config["services"].values()
        for port in service.get("ports", [])
    }


def _resource_names(config: dict[str, Any], kind: str) -> set[str]:
    return {str(resource["name"]) for resource in config[kind].values()}


def test_two_compose_projects_have_disjoint_ports_networks_and_volumes(tmp_path: Path) -> None:
    source_project = "hc-migration-src-contract"
    target_project = "hc-migration-dst-contract"
    common = {
        "HC_RELEASE_ID": "platform-v0.1.0-contract",
        "HC_RELEASE_MANIFEST_DIGEST": "contract-release-digest",
        "HC_MIGRATION_MANIFEST_DIGEST": "contract-migration-digest",
        "HC_API_IMAGE_DIGEST": "contract-api-digest",
        "HC_WORKER_IMAGE_DIGEST": "contract-worker-digest",
    }
    source_ports = dict(zip(PORT_VARIABLES, map(str, range(21001, 21010)), strict=True))
    target_ports = dict(zip(PORT_VARIABLES, map(str, range(22001, 22010)), strict=True))
    source_env = tmp_path / "source.env"
    target_env = tmp_path / "target.env"
    _write_env(
        source_env,
        {
            **common,
            **source_ports,
            "HC_COMPOSE_PROJECT_NAME": source_project,
            "HC_PLATFORM_ENVIRONMENT_ID": source_project,
            "HC_MINIO_API_BIND_ADDRESS": "0.0.0.0",
        },
    )
    _write_env(
        target_env,
        {
            **common,
            **target_ports,
            "HC_COMPOSE_PROJECT_NAME": target_project,
            "HC_PLATFORM_ENVIRONMENT_ID": target_project,
            "HC_OBJECT_STORE_ENDPOINT": (
                f"http://host.docker.internal:{source_ports['HC_MINIO_API_HOST_PORT']}"
            ),
        },
    )

    source = _render(source_project, source_env)
    target = _render(target_project, target_env)

    assert source["name"] == source_project
    assert target["name"] == target_project
    assert _published_ports(source).isdisjoint(_published_ports(target))
    assert _resource_names(source, "networks").isdisjoint(_resource_names(target, "networks"))
    assert _resource_names(source, "volumes").isdisjoint(_resource_names(target, "volumes"))

    assert source["services"]["minio"]["ports"][0]["host_ip"] == "0.0.0.0"
    target_services = target["services"]
    assert target_services["api"]["environment"]["HC_OBJECT_STORE_ENDPOINT"] == (
        f"http://host.docker.internal:{source_ports['HC_MINIO_API_HOST_PORT']}"
    )
    for service_name in ("api", "worker"):
        assert "host.docker.internal=host-gateway" in target_services[service_name]["extra_hosts"]
        assert "@postgres:5432/" in target_services[service_name]["environment"]["HC_POSTGRES_DSN"]
        assert source_project not in target_services[service_name]["environment"]["HC_POSTGRES_DSN"]

    assert {name: service.get("image") for name, service in source["services"].items()} == {
        name: service.get("image") for name, service in target_services.items()
    }
    for service_name in ("api", "worker"):
        source_environment = source["services"][service_name]["environment"]
        target_environment = target_services[service_name]["environment"]
        for key in (
            "HC_RELEASE_ID",
            "HC_RELEASE_MANIFEST_DIGEST",
            "HC_MIGRATION_MANIFEST_DIGEST",
            "HC_COMPONENT_IMAGE_DIGEST",
        ):
            assert source_environment[key] == target_environment[key]
