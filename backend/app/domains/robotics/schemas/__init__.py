"""Strict mutation schemas for the P14-P17 robotics contract."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Id = Annotated[str, StringConstraints(min_length=1, max_length=160)]
Hash = Annotated[str, StringConstraints(pattern=r"^sha256:[a-f0-9]{64}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyCommand(StrictModel):
    pass


class TokenCommand(StrictModel):
    preflight_token: str = Field(min_length=16, max_length=512)


class CreateRobotModelRequest(StrictModel):
    manufacturer: str = Field(min_length=1, max_length=120)
    model_code: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=200)


class CreateVersionRequest(StrictModel):
    base_version_id: Id | None = None
    version_note: str | None = Field(default=None, max_length=500)


class AssetObjectInput(StrictModel):
    relative_path: str = Field(min_length=1, max_length=1024)
    role: Literal["ENTRY_URDF", "URDF", "MESH", "TEXTURE", "CONFIG", "OTHER"]
    media_type: str = Field(min_length=1, max_length=255)
    bytes: Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]*)$")]
    sha256: Hash

    @field_validator("relative_path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        if (
            normalized.startswith("/")
            or normalized.startswith("//")
            or "://" in normalized
            or any(part in {"", ".", ".."} for part in normalized.split("/"))
        ):
            raise ValueError("asset path must be normalized and manifest-relative")
        return normalized


class CreateAssetUploadSessionRequest(StrictModel):
    objects: list[AssetObjectInput] = Field(min_length=1)


class UploadAuthorizationRequest(StrictModel):
    object_ids: list[Id] = Field(min_length=1)

    @field_validator("object_ids")
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("object_ids must be unique")
        return value


class UploadObjectFact(StrictModel):
    relative_path: str
    bytes: Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]*)$")]
    sha256: Hash
    provider_object_id: str = Field(min_length=1, max_length=512)


class CompleteAssetUploadRequest(StrictModel):
    manifest_hash: Hash
    objects: list[UploadObjectFact] = Field(min_length=1)


class CreateValidationRequest(StrictModel):
    validation_input_hash: Hash
    force_new_run: bool = False


class CreateRobotModelSampleValidationRequest(StrictModel):
    candidate_id: Id
    start_ns: Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]*)$")]
    end_ns: Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]*$")]
    max_points: int = Field(ge=100, le=5000)
    validation_input_hash: Hash

    @model_validator(mode="after")
    def valid_window(self) -> CreateRobotModelSampleValidationRequest:
        if int(self.end_ns) <= int(self.start_ns):
            raise ValueError("end_ns must be greater than start_ns")
        return self


class PublishPreflightRequest(StrictModel):
    expected_hash: Hash
    expected_etag: str = Field(min_length=1, max_length=256)
    validation_report_id: Id
    compatibility_check_id: Id | None = None
    change_summary: str = Field(min_length=1, max_length=500)
    acknowledge_warning_codes: list[str] = Field(default_factory=list)


class ReasonPreflightRequest(StrictModel):
    reason: str = Field(min_length=1, max_length=500)


class BindingTarget(StrictModel):
    scope_type: Literal["ROBOT_MODEL_DEFAULT", "ROBOT_INSTANCE"]
    scope_id: Id
    valid_from: datetime
    valid_to: datetime | None = None

    @model_validator(mode="after")
    def valid_interval(self) -> BindingTarget:
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be after valid_from")
        return self


class RobotModelBindingPreflightRequest(StrictModel):
    robot_model_version_id: Id
    targets: list[BindingTarget] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=500)


class CreateRobotRequest(StrictModel):
    display_name: str = Field(min_length=1, max_length=200)
    serial_no: str = Field(min_length=1, max_length=200)
    robot_model_id: Id


class ComponentPreflightRequest(StrictModel):
    component_type: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=200)
    serial_no: str | None = Field(default=None, max_length=200)
    parent_component_id: Id | None = None
    topology_revision: Id
    valid_from: datetime


class MountChangePreflightRequest(StrictModel):
    new_robot_id: Id
    new_parent_component_id: Id | None
    valid_from: datetime
    topology_revision: Id
    reason: str = Field(min_length=1, max_length=500)


class CreateCalibrationSetRequest(StrictModel):
    robot_id: Id
    component_id: Id
    display_name: str = Field(min_length=1, max_length=200)
    valid_from: datetime
    valid_to: datetime | None = None

    @model_validator(mode="after")
    def valid_interval(self) -> CreateCalibrationSetRequest:
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be after valid_from")
        return self


class CalibrationSourceArtifact(StrictModel):
    kind: Literal["IMPORT_SOURCE"]
    display_name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=255)
    bytes: Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]*)$")]
    sha256: Hash
    classification: Literal["INTERNAL", "SENSITIVE", "RESTRICTED"]
    provider_object_id: str = Field(min_length=1, max_length=512)


class CalibrationImportRequest(CreateCalibrationSetRequest):
    source_artifact: CalibrationSourceArtifact


class CalibrationRecordWriteRequest(StrictModel):
    record: dict[str, Any] = Field(min_length=1)


class CalibrationRecordPatchRequest(StrictModel):
    patch: dict[str, Any] = Field(min_length=1)


class CalibrationSetValidationRequest(StrictModel):
    expected_revision: Id


class CloneCalibrationSetRequest(StrictModel):
    valid_from: datetime
    valid_to: datetime | None = None
    robot_model_version_id: Id | None = None
    component_snapshot_id: Id | None = None
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def clone_interval(self) -> CloneCalibrationSetRequest:
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be after valid_from")
        return self


class CreateCalibrationValidationRequest(StrictModel):
    expected_etag: str = Field(min_length=1, max_length=256)
    content_hash: Hash
    validation_context_hash: Hash


class SchemaDefinition(StrictModel):
    fields: list[dict[str, Any]]
    compatibility_policy: Literal["STRICT", "BACKWARD", "FORWARD", "FULL", "MANUAL"]
    encoding: dict[str, Any] | None = None
    coordinate: dict[str, Any] | None = None
    timestamp: dict[str, Any] | None = None


class CreateDataSchemaRequest(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    logical_type: str = Field(min_length=1, max_length=80)
    initial_version: SchemaDefinition


class CreateStreamSchemaRequest(StrictModel):
    family_id: Id
    schema_name: str = Field(min_length=1, max_length=200)
    logical_type: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    compatibility_mode: Literal["STRICT", "BACKWARD", "FORWARD", "FULL", "MANUAL"]
    schema_definition: SchemaDefinition


class UpdateStreamSchemaRequest(StrictModel):
    description: str | None = Field(default=None, max_length=2000)
    compatibility_mode: Literal["STRICT", "BACKWARD", "FORWARD", "FULL", "MANUAL"] | None = None
    schema_definition: SchemaDefinition
    change_summary: str = Field(min_length=1, max_length=500)


class ValidateStreamSchemaRequest(StrictModel):
    expected_etag: str = Field(min_length=1, max_length=256)
    target_hash: Hash
    rule_set_version: str | None = Field(default=None, max_length=128)


class CreateSchemaVersionRequest(StrictModel):
    parent_version_id: Id
    change_summary: str = Field(min_length=1, max_length=500)


class UpdateSchemaVersionRequest(SchemaDefinition):
    change_summary: str = Field(min_length=1, max_length=500)


class SchemaValidationRequest(StrictModel):
    expected_etag: str = Field(min_length=1, max_length=256)
    expected_hash: Hash
    rule_set_version: str = Field(min_length=1, max_length=160)


class CreateCompatibilityRequest(StrictModel):
    baseline_version_id: Id
    target_version_id: Id
    baseline_hash: Hash
    target_hash: Hash
    mode: Literal["STRICT", "BACKWARD", "FORWARD", "FULL", "MANUAL"]
    rule_set_version: str = Field(min_length=1, max_length=160)
    mapping_overrides: list[dict[str, Any]] = Field(default_factory=list)


class SchemaImportValidationRequest(StrictModel):
    media_type: Literal["application/json", "application/yaml"]
    content: str = Field(min_length=2, max_length=1_048_576)


class SchemaImportCommitRequest(StrictModel):
    import_validation_id: Id
    target_schema_id: Id | None
    change_summary: str = Field(min_length=1, max_length=500)
