"""Dispatch explicit provenance contracts without treating a sidecar as authority."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from hc_data_platform.capture_schemes.responses import CaptureRecordingSnapshot
from hc_data_platform.security.audit import canonical_hash

SHARED_CONTEXT = "hc-capture-context/v1"


def read_context(
    root: Path, *, expected_snapshot: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    path = root / "capture-context.json"
    if not path.exists():
        return None
    context = json.loads(path.read_text())
    if not isinstance(context, dict):
        raise ValueError("CAPTURE_CONTEXT_INVALID: context must be an object")
    if context.get("contract_version") == "openarm-capture/v1":
        return context  # The existing OpenArm reader validates its completion and frame provenance.
    if context.get("schema_version") != SHARED_CONTEXT:
        raise ValueError("CAPTURE_CONTEXT_UNSUPPORTED: unknown provenance contract")
    try:
        if set(context) != {
            "schema_version",
            "recording_id",
            "capture_snapshot",
            "feature_presence",
        }:
            raise ValueError("unexpected context fields")
        snapshot = context["capture_snapshot"]
        CaptureRecordingSnapshot.model_validate(snapshot)
        content = {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
        if (
            snapshot["source_format"] != "LEROBOT_V3"
            or snapshot["recording_id"] != context["recording_id"]
            or canonical_hash(content) != snapshot["snapshot_sha256"]
            or canonical_hash(snapshot["output"]) != snapshot["output_contract_sha256"]
        ):
            raise ValueError("context identity or digest differs")
        info = json.loads((root / "meta/info.json").read_text())
        sources = snapshot["effective_adapter"]["feature_sources"]
        presence = {
            key: sources.get(key, key) in info["features"] for key in snapshot["output"]["features"]
        }
        if (
            not isinstance(context["feature_presence"], dict)
            or any(type(value) is not bool for value in context["feature_presence"].values())
            or context["feature_presence"] != presence
        ):
            raise ValueError("presence differs from actual source metadata")
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise ValueError("CAPTURE_CONTEXT_INVALID: " + str(exc)) from exc
    if expected_snapshot is not None:
        expected = {
            key: value for key, value in expected_snapshot.items() if key != "reference_origin"
        }
        if snapshot != expected:
            raise ValueError(
                "CAPTURE_VERSION_MISMATCH: source context differs from frozen Raw snapshot"
            )
    return context


def verify_source_context(
    root: Path, descriptors: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    context = read_context(root)
    if context is not None and context.get("contract_version") == "openarm-capture/v1":
        from .capture import verify_completion

        verify_completion(root, descriptors)
    return context
