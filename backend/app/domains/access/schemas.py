"""Pydantic v2 wire and internal schemas for access control and audit.

Field names intentionally mirror ``access-audit-schemas.json``.  Capability and
event-name membership are validated by :mod:`service` against the vendored
integration registry; they are not duplicated as Python enums here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

try:  # Frozen contract name; current foundation snapshot still exports Int64.
    from app.core.int64 import Int64Str
except ImportError:  # pragma: no cover - removed when T8 aligns the frozen signature
    from app.core.int64 import Int64 as Int64Str


OpaqueId = Annotated[str, StringConstraints(min_length=1, max_length=256)]
Etag = Annotated[str, StringConstraints(min_length=3, max_length=256)]
CapabilityKey = Annotated[str, StringConstraints(min_length=1, max_length=128)]
CanonicalAuditEventName = Annotated[str, StringConstraints(min_length=3, max_length=256)]
SafeAuditScalar = str | int | float | bool | None
SafeAuditValue = SafeAuditScalar | list[SafeAuditScalar]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=False)


ProjectRoleId = Literal["PROJECT_ADMIN", "PROJECT_DEVELOPER", "PROJECT_DATA_PROCESSOR"]
ScopeType = Literal["ORGANIZATION", "PROJECT", "REGION", "RESOURCE_SET", "RESOURCE"]


class OrganizationScope(StrictModel):
    type: Literal["ORGANIZATION"]
    organization_id: OpaqueId


class ProjectScope(StrictModel):
    type: Literal["PROJECT"]
    organization_id: OpaqueId
    project_id: OpaqueId


class RegionScope(StrictModel):
    type: Literal["REGION"]
    organization_id: OpaqueId
    project_id: OpaqueId
    region_code: Annotated[str, StringConstraints(min_length=1, max_length=64)]


class ResourceSetScope(StrictModel):
    type: Literal["RESOURCE_SET"]
    organization_id: OpaqueId
    project_id: OpaqueId
    resource_set_id: OpaqueId


class ResourceScope(StrictModel):
    type: Literal["RESOURCE"]
    organization_id: OpaqueId
    project_id: OpaqueId
    resource_type: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    resource_id: OpaqueId


ScopeRef = Annotated[
    OrganizationScope | ProjectScope | RegionScope | ResourceSetScope | ResourceScope,
    Field(discriminator="type"),
]
ProjectDescendantScopeRef = Annotated[
    ProjectScope | RegionScope | ResourceSetScope | ResourceScope,
    Field(discriminator="type"),
]


class ProjectMembershipSubject(StrictModel):
    type: Literal["PROJECT_MEMBERSHIP"]
    principal_id: OpaqueId
    project_membership_id: OpaqueId
    organization_id: OpaqueId
    project_id: OpaqueId


class OrganizationMembershipSubject(StrictModel):
    type: Literal["ORGANIZATION_MEMBERSHIP"]
    principal_id: OpaqueId
    organization_membership_id: OpaqueId
    organization_id: OpaqueId


AuthorizationSubjectRef = Annotated[
    ProjectMembershipSubject | OrganizationMembershipSubject, Field(discriminator="type")
]


class AccessBlockedReason(StrictModel):
    action: str
    code: str
    message_key: str


class AuditBlockedReason(StrictModel):
    action: str
    code: str
    message_key: str


class ResourceAccessDecision(StrictModel):
    allowed_actions: list[str]
    blocked_reasons: list[AccessBlockedReason | AuditBlockedReason]


class CapabilityCatalog(StrictModel):
    catalog_version: str
    contract_status: Literal["CONDITIONAL", "ACTIVE", "SUPERSEDED"]
    capability_keys: list[CapabilityKey]


class RoleVersion(StrictModel):
    role_id: ProjectRoleId
    display_name: str
    role_version: str
    catalog_version: str
    base_capability_keys: list[CapabilityKey]
    capability_ceiling_keys: list[CapabilityKey]
    status: Literal["ACTIVE", "SUPERSEDED"]


class PrincipalSummary(StrictModel):
    principal_id: OpaqueId
    type: Literal["HUMAN", "SERVICE"]
    display_name: str
    secondary_display: str | None = None
    identity_status: Literal["ACTIVE", "DISABLED", "UNKNOWN"]


class Member(StrictModel):
    member_id: OpaqueId
    principal: PrincipalSummary
    organization_id: OpaqueId
    project_id: OpaqueId
    status: Literal["ACTIVE", "DISABLED", "REMOVED"]
    role_id: ProjectRoleId
    role_version: str
    joined_at: datetime
    last_active_at: datetime | None = None
    etag: Etag
    allowed_actions: list[str]
    blocked_reasons: list[AccessBlockedReason]


class RoleAssignment(StrictModel):
    role_assignment_id: OpaqueId
    subject: ProjectMembershipSubject
    role_id: ProjectRoleId
    role_version: str
    scope: ProjectScope
    inherit: bool
    valid_from: datetime
    valid_to: datetime | None
    status: Literal["ACTIVE", "REVOKED", "EXPIRED"]
    etag: Etag


class ScopeGrant(StrictModel):
    scope_grant_id: OpaqueId
    subject: AuthorizationSubjectRef
    scope: ScopeRef
    effect: Literal["ALLOW", "DENY"]
    capability_keys: list[CapabilityKey]
    ceiling_version: str
    inherit: bool
    valid_from: datetime
    valid_to: datetime | None
    status: Literal["ACTIVE", "REVOKED", "EXPIRED"]
    etag: Etag


class AuthorizationEvidence(StrictModel):
    capability_key: CapabilityKey
    decision: Literal["ALLOW", "DENY"]
    source_type: Literal["ROLE_ASSIGNMENT", "SCOPE_GRANT"]
    source_id: OpaqueId
    inherited_from_scope: ScopeRef


class AuthorizationSnapshot(StrictModel):
    principal_id: OpaqueId
    scope: ScopeRef
    capability_keys: list[CapabilityKey]
    denied_capability_keys: list[CapabilityKey]
    evidence: list[AuthorizationEvidence]
    catalog_version: str
    role_version: str
    policy_version: str
    evaluated_at: datetime
    expires_at: datetime
    refresh_state: Literal["CURRENT", "STALE", "FAILED"]


class ScopeGrantInput(StrictModel):
    scope: ProjectDescendantScopeRef
    effect: Literal["ALLOW", "DENY"]
    capability_keys: Annotated[list[CapabilityKey], Field(min_length=1)]
    inherit: bool


class ChangeRoleOperation(StrictModel):
    type: Literal["CHANGE_ROLE"]
    membership_id: OpaqueId
    membership_etag: Etag
    current_role_assignment_id: OpaqueId
    current_role_assignment_etag: Etag
    role_id: ProjectRoleId
    role_version: str


class AddScopeGrantOperation(StrictModel):
    type: Literal["ADD_SCOPE_GRANT"]
    membership_id: OpaqueId
    membership_etag: Etag
    current_role_assignment_id: OpaqueId
    current_role_assignment_etag: Etag
    role_version: str
    grant: ScopeGrantInput


class RevokeScopeGrantOperation(StrictModel):
    type: Literal["REVOKE_SCOPE_GRANT"]
    membership_id: OpaqueId
    membership_etag: Etag
    grant_id: OpaqueId
    grant_etag: Etag


class DisableMembershipOperation(StrictModel):
    type: Literal["DISABLE_MEMBERSHIP"]
    membership_id: OpaqueId
    membership_etag: Etag


class EnableMembershipOperation(StrictModel):
    type: Literal["ENABLE_MEMBERSHIP"]
    membership_id: OpaqueId
    membership_etag: Etag
    current_role_assignment_id: OpaqueId
    current_role_assignment_etag: Etag
    role_id: ProjectRoleId
    role_version: str


AccessOperation = Annotated[
    ChangeRoleOperation
    | AddScopeGrantOperation
    | RevokeScopeGrantOperation
    | DisableMembershipOperation
    | EnableMembershipOperation,
    Field(discriminator="type"),
]


class AccessChangePreflightRequest(StrictModel):
    operations: Annotated[list[AccessOperation], Field(min_length=1, max_length=100)]
    project_policy_revision: str
    project_policy_etag: Etag
    reason: Annotated[str, StringConstraints(min_length=1, max_length=1000)]


class PreflightCommitRequest(StrictModel):
    preflight_token: Annotated[str, StringConstraints(min_length=20, max_length=4096)]


class EmailInvitationTarget(StrictModel):
    type: Literal["EMAIL"]
    value: Annotated[str, StringConstraints(min_length=3, max_length=320)]


class ExternalInvitationTarget(StrictModel):
    type: Literal["EXTERNAL_SUBJECT"]
    issuer: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    subject: Annotated[str, StringConstraints(min_length=1, max_length=512)]


class CreateInvitationOperation(StrictModel):
    type: Literal["CREATE_INVITATION"]
    target: Annotated[EmailInvitationTarget | ExternalInvitationTarget, Field(discriminator="type")]
    role_id: ProjectRoleId
    role_version: str
    intended_scope: ProjectScope
    invitation_expires_at: datetime


class ExistingInvitationOperation(StrictModel):
    type: Literal["RESEND_INVITATION", "REVOKE_INVITATION"]
    invitation_id: OpaqueId
    invitation_etag: Etag


class InvitationPreflightRequest(StrictModel):
    operation: CreateInvitationOperation | ExistingInvitationOperation
    project_policy_revision: str
    project_policy_etag: Etag
    reason: Annotated[str, StringConstraints(min_length=1, max_length=1000)]


class AuditActorSnapshot(StrictModel):
    type: Literal["HUMAN", "SERVICE", "SYSTEM"]
    principal_id: OpaqueId
    display_name: str
    role_ids: list[str]
    delegated_by_principal_id: OpaqueId | None = None


class AuditScope(StrictModel):
    organization_id: OpaqueId
    project_id: OpaqueId
    region_code: str | None


class AuditResourceRef(StrictModel):
    type: str
    id: OpaqueId
    display_name: str | None
    parent_refs: list[dict[str, str]]


class AuditRequestContext(StrictModel):
    request_id: OpaqueId
    job_id: OpaqueId | None
    client_type: str
    ip_address: str | None
    device_summary: str | None


class SafeAuditObject(StrictModel):
    model_config = ConfigDict(extra="allow")


class AuditChangeSummary(StrictModel):
    summary_code: str
    changed_fields: list[str]
    before: dict[str, SafeAuditValue]
    after: dict[str, SafeAuditValue]
    omitted_field_classes: list[str]


class AuditRelationships(StrictModel):
    parent_event_id: OpaqueId | None
    related_event_ids: list[OpaqueId]
    resource_refs: list[AuditResourceRef]


class AuditProducer(StrictModel):
    service: str
    producer_event_id: OpaqueId
    contract_version: str


class AuditOutcomeValue(StrictModel):
    status: Literal["SUCCEEDED", "DENIED", "FAILED", "PARTIAL"]
    reason_code: str | None
    http_status: Annotated[int, Field(ge=100, le=599)] | None


class AuditEventInput(StrictModel):
    schema_version: Literal[1]
    event_name: CanonicalAuditEventName
    occurred_at: datetime
    actor: AuditActorSnapshot
    scope: AuditScope
    resource: AuditResourceRef
    request: AuditRequestContext
    outcome: AuditOutcomeValue
    risk_signal_codes: list[str]
    change: AuditChangeSummary | None
    relationships: AuditRelationships
    producer: AuditProducer


class AuditRisk(StrictModel):
    level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    signal_codes: list[str]
    policy_version: str


class AuditRetention(StrictModel):
    class_: Literal["STANDARD", "SECURITY", "LEGAL_HOLD"] = Field(alias="class")
    policy_version: str
    retain_until: datetime | None
    legal_hold: bool


class AuditIntegrity(StrictModel):
    version: str
    record_digest: str
    checkpoint_id: OpaqueId | None


class AuditEventRecord(StrictModel):
    event_id: OpaqueId
    input: AuditEventInput
    recorded_at: datetime
    ingest_sequence: Int64Str
    catalog_version: Literal["p19-v1-142"]
    risk: AuditRisk
    retention: AuditRetention
    integrity: AuditIntegrity


class AuditEventFilters(StrictModel):
    occurred_from: datetime
    occurred_to: datetime
    actor_id: list[OpaqueId]
    event_name: list[CanonicalAuditEventName]
    resource_type: list[str]
    resource_id: OpaqueId | None
    outcome: list[Literal["SUCCEEDED", "DENIED", "FAILED", "PARTIAL"]]
    risk_level: list[Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]]
    request_id: OpaqueId | None
    region_code: list[str]
    sort: Literal["occurred_at:desc,event_id:desc"]


class AuditExportPreflightRequest(StrictModel):
    filters: AuditEventFilters
    format: Literal["CSV", "JSONL"]
    field_profile: Literal["STANDARD", "SECURITY_REVIEW"]
    reason: Annotated[str, StringConstraints(min_length=1, max_length=1000)]


class AuditExportCreateCommand(StrictModel):
    normalized_filters: AuditEventFilters
    snapshot_at: datetime
    format: Literal["CSV", "JSONL"]
    field_profile: Literal["STANDARD", "SECURITY_REVIEW"]
    reason: Annotated[str, StringConstraints(min_length=1, max_length=1000)]
    preflight_token: Annotated[str, StringConstraints(min_length=20, max_length=4096)]


class CursorPageInfo(StrictModel):
    has_next_page: bool
    has_previous_page: bool
    start_cursor: str | None
    end_cursor: str | None


class Envelope(StrictModel):
    data: Any
    scope: ProjectScope | AuditScope
    request_id: OpaqueId
    contract_version: Literal["v1"] = "v1"


class CursorEnvelope(StrictModel):
    items: list[Any]
    page_info: CursorPageInfo
    snapshot_at: datetime
    scope: ProjectScope | AuditScope
    request_id: OpaqueId
    contract_version: Literal["v1"] = "v1"
