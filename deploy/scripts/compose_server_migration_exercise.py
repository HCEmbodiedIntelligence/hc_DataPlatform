#!/usr/bin/env python3
"""Disposable dual-Compose whole-platform migration exercise.

This is intentionally a functional acceptance profile, not production KMS or
performance evidence. It drives the public ``hc-platform backup/restore`` CLI,
uses a real PostgreSQL/MinIO/Temporal stack, and records only non-secret facts.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import boto3
import psycopg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from hc_data_platform.backup.contracts import (
    BackupManifestV1,
    BackupRepositoryV1,
    RestoreTargetV1,
    canonical_json_bytes,
)
from hc_data_platform.backup.restore import (
    RestorePlanRequestV1,
    RestoreReadinessEvidenceV1,
    RestoreTemporalReadinessV1,
)
from hc_data_platform.backup.restore_execution import (
    RestoreApprovalSignatureV1,
    RestoreApprovalV1,
)
from hc_data_platform.backup.temporal import (
    TemporalConnectionConfig,
    TemporalProviderRecoveryContract,
    TemporalSdkAdminAdapter,
)
from hc_data_platform.backup.whole import WholeBackupPlanV1
from hc_data_platform.core.migrations import load_migrations
from hc_data_platform.platform_control.maintenance_contract import (
    MaintenanceCommandV1,
    MaintenanceState,
)
from hc_data_platform.platform_ops.maintenance import (
    PostgresMaintenanceRepository,
)

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
OBJECT_BYTES = 100_000_000
POSTGRES_IMAGE = (
    "postgres:16.10-bookworm@sha256:"
    "61c57e5eeda4d69232c97cb23ae5d95d170a1a2fd0b40a6b4f34fb56819b056d"
)


def _run(
    arguments: list[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    stdout: Any = subprocess.PIPE,
    timeout: int = 900,
) -> subprocess.CompletedProcess[Any]:
    try:
        return subprocess.run(
            arguments,
            cwd=cwd,
            env=env,
            check=True,
            stdout=stdout,
            stderr=subprocess.PIPE,
            text=stdout == subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.CalledProcessError as exc:
        detail = "\n".join(
            value.strip()
            for value in (str(exc.stdout or ""), str(exc.stderr or ""))
            if value.strip()
        )
        raise RuntimeError(
            f"{Path(arguments[0]).name} exited {exc.returncode}: {detail[-4000:]}"
        ) from None


def _utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _private_json(path: Path, value: object) -> None:
    path.write_bytes(canonical_json_bytes(value))
    path.chmod(0o600)


def _private_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)


def _ports(count: int) -> tuple[int, ...]:
    sockets: list[socket.socket] = []
    values: list[int] = []
    try:
        for _ in range(count):
            current = socket.socket()
            current.bind(("127.0.0.1", 0))
            sockets.append(current)
            values.append(int(current.getsockname()[1]))
        return tuple(values)
    finally:
        for current in sockets:
            current.close()


def _compose(project: str, env_file: Path, *arguments: str, timeout: int = 900) -> str:
    result = _run(
        [
            "docker",
            "compose",
            "-p",
            project,
            "--env-file",
            str(env_file),
            "-f",
            str(ROOT / "compose.dev.yaml"),
            "-f",
            str(ROOT / "compose.minio-test.yaml"),
            *arguments,
        ],
        timeout=timeout,
    )
    return str(result.stdout)


def _compose_container(project: str, env_file: Path, service: str) -> str:
    value = _compose(project, env_file, "ps", "--all", "-q", service).strip()
    if not value:
        raise RuntimeError(f"missing run-owned service: {project}/{service}")
    return value


def _wait_compose_health(project: str, env_file: Path, services: tuple[str, ...]) -> None:
    for _ in range(120):
        statuses: list[str] = []
        for service in services:
            container = _compose_container(project, env_file, service)
            completed = _run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                    container,
                ]
            )
            statuses.append(str(completed.stdout).strip())
        if all(status == "healthy" for status in statuses):
            init = _compose_container(project, env_file, "minio-init")
            code = _run(["docker", "inspect", "--format", "{{.State.ExitCode}}", init]).stdout
            if str(code).strip() != "0":
                raise RuntimeError("run-owned MinIO initialization failed")
            return
        time.sleep(1)
    raise RuntimeError("run-owned Compose infrastructure did not become healthy")


def _wait_http(url: str, *, attempts: int = 120) -> None:
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(1)
    raise RuntimeError("HTTP endpoint did not become ready")


def _http_json(
    method: str,
    url: str,
    body: dict[str, object] | None = None,
    *,
    token: str | None = None,
) -> tuple[int, dict[str, Any]]:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url,
        data=(json.dumps(body).encode("utf-8") if body is not None else None),
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
            return response.status, payload
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP acceptance request failed with status {exc.code}") from exc


def _command(operation: Any, next_state: MaintenanceState, approval: str | None = None) -> Any:
    return MaintenanceCommandV1(
        operation_id=operation.operation_id,
        environment_id=operation.environment_id,
        owner_instance_id=str(operation.owner_instance_id),
        fencing_token=operation.fencing_token,
        expected_state=operation.state,
        expected_state_version=operation.state_version,
        next_state=next_state,
        manual_approval_id=approval,
    )


def _enter_maintenance(dsn: str, operation_id: str, environment_id: str) -> Any:
    owner = UUID("77777777-7777-4777-8777-777777777777")
    repository = PostgresMaintenanceRepository.from_dsn(dsn)
    operation = repository.request_operation(
        operation_id=operation_id,
        environment_id=environment_id,
        operation_kind="BACKUP",
        plan_digest="sha256:" + "a" * 64,
        requested_by="compose-migration-exercise",
    )
    operation = repository.acquire(operation.operation_id, owner_instance_id=owner)
    for state in (
        MaintenanceState.READ_ONLY,
        MaintenanceState.DRAINING,
        MaintenanceState.FENCED,
        MaintenanceState.EXECUTING,
    ):
        operation = repository.transition(_command(operation, state))
    return operation


def _enable_target_writes(dsn: str, operation_id: str, environment_id: str) -> None:
    owner = UUID("88888888-8888-4888-8888-888888888888")
    repository = PostgresMaintenanceRepository.from_dsn(dsn)
    operation = repository.request_operation(
        operation_id=operation_id,
        environment_id=environment_id,
        operation_kind="RESTORE",
        plan_digest="sha256:" + "b" * 64,
        requested_by="compose-migration-exercise",
    )
    operation = repository.acquire(operation.operation_id, owner_instance_id=owner)
    for state in (
        MaintenanceState.READ_ONLY,
        MaintenanceState.DRAINING,
        MaintenanceState.FENCED,
        MaintenanceState.EXECUTING,
        MaintenanceState.VERIFYING,
    ):
        operation = repository.transition(_command(operation, state))
    if operation.fencing_token is None:
        raise RuntimeError("target write-enable operation has no fencing token")
    operation = repository.mark_reconciliation_passed(
        operation.operation_id,
        owner_instance_id=owner,
        fencing_token=operation.fencing_token,
        expected_state_version=operation.state_version,
    )
    for state in (
        MaintenanceState.RELEASING,
        MaintenanceState.WRITE_ENABLE_PENDING,
        MaintenanceState.SUCCEEDED,
    ):
        operation = repository.transition(_command(operation, state, "compose-functional-approval"))


async def _temporal_contract(target: str, run_id: str, path: Path) -> tuple[str, str, str]:
    cluster_reference = f"temporal://compose/{run_id}"
    provider = TemporalSdkAdminAdapter(
        TemporalConnectionConfig(
            deployment_mode="compose_functional",
            target=target,
            namespace="default",
            cluster_reference=cluster_reference,
            tls_enabled=False,
            stability_attempts=3,
            stability_interval_milliseconds=100,
        )
    )
    snapshot = await provider.capture_inventory()
    now = _utc()
    contract = TemporalProviderRecoveryContract(
        provider="compose_functional",
        cluster_reference=cluster_reference,
        namespace="default",
        expected_cluster_identity_sha256=snapshot.cluster_identity_sha256,
        expected_namespace_identity_sha256=snapshot.namespace_identity_sha256,
        expected_service_version=snapshot.service_version,
        maximum_rpo_seconds=900,
        maximum_rto_seconds=7200,
        protection_reference=f"evidence://compose/{run_id}/temporal-protection",
        protection_observed_at=_timestamp(now - timedelta(seconds=1)),
        evidence_reference=f"evidence://compose/{run_id}/temporal",
        evidence_sha256=hashlib.sha256(run_id.encode()).hexdigest(),
        restore_runbook_reference="runbook://planned-migration/temporal",
        verified_at=_timestamp(now),
        valid_until=_timestamp(now + timedelta(hours=1)),
    )
    _private_json(path, contract.model_dump(mode="json"))
    return (
        snapshot.cluster_identity_sha256,
        snapshot.namespace_identity_sha256,
        snapshot.service_version,
    )


def _seed_source(
    dsn: str,
    endpoint: str,
    bucket: str,
    prefix: str,
    work: Path,
) -> dict[str, object]:
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id="minio",
        aws_secret_access_key="minio-local-only",
        region_name="us-east-1",
    )
    payload = work / "real-100mb-object.bin"
    digest = hashlib.sha256()
    with payload.open("wb", buffering=0) as stream:
        remaining = OBJECT_BYTES
        while remaining:
            chunk = os.urandom(min(1024 * 1024, remaining))
            digest.update(chunk)
            stream.write(chunk)
            remaining -= len(chunk)
    if payload.stat().st_blocks * 512 < OBJECT_BYTES:
        raise RuntimeError("the acceptance object is sparse")
    key = f"{prefix}real-100mb-object.bin"
    client.put_bucket_versioning(
        Bucket=bucket,
        VersioningConfiguration={"Status": "Enabled"},
    )
    versioning = client.get_bucket_versioning(Bucket=bucket)
    if versioning.get("Status") != "Enabled":
        raise RuntimeError("source object bucket versioning was not enabled")
    client.upload_file(str(payload), bucket, key)
    head = client.head_object(Bucket=bucket, Key=key)
    version_id = str(head.get("VersionId", ""))
    if not version_id or version_id == "null":
        raise RuntimeError("source object upload did not produce an immutable version ID")
    with psycopg.connect(dsn) as connection:
        connection.execute("CREATE SCHEMA migration_exercise")
        connection.execute(
            """
            CREATE TABLE migration_exercise.object_references (
                reference_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                object_key text NOT NULL UNIQUE,
                version_id text NOT NULL,
                size_bytes bigint NOT NULL,
                sha256 text NOT NULL,
                created_at timestamptz NOT NULL DEFAULT statement_timestamp()
            )
            """
        )
        connection.execute(
            """
            INSERT INTO migration_exercise.object_references (
                object_key, version_id, size_bytes, sha256
            ) VALUES (%s, %s, %s, %s)
            """,
            (key, version_id, OBJECT_BYTES, digest.hexdigest()),
        )
    payload.unlink()
    return {
        "object_key": key,
        "version_id": version_id,
        "size_bytes": OBJECT_BYTES,
        "sha256": digest.hexdigest(),
    }


def _database_facts(dsn: str) -> dict[str, object]:
    with psycopg.connect(dsn) as connection:
        row = connection.execute(
            """
            SELECT count(*), min(object_key), min(version_id),
                   sum(size_bytes), min(sha256)
            FROM migration_exercise.object_references
            """
        ).fetchone()
        auth = connection.execute("SELECT count(*) FROM access_control.accounts").fetchone()
        audit = connection.execute("SELECT count(*) FROM access_control.audit_events").fetchone()
    assert row is not None and auth is not None and audit is not None
    material = {
        "object_reference_count": int(row[0]),
        "object_key": str(row[1]),
        "version_id": str(row[2]),
        "object_bytes": int(row[3]),
        "object_sha256": str(row[4]),
        "principal_count": int(auth[0]),
        "audit_count": int(audit[0]),
    }
    return {
        **material,
        "aggregate_sha256": hashlib.sha256(canonical_json_bytes(material)).hexdigest(),
    }


def _operational_facts(dsn: str) -> dict[str, int]:
    with psycopg.connect(dsn) as connection:
        outbox = connection.execute(
            """
            SELECT count(*)::bigint,
                   count(*) FILTER (WHERE published_at IS NULL)::bigint,
                   count(*) FILTER (
                       WHERE claimed_until IS NOT NULL
                         AND claimed_until > statement_timestamp()
                   )::bigint
              FROM core.outbox_events
            """
        ).fetchone()
        running = connection.execute(
            "SELECT count(*)::bigint FROM workflow.jobs WHERE status = 'RUNNING'"
        ).fetchone()
        dispatched = connection.execute(
            "SELECT count(*)::bigint FROM ingest.workflow_triggers WHERE status = 'DISPATCHED'"
        ).fetchone()
    if outbox is None or running is None or dispatched is None:
        raise RuntimeError("operational database facts are unavailable")
    return {
        "outbox_event_count": int(outbox[0]),
        "outbox_pending_count": int(outbox[1]),
        "outbox_active_claim_count": int(outbox[2]),
        "running_workflow_count": int(running[0]),
        "dispatched_workflow_trigger_count": int(dispatched[0]),
    }


def _clone_temporal_databases(
    source_container: str,
    target_container: str,
    work: Path,
) -> None:
    for database in ("temporal", "temporal_visibility"):
        dump = work / f"{database}.dump"
        with dump.open("wb") as stream:
            _run(
                ["docker", "exec", source_container, "pg_dump", "-U", "hc", "-Fc", database],
                stdout=stream,
            )
        _run(
            [
                "docker",
                "exec",
                target_container,
                "psql",
                "-U",
                "hc",
                "-d",
                "postgres",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                f"DROP DATABASE IF EXISTS {database} WITH (FORCE)",
                "-c",
                f"CREATE DATABASE {database} OWNER hc",
            ]
        )
        target_path = f"/tmp/{database}.dump"
        _run(["docker", "cp", str(dump), f"{target_container}:{target_path}"])
        _run(
            [
                "docker",
                "exec",
                target_container,
                "pg_restore",
                "-U",
                "hc",
                "-d",
                database,
                "--no-owner",
                target_path,
            ]
        )
        _run(["docker", "exec", target_container, "rm", "-f", target_path])
        dump.unlink()


def _vault_setup(name: str, run_id: str, port: int, token: str) -> None:
    _run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--label",
            f"hc.migration.run_id={run_id}",
            "--log-driver",
            "none",
            "--publish",
            f"127.0.0.1:{port}:8200",
            "--env",
            f"VAULT_DEV_ROOT_TOKEN_ID={token}",
            "--env",
            "VAULT_DEV_LISTEN_ADDRESS=0.0.0.0:8200",
            "docker.m.daocloud.io/hashicorp/vault:1.20.3",
        ]
    )
    environment = ["--env", f"VAULT_TOKEN={token}", "--env", "VAULT_ADDR=http://127.0.0.1:8200"]
    for _ in range(60):
        try:
            _run(["docker", "exec", *environment, name, "vault", "status"], timeout=10)
            break
        except (RuntimeError, subprocess.SubprocessError):
            time.sleep(1)
    else:
        raise RuntimeError("functional Vault did not become ready")
    _run(["docker", "exec", *environment, name, "vault", "secrets", "enable", "transit"])
    _run(
        [
            "docker",
            "exec",
            *environment,
            name,
            "vault",
            "write",
            "-f",
            "transit/keys/backup-hmac",
        ]
    )
    secrets_by_path = {
        "secret/hc-data-platform/hc-data-postgres": {"dsn": "compose-functional-dsn"},
        "secret/hc-data-platform/hc-data-object-store": {
            "access-key": "compose-functional-access",
            "secret-key": "compose-functional-secret",
        },
        "secret/hc-data-platform/hc-data-application": {
            "cursor-secret": "compose-functional-cursor",
            "data-source-credential-key": "compose-functional-credential",
            "auth-abuse-hmac-secret": "compose-functional-abuse",
            "auth-turnstile-secret": "compose-functional-turnstile",
        },
    }
    for path, values in secrets_by_path.items():
        _run(
            [
                "docker",
                "exec",
                *environment,
                name,
                "vault",
                "kv",
                "put",
                path,
                *(f"{key}={value}" for key, value in values.items()),
            ]
        )


def _cli(arguments: list[str], environment: dict[str, str]) -> dict[str, Any]:
    completed = _run(
        [str(BACKEND / ".venv/bin/hc-platform"), *arguments],
        cwd=BACKEND,
        env=environment,
        timeout=1800,
    )
    value = json.loads(str(completed.stdout))
    if not isinstance(value, dict):
        raise RuntimeError("platform CLI returned a non-object result")
    return value


def _resource_inventory(project: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for kind, command in (
        ("containers", ["docker", "ps", "-a", "--format", "{{.ID}}", "--filter"]),
        ("volumes", ["docker", "volume", "ls", "--format", "{{.Name}}", "--filter"]),
        ("networks", ["docker", "network", "ls", "--format", "{{.Name}}", "--filter"]),
    ):
        completed = _run([*command, f"label=com.docker.compose.project={project}"])
        result[kind] = sorted(item for item in str(completed.stdout).splitlines() if item)
    return result


def _standalone_container_inventory(name: str, run_id: str) -> dict[str, list[str]]:
    completed = _run(
        [
            "docker",
            "ps",
            "-a",
            "--format",
            "{{.ID}}",
            "--filter",
            f"name=^/{name}$",
        ]
    )
    identifiers = sorted(item for item in str(completed.stdout).splitlines() if item)
    for identifier in identifiers:
        raw = _run(["docker", "inspect", identifier])
        document = json.loads(str(raw.stdout))[0]
        labels = document["Config"].get("Labels") or {}
        if labels.get("hc.migration.run_id") != run_id:
            raise RuntimeError("standalone migration resource has the wrong run label")
    return {"containers": identifiers, "volumes": [], "networks": []}


def _compose_service_evidence(
    project: str,
    env_file: Path,
    services: tuple[str, ...],
) -> dict[str, dict[str, object]]:
    identifiers = [_compose_container(project, env_file, service) for service in services]
    documents = json.loads(str(_run(["docker", "inspect", *identifiers]).stdout))
    if not isinstance(documents, list) or len(documents) != len(services):
        raise RuntimeError("Compose service inspection is incomplete")
    result: dict[str, dict[str, object]] = {}
    for expected_service, document in zip(services, documents, strict=True):
        labels = document["Config"].get("Labels") or {}
        if (
            labels.get("com.docker.compose.project") != project
            or labels.get("com.docker.compose.service") != expected_service
        ):
            raise RuntimeError("Compose service is not owned by the exact migration project")
        state = document["State"]
        health_document = state.get("Health") or {}
        health = health_document.get("Status")
        result[expected_service] = {
            "container_id_sha256": hashlib.sha256(str(document["Id"]).encode()).hexdigest(),
            "image_id": str(document["Image"]),
            "running": bool(state.get("Running")),
            "status": str(state.get("Status")),
            "health": None if health is None else str(health),
        }
    return result


def _wait_services_running(
    project: str,
    env_file: Path,
    services: tuple[str, ...],
) -> dict[str, dict[str, object]]:
    stable_observations = 0
    latest: dict[str, dict[str, object]] = {}
    for _ in range(120):
        latest = _compose_service_evidence(project, env_file, services)
        if all(
            fact["running"] is True and fact["health"] not in {"starting", "unhealthy"}
            for fact in latest.values()
        ):
            stable_observations += 1
            if stable_observations == 3:
                return latest
        else:
            stable_observations = 0
        time.sleep(1)
    raise RuntimeError("Compose services did not remain running for three observations")


def _record_command(
    evidence: dict[str, Any],
    command: str,
    *,
    result: str = "PASS",
    status: str | None = None,
) -> None:
    record: dict[str, str] = {"command": command, "result": result}
    if status is not None:
        record["status"] = status
    commands = evidence["commands"]
    if not isinstance(commands, list):
        raise RuntimeError("migration command evidence is malformed")
    commands.append(record)


def _scan_outputs_for_secrets(
    work_root: Path,
    signing_key: Path,
    projects: tuple[str, str],
    forbidden_values: tuple[str, ...],
    evidence_bytes: bytes,
) -> dict[str, object]:
    forbidden = tuple(
        sorted(
            {value.encode() for value in forbidden_values if len(value.encode()) >= 6},
            key=len,
            reverse=True,
        )
    )
    file_facts: list[dict[str, object]] = []
    artifact_bytes_scanned = 0
    for path in sorted(work_root.rglob("*")):
        if not path.is_file() or path == signing_key:
            continue
        if path.is_symlink():
            raise RuntimeError("migration output artifact is an unsafe symlink")
        raw = path.read_bytes()
        if any(secret in raw for secret in forbidden):
            raise RuntimeError("migration output artifact contains forbidden secret material")
        artifact_bytes_scanned += len(raw)
        file_facts.append(
            {
                "path": str(path.relative_to(work_root)),
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    if any(secret in evidence_bytes for secret in forbidden):
        raise RuntimeError("migration machine evidence contains forbidden secret material")

    log_facts: list[dict[str, object]] = []
    container_log_bytes_scanned = 0
    for project in projects:
        for identifier in _resource_inventory(project)["containers"]:
            completed = _run(["docker", "logs", identifier])
            stdout = str(completed.stdout or "").encode()
            stderr = str(completed.stderr or "").encode()
            raw = stdout + stderr
            if any(secret in raw for secret in forbidden):
                raise RuntimeError("migration container log contains forbidden secret material")
            container_log_bytes_scanned += len(raw)
            log_facts.append(
                {
                    "container_id_sha256": hashlib.sha256(identifier.encode()).hexdigest(),
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
    artifact_set_sha256 = hashlib.sha256(canonical_json_bytes(file_facts)).hexdigest()
    log_set_sha256 = hashlib.sha256(canonical_json_bytes(log_facts)).hexdigest()
    return {
        "status": "PASS",
        "forbidden_match_count": 0,
        "artifact_file_count": len(file_facts),
        "artifact_bytes_scanned": artifact_bytes_scanned,
        "artifact_set_sha256": artifact_set_sha256,
        "machine_evidence_bytes_scanned": len(evidence_bytes),
        "container_log_count": len(log_facts),
        "container_log_bytes_scanned": container_log_bytes_scanned,
        "container_log_set_sha256": log_set_sha256,
        "vault_dependency_log_driver": "none",
        "excluded_runtime_private_key_files": 1,
    }


def exercise(run_id: str, evidence_path: Path, *, keep_on_failure: bool) -> int:
    if not run_id.replace("-", "").isalnum() or len(run_id) < 6 or run_id != run_id.lower():
        raise ValueError("run ID must be lowercase alphanumeric/hyphen and at least six characters")
    exercise_started_at = _utc()
    source_project = f"hc-migration-src-{run_id}"
    target_project = f"hc-migration-dst-{run_id}"
    if "hc-data-platform-dev" in {source_project, target_project}:
        raise RuntimeError("the disposable project guard failed")
    vault_name = f"hc-migration-vault-{run_id}"
    for project in (source_project, target_project):
        existing = _resource_inventory(project)
        if any(existing.values()):
            raise RuntimeError("the exact disposable Compose project already exists")
    if any(_standalone_container_inventory(vault_name, run_id).values()):
        raise RuntimeError("the exact disposable Vault container already exists")
    source_ports = _ports(9)
    target_ports = _ports(9)
    vault_port = _ports(1)[0]
    work_root = Path(tempfile.mkdtemp(prefix=f"hc-migration-{run_id}-"))
    work_root.chmod(0o700)
    source_env = work_root / "source.env"
    target_env = work_root / "target.env"
    repository_root = work_root / f"hc-migration-{run_id}-repository"
    backup_staging = work_root / "backup-staging"
    restore_staging = work_root / "restore-staging"
    repository_root.mkdir(mode=0o700)
    backup_staging.mkdir(mode=0o700)
    restore_staging.mkdir(mode=0o700)
    signing_key = work_root / f"hc-migration-{run_id}-signing.key"
    private_key = Ed25519PrivateKey.generate()
    signing_key.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
    )
    signing_key.chmod(0o600)
    release = json.loads(
        (BACKEND / "tests/backup/fixtures/hc-platform-backup-v1.golden.json").read_text()
    )["manifest"]["release"]
    migration_ledger = sorted(
        (migration.version, migration.checksum_sha256)
        for migration in load_migrations(BACKEND / "migrations")
    )
    release["migration_manifest_sha256"] = hashlib.sha256(
        canonical_json_bytes(migration_ledger)
    ).hexdigest()
    common_release = {
        "HC_RELEASE_ID": f"platform-v0.1.0-{run_id}",
        "HC_GIT_COMMIT": release["git_commit"],
        "HC_RELEASE_MANIFEST_DIGEST": "sha256:" + release["release_manifest_sha256"],
        "HC_MIGRATION_MANIFEST_DIGEST": "sha256:" + release["migration_manifest_sha256"],
        "HC_API_IMAGE_DIGEST": release["images"]["api"],
        "HC_WORKER_IMAGE_DIGEST": release["images"]["worker"],
    }
    source_values = {
        "HC_COMPOSE_PROJECT_NAME": source_project,
        "HC_PLATFORM_ENVIRONMENT_ID": f"src-{run_id}",
        "HC_POSTGRES_HOST_PORT": source_ports[0],
        "HC_MINIO_API_HOST_PORT": source_ports[1],
        "HC_MINIO_CONSOLE_HOST_PORT": source_ports[2],
        "HC_OBJECT_EDGE_HOST_PORT": source_ports[3],
        "HC_TEMPORAL_HOST_PORT": source_ports[4],
        "HC_TEMPORAL_UI_HOST_PORT": source_ports[5],
        "HC_API_HOST_PORT": source_ports[6],
        "HC_FRONTEND_HOST_PORT": source_ports[7],
        "HC_GATEWAY_HOST_PORT": source_ports[8],
        **common_release,
    }
    target_values = {
        "HC_COMPOSE_PROJECT_NAME": target_project,
        "HC_PLATFORM_ENVIRONMENT_ID": f"dst-{run_id}",
        "HC_POSTGRES_HOST_PORT": target_ports[0],
        "HC_MINIO_API_HOST_PORT": target_ports[1],
        "HC_MINIO_CONSOLE_HOST_PORT": target_ports[2],
        "HC_OBJECT_EDGE_HOST_PORT": target_ports[3],
        "HC_TEMPORAL_HOST_PORT": target_ports[4],
        "HC_TEMPORAL_UI_HOST_PORT": target_ports[5],
        "HC_API_HOST_PORT": target_ports[6],
        "HC_FRONTEND_HOST_PORT": target_ports[7],
        "HC_GATEWAY_HOST_PORT": target_ports[8],
        "HC_OBJECT_STORE_ENDPOINT": f"http://host.docker.internal:{source_ports[1]}",
        "HC_OBJECT_STORE_PUBLIC_ENDPOINT": f"http://127.0.0.1:{source_ports[3]}",
        **common_release,
    }
    _private_text(source_env, "".join(f"{key}={value}\n" for key, value in source_values.items()))
    _private_text(target_env, "".join(f"{key}={value}\n" for key, value in target_values.items()))
    vault_token = secrets.token_urlsafe(32)
    evidence: dict[str, Any] = {
        "format_version": "hc-compose-server-migration-exercise/v1",
        "run_id": run_id,
        "started_at": _timestamp(exercise_started_at),
        "status": "FAILED",
        "functional_object_bytes": OBJECT_BYTES,
        "performance_benchmark_claimed": False,
        "object_migration_mode": "reuse_external",
        "source_project": source_project,
        "target_project": target_project,
        "commands": [],
        "cleanup": {},
    }
    success = False
    write_pause_started_monotonic: float | None = None
    try:
        _vault_setup(vault_name, run_id, vault_port, vault_token)
        _record_command(
            evidence,
            f"docker run --name {vault_name} --label hc.migration.run_id={run_id} vault:1.20.3",
        )
        _compose(
            source_project,
            source_env,
            "up",
            "-d",
            "--wait",
            "--wait-timeout",
            "600",
            "postgres",
            "minio",
            "minio-init",
            "temporal",
            "migration",
            "runtime-cache-init",
            "api",
            "worker",
            "frontend",
            "gateway",
            timeout=1200,
        )
        _record_command(
            evidence,
            f"docker compose -p {source_project} up -d --wait postgres minio minio-init "
            "temporal migration runtime-cache-init api worker frontend gateway",
        )
        _compose(
            target_project,
            target_env,
            "up",
            "-d",
            "postgres",
            "minio",
            "minio-init",
            "temporal",
            timeout=900,
        )
        _wait_compose_health(
            target_project,
            target_env,
            ("postgres", "minio", "temporal"),
        )
        _record_command(
            evidence,
            f"docker compose -p {target_project} up -d postgres minio minio-init temporal",
        )
        _wait_http(f"http://127.0.0.1:{source_ports[6]}/health/live")
        username = f"migration-{run_id}"
        password = secrets.token_urlsafe(24)
        status, _registered = _http_json(
            "POST",
            f"http://127.0.0.1:{source_ports[6]}/api/v1/auth/registrations",
            {"username": username, "password": password},
        )
        if status != 201:
            raise RuntimeError("source registration did not succeed")
        status, session = _http_json(
            "POST",
            f"http://127.0.0.1:{source_ports[6]}/api/v1/auth/sessions",
            {"username": username, "password": password},
        )
        if status != 201:
            raise RuntimeError("source login did not succeed")
        session_token = str(session["access_token"])
        _http_json(
            "GET",
            f"http://127.0.0.1:{source_ports[6]}/api/v1/auth/session/bootstrap",
            token=session_token,
        )
        source_dsn = f"postgresql://hc:hc@127.0.0.1:{source_ports[0]}/hc_data"
        target_dsn = f"postgresql://hc:hc@127.0.0.1:{target_ports[0]}/hc_data"
        object_prefix = f"migration/{run_id}/"
        object_fact = _seed_source(
            source_dsn,
            f"http://127.0.0.1:{source_ports[1]}",
            "hc-data-local",
            object_prefix,
            work_root,
        )
        _record_command(
            evidence,
            "source API registration/login/bootstrap + PostgreSQL/MinIO 100 MB seed verification",
        )
        with psycopg.connect(target_dsn) as connection:
            empty = connection.execute(
                """
                SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
                """
            ).fetchone()
        if empty is None or int(empty[0]) != 0:
            raise RuntimeError("target PostgreSQL is not a fresh empty database")
        write_pause_started_monotonic = time.monotonic()
        _compose(source_project, source_env, "stop", "api", "worker")
        operation_id = f"migration-backup-{run_id}"
        source_environment = f"src-{run_id}"
        operation = _enter_maintenance(source_dsn, operation_id, source_environment)
        _record_command(
            evidence,
            f"docker compose -p {source_project} stop api worker + maintenance fence",
            status="EXECUTING",
        )
        source_facts = _database_facts(source_dsn)
        temporal_policy = work_root / "temporal-provider-policy.json"
        temporal_identity = asyncio.run(
            _temporal_contract(f"127.0.0.1:{source_ports[4]}", run_id, temporal_policy)
        )
        rendered_helm = work_root / "helm-rendered.yaml"
        rendered = _run(
            [
                "helm",
                "template",
                f"migration-{run_id}",
                str(ROOT / "deploy/helm/hc-data-platform"),
                "--values",
                str(ROOT / "deploy/helm/hc-data-platform/values-ci.yaml"),
            ]
        )
        rendered_helm.write_text(str(rendered.stdout), encoding="utf-8")
        now = _utc()
        backup_id = f"migration-backup-{run_id}"
        repository_key = f"kms://compose/{run_id}/repository/versions/v1"
        signing_reference = f"kms://compose/{run_id}/signing/versions/v1"
        golden = json.loads(
            (BACKEND / "tests/backup/fixtures/hc-platform-backup-v1.golden.json").read_text()
        )["manifest"]
        backup_repository = BackupRepositoryV1(
            repository_id=f"migration-repository-{run_id}",
            provider="s3_compatible",
            bucket_reference=f"migration-repository-{run_id}",
            kms_key_reference=repository_key,
            retention_until=now + timedelta(days=1),
            cross_site_replica_reference=f"functional-replica-{run_id}",
        )
        plan = WholeBackupPlanV1(
            operation_id=operation_id,
            backup_id=backup_id,
            mode="snapshot",
            created_at=now - timedelta(seconds=1),
            completed_at=now,
            source_platform_id=f"platform-src-{run_id}",
            source_environment_id=source_environment,
            verifier_identity="compose-migration-exercise",
            release=release,
            backup_tool=golden["backup_tool"],
            postgresql={
                "physical_recovery": "provider_managed_pitr",
                "pitr_coordinate": f"provider-pitr://compose/{run_id}",
            },
            object_store={
                "provider": "s3_compatible",
                "bucket_reference": f"external-oss-{run_id}",
                "prefix": object_prefix,
            },
            temporal={
                "cluster_reference": f"temporal://compose/{run_id}",
                "namespace": "default",
                "service_version": temporal_identity[2],
            },
            deployment_configuration={
                "gitops_repository_reference": f"gitops://compose/{run_id}/release"
            },
            secrets_and_kms={
                "repository_key_reference": repository_key,
                "signing_key_reference": signing_reference,
                "portable_recipient_key_references": (),
            },
            backup_repository=backup_repository,
            external_evidence={
                "runtime_logs_evidence_sha256": hashlib.sha256(b"runtime-logs").hexdigest(),
                "external_dependencies_evidence_sha256": hashlib.sha256(
                    b"external-dependencies"
                ).hexdigest(),
            },
        )
        plan_path = work_root / "whole-backup-plan.json"
        _private_json(plan_path, plan.model_dump(mode="json"))
        cli_env = os.environ.copy()
        cli_env.update(
            {
                "HC_BACKUP_RUNTIME_PROFILE": "compose_functional",
                "HC_BACKUP_FUNCTIONAL_RUN_ID": run_id,
                "HC_BACKUP_FUNCTIONAL_REPOSITORY_DIRECTORY": str(repository_root),
                "HC_BACKUP_FUNCTIONAL_SIGNING_KEY_FILE": str(signing_key),
                "HC_BACKUP_POSTGRES_HOST": "127.0.0.1",
                "HC_BACKUP_POSTGRES_PORT": str(source_ports[0]),
                "HC_BACKUP_POSTGRES_DATABASE": "hc_data",
                "HC_BACKUP_POSTGRES_USERNAME": "hc",
                "HC_BACKUP_POSTGRES_PASSWORD": "hc",
                "HC_BACKUP_POSTGRES_SSLMODE": "disable",
                "HC_BACKUP_POSTGRES_ENVIRONMENT_ID": source_environment,
                "HC_BACKUP_POSTGRES_OPERATION_ID": operation_id,
                "HC_BACKUP_POSTGRES_OWNER_INSTANCE_ID": str(operation.owner_instance_id),
                "HC_BACKUP_POSTGRES_FENCING_TOKEN": str(operation.fencing_token),
                "HC_BACKUP_OBJECT_ENDPOINT": f"http://127.0.0.1:{source_ports[1]}",
                "HC_BACKUP_OBJECT_BUCKET": "hc-data-local",
                "HC_BACKUP_OBJECT_ACCESS_KEY": "minio",
                "HC_BACKUP_OBJECT_SECRET_KEY": "minio-local-only",
                "HC_BACKUP_OBJECT_BUCKET_REFERENCE": f"external-oss-{run_id}",
                "HC_BACKUP_OBJECT_PREFIX": object_prefix,
                "HC_BACKUP_OBJECT_PROVIDER": "s3_compatible",
                "HC_BACKUP_TEMPORAL_DEPLOYMENT_MODE": "compose_functional",
                "HC_BACKUP_TEMPORAL_TARGET": f"127.0.0.1:{source_ports[4]}",
                "HC_BACKUP_TEMPORAL_NAMESPACE": "default",
                "HC_BACKUP_TEMPORAL_CLUSTER_REFERENCE": f"temporal://compose/{run_id}",
                "HC_BACKUP_TEMPORAL_TLS_ENABLED": "false",
                "HC_BACKUP_TEMPORAL_PROVIDER_POLICY_FILE": str(temporal_policy),
                "HC_BACKUP_VAULT_ENDPOINT": f"http://127.0.0.1:{vault_port}",
                "HC_BACKUP_VAULT_TOKEN": vault_token,
                "HC_BACKUP_VAULT_PROVIDER_REFERENCE": f"compose-{run_id}",
                "HC_BACKUP_VAULT_HMAC_KEY_NAME": "backup-hmac",
                "HC_BACKUP_VAULT_HMAC_KEY_VERSION": "1",
                "HC_BACKUP_VAULT_HMAC_KEY_REFERENCE": (
                    f"kms://vault/compose-{run_id}/transit/backup-hmac/versions/v1"
                ),
                "HC_BACKUP_CATALOG_DSN": source_dsn,
            }
        )
        operation = PostgresMaintenanceRepository.from_dsn(source_dsn).renew(
            operation.operation_id,
            owner_instance_id=operation.owner_instance_id,
            fencing_token=operation.fencing_token,
        )
        backup_result = _cli(
            [
                "backup",
                "create",
                "--plan",
                str(plan_path),
                "--staging-directory",
                str(backup_staging),
                "--helm-rendered-manifest",
                str(rendered_helm),
            ],
            cli_env,
        )
        _record_command(
            evidence,
            "hc-platform backup create --plan <run-plan> --staging-directory <run-staging> "
            "--helm-rendered-manifest <run-render>",
            status=str(backup_result.get("status")),
        )
        manifest_path = repository_root / backup_id / "manifest.json"
        manifest = BackupManifestV1.model_validate_json(manifest_path.read_bytes())
        repository_size = sum(
            path.stat().st_size
            for path in (repository_root / backup_id).rglob("*")
            if path.is_file()
        )
        if repository_size >= OBJECT_BYTES:
            raise RuntimeError("reuse_external backup unexpectedly contains the business object")
        _compose(source_project, source_env, "stop", "temporal")
        _compose(target_project, target_env, "stop", "temporal")
        _clone_temporal_databases(
            _compose_container(source_project, source_env, "postgres"),
            _compose_container(target_project, target_env, "postgres"),
            work_root,
        )
        _record_command(
            evidence,
            "provider-owned Temporal cluster recovery source -> isolated target",
        )
        _compose(target_project, target_env, "up", "-d", "--wait", "temporal")
        _compose(source_project, source_env, "stop", "postgres")
        public_raw = private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        signer_sha = hashlib.sha256(public_raw).hexdigest()
        dependencies = tuple(
            sorted(
                manifest.secrets_and_kms.dependencies,
                key=lambda item: (
                    item.environment_variable,
                    item.secret_reference,
                    item.secret_key_name,
                    item.version,
                ),
            )
        )
        requested_at = _utc()
        target_environment = f"dst-{run_id}"
        target = RestoreTargetV1(
            operation_id=f"migration-restore-{run_id}",
            expected_backup_id=backup_id,
            expected_source_platform_id=manifest.source_platform_id,
            expected_source_environment_id=manifest.source_environment_id,
            target_instance_id=f"platform-dst-{run_id}",
            target_environment_id=target_environment,
            purpose="planned_migration",
            target_is_empty=True,
            requested_release=manifest.release,
            postgresql_major=manifest.postgresql.major_version,
            trusted_signing_key_sha256={signer_sha},
            available_kms_key_references={repository_key, signing_reference},
        )
        readiness = RestoreReadinessEvidenceV1(
            target_instance_id=target.target_instance_id,
            target_environment_id=target.target_environment_id,
            observed_at=requested_at - timedelta(seconds=1),
            valid_until=requested_at + timedelta(hours=1),
            evidence_reference=f"evidence://compose/{run_id}/target-readiness",
            evidence_sha256=hashlib.sha256(b"target-readiness").hexdigest(),
            postgresql_available_bytes=10_000_000_000,
            object_store_available_bytes=0,
            deployed_release=manifest.release,
            available_kms_key_references=target.available_kms_key_references,
            temporal=RestoreTemporalReadinessV1(
                cluster_reference=manifest.temporal.cluster_reference,
                namespace=manifest.temporal.namespace,
                service_version=manifest.temporal.service_version,
                ready=True,
                evidence_sha256=hashlib.sha256(b"target-temporal").hexdigest(),
            ),
            external_dependencies=dependencies,
            external_dependencies_ready=True,
            api_writes_disabled=True,
            worker_replicas_zero=True,
            domain_names_ready=True,
            certificates_ready=True,
        )
        request = RestorePlanRequestV1(
            request_id=f"migration-request-{run_id}",
            requested_at=requested_at,
            target=target,
            target_postgresql_database="hc_data",
            object_restore_mode="reuse_external",
            target_object_store_bucket_reference=manifest.object_store.bucket_reference,
            target_object_store_prefix=manifest.object_store.prefix.strip("/"),
            readiness=readiness,
        )
        request_path = work_root / "restore-target.json"
        _private_json(request_path, request.model_dump(mode="json"))
        target_compose_file = work_root / "target-compose.yaml"
        _private_text(target_compose_file, _compose(target_project, target_env, "config"))
        restore_env = cli_env.copy()
        restore_env.update(
            {
                "HC_RESTORE_RUNTIME_PROFILE": "compose_functional",
                "HC_RESTORE_FUNCTIONAL_RUN_ID": run_id,
                "HC_RESTORE_COMPOSE_PROJECT_NAME": target_project,
                "HC_RESTORE_COMPOSE_FILE": str(target_compose_file),
                "HC_RESTORE_COMPOSE_ENV_FILE": str(target_env),
                "HC_RESTORE_POSTGRES_HOST": "127.0.0.1",
                "HC_RESTORE_POSTGRES_PORT": str(target_ports[0]),
                "HC_RESTORE_POSTGRES_DATABASE": "hc_data",
                "HC_RESTORE_POSTGRES_USERNAME": "hc",
                "HC_RESTORE_POSTGRES_PASSWORD": "hc",
                "HC_RESTORE_POSTGRES_SSLMODE": "disable",
                "HC_RESTORE_SOURCE_OBJECT_STORE_ENDPOINT": f"http://127.0.0.1:{source_ports[1]}",
                "HC_RESTORE_SOURCE_OBJECT_STORE_ACCESS_KEY": "minio",
                "HC_RESTORE_SOURCE_OBJECT_STORE_SECRET_KEY": "minio-local-only",
                "HC_RESTORE_SOURCE_OBJECT_STORE_BUCKET": "hc-data-local",
                "HC_RESTORE_SOURCE_OBJECT_STORE_BUCKET_REFERENCE": (
                    manifest.object_store.bucket_reference
                ),
                "HC_RESTORE_OBJECT_STORE_ENDPOINT": f"http://127.0.0.1:{source_ports[1]}",
                "HC_RESTORE_OBJECT_STORE_ACCESS_KEY": "minio",
                "HC_RESTORE_OBJECT_STORE_SECRET_KEY": "minio-local-only",
                "HC_RESTORE_OBJECT_STORE_BUCKET": "hc-data-local",
                "HC_RESTORE_OBJECT_STORE_BUCKET_REFERENCE": (
                    manifest.object_store.bucket_reference
                ),
                "HC_RESTORE_OBJECT_STORE_PREFIX": manifest.object_store.prefix.strip("/"),
                "HC_RESTORE_TEMPORAL_DEPLOYMENT_MODE": "compose_functional",
                "HC_RESTORE_TEMPORAL_TARGET": f"127.0.0.1:{target_ports[4]}",
                "HC_RESTORE_TEMPORAL_NAMESPACE": "default",
                "HC_RESTORE_TEMPORAL_CLUSTER_REFERENCE": manifest.temporal.cluster_reference,
                "HC_RESTORE_TEMPORAL_TLS_ENABLED": "false",
                "HC_RESTORE_EXECUTION_OWNER_ID": f"migration-restore-{run_id}",
                "HC_RESTORE_EXECUTION_FENCING_TOKEN": "1",
                "HC_RESTORE_RECONCILIATION_VERIFIER_IDENTITY": ("compose-migration-exercise"),
            }
        )
        restore_plan = _cli(
            [
                "restore",
                "plan",
                backup_id,
                "--target",
                target_environment,
                "--backup-plan",
                str(plan_path),
                "--target-document",
                str(request_path),
                "--staging-directory",
                str(restore_staging),
                "--dry-run",
            ],
            restore_env,
        )
        _record_command(
            evidence,
            "hc-platform restore plan <backup-id> --target <target> --dry-run",
            status=str(restore_plan.get("status")),
        )
        restore_plan_path = work_root / "restore-plan.json"
        _private_json(restore_plan_path, restore_plan)
        approval_key = Ed25519PrivateKey.generate()
        approval_raw = approval_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        approval_sha = hashlib.sha256(approval_raw).hexdigest()
        approved_at = _utc()
        approval = RestoreApprovalV1(
            approval_id=f"migration-approval-{run_id}",
            approval_reference=f"approval://compose/{run_id}/restore",
            plan_id=str(restore_plan["plan_id"]),
            plan_sha256=hashlib.sha256(canonical_json_bytes(restore_plan)).hexdigest(),
            plan_checkpoint_sha256=str(restore_plan["checkpoint_sha256"]),
            operation_id=target.operation_id,
            backup_id=backup_id,
            target_instance_id=target.target_instance_id,
            target_environment_id=target.target_environment_id,
            approver_identity="compose-functional-approver",
            approval_key_sha256=approval_sha,
            approved_at=approved_at,
            expires_at=approved_at + timedelta(hours=1),
        )
        approval_path = work_root / "restore-approval.json"
        approval_bytes = canonical_json_bytes(approval.model_dump(mode="json"))
        approval_path.write_bytes(approval_bytes)
        approval_path.chmod(0o600)
        signature = RestoreApprovalSignatureV1(
            approval_key_sha256=approval_sha,
            approval_sha256=hashlib.sha256(approval_bytes).hexdigest(),
            signature_base64url=base64.urlsafe_b64encode(approval_key.sign(approval_bytes))
            .rstrip(b"=")
            .decode(),
        )
        approval_signature_path = work_root / "restore-approval.sig"
        _private_json(approval_signature_path, signature.model_dump(mode="json"))
        restore_env["HC_RESTORE_APPROVAL_PUBLIC_KEY_BASE64URL"] = (
            base64.urlsafe_b64encode(approval_raw).rstrip(b"=").decode()
        )
        restore_env["HC_RESTORE_APPROVAL_KEY_SHA256"] = approval_sha
        checkpoint = work_root / "restore-checkpoint.json"
        execute_arguments = [
            "restore",
            "execute",
            backup_id,
            "--target",
            target_environment,
            "--backup-plan",
            str(plan_path),
            "--target-document",
            str(request_path),
            "--restore-plan",
            str(restore_plan_path),
            "--approval",
            str(approval_path),
            "--approval-signature",
            str(approval_signature_path),
            "--checkpoint",
            str(checkpoint),
            "--staging-directory",
            str(restore_staging),
        ]
        execute_result = _cli(execute_arguments, restore_env)
        _record_command(
            evidence,
            "hc-platform restore execute <backup-id> --target <target> "
            "--checkpoint <run-checkpoint>",
            status=str(execute_result.get("status")),
        )
        repeated_execute = _cli(execute_arguments, restore_env)
        _record_command(
            evidence,
            "hc-platform restore execute <backup-id> --target <target> --checkpoint "
            "<same-run-checkpoint> (idempotent replay)",
            status=str(repeated_execute.get("status")),
        )
        reconciled_at = _timestamp(_utc())
        report_path = work_root / "reconciliation-report.json"
        reconcile_result = _cli(
            [
                "restore",
                "reconcile",
                backup_id,
                "--target",
                target_environment,
                "--backup-plan",
                str(plan_path),
                "--target-document",
                str(request_path),
                "--restore-plan",
                str(restore_plan_path),
                "--checkpoint",
                str(checkpoint),
                "--staging-directory",
                str(restore_staging),
                "--report",
                str(report_path),
                "--reconciled-at",
                reconciled_at,
            ],
            restore_env,
        )
        _record_command(
            evidence,
            "hc-platform restore reconcile <backup-id> --target <target> "
            "--checkpoint <same-run-checkpoint>",
            status=str(reconcile_result.get("status")),
        )
        reconciliation_report = json.loads(report_path.read_text(encoding="utf-8"))
        reconciliation_checks = reconciliation_report.get("checks")
        if (
            reconciliation_report.get("overall_status") != "PASS"
            or not isinstance(reconciliation_checks, list)
            or len(reconciliation_checks) != 9
            or any(item.get("status") != "PASS" for item in reconciliation_checks)
        ):
            raise RuntimeError("restore reconciliation report is not a full nine-check PASS")
        target_facts = _database_facts(target_dsn)
        if target_facts != source_facts:
            raise RuntimeError("restored target database facts differ from source")
        client = boto3.client(
            "s3",
            endpoint_url=f"http://127.0.0.1:{source_ports[1]}",
            aws_access_key_id="minio",
            aws_secret_access_key="minio-local-only",
            region_name="us-east-1",
        )
        response = client.get_object(
            Bucket="hc-data-local",
            Key=str(object_fact["object_key"]),
            VersionId=str(object_fact["version_id"]),
        )
        digest = hashlib.sha256()
        count = 0
        while chunk := response["Body"].read(1024 * 1024):
            digest.update(chunk)
            count += len(chunk)
        if count != OBJECT_BYTES or digest.hexdigest() != object_fact["sha256"]:
            raise RuntimeError("target could not read the exact external object version")
        _record_command(
            evidence,
            "target external OSS exact VersionId full-stream size/SHA-256 read",
        )
        _enable_target_writes(
            target_dsn,
            f"migration-enable-{run_id}",
            target_environment,
        )
        if write_pause_started_monotonic is None:
            raise RuntimeError("write-pause timing boundary was not recorded")
        write_pause_seconds = round(time.monotonic() - write_pause_started_monotonic, 3)
        _compose(target_project, target_env, "up", "-d", "--no-deps", "worker")
        _wait_http(f"http://127.0.0.1:{target_ports[6]}/health/live")
        _wait_http(f"http://127.0.0.1:{target_ports[7]}/")
        _wait_http(f"http://127.0.0.1:{target_ports[8]}/healthz")
        target_service_facts = _wait_services_running(
            target_project,
            target_env,
            ("postgres", "temporal", "api", "frontend", "gateway", "worker"),
        )
        _record_command(
            evidence,
            f"docker compose -p {target_project} up -d api frontend gateway worker",
        )
        _http_json(
            "GET",
            f"http://127.0.0.1:{target_ports[6]}/api/v1/auth/session/bootstrap",
            token=session_token,
        )
        controlled_username = f"migration-write-{run_id}"
        controlled_password = secrets.token_urlsafe(24)
        status, _controlled = _http_json(
            "POST",
            f"http://127.0.0.1:{target_ports[6]}/api/v1/auth/registrations",
            {"username": controlled_username, "password": controlled_password},
        )
        if status != 201:
            raise RuntimeError("controlled target write did not succeed")
        with psycopg.connect(target_dsn) as connection:
            written = connection.execute(
                "SELECT count(*) FROM access_control.accounts WHERE canonical_username = %s",
                (controlled_username,),
            ).fetchone()
        if written is None or int(written[0]) != 1:
            raise RuntimeError("controlled write is absent from target PostgreSQL")
        target_operational_facts = _operational_facts(target_dsn)
        if (
            target_operational_facts["outbox_active_claim_count"] != 0
            or target_operational_facts["running_workflow_count"] != 0
            or target_operational_facts["dispatched_workflow_trigger_count"] != 0
        ):
            raise RuntimeError("target Outbox or workflow facts are not quiescent")
        _record_command(
            evidence,
            "target restored-session query + controlled registration + "
            "Outbox/workflow quiescence check",
        )
        _compose(source_project, source_env, "up", "-d", "--wait", "postgres")
        source_after = _database_facts(source_dsn)
        with psycopg.connect(source_dsn) as connection:
            leaked = connection.execute(
                "SELECT count(*) FROM access_control.accounts WHERE canonical_username = %s",
                (controlled_username,),
            ).fetchone()
            catalog_audit = connection.execute(
                """
                SELECT count(*) FROM access_control.audit_events
                WHERE action = 'platform.backup.catalog.created'
                  AND resource_id = %s
                """,
                (backup_id,),
            ).fetchone()
        _compose(source_project, source_env, "stop", "postgres")
        source_service_facts = _compose_service_evidence(
            source_project,
            source_env,
            (
                "postgres",
                "api",
                "worker",
                "temporal",
                "minio",
                "frontend",
                "gateway",
            ),
        )
        if (
            any(
                source_service_facts[service]["running"] is True
                for service in ("postgres", "api", "worker", "temporal")
            )
            or source_service_facts["minio"]["running"] is not True
        ):
            raise RuntimeError("source service stop/external OSS retention boundary is invalid")
        leaked_count = -1 if leaked is None else int(leaked[0])
        catalog_audit_count = -1 if catalog_audit is None else int(catalog_audit[0])
        business_fact_keys = (
            "object_reference_count",
            "object_key",
            "version_id",
            "object_bytes",
            "object_sha256",
            "principal_count",
        )
        source_business_unchanged = all(
            source_after[key] == source_facts[key] for key in business_fact_keys
        )
        audit_delta = int(str(source_after["audit_count"])) - int(str(source_facts["audit_count"]))
        evidence["post_cutover_isolation"] = {
            "source_business_unchanged": source_business_unchanged,
            "source_expected_backup_catalog_audit_rows": catalog_audit_count,
            "source_operational_audit_delta": audit_delta,
            "controlled_write_rows_in_source": leaked_count,
            "controlled_write_rows_in_target": int(written[0]),
        }
        if not source_business_unchanged:
            raise RuntimeError("source PostgreSQL business facts changed after cutover")
        if audit_delta != 1 or catalog_audit_count != 1:
            raise RuntimeError("source PostgreSQL has unexpected post-snapshot audit drift")
        if leaked_count != 0:
            raise RuntimeError("target controlled write leaked into source PostgreSQL")
        component_image_ids: dict[str, str] = {}
        for service in ("api", "frontend", "worker", "gateway"):
            source_image = str(source_service_facts[service]["image_id"])
            target_image = str(target_service_facts[service]["image_id"])
            if source_image != target_image:
                raise RuntimeError("source and target component image digests differ")
            component_image_ids[service] = source_image
        evidence.update(
            {
                "release": manifest.release.model_dump(mode="json"),
                "api_image_id": component_image_ids["api"],
                "component_image_ids": component_image_ids,
                "source_database": source_facts,
                "target_database": target_facts,
                "target_operational_facts": target_operational_facts,
                "runtime_services": {
                    "source": source_service_facts,
                    "target": target_service_facts,
                },
                "object_inventory": {
                    "count": 1,
                    "bytes": OBJECT_BYTES,
                    "content_sha256": object_fact["sha256"],
                    "version_id_sha256": hashlib.sha256(
                        str(object_fact["version_id"]).encode()
                    ).hexdigest(),
                },
                "backup": {
                    "manifest_sha256": backup_result["manifest_sha256"],
                    "repository_bytes": repository_size,
                    "stored_object_count": backup_result["stored_object_count"],
                },
                "restore": {
                    "plan_id": restore_plan["plan_id"],
                    "object_capacity_bytes": restore_plan["capacities"][1]["required_bytes"],
                    "status": execute_result["status"],
                    "repeat_status": repeated_execute["status"],
                    "reconciliation_status": reconcile_result["status"],
                },
                "reconciliation": {
                    "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
                    "overall_status": reconciliation_report["overall_status"],
                    "check_count": len(reconciliation_checks),
                    "checks": [
                        {
                            "name": item["name"],
                            "status": item["status"],
                            "coverage": item["coverage"],
                            "total_count": item["total_count"],
                            "checked_count": item["checked_count"],
                            "issue_count": item["issue_count"],
                            "evidence_sha256": item["evidence_sha256"],
                        }
                        for item in reconciliation_checks
                    ],
                },
                "cutover": {
                    "rpo_zero": True,
                    "write_pause_seconds": write_pause_seconds,
                    "write_pause_target_seconds": 1800,
                    "write_pause_within_target": write_pause_seconds <= 1800,
                },
                "source_login_and_query": "PASS",
                "target_login_and_controlled_write": "PASS",
                "source_postgresql_stopped_after_cutover": True,
                "source_platform_services_stopped_after_cutover": True,
                "source_external_oss_retained_until_cleanup": True,
            }
        )
        _record_command(
            evidence,
            "owner-scoped output artifact + Compose container log forbidden-secret scan",
            status="ZERO_MATCH",
        )
        evidence["secret_artifact_scan"] = _scan_outputs_for_secrets(
            work_root,
            signing_key,
            (source_project, target_project),
            (
                vault_token,
                password,
                session_token,
                controlled_password,
                source_dsn,
                target_dsn,
                "minio-local-only",
                "compose-functional-dsn",
                "compose-functional-access",
                "compose-functional-secret",
                "compose-functional-cursor",
                "compose-functional-credential",
                "compose-functional-abuse",
                "compose-functional-turnstile",
            ),
            canonical_json_bytes(evidence),
        )
        evidence["status"] = "FUNCTIONAL_VERIFICATION_PASSED_CLEANUP_PENDING"
        success = True
        return 0
    except BaseException as exc:
        evidence["status"] = "FAILED"
        evidence["failure"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        raise
    finally:
        cleanup_targets = {
            source_project: _resource_inventory(source_project),
            target_project: _resource_inventory(target_project),
            vault_name: _standalone_container_inventory(vault_name, run_id),
        }
        evidence["cleanup"]["exact_targets_before"] = cleanup_targets
        if success or not keep_on_failure:
            for project, env_file in (
                (source_project, source_env),
                (target_project, target_env),
            ):
                _compose(project, env_file, "down", "--volumes", "--remove-orphans")
                _record_command(
                    evidence,
                    f"docker compose -p {project} down --volumes --remove-orphans",
                )
            if cleanup_targets[vault_name]["containers"]:
                vault = _run(["docker", "inspect", vault_name])
                vault_document = json.loads(str(vault.stdout))[0]
                if vault_document["Config"]["Labels"].get("hc.migration.run_id") != run_id:
                    raise RuntimeError("Vault cleanup label does not match the exact run ID")
                _run(["docker", "rm", "--force", vault_name])
                _record_command(evidence, f"docker rm --force {vault_name}")
            evidence["cleanup"]["exact_targets_after"] = {
                source_project: _resource_inventory(source_project),
                target_project: _resource_inventory(target_project),
                vault_name: _standalone_container_inventory(vault_name, run_id),
            }
            if any(
                resources[kind]
                for resources in evidence["cleanup"]["exact_targets_after"].values()
                for kind in ("containers", "networks", "volumes")
            ):
                raise RuntimeError("exact migration resource cleanup left Docker residuals")
            evidence["cleanup"]["status"] = "PASS"
        else:
            evidence["cleanup"]["status"] = "KEPT_FOR_DEBUG"
        if success or not keep_on_failure:
            for path in sorted(work_root.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            work_root.rmdir()
            evidence["cleanup"]["temporary_work_root_removed"] = not work_root.exists()
        else:
            evidence["cleanup"]["temporary_work_root_removed"] = False
        if success and evidence["cleanup"]["status"] == "PASS":
            evidence["status"] = "PASS"
            evidence["completed_at"] = _timestamp(_utc())
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=f"{int(time.time())}-{secrets.token_hex(3)}")
    parser.add_argument(
        "--evidence",
        type=Path,
        default=ROOT / "artifacts/migration/compose-server-migration-latest.json",
    )
    parser.add_argument("--keep-on-failure", action="store_true")
    arguments = parser.parse_args()
    return exercise(arguments.run_id, arguments.evidence, keep_on_failure=arguments.keep_on_failure)


if __name__ == "__main__":
    raise SystemExit(main())
