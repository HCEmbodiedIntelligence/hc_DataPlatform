"""Wire models for the P19 redacted audit read projection.

The durable ``core.audit_events`` table deliberately has a small cross-domain
shape.  These models are the safe browser contract layered on top of it.  In
particular, the repository never promotes ``details`` into this contract:
callers receive only stable identifiers and server-owned, redacted summaries.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Identifier = Annotated[
    str,
    Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$",
    ),
]
OrganizationId = Identifier
ProjectId = Identifier
RegionCode = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    ),
]
AuditAction = Literal["VIEW", "EXPORT", "MANAGE_RETENTION"]
AuditOutcome = Literal["SUCCEEDED", "DENIED", "FAILED", "PARTIAL"]
AuditRiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
AuditIntegrityStatus = Literal["UNKNOWN", "PASSED", "FAILED"]


class _AuditProjectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AuditScope(_AuditProjectionModel):
    organization_id: OrganizationId
    project_id: ProjectId
    region_code: RegionCode | None = None


class AuditBlockedReason(_AuditProjectionModel):
    action: AuditAction = "VIEW"
    code: str = Field(min_length=1, max_length=128, pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    message: str = Field(min_length=1, max_length=512)


class AuditActorSnapshot(_AuditProjectionModel):
    type: Literal["USER", "SERVICE_ACCOUNT", "SYSTEM", "ANONYMOUS"]
    principal_id: Identifier | None = None
    display_name: str = Field(min_length=1, max_length=256)
    role_ids: tuple[str, ...] = Field(default=(), max_length=32)
    delegated_by_principal_id: Identifier | None = None


class AuditResourceParentRef(_AuditProjectionModel):
    type: str = Field(min_length=1, max_length=128, pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    id: Identifier


class AuditResourceRef(_AuditProjectionModel):
    type: str = Field(min_length=1, max_length=128, pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    id: Identifier
    display_name: str = Field(min_length=1, max_length=256)
    parent_refs: tuple[AuditResourceParentRef, ...] = Field(default=(), max_length=20)


class AuditRequestContext(_AuditProjectionModel):
    request_id: Identifier
    job_id: Identifier | None = None
    client_type: Literal["WEB", "API", "WORKER", "SYSTEM"]
    # These fields are intentionally always null for the baseline core event
    # store. A future field-profile service may populate independently stored,
    # policy-authorized values without ever reading arbitrary event details.
    ip_address: None = None
    device_summary: None = None


class AuditOutcomeProjection(_AuditProjectionModel):
    status: AuditOutcome
    reason_code: str | None = Field(default=None, max_length=128)
    http_status: int | None = Field(default=None, ge=100, le=599)


class AuditRiskProjection(_AuditProjectionModel):
    level: AuditRiskLevel
    signal_codes: tuple[str, ...] = Field(default=(), max_length=32)


class AuditChangeSummary(_AuditProjectionModel):
    """A deliberately metadata-only change summary.

    The current durable stream has before/after digests but no globally approved
    field-level allowlist. Returning a digest or the arbitrary JSON details
    would turn P19 into a disclosure channel, so this shape remains optional
    and is not populated until such a server-side allowlist exists.
    """

    summary_code: str = Field(min_length=1, max_length=128)
    changed_fields: tuple[str, ...] = Field(default=(), max_length=64)
    before: dict[
        str, str | int | float | bool | None | tuple[str | int | float | bool | None, ...]
    ] = Field(default_factory=dict)
    after: dict[
        str, str | int | float | bool | None | tuple[str | int | float | bool | None, ...]
    ] = Field(default_factory=dict)
    omitted_field_classes: tuple[str, ...] = Field(default=(), max_length=32)


class AuditRelationships(_AuditProjectionModel):
    parent_event_id: Identifier | None = None
    related_event_ids: tuple[Identifier, ...] = Field(default=(), max_length=100)
    resource_refs: tuple[AuditResourceRef, ...] = Field(default=(), max_length=100)


class AuditRetentionProjection(_AuditProjectionModel):
    class_name: Literal["STANDARD", "SECURITY", "LEGAL_HOLD"] = Field(alias="class")
    policy_version: str = Field(min_length=1, max_length=128)
    retain_until: datetime | None = None
    legal_hold: bool = False

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class AuditIntegrityProjection(_AuditProjectionModel):
    status: AuditIntegrityStatus = "UNKNOWN"
    version: str = Field(min_length=1, max_length=128)
    record_digest: None = None
    checkpoint_id: None = None


class AuditIntegrityCheck(_AuditProjectionModel):
    """Safe aggregate result of a server-side audit-chain verification."""

    status: Literal["PASSED", "FAILED"]
    version: str = Field(min_length=1, max_length=128)
    checked_at: datetime
    checked_event_count: int = Field(ge=0)
    checked_chain_count: int = Field(ge=0)
    verified_through: datetime | None = None


class AuditProducer(_AuditProjectionModel):
    service: str = Field(min_length=1, max_length=128)
    producer_event_id: Identifier
    contract_version: Literal["audit-event-v1"] = "audit-event-v1"


class AuditReadProjection(_AuditProjectionModel):
    schema_version: Literal[1] = 1
    event_id: Identifier
    # A string rather than a Literal keeps historical, safe events observable
    # while the frontend reports an unknown catalog entry instead of guessing.
    event_name: str = Field(
        min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$"
    )
    occurred_at: datetime
    recorded_at: datetime
    actor: AuditActorSnapshot
    scope: AuditScope
    resource: AuditResourceRef
    request: AuditRequestContext
    outcome: AuditOutcomeProjection
    risk: AuditRiskProjection
    change: AuditChangeSummary | None = None
    relationships: AuditRelationships = Field(default_factory=AuditRelationships)
    retention: AuditRetentionProjection
    integrity: AuditIntegrityProjection
    producer: AuditProducer
    allowed_actions: tuple[AuditAction, ...] = ("VIEW",)
    blocked_reasons: tuple[AuditBlockedReason, ...] = ()


class AuditPageInfo(_AuditProjectionModel):
    has_next_page: bool
    has_previous_page: bool
    start_cursor: str | None = Field(default=None, min_length=16, max_length=16_384)
    end_cursor: str | None = Field(default=None, min_length=16, max_length=16_384)


class AuditRedaction(_AuditProjectionModel):
    policy_version: str = Field(min_length=1, max_length=128)
    omitted_field_classes: tuple[str, ...] = Field(default=(), max_length=32)


class AuditBootstrap(_AuditProjectionModel):
    scope: AuditScope
    metrics: dict[str, str]
    as_of: datetime
    catalog_version: str = Field(min_length=1, max_length=128)
    policy_version: str = Field(min_length=1, max_length=128)
    integrity: AuditIntegrityStatus = "UNKNOWN"
    allowed_actions: tuple[AuditAction, ...] = ("VIEW",)
    blocked_reasons: tuple[AuditBlockedReason, ...] = ()


class AuditFacets(_AuditProjectionModel):
    event_names: tuple[str, ...] = Field(default=(), max_length=500)
    actor_ids: tuple[Identifier, ...] = Field(default=(), max_length=500)
    resource_types: tuple[str, ...] = Field(default=(), max_length=500)
    outcomes: tuple[AuditOutcome, ...] = Field(default=(), max_length=4)
    risk_levels: tuple[AuditRiskLevel, ...] = Field(default=(), max_length=4)


class AuditBootstrapEnvelope(_AuditProjectionModel):
    data: AuditBootstrap
    scope: AuditScope
    request_id: Identifier
    contract_version: Literal["v1"] = "v1"


class AuditFacetsEnvelope(_AuditProjectionModel):
    data: AuditFacets
    scope: AuditScope
    request_id: Identifier
    contract_version: Literal["v1"] = "v1"


class AuditCursorEnvelope(_AuditProjectionModel):
    items: tuple[AuditReadProjection, ...] = ()
    page_info: AuditPageInfo
    snapshot_at: datetime
    redaction: AuditRedaction
    scope: AuditScope
    request_id: Identifier
    contract_version: Literal["v1"] = "v1"


class AuditEventEnvelope(_AuditProjectionModel):
    data: AuditReadProjection
    scope: AuditScope
    request_id: Identifier
    contract_version: Literal["v1"] = "v1"


class AuditIntegrityEnvelope(_AuditProjectionModel):
    data: AuditIntegrityCheck
    scope: AuditScope
    request_id: Identifier
    contract_version: Literal["v1"] = "v1"


class AuditRetentionPolicy(_AuditProjectionModel):
    """Versioned project/region retention policy used by every P19 projection."""

    scope: AuditScope
    policy_version: int = Field(ge=1)
    standard_days: int = Field(ge=30, le=3650)
    security_days: int = Field(ge=90, le=3650)
    etag: str = Field(min_length=3, max_length=256)
    updated_by: Identifier
    updated_at: datetime


class AuditLegalHold(_AuditProjectionModel):
    hold_id: Identifier
    scope: AuditScope
    reason: str = Field(min_length=3, max_length=2000)
    occurred_from: datetime
    occurred_to: datetime
    status: Literal["ACTIVE", "RELEASED"]
    created_by: Identifier
    created_at: datetime
    released_by: Identifier | None = None
    released_at: datetime | None = None


class AuditExportProgress(_AuditProjectionModel):
    exported_event_count: int = Field(ge=0)
    scanned_page_count: int = Field(ge=0)


class AuditExportArtifact(_AuditProjectionModel):
    media_type: Literal["application/x-ndjson"] = "application/x-ndjson"
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: str = Field(pattern=r"^(0|[1-9][0-9]*)$")


class AuditExportJob(_AuditProjectionModel):
    job_id: Identifier
    scope: AuditScope
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]
    progress: AuditExportProgress
    occurred_from: datetime
    occurred_to: datetime
    created_by: Identifier
    created_at: datetime
    updated_at: datetime
    artifact: AuditExportArtifact | None = None
    error_code: str | None = Field(default=None, max_length=128)
    error_message: str | None = Field(default=None, max_length=512)


class AuditExportDownloadAuthorization(_AuditProjectionModel):
    job_id: Identifier
    download_url: str = Field(min_length=1, max_length=16_384)
    expires_at: datetime
    artifact: AuditExportArtifact
