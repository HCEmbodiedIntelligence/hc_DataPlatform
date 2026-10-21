"""Render processing configuration and read-only upload evidence contracts."""

from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory

from hc_data_platform.core.openapi import aggregate_fragments, render, runtime_document


def main():
    runtime = runtime_document()
    target = Path("openapi/robot_processing.yaml")
    # Exclude this generated fragment before resolving shared schema ownership.
    # Other features may have added previously missing routes since the last run.
    with TemporaryDirectory() as temp:
        for fragment in target.parent.glob("*.yaml"):
            if fragment != target:
                copyfile(fragment, Path(temp) / fragment.name)
        existing = aggregate_fragments(Path(temp))
    paths = {
        p: v
        for p, v in runtime["paths"].items()
        if "processing-config" in p
        or "robot-processing-templates" in p
        or "data-requirements" in p
        or p.endswith(("/mcap-versions", "/mcap-channels"))
    }
    # Reference closure; common fragments own common definitions.
    old_owned = existing.get("components", {}).get("schemas", {})
    pending = [paths]
    schemas = {}
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            ref = value.get("$ref", "")
            if ref.startswith("#/components/schemas/"):
                name = ref.split("/")[-1]
                if name not in schemas and name not in old_owned:
                    schemas[name] = runtime["components"]["schemas"][name]
                    pending.append(schemas[name])
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    target.write_text(
        render(
            {
                "openapi": "3.1.0",
                "info": {"title": "Robot processing configurations", "version": "2026-10-20"},
                "paths": paths,
                "components": {"schemas": schemas},
            }
        )
    )


if __name__ == "__main__":
    main()
