"""Render the organization robot-asset fragment from the runtime schema."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from hc_data_platform.core.openapi import render, runtime_document

_PATH_PREFIX = "/api/v1/organizations/{organization_id}/robots"
_SCHEMAS = (
    "Connectivity",
    "EffectiveModelBinding",
    "BindOrganizationRobotModelRequest",
    "CreateRobotRequest",
    "OrganizationRobotBootstrapEnvelope",
    "OrganizationRobotModelBinding",
    "OrganizationRobotModelBindingPage",
    "OrganizationRobotPage",
    "OrganizationRobotScope",
    "RobotBootstrap",
    "RobotRecord",
)


def document() -> dict[str, Any]:
    runtime = runtime_document()
    return {
        "openapi": "3.1.0",
        "info": {"title": "HC robotics contract", "version": "2026-08-19"},
        "paths": {
            path: item for path, item in runtime["paths"].items() if path.startswith(_PATH_PREFIX)
        },
        "components": {
            "schemas": {name: runtime["components"]["schemas"][name] for name in _SCHEMAS}
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("openapi/robotics.yaml"))
    args = parser.parse_args()
    args.output.write_text(render(document()), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
