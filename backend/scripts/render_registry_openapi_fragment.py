"""Render the P14 formal fragment from the production-composed runtime schema.

The fragment is intentionally emitted from the router's own runtime contract: it
prevents a hand-maintained registry fragment from drifting in parameters, problem
responses, or Pydantic schema constraints.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from hc_data_platform.core.openapi import render, runtime_document

_PATH_PREFIX = "/api/v1/organizations/{organization_id}/robot-model"
_SHARED_SCHEMAS = frozenset(
    {"ProblemDetails", "HTTPValidationError", "ValidationError", "PageInfo"}
)


def _schema_references(value: object) -> set[str]:
    references: set[str] = set()
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
            references.add(reference.removeprefix("#/components/schemas/"))
        for child in value.values():
            references.update(_schema_references(child))
    elif isinstance(value, list):
        for child in value:
            references.update(_schema_references(child))
    return references


def _schema_closure(runtime: dict[str, Any], paths: dict[str, Any]) -> dict[str, Any]:
    schemas = runtime["components"]["schemas"]
    pending = list(_schema_references(paths))
    selected: set[str] = set()
    while pending:
        name = pending.pop()
        if name in selected or name in _SHARED_SCHEMAS or name not in schemas:
            continue
        selected.add(name)
        pending.extend(_schema_references(schemas[name]) - selected)
    return {name: schemas[name] for name in sorted(selected)}


def document() -> dict[str, Any]:
    runtime = runtime_document()
    paths = {path: item for path, item in runtime["paths"].items() if path.startswith(_PATH_PREFIX)}
    return {
        "openapi": "3.1.0",
        "info": {"title": "HC registry contract", "version": "2026-08-19"},
        "paths": paths,
        "components": {"schemas": _schema_closure(runtime, paths)},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("openapi/registry.yaml"))
    args = parser.parse_args()
    args.output.write_text(render(document()), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
