"""Render the formal P02 fragment from the production-composed runtime schema."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from hc_data_platform.core.openapi import render, runtime_document

_PATH_PREFIX = "/api/v1/projects/{project_id}/regions/{region_code}/data-sources"
_SCHEMAS = (
    "BasicCredentialInput",
    "ConnectionTestJobEnvelope",
    "CreateDataSourceCommand",
    "CredentialSummary",
    "DataSourceBlockedReason",
    "DataSourceComponentError",
    "DataSourceConnectionTestJob",
    "DataSourceDetail",
    "DataSourceEdgeAgentBinding",
    "DataSourceEdgeAgentConfiguration",
    "DataSourceEnvelope",
    "DataSourceFacet",
    "DataSourceFacets",
    "DataSourceJobError",
    "DataSourceJobProgress",
    "DataSourceMetrics",
    "DataSourceObservedVersions",
    "DataSourceOssImportBinding",
    "DataSourceOssImportConfiguration",
    "DataSourcePage",
    "DataSourceResourceReference",
    "DataSourceRobotBinding",
    "DataSourceRobotConfiguration",
    "DataSourceRobotFacet",
    "DataSourceSafeError",
    "DataSourceScope",
    "DataSourceSummary",
    "DataSourceUnknownBinding",
    "DataSourceUnknownConfiguration",
    "DeviceCertificateCredentialInput",
    "HeartbeatSummary",
    "LastUploadSummary",
    "RotateCredentialCommand",
    "SourceStateCommand",
    "TestConnectionCommand",
    "TokenCredentialInput",
    "UpdateDataSourceCommand",
    "UploadPolicySummary",
    "WritableEdgeAgentBinding",
    "WritableEdgeAgentConfiguration",
    "WritableOssImportBinding",
    "WritableOssImportConfiguration",
    "WritableRobotBinding",
    "WritableRobotConfiguration",
)


def document() -> dict[str, Any]:
    runtime = runtime_document()
    return {
        "openapi": "3.1.0",
        "info": {"title": "HC P02 data-source contract", "version": "2026-08-19"},
        "paths": {
            path: item
            for path, item in runtime["paths"].items()
            if path == _PATH_PREFIX or path.startswith(f"{_PATH_PREFIX}/")
        },
        "components": {
            "schemas": {name: runtime["components"]["schemas"][name] for name in _SCHEMAS}
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("openapi/data_sources.yaml"))
    args = parser.parse_args()
    args.output.write_text(render(document()), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
