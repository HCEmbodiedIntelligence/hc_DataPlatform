"""Reject sensitive material and missing correlation fields in exported JSON logs."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = ("request_id", "project_id", "resource_id", "workflow_id")
FORBIDDEN_KEY_PARTS = (
    "authorization",
    "access_token",
    "refresh_token",
    "jwt",
    "signed_url",
    "signature",
    "object_store_secret_key",
)
FORBIDDEN_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"[?&](?:X-Amz-Signature|Signature|sig)=[^&\s]+", re.IGNORECASE),
    re.compile(r"[?&](?:X-Amz-Credential|OSSAccessKeyId)=[^&\s]+", re.IGNORECASE),
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
    if require_context:
        for field in REQUIRED_FIELDS:
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
