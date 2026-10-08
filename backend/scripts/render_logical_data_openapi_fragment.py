"""Render the logical data fragment from the actual composed runtime."""

from pathlib import Path

from hc_data_platform.core.openapi import render, runtime_document


def main():
    runtime = runtime_document()
    document = {
        "openapi": "3.1.0",
        "info": {"title": "HC logical frozen data contract", "version": "2026-10-08"},
        "paths": {
            path: item
            for path, item in runtime["paths"].items()
            if any(
                isinstance(operation, dict) and "logical_data" in operation.get("tags", [])
                for operation in item.values()
            )
        },
        "components": {
            "schemas": {
                name: runtime["components"]["schemas"][name]
                for name in (
                    "SnapshotSelection",
                    "LogicalStream",
                    "LogicalSnapshot",
                    "LogicalSnapshotView",
                    "AnnotationLogicalSnapshot",
                    "FrozenRobotScene",
                    "LogicalRobotScene",
                    "LogicalRobotAssetGrant",
                    "LogicalSample",
                    "LogicalSampleBatch",
                    "LogicalMediaGrant",
                )
            },
        },
    }
    Path("openapi/logical_data.yaml").write_text(render(document), encoding="utf-8")


if __name__ == "__main__":
    main()
