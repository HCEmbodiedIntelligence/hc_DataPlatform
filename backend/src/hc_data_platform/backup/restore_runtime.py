"""Production-shaped write fence and read-only service activation for restore."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from hc_data_platform.backup.contracts import canonical_json_bytes
from hc_data_platform.backup.postgresql import PostgresEndpoint
from hc_data_platform.backup.restore import RestorePlanRequestV1, RestorePlanV1
from hc_data_platform.backup.restore_execution import RestoreExecutionError

_KUBERNETES_DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$")
_COMPOSE_RUN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{5,63}$")


class KubernetesRestoreRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api_server: str = Field(pattern=r"^https://[^\x00\r\n]+$")
    namespace: str = Field(pattern=r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$")
    api_deployment: str = Field(pattern=r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$")
    worker_deployments: tuple[str, ...] = Field(min_length=1)
    api_replicas: int = Field(default=1, ge=1, le=100)
    bearer_token: SecretStr
    ca_bundle_path: str = Field(min_length=1, max_length=4096)
    timeout_seconds: int = Field(default=15, ge=1, le=120)
    readiness_attempts: int = Field(default=60, ge=1, le=600)
    readiness_interval_seconds: float = Field(default=1, ge=0.1, le=10)

    @field_validator("worker_deployments")
    @classmethod
    def require_workers_sorted(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))) or any(
            _KUBERNETES_DNS_LABEL.fullmatch(name) is None for name in value
        ):
            raise ValueError("worker deployment names must be sorted and unique")
        return value

    @field_validator("bearer_token")
    @classmethod
    def require_token(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw or any(char in raw for char in ("\x00", "\r", "\n")):
            raise ValueError("Kubernetes bearer token is invalid")
        return value


@dataclass(frozen=True)
class TargetFenceEvidence:
    state: str
    evidence_sha256: str


class RestoreTargetFencePort(Protocol):
    def inspect(self, target_environment_id: str) -> TargetFenceEvidence: ...

    def install(self, target_environment_id: str) -> TargetFenceEvidence: ...


class PostgresRestoreTargetFence:
    """Install the target environment as read-only after exact database restore."""

    def __init__(
        self,
        endpoint: PostgresEndpoint,
        *,
        connection_factory: Callable[[PostgresEndpoint], Any] | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._connection_factory = connection_factory or _connect_postgres

    def inspect(self, target_environment_id: str) -> TargetFenceEvidence:
        try:
            with self._connection_factory(self._endpoint) as connection:
                connection.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY, DEFERRABLE"
                )
                exists = connection.execute(
                    "SELECT to_regclass('platform.environment_fences') IS NOT NULL"
                ).fetchone()
                if exists is None or not bool(exists[0]):
                    return _target_fence_evidence(target_environment_id, "database_unrestored", 0)
                row = connection.execute(
                    """
                    SELECT mode, fencing_token
                    FROM platform.environment_fences
                    WHERE environment_id = %s
                    """,
                    (target_environment_id,),
                ).fetchone()
        except RestoreExecutionError:
            raise
        except Exception as exc:
            raise RestoreExecutionError(
                "RESTORE_TARGET_FENCE_UNAVAILABLE",
                "the restored database fence could not be inspected",
            ) from exc
        if row is None:
            return _target_fence_evidence(target_environment_id, "target_fence_absent", 0)
        if str(row[0]) != "READ_ONLY_MAINTENANCE":
            raise RestoreExecutionError(
                "RESTORE_TARGET_FENCE_OPEN",
                "the restored target environment is not read-only",
            )
        return _target_fence_evidence(target_environment_id, "read_only", int(row[1]))

    def install(self, target_environment_id: str) -> TargetFenceEvidence:
        try:
            with self._connection_factory(self._endpoint) as connection:
                exists = connection.execute(
                    "SELECT to_regclass('platform.environment_fences') IS NOT NULL"
                ).fetchone()
                if exists is None or not bool(exists[0]):
                    raise RestoreExecutionError(
                        "RESTORE_TARGET_FENCE_SCHEMA_MISSING",
                        "the restored database lacks the maintenance fence schema",
                    )
                row = connection.execute(
                    """
                    SELECT mode, fencing_token
                    FROM platform.environment_fences
                    WHERE environment_id = %s
                    FOR UPDATE
                    """,
                    (target_environment_id,),
                ).fetchone()
                if row is None:
                    row = connection.execute(
                        """
                        INSERT INTO platform.environment_fences (
                            environment_id, mode, fencing_token,
                            mode_changed_at, write_enabled_at
                        ) VALUES (
                            %s, 'READ_ONLY_MAINTENANCE',
                            nextval('platform.maintenance_fencing_token_seq'),
                            statement_timestamp(), statement_timestamp()
                        )
                        RETURNING mode, fencing_token
                        """,
                        (target_environment_id,),
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("restore target fence insert returned no row")
                elif str(row[0]) != "READ_ONLY_MAINTENANCE":
                    raise RestoreExecutionError(
                        "RESTORE_TARGET_FENCE_CONFLICT",
                        "an existing target environment fence is writable",
                    )
        except RestoreExecutionError:
            raise
        except Exception as exc:
            raise RestoreExecutionError(
                "RESTORE_TARGET_FENCE_INSTALL_FAILED",
                "the restored target read-only fence could not be installed",
            ) from exc
        return _target_fence_evidence(target_environment_id, "read_only", int(row[1]))


class ComposeRestoreRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    project_name: str
    compose_file: Path
    env_file: Path
    start_read_only_api: bool = True
    command_timeout_seconds: int = Field(default=300, ge=1, le=1800)
    readiness_attempts: int = Field(default=60, ge=1, le=600)
    readiness_interval_seconds: float = Field(default=1, ge=0.1, le=10)

    @model_validator(mode="after")
    def bind_exact_disposable_project(self) -> ComposeRestoreRuntimeConfig:
        if (
            _COMPOSE_RUN_ID.fullmatch(self.run_id) is None
            or self.project_name != f"hc-migration-dst-{self.run_id}"
        ):
            raise ValueError("Compose restore project is not bound to the exact run ID")
        for path, private in ((self.compose_file, False), (self.env_file, True)):
            resolved = path.resolve(strict=True)
            info = resolved.stat()
            if (
                path.is_symlink()
                or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or (private and stat.S_IMODE(info.st_mode) & 0o077)
            ):
                raise ValueError("Compose restore input file is unsafe")
        return self


class ComposeRestoreRuntime:
    """Activate only an exact run-owned Compose API behind the database fence."""

    _WORKERS = frozenset({"worker", "media-worker"})
    _READ_ONLY_SERVICES = ("api", "frontend", "gateway")

    def __init__(
        self,
        config: ComposeRestoreRuntimeConfig,
        target_fence: RestoreTargetFencePort,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._target_fence = target_fence
        self._runner = runner
        self._sleeper = sleeper

    def assert_read_only(
        self,
        plan: RestorePlanV1,
        *,
        execution_owner_id: str,
        fencing_token: int,
    ) -> str:
        services = self._service_states()
        self._require_workers_stopped(services)
        database = self._target_fence.inspect(plan.target_environment_id)
        api_running = services.get("api", {}).get("running") is True
        if api_running and database.state != "read_only":
            raise RestoreExecutionError(
                "RESTORE_API_NOT_FENCED",
                "the target Compose API is active before its database read-only fence",
            )
        return _sha_json(
            {
                "plan_id": plan.plan_id,
                "execution_owner_id": execution_owner_id,
                "fencing_token": fencing_token,
                "project_name": self.config.project_name,
                "services": services,
                "database_fence_sha256": database.evidence_sha256,
            }
        )

    def ensure_read_only_services(
        self,
        plan: RestorePlanV1,
        request: RestorePlanRequestV1,
    ) -> str:
        if (
            request.target.target_environment_id != plan.target_environment_id
            or not request.readiness.api_writes_disabled
            or not request.readiness.worker_replicas_zero
        ):
            raise RestoreExecutionError(
                "RESTORE_SERVICE_PLAN_MISMATCH",
                "read-only Compose activation differs from the restore plan",
            )
        before = self._service_states()
        self._require_workers_stopped(before)
        database = self._target_fence.install(plan.target_environment_id)
        if self.config.start_read_only_api:
            self._compose("up", "-d", "--no-deps", *self._READ_ONLY_SERVICES)
            services = self._wait_for_read_only_services()
        else:
            services = self._service_states()
        self._require_workers_stopped(services)
        return _sha_json(
            {
                "plan_id": plan.plan_id,
                "project_name": self.config.project_name,
                "database_fence_sha256": database.evidence_sha256,
                "services": services,
            }
        )

    def _wait_for_read_only_services(self) -> Mapping[str, Mapping[str, object]]:
        for attempt in range(self.config.readiness_attempts):
            services = self._service_states()
            if all(
                services.get(name, {}).get("running") is True for name in self._READ_ONLY_SERVICES
            ):
                return services
            if attempt + 1 < self.config.readiness_attempts:
                self._sleeper(self.config.readiness_interval_seconds)
        raise RestoreExecutionError(
            "RESTORE_READ_ONLY_SERVICES_NOT_READY",
            "target Compose read-only services did not start within the bounded interval",
        )

    def _service_states(self) -> dict[str, dict[str, object]]:
        listing = self._run(
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={self.config.project_name}",
            "--format",
            "{{.ID}}",
        )
        identifiers = tuple(item for item in listing.splitlines() if item)
        if not identifiers:
            raise RestoreExecutionError(
                "RESTORE_COMPOSE_PROJECT_UNAVAILABLE",
                "the exact target Compose project has no containers",
            )
        raw = self._run("docker", "inspect", *identifiers)
        try:
            documents = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RestoreExecutionError(
                "RESTORE_COMPOSE_RESPONSE_INVALID",
                "the Docker inspection response is malformed",
            ) from exc
        if not isinstance(documents, list) or len(documents) != len(identifiers):
            raise RestoreExecutionError(
                "RESTORE_COMPOSE_RESPONSE_INVALID",
                "the Docker inspection response is incomplete",
            )
        result: dict[str, dict[str, object]] = {}
        for document in documents:
            if not isinstance(document, Mapping):
                raise RestoreExecutionError(
                    "RESTORE_COMPOSE_RESPONSE_INVALID",
                    "a Docker inspection record is malformed",
                )
            config = document.get("Config")
            state = document.get("State")
            if not isinstance(config, Mapping) or not isinstance(state, Mapping):
                raise RestoreExecutionError(
                    "RESTORE_COMPOSE_RESPONSE_INVALID",
                    "a Docker inspection record lacks state evidence",
                )
            labels = config.get("Labels")
            if not isinstance(labels, Mapping):
                labels = {}
            project = labels.get("com.docker.compose.project")
            service = labels.get("com.docker.compose.service")
            identifier = document.get("Id")
            image = document.get("Image")
            if (
                project != self.config.project_name
                or not isinstance(service, str)
                or not service
                or service in result
                or not isinstance(identifier, str)
                or not isinstance(image, str)
            ):
                raise RestoreExecutionError(
                    "RESTORE_COMPOSE_PROJECT_MISMATCH",
                    "a Docker resource is not uniquely owned by the exact restore project",
                )
            result[service] = {
                "container_id": identifier,
                "image_id": image,
                "running": state.get("Running") is True,
                "status": state.get("Status"),
            }
        return result

    def _require_workers_stopped(self, services: Mapping[str, Mapping[str, object]]) -> None:
        if any(services.get(name, {}).get("running") is True for name in self._WORKERS):
            raise RestoreExecutionError(
                "RESTORE_WORKERS_NOT_ZERO",
                "one or more target Compose workers can execute writes",
            )

    def _compose(self, *arguments: str) -> str:
        return self._run(
            "docker",
            "compose",
            "-p",
            self.config.project_name,
            "--env-file",
            str(self.config.env_file),
            "-f",
            str(self.config.compose_file),
            *arguments,
        )

    def _run(self, *arguments: str) -> str:
        try:
            completed = self._runner(
                arguments,
                check=True,
                capture_output=True,
                text=True,
                timeout=self.config.command_timeout_seconds,
            )
        except Exception as exc:
            raise RestoreExecutionError(
                "RESTORE_COMPOSE_COMMAND_FAILED",
                "an exact target Compose command failed",
            ) from exc
        if len(completed.stdout.encode("utf-8")) > 8 * 1024 * 1024:
            raise RestoreExecutionError(
                "RESTORE_COMPOSE_RESPONSE_INVALID",
                "a Docker command returned excessive output",
            )
        return completed.stdout


class KubernetesRestoreRuntime:
    """Keep workers at zero and start only an API protected by a DB read-only fence."""

    def __init__(
        self,
        config: KubernetesRestoreRuntimeConfig,
        target_fence: RestoreTargetFencePort,
        *,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._target_fence = target_fence
        self._client = client or httpx.Client(
            base_url=config.api_server.rstrip("/"),
            headers={
                "Authorization": f"Bearer {config.bearer_token.get_secret_value()}",
                "Accept": "application/json",
            },
            verify=config.ca_bundle_path,
            timeout=config.timeout_seconds,
        )
        self._sleeper = sleeper

    def assert_read_only(
        self,
        plan: RestorePlanV1,
        *,
        execution_owner_id: str,
        fencing_token: int,
    ) -> str:
        workers = [self._deployment(name) for name in self.config.worker_deployments]
        api = self._deployment(self.config.api_deployment)
        worker_states = [_replica_state(item) for item in workers]
        api_state = _replica_state(api)
        if any(state[0] != 0 or state[1] != 0 or state[2] != 0 for state in worker_states):
            raise RestoreExecutionError(
                "RESTORE_WORKERS_NOT_ZERO",
                "one or more target worker deployments can execute writes",
            )
        database = self._target_fence.inspect(plan.target_environment_id)
        if database.state != "read_only" and any(value != 0 for value in api_state[:3]):
            raise RestoreExecutionError(
                "RESTORE_API_NOT_FENCED",
                "the target API is active before its database read-only fence",
            )
        if api_state[0] > self.config.api_replicas:
            raise RestoreExecutionError(
                "RESTORE_API_REPLICA_CONFLICT",
                "the target API exceeds the approved read-only replica count",
            )
        material = {
            "plan_id": plan.plan_id,
            "execution_owner_id": execution_owner_id,
            "fencing_token": fencing_token,
            "namespace": self.config.namespace,
            "api": api_state,
            "workers": worker_states,
            "database_fence_sha256": database.evidence_sha256,
        }
        return _sha_json(material)

    def ensure_read_only_services(
        self,
        plan: RestorePlanV1,
        request: RestorePlanRequestV1,
    ) -> str:
        if (
            request.target.target_environment_id != plan.target_environment_id
            or not request.readiness.api_writes_disabled
            or not request.readiness.worker_replicas_zero
        ):
            raise RestoreExecutionError(
                "RESTORE_SERVICE_PLAN_MISMATCH",
                "read-only service activation differs from the restore plan",
            )
        database = self._target_fence.install(plan.target_environment_id)
        for name in self.config.worker_deployments:
            self._scale(name, 0)
        self._scale(self.config.api_deployment, self.config.api_replicas)
        final: dict[str, object] | None = None
        for attempt in range(self.config.readiness_attempts):
            api = self._deployment(self.config.api_deployment)
            workers = [self._deployment(name) for name in self.config.worker_deployments]
            api_state = _replica_state(api)
            worker_states = [_replica_state(item) for item in workers]
            if (
                api_state[0] == self.config.api_replicas
                and api_state[1] == self.config.api_replicas
                and api_state[2] == self.config.api_replicas
                and all(state[0] == state[1] == state[2] == 0 for state in worker_states)
            ):
                final = {"api": api_state, "workers": worker_states}
                break
            if attempt + 1 < self.config.readiness_attempts:
                self._sleeper(self.config.readiness_interval_seconds)
        if final is None:
            raise RestoreExecutionError(
                "RESTORE_READ_ONLY_SERVICES_NOT_READY",
                "target read-only services did not reach their bounded replica state",
            )
        return _sha_json(
            {
                "plan_id": plan.plan_id,
                "namespace": self.config.namespace,
                "database_fence_sha256": database.evidence_sha256,
                **final,
            }
        )

    def _scale(self, name: str, replicas: int) -> None:
        path = self._deployment_path(name) + "/scale"
        self._request("PATCH", path, json_data={"spec": {"replicas": replicas}})

    def _deployment(self, name: str) -> Mapping[str, Any]:
        value = self._request("GET", self._deployment_path(name))
        if not isinstance(value, Mapping):
            raise RestoreExecutionError(
                "RESTORE_KUBERNETES_RESPONSE_INVALID",
                "the Kubernetes deployment response is malformed",
            )
        return value

    def _deployment_path(self, name: str) -> str:
        return (
            "/apis/apps/v1/namespaces/"
            + quote(self.config.namespace, safe="")
            + "/deployments/"
            + quote(name, safe="")
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_data: object | None = None,
    ) -> object:
        try:
            response = self._client.request(
                method,
                path,
                headers=(
                    {"Content-Type": "application/merge-patch+json"} if method == "PATCH" else None
                ),
                json=json_data,
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            raise RestoreExecutionError(
                "RESTORE_KUBERNETES_REQUEST_FAILED",
                "the Kubernetes restore runtime request failed",
            ) from exc


def _connect_postgres(endpoint: PostgresEndpoint) -> Any:
    import psycopg

    return psycopg.connect(
        host=endpoint.host,
        port=endpoint.port,
        dbname=endpoint.database,
        user=endpoint.username,
        password=endpoint.password.get_secret_value(),
        sslmode=endpoint.sslmode,
    )


def _target_fence_evidence(
    environment_id: str,
    state: str,
    fencing_token: int,
) -> TargetFenceEvidence:
    return TargetFenceEvidence(
        state=state,
        evidence_sha256=_sha_json(
            {
                "environment_id": environment_id,
                "state": state,
                "fencing_token": fencing_token,
            }
        ),
    )


def _replica_state(deployment: Mapping[str, Any]) -> tuple[int, int, int, str]:
    spec = deployment.get("spec")
    status = deployment.get("status")
    metadata = deployment.get("metadata")
    if (
        not isinstance(spec, Mapping)
        or not isinstance(status, Mapping)
        or not isinstance(metadata, Mapping)
    ):
        raise RestoreExecutionError(
            "RESTORE_KUBERNETES_RESPONSE_INVALID",
            "a Kubernetes deployment lacks bounded replica evidence",
        )
    replicas = _nonnegative_integer(spec.get("replicas", 0))
    available = _nonnegative_integer(status.get("availableReplicas", 0))
    ready = _nonnegative_integer(status.get("readyReplicas", 0))
    resource_version = metadata.get("resourceVersion")
    if not isinstance(resource_version, str) or not resource_version:
        raise RestoreExecutionError(
            "RESTORE_KUBERNETES_RESPONSE_INVALID",
            "a Kubernetes deployment has no resource version",
        )
    return replicas, available, ready, resource_version


def _nonnegative_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RestoreExecutionError(
            "RESTORE_KUBERNETES_RESPONSE_INVALID",
            "a Kubernetes replica count is invalid",
        )
    return value


def _sha_json(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
