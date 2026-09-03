"""Render the P16 fragment from the production-composed runtime schema."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from hc_data_platform.core.openapi import render, runtime_document

_PATH_PREFIX = "/api/v1/projects/{project_id}/regions/{region_code}"
_SCHEMAS = (
    "CalibrationBlockedReason",
    "CalibrationCameraIntrinsics",
    "CalibrationDocument",
    "CalibrationFrameTransform",
    "CalibrationPublishPreflight",
    "CalibrationPublishPreflightEnvelope",
    "CalibrationPublishPreflightRequest",
    "CalibrationPublishRequest",
    "CalibrationScope",
    "CalibrationSetEnvelope",
    "CalibrationSetPage",
    "CalibrationSetRecord",
    "CalibrationValidation",
    "CalibrationValidationFinding",
    "CalibrationValidationReport",
    "CalibrationValidationReportEnvelope",
    "CalibrationVersionDocument",
    "CalibrationVersionDocumentEnvelope",
    "CalibrationVersionPage",
    "CalibrationVersionSummary",
    "CreateCalibrationSetRequest",
    "RecalibrateCalibrationSetRequest",
)


def document() -> dict[str, Any]:
    runtime = runtime_document()
    return {
        "openapi": "3.1.0",
        "info": {"title": "HC calibrations contract", "version": "2026-08-19"},
        "paths": {
            path: item
            for path, item in runtime["paths"].items()
            if path.startswith(f"{_PATH_PREFIX}/calibration-sets")
            or path.startswith(f"{_PATH_PREFIX}/calibration-validation-reports")
        },
        "components": {
            "schemas": {name: runtime["components"]["schemas"][name] for name in _SCHEMAS}
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("openapi/calibrations.yaml"))
    args = parser.parse_args()
    args.output.write_text(render(document()), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
