"""Versioned completion seal for unsliced OpenArm recordings (G0 unchanged)."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StrictBool, model_validator

from hc_data_platform.continuous_recordings.asset_models import (
    CreateRecordingUploadCommand,
    RecordingAssetManifestV1,
)
from hc_data_platform.ingest.models import Identifier, Sha256

from .models import StrictModel


class OpenArmRawRecordingUpload(StrictModel):
    schema_version: Literal["openarm-raw-recording-upload/v1"]
    recording_id: Identifier
    rollout_id: Identifier
    data_package_id: Identifier
    collection_task_id: Identifier
    collection_job_id: Identifier
    robot_id: Identifier
    device_id: Identifier
    capture_started_at: AwareDatetime
    capture_ended_at: AwareDatetime
    assets: tuple[RecordingAssetManifestV1, ...] = Field(min_length=3, max_length=256)

    @model_validator(mode="after")
    def raw_inventory(self):
        paths = [a.path for a in self.assets]
        if (
            len(paths) != len(set(paths))
            or not {"raw.mcap", "session.json", "manifest.json"} <= set(paths)
            or self.capture_ended_at <= self.capture_started_at
            or any(a.role.value != "AUXILIARY" for a in self.assets)
            or any(p.endswith((".sqlite3", ".db")) or p == "quality.json" for p in paths)
        ):
            raise ValueError("invalid original recording inventory")
        return self


class OpenArmRecordingComplete(StrictModel):
    schema_version: Literal["openarm-recording-complete/v1", "openarm-recording-complete/v2"]
    export_id: UUID
    session_id: str = Field(min_length=1, max_length=128)
    robot_id: Identifier
    local_robot_id: Identifier
    collection_task_id: Identifier
    synthetic: StrictBool
    content_sha256: Sha256
    recording_upload: CreateRecordingUploadCommand | OpenArmRawRecordingUpload

    @model_validator(mode="after")
    def version_matches(self):
        if (self.schema_version.endswith("/v2")) != isinstance(
            self.recording_upload, OpenArmRawRecordingUpload
        ):
            raise ValueError("completion and payload versions differ")
        return self
