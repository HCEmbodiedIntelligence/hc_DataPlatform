"""Versioned metadata eligibility shared by browser detection and native processing.

This checks declared metadata, not file bytes. Commit verifies the uploaded metadata
and workers validate the actual tables, codecs, depth evidence and source hashes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .profiles import profile_for_info
from .source_profile import validate_processing_info, validate_source_info

NATIVE_PROCESSING_REVISION = "lerobot-native-episode/1"


class LeRobotCapabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    info: dict[str, Any]

    @field_validator("info")
    @classmethod
    def finite_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, allow_nan=False).encode()) > 1024**2:
            raise ValueError("LeRobot metadata exceeds 1 MiB")
        return value


class LeRobotCapabilityReason(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    stage: Literal["STORAGE", "PROCESSING"]
    code: str
    detail: str


class LeRobotCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["lerobot-capabilities/v1"] = "lerobot-capabilities/v1"
    capability_revision: str
    info_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    validation_scope: Literal["DECLARED_METADATA"] = "DECLARED_METADATA"
    storage_supported: bool
    processing_supported: bool
    profile_id: str | None = None
    output_storage_format: Literal["lerobot_v3"] | None = None
    reasons: tuple[LeRobotCapabilityReason, ...] = ()


def validate_processing_selection(info: object, revision: str | None) -> dict[str, Any]:
    # Historical manifests without a revision retain their established profile.
    if revision not in (None, NATIVE_PROCESSING_REVISION):
        raise ValueError("LEROBOT_CAPABILITY_REVISION_UNSUPPORTED: unknown processing revision")
    return validate_processing_info(info, preserve_features=True)


def detect_capabilities(info: dict[str, Any]) -> LeRobotCapabilities:
    digest = hashlib.sha256(
        json.dumps(info, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    values: dict[str, Any] = dict(
        capability_revision=NATIVE_PROCESSING_REVISION,
        info_sha256=digest,
        storage_supported=False,
        processing_supported=False,
    )
    stage: Literal["STORAGE", "PROCESSING"] = "STORAGE"
    try:
        validate_source_info(info)
        values["storage_supported"] = True
        stage = "PROCESSING"
        validate_processing_selection(info, NATIVE_PROCESSING_REVISION)
        profile = profile_for_info(info, preserve_features=True)
        values.update(
            processing_supported=True,
            profile_id=profile.profile_id,
            output_storage_format="lerobot_v3",
        )
    except (ValueError, TypeError, AttributeError) as exc:
        message = str(exc)
        code = message.split(":", 1)[0]
        if not code.startswith(("LEROBOT_", "OPENARM_")):
            code = (
                "LEROBOT_METADATA_UNSUPPORTED"
                if stage == "STORAGE"
                else "LEROBOT_PROFILE_UNSUPPORTED"
            )
        values["reasons"] = (LeRobotCapabilityReason(stage=stage, code=code, detail=message),)
    return LeRobotCapabilities(**values)
