from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hc_data_platform.core.pagination import PageInfo


class DataSchemaScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    organization_id: str = Field(min_length=1, max_length=128)


class DataSchemaRouteScope(DataSchemaScope):
    project_id: str | None = Field(default=None, min_length=1, max_length=128)
    region_code: str | None = Field(default=None, min_length=1, max_length=64)


class DataSchemaDatasetReferenceScope(DataSchemaScope):
    """Exact dataset scope for a durable schema-version provenance fact."""

    project_id: str = Field(min_length=1, max_length=128)
    region_code: str = Field(min_length=1, max_length=64)


class SchemaBlockedReason(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=512)


class SchemaHash(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    algorithm: str = Field(min_length=1, max_length=64)
    canonicalization_version: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=128)


class DataSchemaVersionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_id: str = Field(min_length=1, max_length=128)
    family_id: str = Field(min_length=1, max_length=128)
    schema_version: str = Field(pattern=r"^[1-9]\d*$")
    display_name: str = Field(min_length=1, max_length=256)
    logical_type: str = Field(min_length=1, max_length=128)
    status: str = Field(min_length=1, max_length=64)
    compatibility_mode: str = Field(min_length=1, max_length=64)
    compatibility_result: str | None = Field(default=None, min_length=1, max_length=64)
    schema_hash: SchemaHash | None = None
    schema_definition: dict[str, Any]
    etag: str = Field(min_length=1, max_length=256)
    allowed_actions: tuple[str, ...] = ()
    blocked_reasons: tuple[SchemaBlockedReason, ...] = ()


class DataSchemaPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[DataSchemaVersionRecord, ...]
    page_info: PageInfo
    snapshot_at: datetime
    scope: DataSchemaScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-19"


class DataSchemaEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    data: DataSchemaVersionRecord
    scope: DataSchemaScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-19"


class DataSchemaDatasetReferenceRequest(BaseModel):
    """Pins one READY immutable dataset version to a published schema version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: str = Field(
        min_length=1, max_length=128, pattern=r"^dataset_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"
    )
    dataset_version_id: str = Field(
        min_length=1, max_length=128, pattern=r"^version_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$"
    )


class DataSchemaDatasetReference(BaseModel):
    """Immutable evidence that a dataset consumed a fixed published schema version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: str = Field(min_length=1, max_length=128)
    schema_version: str = Field(pattern=r"^[1-9]\d*$")
    dataset_id: str = Field(min_length=1, max_length=128)
    dataset_version_id: str = Field(min_length=1, max_length=128)
    associated_by: str = Field(min_length=1, max_length=128)
    associated_at: datetime


class DataSchemaDatasetReferenceEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data: DataSchemaDatasetReference
    scope: DataSchemaDatasetReferenceScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-21"


class DataSchemaDatasetReferencePage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[DataSchemaDatasetReference, ...]
    page_info: PageInfo
    scope: DataSchemaDatasetReferenceScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-21"


class StreamSchemaDefinitionField(BaseModel):
    """The supported, portable field vocabulary for an authored stream schema."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    type: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=1_024)
    required: bool = False


class StreamSchemaDefinition(BaseModel):
    """Canonical P17 document; unsupported JSON-Schema keywords are rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fields: tuple[StreamSchemaDefinitionField, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def require_unique_field_names(self) -> StreamSchemaDefinition:
        names = [field.name for field in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("schema field names must be unique")
        return self


class CreateStreamSchemaRequest(BaseModel):
    """Creates v1 or the next immutable draft version for one schema family."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$"
    )
    family_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$"
    )
    display_name: str = Field(min_length=1, max_length=256)
    logical_type: str = Field(min_length=1, max_length=128)
    compatibility_mode: str = Field(pattern=r"^(STRICT|BACKWARD|FORWARD|FULL)$")
    schema_definition: StreamSchemaDefinition
    change_summary: str = Field(min_length=1, max_length=2_000)


class UpdateStreamSchemaDraftRequest(BaseModel):
    """Edits only the currently addressed DRAFT version under a strong ETag."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    display_name: str | None = Field(default=None, min_length=1, max_length=256)
    logical_type: str | None = Field(default=None, min_length=1, max_length=128)
    compatibility_mode: str | None = Field(
        default=None, pattern=r"^(STRICT|BACKWARD|FORWARD|FULL)$"
    )
    schema_definition: StreamSchemaDefinition | None = None
    change_summary: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def require_a_draft_change(self) -> UpdateStreamSchemaDraftRequest:
        if (
            self.display_name is None
            and self.logical_type is None
            and self.compatibility_mode is None
            and self.schema_definition is None
        ):
            raise ValueError("a draft update must change at least one schema property")
        return self


class DataSchemaValidationFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=128)
    severity: str = Field(pattern=r"^(ERROR|WARNING)$")
    message: str = Field(min_length=1, max_length=512)
    path: str = Field(min_length=1, max_length=512)


class DataSchemaValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    schema_id: str = Field(min_length=1, max_length=128)
    schema_version: str = Field(pattern=r"^[1-9]\d*$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    compatibility_check_id: str = Field(min_length=1, max_length=128)
    compatibility_result: str = Field(pattern=r"^(PASSED|FAILED)$")
    status: str = Field(pattern=r"^(PASSED|FAILED)$")
    findings: tuple[DataSchemaValidationFinding, ...] = ()
    checked_by: str = Field(min_length=1, max_length=128)
    checked_at: datetime


class DataSchemaValidationReportEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data: DataSchemaValidationReport
    scope: DataSchemaScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-21"


class DataSchemaPublishPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_etag: str = Field(min_length=1, max_length=256)
    validation_report_id: str = Field(min_length=1, max_length=128)
    compatibility_check_id: str = Field(min_length=1, max_length=128)
    change_summary: str = Field(min_length=1, max_length=2_000)
    acknowledge_warning_codes: tuple[str, ...] = Field(default=(), max_length=64)


class DataSchemaPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    preflight_token: str = Field(min_length=32, max_length=256)


class DataSchemaPublishPreflight(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    preflight_token: str | None = Field(default=None, min_length=32, max_length=256)
    expires_at: datetime | None = None
    resource_revision: str = Field(min_length=1, max_length=256)
    impacts: tuple[SchemaBlockedReason, ...] = ()
    warnings: tuple[SchemaBlockedReason, ...] = ()
    blockers: tuple[SchemaBlockedReason, ...] = ()


class DataSchemaPublishPreflightEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data: DataSchemaPublishPreflight
    scope: DataSchemaScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-21"


class CanonicalRouteQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_id: str = Field(min_length=1, max_length=128)
    schema_version: str = Field(pattern=r"^[1-9]\d*$")
    component_id: str = Field(min_length=1, max_length=128)
    detail_tab: str = Field(min_length=1, max_length=64)


class DataSchemaRouteResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    canonical_query: CanonicalRouteQuery
    scope: DataSchemaRouteScope
    relation_revision: str = Field(min_length=1, max_length=256)
    allowed_actions: tuple[str, ...] = ("VIEW",)
    blocked_reasons: tuple[SchemaBlockedReason, ...] = ()


class DataSchemaRouteEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    data: DataSchemaRouteResolution
    scope: DataSchemaRouteScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "2026-08-19"
