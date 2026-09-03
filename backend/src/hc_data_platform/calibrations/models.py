"""Wire models for authoritative P16 calibration reads."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from hc_data_platform.core.pagination import PageInfo


class CalibrationScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str = Field(min_length=1, max_length=128)
    region_code: str = Field(min_length=1, max_length=64)


class CalibrationBlockedReason(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=512)


class CalibrationValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str = Field(min_length=1, max_length=64)
    content_hash: str = Field(min_length=1, max_length=128)
    validation_context_hash: str = Field(min_length=1, max_length=128)
    report_id: str = Field(min_length=1, max_length=128)


class CalibrationSetRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    robot_instance_id: str = Field(min_length=1, max_length=128)
    component_id: str | None = Field(default=None, min_length=1, max_length=128)
    version: str = Field(pattern=r"^(0|[1-9]\d*)$")
    snapshot_status: str = Field(min_length=1, max_length=64)
    availability: str | None = Field(default=None, min_length=1, max_length=64)
    content_hash: str | None = Field(default=None, min_length=1, max_length=128)
    validation_context_hash: str | None = Field(default=None, min_length=1, max_length=128)
    validation: CalibrationValidation | None = None
    etag: str = Field(min_length=1, max_length=256)
    allowed_actions: tuple[str, ...] = ()
    blocked_reasons: tuple[CalibrationBlockedReason, ...] = ()


class CalibrationSetPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[CalibrationSetRecord, ...]
    page_info: PageInfo
    snapshot_at: datetime
    scope: CalibrationScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-19"


class CalibrationSetEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data: CalibrationSetRecord
    scope: CalibrationScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-19"


class CalibrationFrameTransform(BaseModel):
    """One directed rigid transform in the durable calibration document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parent_frame: str = Field(min_length=1, max_length=128)
    child_frame: str = Field(min_length=1, max_length=128)
    translation_m: tuple[FiniteFloat, FiniteFloat, FiniteFloat]
    quaternion_xyzw: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]
    covariance: tuple[FiniteFloat, ...] | None = Field(default=None, min_length=36, max_length=36)

    @model_validator(mode="after")
    def require_distinct_frames_and_real_rotation(self) -> CalibrationFrameTransform:
        if self.parent_frame == self.child_frame:
            raise ValueError("a transform cannot use the same parent and child frame")
        if sum(component * component for component in self.quaternion_xyzw) < 1e-12:
            raise ValueError("quaternion_xyzw must not have zero length")
        return self


class CalibrationCameraIntrinsics(BaseModel):
    """Camera facts used by the calibration viewer; no implicit zero matrix exists."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    frame_id: str = Field(min_length=1, max_length=128)
    width_px: int = Field(ge=1, le=32_768)
    height_px: int = Field(ge=1, le=32_768)
    fx_px: FiniteFloat = Field(gt=0)
    fy_px: FiniteFloat = Field(gt=0)
    cx_px: FiniteFloat = Field(ge=0)
    cy_px: FiniteFloat = Field(ge=0)
    distortion: tuple[FiniteFloat, ...] = Field(default=(), max_length=16)


class CalibrationDocument(BaseModel):
    """Canonical, hashable input for one immutable calibration version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    frame_transforms: tuple[CalibrationFrameTransform, ...] = Field(min_length=1, max_length=256)
    camera_intrinsics: tuple[CalibrationCameraIntrinsics, ...] = Field(default=(), max_length=128)

    @model_validator(mode="after")
    def reject_duplicate_document_facts(self) -> CalibrationDocument:
        edges = {(edge.parent_frame, edge.child_frame) for edge in self.frame_transforms}
        if len(edges) != len(self.frame_transforms):
            raise ValueError("a calibration document cannot duplicate a directed frame transform")
        frame_ids = [camera.frame_id for camera in self.camera_intrinsics]
        if len(frame_ids) != len(set(frame_ids)):
            raise ValueError("a calibration document cannot duplicate camera intrinsics")
        return self


class CreateCalibrationSetRequest(BaseModel):
    """Create a draft v1 from a manually entered or imported real document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    set_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )
    robot_instance_id: str = Field(min_length=1, max_length=128)
    component_id: str | None = Field(default=None, min_length=1, max_length=128)
    source: str = Field(pattern=r"^(MANUAL|IMPORT)$")
    document: CalibrationDocument


class RecalibrateCalibrationSetRequest(BaseModel):
    """Create a successor draft; previously stored version content is immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document: CalibrationDocument
    change_summary: str = Field(min_length=1, max_length=2_000)


class CalibrationVersionDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    set_id: str = Field(min_length=1, max_length=128)
    version: str = Field(pattern=r"^(0|[1-9]\d*)$")
    source: str = Field(min_length=1, max_length=64)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    document: CalibrationDocument
    created_by: str = Field(min_length=1, max_length=128)
    created_at: datetime


class CalibrationVersionDocumentEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data: CalibrationVersionDocument
    scope: CalibrationScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-21"


class CalibrationVersionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(pattern=r"^(0|[1-9]\d*)$")
    source: str = Field(min_length=1, max_length=64)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_by: str = Field(min_length=1, max_length=128)
    created_at: datetime


class CalibrationVersionPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[CalibrationVersionSummary, ...]
    page_info: PageInfo
    scope: CalibrationScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-21"


class CalibrationDatasetAssociationRequest(BaseModel):
    """Pin one immutable, ready dataset version to a ready calibration version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: str = Field(
        min_length=1, max_length=128, pattern=r"^dataset_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"
    )
    dataset_version_id: str = Field(
        min_length=1, max_length=128, pattern=r"^version_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"
    )


class CalibrationDatasetAssociation(BaseModel):
    """A durable, immutable calibration-to-dataset-version fact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    set_id: str = Field(min_length=1, max_length=128)
    calibration_version: str = Field(pattern=r"^(0|[1-9]\d*)$")
    dataset_id: str = Field(min_length=1, max_length=128)
    dataset_version_id: str = Field(min_length=1, max_length=128)
    associated_by: str = Field(min_length=1, max_length=128)
    associated_at: datetime


class CalibrationDatasetAssociationEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data: CalibrationDatasetAssociation
    scope: CalibrationScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-21"


class CalibrationDatasetAssociationPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[CalibrationDatasetAssociation, ...]
    page_info: PageInfo
    scope: CalibrationScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-21"


class CalibrationValidationFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=128)
    severity: str = Field(pattern=r"^(ERROR|WARNING)$")
    message: str = Field(min_length=1, max_length=512)
    path: str = Field(min_length=1, max_length=512)


class CalibrationValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    set_id: str = Field(min_length=1, max_length=128)
    version: str = Field(pattern=r"^(0|[1-9]\d*)$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    validation_context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: str = Field(pattern=r"^(PASSED|FAILED)$")
    findings: tuple[CalibrationValidationFinding, ...] = ()
    checked_by: str = Field(min_length=1, max_length=128)
    checked_at: datetime


class CalibrationValidationReportEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data: CalibrationValidationReport
    scope: CalibrationScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-21"


class CalibrationPublishPreflightRequest(BaseModel):
    """The browser's declared publish inputs, rechecked against stored facts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_hash: str = Field(min_length=1, max_length=128)
    expected_etag: str = Field(min_length=1, max_length=256)
    validation_report_id: str = Field(min_length=1, max_length=128)
    compatibility_check_id: str | None = Field(default=None, min_length=1, max_length=128)
    change_summary: str = Field(min_length=1, max_length=2_000)
    acknowledge_warning_codes: tuple[str, ...] = Field(default=(), max_length=64)
    validation_context_hash: str = Field(min_length=1, max_length=128)


class CalibrationPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    preflight_token: str = Field(min_length=32, max_length=2_048)


class CalibrationPublishPreflight(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    preflight_token: str | None = Field(default=None, min_length=32, max_length=2_048)
    expires_at: datetime | None = None
    resource_revision: str = Field(min_length=1, max_length=256)
    impacts: tuple[CalibrationBlockedReason, ...] = ()
    warnings: tuple[CalibrationBlockedReason, ...] = ()
    blockers: tuple[CalibrationBlockedReason, ...] = ()


class CalibrationPublishPreflightEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data: CalibrationPublishPreflight
    scope: CalibrationScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-21"
