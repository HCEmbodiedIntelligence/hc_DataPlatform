"""Executable release gates for cross-owner blockers found by BE-12.

These checks use ``pytest.xfail`` only while authoritative current-state evidence
still contradicts an acceptance requirement.  They become ordinary passing tests
as owners close the contract/runtime gap; an xfail is never counted as release
acceptance by ``ACCEPTANCE-MATRIX.md``.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import json
import re
from collections.abc import Iterable
from dataclasses import fields
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from pydantic import ValidationError
from temporalio.exceptions import ApplicationError

from hc_data_platform.alignment.models import AlignedValueV1, AlignmentStrategy
from hc_data_platform.core.app import create_app
from hc_data_platform.core.capacity_evidence import validate_capacity_evidence
from hc_data_platform.core.config import Settings
from hc_data_platform.core.health import ReadinessProbe
from hc_data_platform.lance_catalog.models import StepRecord
from hc_data_platform.workflow import activities
from hc_data_platform.workflow.models import VerificationActivityInput

BACKEND = Path(__file__).parents[2]
SOURCE = BACKEND / "src" / "hc_data_platform"
CHART = BACKEND.parent / "deploy" / "helm" / "hc-data-platform"
REQUIRED_METRICS = {
    "hc_data_upload_backlog",
    "hc_data_workflow_failures_total",
    "hc_data_qc_outcomes_total",
    "hc_data_lance_commits_total",
    "hc_data_transcode_duration_seconds",
    "hc_data_exports_total",
}
REQUIRED_LOG_FIELDS = {
    "timestamp",
    "severity",
    "service",
    "instance_id",
    "node_name",
    "role",
    "release_id",
    "request_id",
    "trace_id",
    "operation_id",
    "workflow_id",
    "event_code",
    "duration_ms",
    "retry_count",
    "error_type",
}


def _xfail_if(condition: bool, finding: str, detail: str) -> None:
    if condition:
        pytest.xfail(f"{finding}: {detail}")


def _source_text(paths: Iterable[Path]) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def _yaml(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")))


def _logger_fields(paths: Iterable[Path]) -> tuple[bool, set[str]]:
    found = False
    fields: set[str] = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                fields.update(
                    key.value
                    for key in node.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                )
            if not isinstance(node, ast.Call):
                continue
            direct_log_event = isinstance(node.func, ast.Name) and node.func.id == "log_event"
            logger_call = (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "logger"
            )
            if direct_log_event or logger_call:
                found = True
                fields.update(keyword.arg for keyword in node.keywords if keyword.arg is not None)
    return found, fields


def test_release_gate_mounts_every_frozen_public_http_path() -> None:
    fragment_paths: set[str] = set()
    for path in sorted((BACKEND / "openapi").glob("*.yaml")):
        fragment_paths.update(_yaml(path).get("paths", {}))

    class ReadyProbe:
        async def check(self) -> None:
            return None

    ready: ReadinessProbe = ReadyProbe()

    app = create_app(
        settings=Settings(environment="test", runtime_backend="memory"),
        readiness_probes={
            "postgresql": ready,
            "temporal": ready,
            "object_storage": ready,
        },
    )
    runtime_paths = set(app.openapi()["paths"])
    missing = sorted(fragment_paths - runtime_paths)
    _xfail_if(bool(missing), "BE12-004", f"runtime API is missing: {missing}")
    assert not missing


def test_release_gate_preserves_all_interpolation_source_timestamps() -> None:
    aligned = AlignedValueV1(
        value=[0.5],
        source_timestamps_ns=(0, 10),
        time_error_ns=5,
        valid=True,
        strategy=AlignmentStrategy.LINEAR,
    )
    try:
        step = StepRecord(
            rollout_id="be12-provenance",
            step_index=0,
            timestamp_ns=5,
            modalities={"joint": aligned.value},
            source_timestamps_ns={"joint": cast(Any, aligned.source_timestamps_ns)},
            time_error_ns={"joint": aligned.time_error_ns},
            valid={"joint": aligned.valid},
            repeated={"joint": aligned.repeated},
        )
    except ValidationError as error:
        pytest.xfail(f"BE12-005: Lance StepRecord rejects interpolation provenance: {error}")
    assert cast(Any, step.source_timestamps_ns["joint"]) == (0, 10)


def test_release_gate_worker_image_and_pilot_have_production_activity_dependencies() -> None:
    dockerfile = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    pilot = _yaml(CHART / "values-ci.yaml")
    main_factory = pilot.get("backend", {}).get("config", {}).get("workflowActivityFactory", "")
    media_template = (CHART / "templates/backend-media-worker.yaml").read_text(encoding="utf-8")
    media_factory_match = re.search(
        r"value:\s*(hc_data_platform\.[A-Za-z0-9_.]+:[A-Za-z0-9_]+)",
        media_template,
    )
    media_factory = "" if media_factory_match is None else media_factory_match.group(1)
    missing = []
    for extra in ("data",):
        if re.search(rf"--extra(?:=|\s+){re.escape(extra)}(?:\s|$)", dockerfile) is None:
            missing.append(f"Docker extra {extra}")
    factories = {"main": main_factory, "media": media_factory}
    for role, factory in factories.items():
        if not factory:
            missing.append(f"{role} workflowActivityFactory")
            continue
        module_name, separator, attribute = factory.partition(":")
        if not separator:
            missing.append(f"{role} workflowActivityFactory syntax")
            continue
        try:
            candidate = getattr(importlib.import_module(module_name), attribute)
            dependencies = candidate()
        except Exception as error:
            missing.append(f"{role} factory invocation ({type(error).__name__}: {error})")
            continue
        if not isinstance(dependencies, activities.ActivityDependencies):
            missing.append(f"{role} factory ActivityDependencies result")
            continue
        required_fields = (
            {field.name for field in fields(dependencies)} - {"aligned_media"}
            if role == "main"
            else {"aligned_media", "aligned_media_repository", "aligned_media_store"}
        )
        missing.extend(
            f"{role} activity dependency {field_name}"
            for field_name in sorted(required_fields)
            if getattr(dependencies, field_name) is None
        )
        if role == "main" and dependencies.aligned_media is not None:
            missing.append("main worker unexpectedly constructs FFmpeg media dependency")
    _xfail_if(bool(missing), "BE12-001", f"missing {', '.join(missing)}")
    assert not missing


def test_release_gate_worker_excludes_validation_only_lerobot_sdk() -> None:
    dockerfile = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    worker_builder = dockerfile.split("FROM project AS worker-builder", maxsplit=1)[1].split(
        "FROM ", maxsplit=1
    )[0]
    included = (
        "lerobot-v3-validation" in worker_builder or "import lance, lerobot" in worker_builder
    )
    _xfail_if(
        included,
        "BE12-010",
        "production Worker includes the validation-only LeRobot/PyTorch/CUDA dependency stack",
    )
    assert not included


def test_release_gate_application_produces_verified_auth_context() -> None:
    auth_sources = _source_text(
        [
            *sorted((SOURCE / "core").glob("*.py")),
            *sorted((SOURCE / "security").glob("*.py")),
        ]
    )
    producer = re.search(r"(?:request\.)?state\.auth_context\s*=", auth_sources)
    _xfail_if(producer is None, "BE12-003", "no application AuthContext producer is installed")
    assert producer is not None


def test_release_gate_domain_metrics_and_contextual_logs_have_source_emitters() -> None:
    source_paths = sorted(SOURCE.rglob("*.py"))
    source = _source_text(source_paths)
    missing_metrics = sorted(metric for metric in REQUIRED_METRICS if metric not in source)
    has_logger, emitted_log_fields = _logger_fields(source_paths)
    missing_log_fields = sorted(REQUIRED_LOG_FIELDS - emitted_log_fields)
    problems = []
    if missing_metrics:
        problems.append(f"metrics={missing_metrics}")
    if not has_logger:
        problems.append("no logger calls")
    if missing_log_fields:
        problems.append(f"log fields={missing_log_fields}")
    _xfail_if(bool(problems), "BE12-002", "; ".join(problems))
    assert not problems


def test_release_gate_unconfigured_activity_port_is_non_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activities.configure_activity_dependencies(activities.ActivityDependencies())

    async def invoke_without_temporal_context(
        stage: str,
        operation: Any,
        *,
        on_cancel: Any = None,
    ) -> Any:
        del stage, on_cancel
        return operation()

    monkeypatch.setattr(activities, "_with_heartbeats", invoke_without_temporal_context)
    request = VerificationActivityInput(
        rollout_id="be12-unconfigured-port",
        object_key="raw/test.mcap",
        source_sha256="a" * 64,
        required_topics=frozenset(),
    )
    try:
        asyncio.run(activities.verify_raw(request))
    except activities.WorkflowPortNotConfigured as error:
        pytest.xfail(f"BE12-009: raw unconfigured-port error remains retryable: {error}")
    except ApplicationError as error:
        assert error.type == "WORKFLOW_PORT_NOT_CONFIGURED"
        assert error.non_retryable is True
    else:
        raise AssertionError("an unconfigured required activity port unexpectedly succeeded")


def test_release_gate_default_pytest_command_registers_asyncio_marker() -> None:
    pyproject = (BACKEND / "pyproject.toml").read_text(encoding="utf-8")
    registered = re.search(r'["\']asyncio(?:\s*:|["\'])', pyproject) is not None
    _xfail_if(
        not registered,
        "BE12-006",
        "strict marker configuration omits pytest-asyncio's asyncio marker",
    )
    assert registered


def test_release_gate_has_measured_end_to_end_capacity_pass() -> None:
    passing_evidence = []
    invalid_pass_claims: dict[str, tuple[str, ...]] = {}
    for path in sorted((BACKEND / "tests" / "load" / "results").glob("*.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        verdict = result.get("verdict", {}).get("end_to_end_5tb_per_day")
        if verdict != "PASS":
            continue
        validation = validate_capacity_evidence(result)
        if validation.accepted:
            passing_evidence.append(path.name)
        else:
            invalid_pass_claims[path.name] = validation.errors
    assert invalid_pass_claims == {}, invalid_pass_claims
    _xfail_if(
        not passing_evidence,
        "BE12-008",
        "no strictly validated 30-minute production-like 5 TB/day plus 30% evidence exists",
    )
    assert passing_evidence
