from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import yaml

ROOT = Path(__file__).resolve().parents[3]

REQUIRED_FIELDS = {
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
FORBIDDEN_NGINX_VARIABLES = {
    "$args",
    "$cookie_",
    "$http_",
    "$is_args",
    "$query_string",
    "$request_body",
    "$request_uri",
    "$uri",
}


def _log_verifier() -> ModuleType:
    path = ROOT / "backend/observability/verify_logs.py"
    specification = importlib.util.spec_from_file_location("hc_observability_verify_logs", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _quoted_json_fields(log_format: str) -> set[str]:
    return set(re.findall(r'\\?"([a-z_]+)\\?"\s*:', log_format))


def test_frontend_and_compose_gateways_emit_only_fixed_safe_json_access_events() -> None:
    entrypoint = (ROOT / "frontend/container/docker-entrypoint.sh").read_text(encoding="utf-8")
    frontend_nginx = (ROOT / "frontend/container/nginx.conf").read_text(encoding="utf-8")
    compose_gateway = (ROOT / "deploy/compose/gateway.conf").read_text(encoding="utf-8")

    frontend_format = next(line for line in entrypoint.splitlines() if "log_format hc_json" in line)
    compose_format = next(
        line for line in compose_gateway.splitlines() if line.startswith("log_format hc_json")
    )
    for log_format in (frontend_format, compose_format):
        assert _quoted_json_fields(log_format) >= REQUIRED_FIELDS
        assert "hc-runtime-log/v1" in log_format
        assert "HTTP.REQUEST_COMPLETED" in log_format
        assert not any(variable in log_format for variable in FORBIDDEN_NGINX_VARIABLES)
        assert "authorization" not in log_format.lower()
        assert "password" not in log_format.lower()
        assert "object" not in log_format.lower()
        assert "token" not in log_format.lower()

    assert "include /tmp/hc-runtime/logging.conf;" in frontend_nginx
    assert "access_log /dev/stdout hc_json;" in frontend_nginx
    assert "access_log /dev/stdout hc_json;" in compose_gateway
    assert "$upstream_http_x_request_id" in compose_gateway


def test_machine_contract_keeps_runtime_audit_and_central_storage_boundaries_separate() -> None:
    contract = yaml.safe_load(
        (ROOT / "docs/architecture/runtime-logging-contract.yaml").read_text(encoding="utf-8")
    )
    assert set(contract["schema"]["required_fields"]) == REQUIRED_FIELDS
    assert contract["schema"]["unknown_attributes"] == "drop"
    assert contract["schema"]["exception_policy"] == "class_name_only"
    assert contract["redaction"]["business_scope"] == ("controlled_id_or_irreversible_sha256_only")
    assert contract["acceptance"]["hostile_secret_and_pii_scan_matches"] == 0
    assert contract["boundaries"]["audit_events"] == ("p19_append_only_audit_not_runtime_log")
    assert contract["boundaries"]["central_log_storage_and_retention"] == "OBS5-02"


def test_export_verifier_requires_fixed_schema_and_rejects_sensitive_or_unknown_values() -> None:
    inspect_record = _log_verifier().inspect_record
    record = {
        "schema_version": "hc-runtime-log/v1",
        **dict.fromkeys(REQUIRED_FIELDS),
        "timestamp": "2026-08-29T00:00:00.000Z",
        "severity": "INFO",
        "service": "hc-data-platform-worker",
        "instance_id": "instance-1",
        "node_name": "node-1",
        "role": "worker",
        "release_id": "platform-v1.0.0",
        "request_id": "request-1",
        "trace_id": "a" * 32,
        "operation_id": "operation-1",
        "workflow_id": "workflow-1",
        "event_code": "WORKFLOW.STARTED",
    }
    assert inspect_record(record, require_context=True) == []

    hostile = {
        **record,
        "request_id": "postgresql://user:password@db/private",
        "password": "OBS5-01-FORBIDDEN",
    }
    failures = inspect_record(hostile, require_context=True)
    assert any("unknown fields" in failure for failure in failures)
    assert any("forbidden key" in failure for failure in failures)
    assert any("sensitive value" in failure for failure in failures)
