from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

import httpx
import pytest

from hc_data_platform.backup.restore_execution import RestoreExecutionError
from hc_data_platform.backup.restore_runtime import (
    ComposeRestoreRuntime,
    ComposeRestoreRuntimeConfig,
    KubernetesRestoreRuntime,
    KubernetesRestoreRuntimeConfig,
    TargetFenceEvidence,
)
from tests.backup.test_restore_execution import _plan
from tests.backup.test_restore_plan import _authenticated, _request


class _TargetFence:
    def __init__(self) -> None:
        self.state = "database_unrestored"
        self.install_calls = 0

    def inspect(self, target_environment_id: str) -> TargetFenceEvidence:
        return TargetFenceEvidence(
            state=self.state,
            evidence_sha256=("1" if target_environment_id else "0") * 64,
        )

    def install(self, target_environment_id: str) -> TargetFenceEvidence:
        assert target_environment_id
        self.install_calls += 1
        self.state = "read_only"
        return TargetFenceEvidence(state="read_only", evidence_sha256="2" * 64)


def _deployment(name: str, replicas: int) -> dict[str, object]:
    return {
        "metadata": {"name": name, "resourceVersion": "1"},
        "spec": {"replicas": replicas},
        "status": {"availableReplicas": replicas, "readyReplicas": replicas},
    }


def _runtime() -> tuple[KubernetesRestoreRuntime, dict[str, dict[str, object]], _TargetFence]:
    deployments = {
        "platform-api": _deployment("platform-api", 0),
        "platform-worker": _deployment("platform-worker", 0),
        "platform-media-worker": _deployment("platform-media-worker", 0),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.split("/deployments/", 1)[1].split("/", 1)[0]
        if request.method == "PATCH":
            payload = json.loads(request.content)
            replicas = payload["spec"]["replicas"]
            deployment = deployments[name]
            deployment["spec"] = {"replicas": replicas}
            deployment["status"] = {
                "availableReplicas": replicas,
                "readyReplicas": replicas,
            }
            deployment["metadata"] = {
                "name": name,
                "resourceVersion": str(
                    int(
                        cast(
                            str,
                            cast(dict[str, object], deployment["metadata"])["resourceVersion"],
                        )
                    )
                    + 1
                ),
            }
            return httpx.Response(200, json={"spec": {"replicas": replicas}})
        return httpx.Response(200, json=deployments[name])

    fence = _TargetFence()
    runtime = KubernetesRestoreRuntime(
        KubernetesRestoreRuntimeConfig(
            api_server="https://kubernetes.default.svc",
            namespace="restore-rehearsal",
            api_deployment="platform-api",
            worker_deployments=("platform-media-worker", "platform-worker"),
            bearer_token="test-service-account-token",
            ca_bundle_path="/unused/in-mock",
            readiness_attempts=2,
            readiness_interval_seconds=0.1,
        ),
        fence,
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="https://kubernetes.default.svc",
        ),
        sleeper=lambda _: None,
    )
    return runtime, deployments, fence


def test_kubernetes_restore_runtime_keeps_workers_zero_and_starts_read_only_api() -> None:
    runtime, deployments, fence = _runtime()
    _authenticated_manifest, manifest, _key, _manifest_bytes, _signature_bytes = _authenticated()
    request = _request(manifest)
    plan = _plan().model_copy(
        update={"target_environment_id": request.target.target_environment_id}
    )

    before = runtime.assert_read_only(
        plan,
        execution_owner_id="restore-job-001",
        fencing_token=7,
    )
    activated = runtime.ensure_read_only_services(plan, request)
    after = runtime.assert_read_only(
        plan,
        execution_owner_id="restore-job-001",
        fencing_token=7,
    )

    assert len(before) == len(activated) == len(after) == 64
    assert fence.install_calls == 1
    assert deployments["platform-api"]["spec"] == {"replicas": 1}
    assert deployments["platform-worker"]["spec"] == {"replicas": 0}
    assert deployments["platform-media-worker"]["spec"] == {"replicas": 0}


def test_kubernetes_restore_runtime_rejects_worker_or_unfenced_api() -> None:
    runtime, deployments, _fence = _runtime()
    plan = _plan()
    deployments["platform-worker"] = _deployment("platform-worker", 1)
    with pytest.raises(RestoreExecutionError) as worker:
        runtime.assert_read_only(
            plan,
            execution_owner_id="restore-job-001",
            fencing_token=7,
        )
    assert worker.value.code == "RESTORE_WORKERS_NOT_ZERO"

    deployments["platform-worker"] = _deployment("platform-worker", 0)
    deployments["platform-api"] = _deployment("platform-api", 1)
    with pytest.raises(RestoreExecutionError) as api:
        runtime.assert_read_only(
            plan,
            execution_owner_id="restore-job-001",
            fencing_token=7,
        )
    assert api.value.code == "RESTORE_API_NOT_FENCED"


class _ComposeRunner:
    def __init__(self) -> None:
        self.running = {"postgres", "temporal"}
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, arguments: tuple[str, ...], **_: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(arguments)
        if arguments[:3] == ("docker", "ps", "-a"):
            services = sorted(self.running | {"api", "frontend", "gateway", "worker"})
            return subprocess.CompletedProcess(arguments, 0, "\n".join(services) + "\n", "")
        if arguments[:2] == ("docker", "inspect"):
            documents = []
            for service in arguments[2:]:
                documents.append(
                    {
                        "Id": f"container-{service}",
                        "Image": "sha256:" + "a" * 64,
                        "Config": {
                            "Labels": {
                                "com.docker.compose.project": "hc-migration-dst-unit001",
                                "com.docker.compose.service": service,
                            }
                        },
                        "State": {
                            "Running": service in self.running,
                            "Status": "running" if service in self.running else "exited",
                        },
                    }
                )
            return subprocess.CompletedProcess(arguments, 0, json.dumps(documents), "")
        assert arguments[:2] == ("docker", "compose")
        self.running.update({"api", "frontend", "gateway"})
        return subprocess.CompletedProcess(arguments, 0, "", "")


def test_compose_restore_runtime_activates_only_run_owned_read_only_services(
    tmp_path: Path,
) -> None:
    compose_file = tmp_path / "compose.dev.yaml"
    env_file = tmp_path / "target.env"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    env_file.write_text("HC_POSTGRES_HOST_PORT=25432\n", encoding="utf-8")
    compose_file.chmod(0o644)
    env_file.chmod(0o600)
    runner = _ComposeRunner()
    fence = _TargetFence()
    runtime = ComposeRestoreRuntime(
        ComposeRestoreRuntimeConfig(
            run_id="unit001",
            project_name="hc-migration-dst-unit001",
            compose_file=compose_file,
            env_file=env_file,
            readiness_attempts=2,
        ),
        fence,
        runner=runner,
        sleeper=lambda _: None,
    )
    _authenticated_manifest, manifest, _key, _manifest_bytes, _signature_bytes = _authenticated()
    request = _request(manifest)
    plan = _plan().model_copy(
        update={"target_environment_id": request.target.target_environment_id}
    )

    before = runtime.assert_read_only(plan, execution_owner_id="restore-job-001", fencing_token=7)
    activated = runtime.ensure_read_only_services(plan, request)
    after = runtime.assert_read_only(plan, execution_owner_id="restore-job-001", fencing_token=7)

    assert len(before) == len(activated) == len(after) == 64
    assert fence.install_calls == 1
    assert {"api", "frontend", "gateway"}.issubset(runner.running)
    assert "worker" not in runner.running
    compose_call = next(call for call in runner.calls if call[:2] == ("docker", "compose"))
    assert "hc-migration-dst-unit001" in compose_call
    assert compose_call[-6:] == (
        "up",
        "-d",
        "--no-deps",
        "api",
        "frontend",
        "gateway",
    )


def test_compose_restore_runtime_rejects_non_run_project(tmp_path: Path) -> None:
    compose_file = tmp_path / "compose.dev.yaml"
    env_file = tmp_path / "target.env"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    env_file.write_text("HC_POSTGRES_HOST_PORT=25432\n", encoding="utf-8")
    env_file.chmod(0o600)

    with pytest.raises(ValueError):
        ComposeRestoreRuntimeConfig(
            run_id="unit001",
            project_name="hc-data-platform-dev",
            compose_file=compose_file,
            env_file=env_file,
        )


@pytest.mark.parametrize(
    "workers",
    [
        ("platform/worker",),
        ("Platform-worker",),
        ("-platform-worker",),
        ("platform-worker-",),
        ("platform-worker", "platform-worker"),
        ("platform-worker", "platform-media-worker"),
    ],
)
def test_kubernetes_restore_runtime_rejects_invalid_worker_deployment_names(
    workers: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError):
        KubernetesRestoreRuntimeConfig(
            api_server="https://kubernetes.default.svc",
            namespace="restore-rehearsal",
            api_deployment="platform-api",
            worker_deployments=workers,
            bearer_token="test-service-account-token",
            ca_bundle_path="/unused/in-mock",
        )
