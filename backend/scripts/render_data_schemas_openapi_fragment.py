from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from hc_data_platform.core.openapi import render, runtime_document

_PATHS = (
    "/api/v1/organizations/{organization_id}/stream-schemas",
    "/api/v1/organizations/{organization_id}/projects/{project_id}/regions/{region_code}"
    "/stream-schemas",
    "/api/v1/projects/{project_id}/regions/{region_code}/route-resolutions/p15-to-p17",
)
_SCHEMAS = (
    "CanonicalRouteQuery",
    "CreateStreamSchemaRequest",
    "DataSchemaDatasetReference",
    "DataSchemaDatasetReferenceEnvelope",
    "DataSchemaDatasetReferencePage",
    "DataSchemaDatasetReferenceRequest",
    "DataSchemaDatasetReferenceScope",
    "DataSchemaEnvelope",
    "DataSchemaPage",
    "DataSchemaPublishPreflight",
    "DataSchemaPublishPreflightEnvelope",
    "DataSchemaPublishPreflightRequest",
    "DataSchemaPublishRequest",
    "DataSchemaRouteEnvelope",
    "DataSchemaRouteResolution",
    "DataSchemaRouteScope",
    "DataSchemaScope",
    "DataSchemaValidationFinding",
    "DataSchemaValidationReport",
    "DataSchemaValidationReportEnvelope",
    "DataSchemaVersionRecord",
    "SchemaBlockedReason",
    "SchemaHash",
    "StreamSchemaDefinition",
    "StreamSchemaDefinitionField",
    "UpdateStreamSchemaDraftRequest",
)


def document() -> dict[str, Any]:
    runtime = runtime_document()
    return {
        "openapi": "3.1.0",
        "info": {"title": "HC data schemas contract", "version": "2026-08-19"},
        "paths": {
            path: item
            for path, item in runtime["paths"].items()
            if any(path.startswith(prefix) for prefix in _PATHS)
        },
        "components": {
            "schemas": {name: runtime["components"]["schemas"][name] for name in _SCHEMAS}
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("openapi/data_schemas.yaml"))
    args = parser.parse_args()
    args.output.write_text(render(document()), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
