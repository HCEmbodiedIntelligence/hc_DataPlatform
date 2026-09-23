"""Versioned completion seal for unsliced OpenArm recordings (G0 unchanged)."""

from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool

from hc_data_platform.continuous_recordings.asset_models import CreateRecordingUploadCommand
from hc_data_platform.ingest.models import Identifier, Sha256

from .models import StrictModel


class OpenArmRecordingComplete(StrictModel):
    schema_version: Literal["openarm-recording-complete/v1"]
    export_id: UUID
    session_id: str = Field(min_length=1, max_length=128)
    robot_id: Identifier
    local_robot_id: Identifier
    collection_task_id: Identifier
    synthetic: StrictBool
    content_sha256: Sha256
    recording_upload: CreateRecordingUploadCommand
