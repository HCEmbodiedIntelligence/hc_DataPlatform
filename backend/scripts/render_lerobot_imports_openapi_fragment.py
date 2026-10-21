"""Render native upload contracts, preserving other fragments' route ownership."""

import argparse
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory

from hc_data_platform.core.openapi import aggregate_fragments, render, runtime_document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    runtime = runtime_document()
    target = Path("openapi/lerobot_imports.yaml")
    with TemporaryDirectory() as directory:
        for fragment in target.parent.glob("*.yaml"):
            if fragment != target:
                copyfile(fragment, Path(directory) / fragment.name)
        existing = aggregate_fragments(Path(directory))
    paths = {
        path: item
        for path, item in runtime["paths"].items()
        if path not in existing["paths"]
        and any(
            "lerobot-imports" in operation.get("tags", [])
            for operation in item.values()
            if isinstance(operation, dict)
        )
    }
    schemas = {}
    pending = [paths]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            reference = value.get("$ref", "")
            if reference.startswith("#/components/schemas/"):
                name = reference.rsplit("/", 1)[-1]
                if name not in schemas and name not in existing["components"]["schemas"]:
                    schemas[name] = runtime["components"]["schemas"][name]
                    pending.append(schemas[name])
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    document = render(
        {
            "openapi": "3.1.0",
            "info": {"title": "Native uploads", "version": "2026-10-21"},
            "paths": paths,
            "components": {"schemas": schemas},
        }
    )
    if args.check:
        if target.read_text() != document:
            raise SystemExit("Native upload OpenAPI fragment is stale; regenerate it.")
    else:
        target.write_text(document, encoding="utf-8")


if __name__ == "__main__":
    main()
