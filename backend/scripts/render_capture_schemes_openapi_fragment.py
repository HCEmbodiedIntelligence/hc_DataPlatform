"""Render shared capture operations and their owned schemas from the composed app."""

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
    target = Path("openapi/capture_schemes.yaml")
    with TemporaryDirectory() as directory:
        for fragment in target.parent.glob("*.yaml"):
            if fragment != target:
                copyfile(fragment, Path(directory) / fragment.name)
        existing = aggregate_fragments(Path(directory))
    paths = {
        path: item
        for path, item in runtime["paths"].items()
        if any(
            "capture-schemes" in operation.get("tags", [])
            for operation in item.values()
            if isinstance(operation, dict)
        )
    }
    owned_elsewhere = existing["components"]["schemas"]
    schemas = {}
    # These pure references and snapshot responses are consumed by the robot
    # protocol but owned here; task models also reuse this one definition.
    pending = [paths] + [
        {"$ref": f"#/components/schemas/{name}"}
        for name in ("CaptureReference", "PrepareRecording", "CaptureRecordingSnapshot")
    ]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            reference = value.get("$ref", "")
            if reference.startswith("#/components/schemas/"):
                name = reference.rsplit("/", 1)[-1]
                if name not in schemas and name not in owned_elsewhere:
                    schemas[name] = runtime["components"]["schemas"][name]
                    pending.append(schemas[name])
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    document = render(
        {
            "openapi": "3.1.0",
            "info": {"title": "Shared capture schemes", "version": "2026-10-21"},
            "paths": paths,
            "components": {"schemas": schemas},
        }
    )
    if args.check:
        if not target.exists() or target.read_text() != document:
            raise SystemExit("Capture OpenAPI fragment is stale; regenerate it.")
    else:
        target.write_text(document, encoding="utf-8")


if __name__ == "__main__":
    main()
