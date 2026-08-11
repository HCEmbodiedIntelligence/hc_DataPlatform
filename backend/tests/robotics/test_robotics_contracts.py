from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError as PydanticValidationError

from app.core.catalog import CAPABILITIES
from app.domains.robotics.router import router
from app.domains.robotics.schemas import (
    BindingTarget,
    CreateRobotModelSampleValidationRequest,
)
from app.domains.robotics.service import canonical_hash

SPEC_PATH = Path(
    "/home/czy/plan/backend/05-robotics-calibration-schema/"
    "robotics-calibration-schema-api.openapi.yaml"
)
ROUTER_PATHS = (
    Path("app/domains/robotics/robots/router.py"),
    Path("app/domains/robotics/calibrations/router.py"),
    Path("app/domains/robotics/schemas/router.py"),
)


def _spec_operations() -> dict[str, dict[str, Any]]:
    document = yaml.safe_load(SPEC_PATH.read_text())
    return {
        operation["operationId"]: {"method": method.upper(), **operation}
        for path_item in document["paths"].values()
        for method, operation in path_item.items()
        if isinstance(operation, dict) and operation.get("operationId")
    }


def _implemented_operations() -> dict[str, tuple[set[str], str]]:
    result: dict[str, tuple[set[str], str]] = {}
    for path in ROUTER_PATHS:
        source = path.read_text()
        for node in ast.parse(source).body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            operation_id = None
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                for keyword in decorator.keywords:
                    if (
                        keyword.arg == "operation_id"
                        and isinstance(keyword.value, ast.Constant)
                        and isinstance(keyword.value.value, str)
                    ):
                        operation_id = keyword.value.value
            if operation_id is None:
                continue
            capabilities = {
                argument.value
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "require"
                for argument in call.args
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
            }
            assert operation_id not in result, f"duplicate operation_id: {operation_id}"
            result[operation_id] = (
                capabilities,
                ast.get_source_segment(source, node) or "",
            )
    return result


def test_runtime_router_matches_all_current_openapi_operations_exactly() -> None:
    expected = set(_spec_operations())
    runtime_ids = [route.operation_id for route in router.routes]
    assert len(expected) == 110
    assert len(runtime_ids) == 110
    assert len(set(runtime_ids)) == 110
    assert set(runtime_ids) == expected


def test_every_operation_uses_the_exact_registered_capability_contract() -> None:
    expected = _spec_operations()
    implemented = _implemented_operations()
    assert set(implemented) == set(expected)
    for operation_id, operation in expected.items():
        required = set(operation.get("x-required-capabilities", []))
        actual = implemented[operation_id][0]
        assert actual == required, operation_id
        assert actual <= CAPABILITIES, operation_id


def test_mutations_are_idempotent_and_emit_every_declared_audit_event() -> None:
    expected = _spec_operations()
    implemented = _implemented_operations()
    for operation_id, operation in expected.items():
        if operation["method"] not in {"POST", "PUT", "PATCH", "DELETE"}:
            continue
        source = implemented[operation_id][1]
        assert "with_idempotency" in source, operation_id
        for event_name in operation.get("x-audit-events", []):
            assert event_name in source, f"{operation_id} does not emit {event_name}"


def test_robotics_domain_has_no_forbidden_cross_domain_imports() -> None:
    forbidden = {
        "app.domains.ingest",
        "app.domains.datasets",
        "app.domains.cleaning",
        "app.domains.annotation",
        "app.domains.storage",
        "app.domains.access",
    }
    for path in Path("app/domains/robotics").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {node.module or ""}
            else:
                continue
            assert not any(
                name == prefix or name.startswith(f"{prefix}.")
                for name in names
                for prefix in forbidden
            ), path


def test_half_open_binding_interval_rejects_empty_or_reversed_ranges() -> None:
    with pytest.raises(PydanticValidationError):
        BindingTarget.model_validate(
            {
                "scope_type": "ROBOT_INSTANCE",
                "scope_id": "rb_fx_01",
                "valid_from": "2026-08-11T01:00:00Z",
                "valid_to": "2026-08-11T01:00:00Z",
            }
        )


def test_sample_validation_rejects_an_empty_nanosecond_window() -> None:
    with pytest.raises(PydanticValidationError):
        CreateRobotModelSampleValidationRequest.model_validate(
            {
                "candidate_id": "candidate_fx_01",
                "start_ns": "100",
                "end_ns": "100",
                "max_points": 100,
                "validation_input_hash": f"sha256:{'a' * 64}",
            }
        )


def test_canonical_hash_is_order_independent_and_sensitive_to_content() -> None:
    left = {"frames": [{"id": "base"}], "parameters": {"scale": 1}}
    reordered = {"parameters": {"scale": 1}, "frames": [{"id": "base"}]}
    changed = {"parameters": {"scale": 2}, "frames": [{"id": "base"}]}
    assert canonical_hash(left) == canonical_hash(reordered)
    assert canonical_hash(left) != canonical_hash(changed)
