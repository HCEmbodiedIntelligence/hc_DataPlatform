"""Render the annotation fragment directly from its FastAPI router."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI

from hc_data_platform.annotation.router import router
from hc_data_platform.core.openapi import render


def document(*, output: Path) -> dict[str, Any]:
    app = FastAPI(title="Annotation API Fragment", version="2.0.0")
    app.include_router(router)
    generated = app.openapi()
    generated["security"] = [{"bearerAuth": []}]
    generated.setdefault("components", {}).setdefault("securitySchemes", {})["bearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    # Shared schemas remain references resolved by the aggregate contract. Their
    # canonical ownership stays in the pre-existing fragment instead of being
    # duplicated merely because FastAPI expanded the annotation router.
    externally_owned: set[str] = set()
    for fragment_path in output.parent.glob("*.yaml"):
        if fragment_path.resolve() == output.resolve():
            continue
        fragment = yaml.safe_load(fragment_path.read_text(encoding="utf-8")) or {}
        externally_owned.update((fragment.get("components", {}).get("schemas", {}) or {}).keys())
    schemas = generated.get("components", {}).get("schemas", {})
    for name in externally_owned:
        schemas.pop(name, None)
    return generated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("openapi/annotation.yaml"))
    args = parser.parse_args()
    args.output.write_text(render(document(output=args.output)), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
