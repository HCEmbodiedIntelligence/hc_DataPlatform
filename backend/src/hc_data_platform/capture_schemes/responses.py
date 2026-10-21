"""Published HTTP responses share the actual capture definitions and references.

Hashed timestamp strings stay verbatim. Endpoints exclude unset optional fields
so serializing an older immutable document cannot add fields to its content.
Parser-specific diagnostic evidence remains extensible and is not discarded.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    Adapter,
    CaptureDefinition,
    CaptureDraftDefinition,
    DeviceMapping,
    OutputContract,
    Sample,
)
from .references import Identifier, Participant, Sha256, SourceFormat, Strict, VersionReference


class CaptureDifference(BaseModel):
    model_config = ConfigDict(extra="allow")

    path: str
    expected: Any
    actual: Any
    source: str | None = None


class CaptureEvidence(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: Literal["PASSED", "FAILED"]
    source_format: SourceFormat
    validation_id: Sha256
    validated_at: str
    actor: str
    parser_version: str
    sample_sha256: Sha256
    sample: Sample | None = None
    sample_robot_id: str | None = None
    draft_sha256: Sha256 | None = None
    mapping_sha256: Sha256 | None = None
    scheme_sha256: Sha256 | None = None
    revision: int | None = Field(default=None, ge=1)
    differences: tuple[CaptureDifference, ...] = ()
    presence: dict[str, bool] = Field(default_factory=dict)
    error_code: str | None = None
    error_detail: str | None = None
    frame_count: int | None = Field(default=None, ge=0)
    frequency_hz: float | None = Field(default=None, gt=0)
    input_structure_sha256: Sha256 | None = None


class CaptureSchemeVersion(CaptureDefinition):
    scheme_id: Identifier
    version: int = Field(ge=1)
    draft_revision: int = Field(ge=1)
    content_sha256: Sha256
    output_contract_sha256: Sha256
    validations: dict[SourceFormat, CaptureEvidence]
    published_at: str
    published_by: str
    source: Literal["SHARED_CAPTURE_SCHEME"]


class CaptureSchemeSummary(Strict):
    scheme_id: Identifier
    revision: int = Field(ge=1)
    draft: CaptureDraftDefinition
    latest_version: int | None = Field(ge=1)
    archived: bool
    instance_count: int = Field(ge=0)
    task_count: int = Field(ge=0)


class CaptureSchemeList(Strict):
    items: tuple[CaptureSchemeSummary, ...]


class CaptureSchemeDetail(Strict):
    scheme_id: Identifier
    revision: int = Field(ge=1)
    draft: CaptureDraftDefinition
    latest_version: int | None = Field(ge=1)
    archived: bool
    validations: tuple[CaptureEvidence, ...]
    versions: tuple[CaptureSchemeVersion, ...]


class CaptureMappingVersion(Strict):
    scheme: VersionReference
    mapping: DeviceMapping
    mapping_version: int = Field(ge=1)
    content_sha256: Sha256
    created_by: str
    created_at: str


class CaptureSchemeLink(Strict):
    scheme_id: Identifier
    revision: int = Field(ge=1)
    scheme_version: int = Field(ge=1)
    mapping_version: int = Field(ge=1)
    is_default: bool
    active: bool
    mapping: CaptureMappingVersion
    scheme: CaptureSchemeVersion
    archived: bool
    status: Literal["READY", "MAPPING_UNVERIFIED"]
    validated_formats: tuple[SourceFormat, ...]
    validations: tuple[CaptureEvidence, ...]


class CaptureSchemeLinks(Strict):
    items: tuple[CaptureSchemeLink, ...]


class CaptureInstanceReference(Strict):
    robot_id: Identifier
    version: int = Field(ge=1)
    mapping_version: int = Field(ge=1)
    active: bool
    is_default: bool


class CaptureTaskReference(Strict):
    project_id: Identifier
    collection_task_id: Identifier
    revision: int = Field(ge=1)
    version: int = Field(ge=1)
    locked_at: datetime | None


class CaptureSchemeReferences(Strict):
    instances: tuple[CaptureInstanceReference, ...]
    tasks: tuple[CaptureTaskReference, ...]


class CaptureSelection(Strict):
    scheme: VersionReference
    participants: tuple[Participant, ...]


class CaptureSelectedParticipant(Participant):
    mapping_sha256: Sha256


class CaptureParticipantChange(Strict):
    kind: Literal["ADD_PARTICIPANTS"]
    base_revision: int = Field(ge=1)
    participants: tuple[Participant, ...]


class CaptureTaskPlan(Strict):
    schema_version: Literal["capture-task-plan/v1"]
    organization_id: Identifier
    project_id: Identifier
    region_code: Identifier
    collection_task_id: Identifier
    dataset_id: Identifier
    revision: int = Field(ge=1)
    scheme: VersionReference
    selection: CaptureSelection
    participants: tuple[CaptureSelectedParticipant, ...]
    output: OutputContract
    output_contract_sha256: Sha256
    created_by: str
    created_at: str
    change: CaptureParticipantChange | None = None
    plan_sha256: Sha256
    locked_at: datetime | None


class CaptureTaskPlanEnvelope(Strict):
    plan: CaptureTaskPlan | None


class CaptureAssociationRemoved(Strict):
    removed: Literal[True]


class CaptureRecordingSnapshot(Strict):
    schema_version: Literal["capture-recording-snapshot/v1"]
    organization_id: Identifier
    project_id: Identifier
    region_code: Identifier
    collection_task_id: Identifier
    dataset_id: Identifier
    robot_id: Identifier
    recording_id: Identifier
    plan_revision: int = Field(ge=1)
    plan_sha256: Sha256
    scheme: VersionReference
    mapping_version: int = Field(ge=1)
    mapping_sha256: Sha256
    source_format: SourceFormat
    effective_adapter: Adapter
    output: OutputContract
    output_contract_sha256: Sha256
    source: Literal["TASK_PLAN"]
    prepared_at: str
    snapshot_sha256: Sha256
