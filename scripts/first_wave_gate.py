#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import NoReturn

from artifact_security import redact_text, sanitize_directory

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
ARTIFACT_ROOT = (ROOT / "artifacts/test-gates/latest").resolve()
COMPOSE_TEST = ["docker", "compose", "-f", str(ROOT / "compose.test.yaml")]
os.environ.setdefault("HC_GATE_UID", str(os.getuid()))
os.environ.setdefault("HC_GATE_GID", str(os.getgid()))

# Exact manifest shipped before the second-wave additions.  The upgrade gate seeds this
# baseline with its real checksums, then lets the current migrator add only new versions.
LEGACY_MIGRATION_BASELINE = (
    "security/001_core.sql",
    "ingest/001_ingest.sql",
    "workflow/001_jobs.sql",
    "workflow/002_job_recovery.sql",
    "verification/0001_verification_reports.sql",
    "verification/0002_runtime_scope.sql",
    "quality/0001_quality_reports.sql",
    "alignment/0001_alignment_fragments.sql",
    "alignment/0002_runtime_manifest.sql",
    "lance_catalog/0001_lance_catalog.sql",
    "annotation/0001_annotation.sql",
    "publishing/0001_publishing.sql",
    "verification/0003_tenant_report_identity.sql",
    "quality/0002_tenant_report_identity.sql",
    "alignment/0003_ready_attempt_immutable.sql",
)


def backend_python() -> list[str]:
    local = BACKEND / ".venv/bin/python"
    if local.is_file():
        return [str(local)]
    return ["uv", "run", "python"]


def run(label: str, command: list[str], *, cwd: Path = ROOT) -> None:
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = ARTIFACT_ROOT / f"{label}.log"
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output, _ = process.communicate()
    safe_output = redact_text(output or "")
    sys.stdout.write(safe_output)
    log_path.write_text(safe_output, encoding="utf-8")
    return_code = process.returncode
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, command)


def pytest_host(label: str, paths: list[str], *, strict_xfail: bool = True) -> None:
    command = [
        *backend_python(),
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "--fail-on-skipped",
        *(["--fail-on-xfailed"] if strict_xfail else []),
        f"--junitxml={ARTIFACT_ROOT / f'{label}.xml'}",
        *paths,
    ]
    run(label, command, cwd=BACKEND)


def start_dependencies(*, require_worker: bool = False) -> None:
    run(
        "dependency-reset",
        [*COMPOSE_TEST, "down", "--volumes", "--remove-orphans"],
    )
    run(
        "dependency-services",
        [
            *COMPOSE_TEST,
            "up",
            "--build",
            "--detach",
            "--wait",
            "postgres",
            "minio",
            "temporal",
        ],
    )
    for service in ("minio-init", "migration", "migration-check"):
        run(
            f"dependency-{service}",
            [*COMPOSE_TEST, "run", "--build", "--rm", "--no-deps", service],
        )
    if require_worker:
        run(
            "dependency-worker",
            [*COMPOSE_TEST, "up", "--build", "--detach", "--wait", "worker"],
        )
    run("dependency-status", [*COMPOSE_TEST, "ps"])


def pytest_container(label: str, paths: list[str], *, strict_xfail: bool = True) -> None:
    command = [
        *COMPOSE_TEST,
        "run",
        "--build",
        "--rm",
        "--no-deps",
        "gate-runner",
        "python",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "--fail-on-skipped",
        *(["--fail-on-xfailed"] if strict_xfail else []),
        f"--junitxml=/artifacts/latest/{label}.xml",
        *paths,
    ]
    run(label, command)


def static_gate() -> None:
    run(
        "compose-real-api-config",
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "compose.dev.yaml"),
            "-f",
            str(ROOT / "compose.real-api.yaml"),
            "config",
            "--quiet",
        ],
    )
    run("compose-test-config", [*COMPOSE_TEST, "config", "--quiet"])
    run(
        "openapi-drift",
        [
            *backend_python(),
            "-m",
            "hc_data_platform.core.openapi",
            "--runtime",
            "--check",
        ],
        cwd=BACKEND,
    )
    runtime_contract = textwrap.dedent(
        """
        import hashlib
        import json
        from collections import Counter
        from pathlib import Path

        import yaml
        from fastapi.routing import APIRoute

        from hc_data_platform.core.app import create_app
        from hc_data_platform.core.config import Settings
        from hc_data_platform.core.openapi import (
            aggregate_fragments,
            check_formal_runtime_contract,
            formal_runtime_contract_issues,
            render,
        )
        from hc_data_platform.dashboard.service import DashboardService

        document_path = Path("openapi.generated.yaml")
        committed = yaml.safe_load(document_path.read_text(encoding="utf-8"))
        formal = aggregate_fragments(Path("openapi"))
        application = create_app(
            settings=Settings(environment="test", runtime_backend="production")
        )
        assert isinstance(application.state.runtime.dashboard, DashboardService)
        runtime = application.openapi()
        check_formal_runtime_contract(formal, runtime)
        assert committed == runtime
        mounted_operations = Counter(
            (route.path, method.lower())
            for route in application.routes
            if isinstance(route, APIRoute)
            for method in route.methods
            if method.lower() in {"get", "post", "put", "patch", "delete"}
        )
        duplicate_mounts = sorted(key for key, count in mounted_operations.items() if count != 1)
        assert duplicate_mounts == [], duplicate_mounts
        expected = {
            "/api/v1/projects/{project_id}/dashboard/snapshot",
            "/api/v1/projects/{project_id}/dashboard/activity",
            "/api/v1/projects/{project_id}/dashboard/coverage",
            "/api/v1/projects/{project_id}/dashboard/pending-items",
        }
        formal_paths = set(formal["paths"])
        runtime_paths = set(runtime["paths"])
        http_methods = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
        formal_operations = {
            (path, method): operation.get("operationId")
            for path, path_item in formal["paths"].items()
            for method, operation in path_item.items()
            if method in http_methods
        }
        runtime_operations = {
            (path, method): operation.get("operationId")
            for path, path_item in runtime["paths"].items()
            for method, operation in path_item.items()
            if method in http_methods
        }
        forbidden_public_starts = sorted(
            (path, method)
            for (path, method), operation_id in runtime_operations.items()
            if "start-ingest" in path
            or "startingest" in str(operation_id).replace("_", "").lower()
            or (
                path == "/api/v1/projects/{project_id}/annotation-tasks"
                and method == "post"
            )
        )
        assert forbidden_public_starts == [], forbidden_public_starts
        assert expected <= formal_paths
        assert expected <= runtime_paths
        assert formal_paths == runtime_paths, {
            "formal_only": sorted(formal_paths - runtime_paths),
            "runtime_only": sorted(runtime_paths - formal_paths),
        }
        assert formal_operations.keys() == runtime_operations.keys()
        contract_issues = formal_runtime_contract_issues(formal, runtime)
        assert contract_issues == ()
        print(json.dumps({
            "generated_runtime_sha256": hashlib.sha256(document_path.read_bytes()).hexdigest(),
            "dashboard_paths": sorted(expected),
            "formal_paths": len(formal_paths),
            "formal_operations": len(formal_operations),
            "contract_issues": list(contract_issues),
            "contract_issue_count": len(contract_issues),
            "duplicate_router_mounts": duplicate_mounts,
            "forbidden_public_starts": forbidden_public_starts,
            "runtime_paths": len(runtime_paths),
            "runtime_operations": len(runtime_operations),
            "runtime_openapi_sha256": hashlib.sha256(render(runtime).encode()).hexdigest(),
        }, sort_keys=True))
        """
    )
    run(
        "runtime-openapi-contract",
        [*backend_python(), "-c", runtime_contract],
        cwd=BACKEND,
    )
    run(
        "frontend-generated-api-drift",
        ["node", "scripts/generate-api-client.mjs", "--check"],
        cwd=ROOT / "frontend",
    )
    run("frontend-typecheck", ["pnpm", "typecheck"], cwd=ROOT / "frontend")
    pytest_host(
        "static-contracts",
        [
            "tests/core/test_migrations.py",
            "tests/gates/test_environment_contract.py",
            "tests/gates/test_production_exposure.py",
            "tests/gates/test_sensitive_artifact_scan.py",
            "tests/system/test_wave2_fixture_contracts.py",
            "tests/workflow/test_worker_healthcheck.py",
        ],
    )
    quality_commands = (
        (
            "backend-format",
            [str(BACKEND / ".venv/bin/ruff"), "format", "--check", "src", "tests"],
            BACKEND,
        ),
        (
            "backend-lint",
            [str(BACKEND / ".venv/bin/ruff"), "check", "src", "tests"],
            BACKEND,
        ),
        (
            "backend-mypy",
            [str(BACKEND / ".venv/bin/mypy"), "src"],
            BACKEND,
        ),
        (
            "frontend-generator-format",
            ["pnpm", "exec", "prettier", "--check", "scripts/generate-api-client.mjs"],
            ROOT / "frontend",
        ),
    )
    quality_failures: list[str] = []
    for label, command, cwd in quality_commands:
        try:
            run(label, command, cwd=cwd)
        except subprocess.CalledProcessError:
            quality_failures.append(label)
    if quality_failures:
        print(json.dumps({"static_quality_failures": quality_failures}, sort_keys=True))
        raise subprocess.CalledProcessError(1, ["static-quality"])


def _reset_postgres(label: str) -> None:
    run(
        f"{label}-reset",
        [*COMPOSE_TEST, "down", "--volumes", "--remove-orphans"],
    )
    run(
        f"{label}-postgres",
        [*COMPOSE_TEST, "up", "--detach", "--wait", "postgres"],
    )


def _migration_service(label: str, service: str) -> None:
    run(
        label,
        [*COMPOSE_TEST, "run", "--build", "--rm", "--no-deps", service],
    )


def migration_gate() -> None:
    _reset_postgres("migration-fresh")
    _migration_service("migration-fresh-upgrade", "migration")
    _migration_service("migration-fresh-status", "migration-check")

    _reset_postgres("migration-existing")
    baseline = repr(LEGACY_MIGRATION_BASELINE)
    seed_program = textwrap.dedent(
        f"""
        import asyncio
        import json
        import os

        import asyncpg

        from hc_data_platform.core import migrations as migration_module

        baseline = {baseline}

        async def main():
            migrations = {{item.version: item for item in migration_module.load_migrations()}}
            connection = await asyncpg.connect(
                os.environ["HC_POSTGRES_DSN"].replace("postgresql+asyncpg://", "postgresql://", 1)
            )
            try:
                async with connection.transaction():
                    await connection.execute(migration_module._TRACKING_TABLE_SQL)
                    for version in baseline:
                        migration = migrations[version]
                        await connection.execute(migration.sql)
                        await connection.execute(
                            "INSERT INTO core.schema_migrations "
                            "(version, checksum_sha256) VALUES ($1, $2)",
                            migration.version,
                            migration.checksum_sha256,
                        )
                    await connection.execute(migrations["security/001_core.sql"].sql)
            finally:
                await connection.close()
            print(json.dumps({{"seeded": list(baseline), "count": len(baseline)}}))

        asyncio.run(main())
        """
    )
    run(
        "migration-existing-seed",
        [
            *COMPOSE_TEST,
            "run",
            "--build",
            "--rm",
            "--no-deps",
            "gate-runner",
            "python",
            "-c",
            seed_program,
        ],
    )
    expected_program = textwrap.dedent(
        f"""
        import asyncio
        import json
        import os

        from hc_data_platform.core.migrations import load_migrations, migration_status

        baseline = set({baseline})

        async def main():
            status = await migration_status(os.environ["HC_POSTGRES_DSN"])
            expected_missing = sorted(
                item.version for item in load_migrations() if item.version not in baseline
            )
            assert status["missing"] == expected_missing, status
            assert status["checksum_drift"] == [], status
            assert status["unknown"] == [], status
            print(json.dumps(status, sort_keys=True))

        asyncio.run(main())
        """
    )
    run(
        "migration-existing-before",
        [
            *COMPOSE_TEST,
            "run",
            "--build",
            "--rm",
            "--no-deps",
            "gate-runner",
            "python",
            "-c",
            expected_program,
        ],
    )
    _migration_service("migration-existing-upgrade", "migration")
    _migration_service("migration-existing-status", "migration-check")
    _migration_service("migration-existing-reupgrade", "migration")
    negative_program = textwrap.dedent(
        """
        import asyncio
        import json
        import os

        import asyncpg

        from hc_data_platform.core.migrations import load_migrations, migration_status

        async def connect():
            return await asyncpg.connect(
                os.environ["HC_POSTGRES_DSN"].replace(
                    "postgresql+asyncpg://", "postgresql://", 1
                )
            )

        async def main():
            migration = load_migrations()[0]
            connection = await connect()
            try:
                await connection.execute(
                    "INSERT INTO core.schema_migrations "
                    "(version, checksum_sha256) VALUES ($1, $2)",
                    "unknown/999_gate.sql", "0" * 64,
                )
            finally:
                await connection.close()
            unknown = await migration_status(os.environ["HC_POSTGRES_DSN"])
            assert unknown["unknown"] == ["unknown/999_gate.sql"], unknown

            connection = await connect()
            try:
                await connection.execute(
                    "DELETE FROM core.schema_migrations WHERE version = $1",
                    "unknown/999_gate.sql",
                )
                await connection.execute(
                    "UPDATE core.schema_migrations SET checksum_sha256 = $1 WHERE version = $2",
                    "f" * 64, migration.version,
                )
            finally:
                await connection.close()
            drift = await migration_status(os.environ["HC_POSTGRES_DSN"])
            assert drift["checksum_drift"] == [migration.version], drift

            connection = await connect()
            try:
                await connection.execute(
                    "UPDATE core.schema_migrations SET checksum_sha256 = $1 WHERE version = $2",
                    migration.checksum_sha256, migration.version,
                )
            finally:
                await connection.close()
            clean = await migration_status(os.environ["HC_POSTGRES_DSN"])
            assert clean["status"] == "current", clean
            assert clean["missing"] == [], clean
            assert clean["checksum_drift"] == [], clean
            assert clean["unknown"] == [], clean
            print(json.dumps({"unknown": unknown, "drift": drift, "restored": clean}, sort_keys=True))

        asyncio.run(main())
        """
    )
    run(
        "migration-unknown-drift-diagnostics",
        [
            *COMPOSE_TEST,
            "run",
            "--build",
            "--rm",
            "--no-deps",
            "gate-runner",
            "python",
            "-c",
            negative_program,
        ],
    )


def worker_gate() -> None:
    start_dependencies(require_worker=True)


def integration_gate() -> None:
    start_dependencies()
    pytest_container(
        "external-integration",
        [
            "tests/annotation/test_annotation_postgres.py",
            "tests/collection_tasks/test_collection_task_postgres.py",
            "tests/dashboard/test_dashboard_postgres.py",
            "tests/ingest/test_minio_integration.py",
            "tests/ingest/test_postgres_integration.py",
            "tests/publishing/test_publication_lineage_postgres.py",
            "tests/security/test_access_postgres.py",
            "tests/security/test_postgres_integration.py",
            "tests/storage/test_postgres_integration.py",
        ],
    )


def replay_gate() -> None:
    start_dependencies()
    pytest_container(
        "worker-replay",
        [
            "tests/workflow/test_temporal_workflows.py",
            "tests/system/test_temporal_worker_recovery.py",
        ],
    )


def security_gate(*, baseline: bool) -> None:
    start_dependencies()
    pytest_container(
        "public-security-baseline" if baseline else "public-security",
        [
            "tests/security",
            "tests/ingest/test_manifest_security_gate.py",
        ],
        strict_xfail=not baseline,
    )


def regression_gate() -> None:
    pytest_host("backend-strict-regression", ["tests"])


def e2e_gate() -> NoReturn:
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "NOT RUN",
        "reason": (
            "BE22 isolated fixtures exist, but the FE second-wave browser adapters are "
            "still explicit dependency failures and no VITE_MOCK_MODE=off browser chain "
            "has run successfully"
        ),
        "required_chain": [
            "register empty account",
            "request and approve project membership/capabilities",
            "create collection task",
            "upload package",
            "Manifest discovery and QC",
            "multi-camera annotation",
            "multi-level Tag review",
            "close collection task",
        ],
        "spec": "frontend/e2e/real-api/main-chain.spec.ts",
    }
    report_path = ARTIFACT_ROOT / "real-api-e2e-not-run.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    raise SystemExit(2)


def artifact_gate() -> None:
    audit_paths = sanitize_directory(ARTIFACT_ROOT)
    if audit_paths:
        print(
            json.dumps(
                {
                    "artifact_redaction_audits": [
                        str(path.relative_to(ARTIFACT_ROOT)) for path in audit_paths
                    ]
                },
                sort_keys=True,
            )
        )
    run(
        "artifact-scan",
        [
            *backend_python(),
            "tests/gates/sensitive_artifact_scan.py",
            str(ARTIFACT_ROOT),
        ],
        cwd=BACKEND,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run first-wave truthful quality gates")
    parser.add_argument(
        "gate",
        choices=(
            "static",
            "migration",
            "worker",
            "integration",
            "replay",
            "security",
            "security-baseline",
            "regression",
            "e2e",
            "artifact",
        ),
    )
    return parser.parse_args()


def _dispatch_gate(gate: str) -> None:
    if gate == "static":
        static_gate()
    elif gate == "migration":
        migration_gate()
    elif gate == "worker":
        worker_gate()
    elif gate == "integration":
        integration_gate()
    elif gate == "replay":
        replay_gate()
    elif gate == "security":
        security_gate(baseline=False)
    elif gate == "security-baseline":
        security_gate(baseline=True)
    elif gate == "regression":
        regression_gate()
    elif gate == "e2e":
        e2e_gate()
    else:
        artifact_gate()


def main() -> None:
    gate = parse_args().gate
    gate_failure: BaseException | None = None
    try:
        _dispatch_gate(gate)
    except BaseException as exc:
        gate_failure = exc

    artifact_failure: BaseException | None = None
    if gate != "artifact":
        try:
            artifact_gate()
        except BaseException as exc:
            artifact_failure = exc

    if artifact_failure is not None:
        if gate_failure is not None:
            print("primary gate and sensitive artifact scan both failed", file=sys.stderr)
        raise artifact_failure
    if gate_failure is not None:
        raise gate_failure


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"gate failed with exit code {exc.returncode}", file=sys.stderr)
        raise SystemExit(exc.returncode) from None
