"""Reject sensitive material and missing correlation fields in exported JSON logs."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = (
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
)
ALLOWED_FIELDS = frozenset(
    {
        "schema_version",
        *REQUIRED_FIELDS,
        "route",
        "http_method",
        "status_code",
    }
)
CORRELATED_FIELDS = ("request_id", "operation_id", "workflow_id")
FORBIDDEN_KEY_PARTS = (
    "authorization",
    "access_token",
    "refresh_token",
    "jwt",
    "cookie",
    "credential",
    "password",
    "request_body",
    "response_body",
    "secret",
    "signed_url",
    "signature",
    "object_store_secret_key",
)
FORBIDDEN_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"[?&](?:X-Amz-Signature|Signature|sig)=[^&\s]+", re.IGNORECASE),
    re.compile(r"[?&](?:X-Amz-Credential|OSSAccessKeyId)=[^&\s]+", re.IGNORECASE),
    re.compile(r"\b(?:postgres(?:ql)?|s3|minio)://[^\s]+", re.IGNORECASE),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
)


def _walk(value: Any, path: str = "$") -> Iterable[tuple[str, str, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            yield child, str(key), item
            yield from _walk(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]")


def inspect_record(record: dict[str, Any], *, require_context: bool) -> list[str]:
    errors: list[str] = []
    if record.get("schema_version") != "hc-runtime-log/v1":
        errors.append("invalid schema_version")
    missing_fields = sorted(set(REQUIRED_FIELDS) - record.keys())
    if missing_fields:
        errors.append(f"missing required fields: {missing_fields}")
    unknown_fields = sorted(record.keys() - ALLOWED_FIELDS)
    if unknown_fields:
        errors.append(f"unknown fields: {unknown_fields}")
    if require_context:
        for field in CORRELATED_FIELDS:
            if not record.get(field):
                errors.append(f"missing non-empty {field}")
    for path, key, value in _walk(record):
        normalized = key.lower().replace("-", "_")
        if any(part in normalized for part in FORBIDDEN_KEY_PARTS):
            errors.append(f"forbidden key at {path}")
        rendered = value if isinstance(value, str) else ""
        for pattern in FORBIDDEN_PATTERNS:
            if pattern.search(rendered):
                errors.append(f"sensitive value at {path} matched {pattern.pattern}")
    return errors


def verify(paths: Iterable[Path], *, require_context: bool) -> dict[str, Any]:
    inspected = 0
    failures: list[dict[str, Any]] = []
    for path in paths:
        for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not raw_line.strip():
                continue
            inspected += 1
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as error:
                failures.append(
                    {"path": str(path), "line": line_number, "errors": [f"invalid JSON: {error}"]}
                )
                continue
            if not isinstance(record, dict):
                failures.append(
                    {"path": str(path), "line": line_number, "errors": ["record is not an object"]}
                )
                continue
            errors = inspect_record(record, require_context=require_context)
            if errors:
                failures.append({"path": str(path), "line": line_number, "errors": errors})
    if inspected == 0:
        failures.append({"path": "", "line": 0, "errors": ["no log records were supplied"]})
    return {"records_inspected": inspected, "passed": not failures, "failures": failures}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--require-context", action="store_true")
    args = parser.parse_args()
    result = verify(args.paths, require_context=args.require_context)
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
