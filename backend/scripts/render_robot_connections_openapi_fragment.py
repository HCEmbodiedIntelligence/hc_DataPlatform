"""Render the single-link and machine task contract from the composed app."""

from pathlib import Path

from hc_data_platform.core.openapi import render, runtime_document


def main():
    runtime = runtime_document()
    schemas = (
        "ConnectionTicket",
        "LocalRobot",
        "RedeemConnection",
        "RedeemedConnection",
        "RobotConnectionInfo",
        "RobotTaskPage",
        "SelectableTask",
        "TaskOwnership",
        "TaskProject",
        "TaskSelection",
    )
    document = {
        "openapi": "3.1.0",
        "info": {"title": "Robot connections and task selection", "version": "2026-10-19"},
        "paths": {
            path: item
            for path, item in runtime["paths"].items()
            if any(
                "robot-connections" in op.get("tags", [])
                for op in item.values()
                if isinstance(op, dict)
            )
        },
        "components": {
            "schemas": {name: runtime["components"]["schemas"][name] for name in schemas}
        },
    }
    Path("openapi/robot_connections.yaml").write_text(render(document), encoding="utf-8")


if __name__ == "__main__":
    main()
