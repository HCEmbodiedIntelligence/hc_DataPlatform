"""P19 HTTP boundary for the formal redacted audit projection."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from hc_data_platform.security.http import VerifiedAuth

from .governance import AuditGovernanceService
from .models import (
    AuditBootstrapEnvelope,
    AuditCursorEnvelope,
    AuditEventEnvelope,
    AuditExportDownloadAuthorization,
    AuditExportJob,
    AuditFacetsEnvelope,
    AuditIntegrityEnvelope,
    AuditLegalHold,
    AuditRetentionPolicy,
    AuditScope,
)
from .repository import AuditQueryFilters
from .service import AuditProjectionService

router = APIRouter(prefix="/api/v1/projects/{project_id}/audit", tags=["audit"])
_service = AuditProjectionService.in_memory()
_governance = AuditGovernanceService.in_memory(_service)

PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {
        "description": "The audit read request could not be completed.",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    }
    for status in (400, 401, 403, 404, 409, 410, 422, 500)
}


def configure_audit_projection(
    service: AuditProjectionService,
    governance: AuditGovernanceService | None = None,
) -> None:
    global _governance, _service
    _service = service
    _governance = governance or AuditGovernanceService.in_memory(service)


def get_audit_projection_service() -> AuditProjectionService:
    return _service


def get_audit_governance_service() -> AuditGovernanceService:
    return _governance


ServiceDependency = Annotated[AuditProjectionService, Depends(get_audit_projection_service)]
GovernanceDependency = Annotated[
    AuditGovernanceService,
    Depends(get_audit_governance_service),
]
OrganizationHeader = Annotated[
    str,
    Header(alias="X-Organization-Id", min_length=1, max_length=128),
]
RegionHeader = Annotated[
    str | None,
    Header(alias="X-Region-Code", min_length=1, max_length=64),
]
OccurredFrom = Annotated[datetime, Query(alias="occurred_from")]
OccurredTo = Annotated[datetime, Query(alias="occurred_to")]
OpaqueIdValues = Annotated[list[str] | None, Query()]
ResourceTypes = Annotated[list[str] | None, Query()]
Cursor = Annotated[str | None, Query(min_length=16, max_length=16_384)]


class UpdateAuditRetentionPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    standard_days: int = Field(ge=30, le=3650)
    security_days: int = Field(ge=90, le=3650)


class CreateAuditLegalHoldRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=2000)
    occurred_from: datetime
    occurred_to: datetime


class CreateAuditExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurred_from: datetime
    occurred_to: datetime


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) and value else "request-id-unavailable"


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


def _scope(
    organization_id: str,
    project_id: str,
    region_code: str | None,
) -> AuditScope:
    return AuditScope(
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
    )


def _filters(
    *,
    occurred_from: datetime,
    occurred_to: datetime,
    actor_id: list[str] | None,
    event_name: list[str] | None,
    resource_type: list[str] | None,
    resource_id: str | None,
    outcome: list[str] | None,
    risk_level: list[str] | None,
    request_id: str | None,
) -> AuditQueryFilters:
    return AuditQueryFilters(
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        actor_ids=tuple(actor_id or ()),
        event_names=tuple(event_name or ()),
        resource_types=tuple(resource_type or ()),
        resource_id=resource_id,
        outcomes=tuple(outcome or ()),
        risk_levels=tuple(risk_level or ()),
        request_id=request_id,
    )


def _check_region_query(region_header: str | None, region_code: list[str] | None) -> str | None:
    requested = tuple(dict.fromkeys(region_code or ()))
    if region_header is None:
        if requested:
            from hc_data_platform.core.errors import problem

            raise problem(
                status=422,
                code="AUDIT_REGION_SCOPE_REQUIRED",
                title="Audit region scope required",
                detail="A region filter requires the matching selected region scope.",
            )
        return None
    if requested and requested != (region_header,):
        from hc_data_platform.core.errors import problem

        raise problem(
            status=422,
            code="AUDIT_REGION_FILTER_MISMATCH",
            title="Audit region filter mismatch",
            detail="The region filter must match the selected region scope.",
        )
    return region_header


@router.get(
    "/bootstrap",
    operation_id="getAuditBootstrap",
    response_model=AuditBootstrapEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_audit_bootstrap(
    project_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    service: ServiceDependency,
    occurred_from: OccurredFrom,
    occurred_to: OccurredTo,
) -> AuditBootstrapEnvelope:
    _no_store(response)
    return service.bootstrap(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        request_id=_request_id(request),
    )


@router.get(
    "/integrity",
    operation_id="verifyAuditIntegrity",
    response_model=AuditIntegrityEnvelope,
    responses=PROBLEM_RESPONSES,
)
def verify_audit_integrity(
    project_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    service: ServiceDependency,
) -> AuditIntegrityEnvelope:
    _no_store(response)
    return service.integrity(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        request_id=_request_id(request),
    )


@router.get(
    "/events/facets",
    operation_id="getAuditFacets",
    response_model=AuditFacetsEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_audit_facets(
    project_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_header: RegionHeader,
    service: ServiceDependency,
    occurred_from: OccurredFrom,
    occurred_to: OccurredTo,
    actor_id: OpaqueIdValues = None,
    event_name: OpaqueIdValues = None,
    resource_type: ResourceTypes = None,
    resource_id: str | None = Query(default=None, min_length=1, max_length=256),
    outcome: OpaqueIdValues = None,
    risk_level: OpaqueIdValues = None,
    request_id: str | None = Query(default=None, min_length=1, max_length=256),
    region_code: OpaqueIdValues = None,
) -> AuditFacetsEnvelope:
    _no_store(response)
    selected_region = _check_region_query(region_header, region_code)
    return service.facets(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=selected_region,
        filters=_filters(
            occurred_from=occurred_from,
            occurred_to=occurred_to,
            actor_id=actor_id,
            event_name=event_name,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            risk_level=risk_level,
            request_id=request_id,
        ),
        request_id=_request_id(request),
    )


@router.get(
    "/events",
    operation_id="listAuditEvents",
    response_model=AuditCursorEnvelope,
    responses=PROBLEM_RESPONSES,
)
def list_audit_events(
    project_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_header: RegionHeader,
    service: ServiceDependency,
    occurred_from: OccurredFrom,
    occurred_to: OccurredTo,
    sort: Annotated[
        str, Query(pattern=r"^occurred_at:desc,event_id:desc$")
    ] = "occurred_at:desc,event_id:desc",
    actor_id: OpaqueIdValues = None,
    event_name: OpaqueIdValues = None,
    resource_type: ResourceTypes = None,
    resource_id: str | None = Query(default=None, min_length=1, max_length=256),
    outcome: OpaqueIdValues = None,
    risk_level: OpaqueIdValues = None,
    request_id: str | None = Query(default=None, min_length=1, max_length=256),
    region_code: OpaqueIdValues = None,
    after: Cursor = None,
    before: Cursor = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> AuditCursorEnvelope:
    del sort
    _no_store(response)
    selected_region = _check_region_query(region_header, region_code)
    return service.list_events(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=selected_region,
        filters=_filters(
            occurred_from=occurred_from,
            occurred_to=occurred_to,
            actor_id=actor_id,
            event_name=event_name,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            risk_level=risk_level,
            request_id=request_id,
        ),
        after=after,
        before=before,
        limit=limit,
        request_id=_request_id(request),
    )


@router.get(
    "/events/{event_id}",
    operation_id="getAuditEvent",
    response_model=AuditEventEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_audit_event(
    project_id: str,
    event_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    service: ServiceDependency,
) -> AuditEventEnvelope:
    _no_store(response)
    return service.event(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        event_id=event_id,
        request_id=_request_id(request),
    )


@router.get(
    "/retention-policy",
    operation_id="getAuditRetentionPolicy",
    response_model=AuditRetentionPolicy,
    responses=PROBLEM_RESPONSES,
)
def get_audit_retention_policy(
    project_id: str,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    governance: GovernanceDependency,
) -> AuditRetentionPolicy:
    _no_store(response)
    policy = governance.get_policy(
        auth=auth,
        scope=_scope(organization_id, project_id, region_code),
    )
    response.headers["ETag"] = policy.etag
    return policy


@router.put(
    "/retention-policy",
    operation_id="updateAuditRetentionPolicy",
    response_model=AuditRetentionPolicy,
    responses=PROBLEM_RESPONSES,
)
def update_audit_retention_policy(
    project_id: str,
    command: UpdateAuditRetentionPolicyRequest,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    governance: GovernanceDependency,
    if_match: Annotated[str, Header(alias="If-Match", min_length=3, max_length=256)],
) -> AuditRetentionPolicy:
    _no_store(response)
    policy = governance.put_policy(
        auth=auth,
        scope=_scope(organization_id, project_id, region_code),
        standard_days=command.standard_days,
        security_days=command.security_days,
        if_match=if_match,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = policy.etag
    return policy


@router.get(
    "/legal-holds",
    operation_id="listAuditLegalHolds",
    response_model=list[AuditLegalHold],
    responses=PROBLEM_RESPONSES,
)
def list_audit_legal_holds(
    project_id: str,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    governance: GovernanceDependency,
) -> tuple[AuditLegalHold, ...]:
    _no_store(response)
    return governance.list_holds(
        auth=auth,
        scope=_scope(organization_id, project_id, region_code),
    )


@router.post(
    "/legal-holds",
    operation_id="createAuditLegalHold",
    response_model=AuditLegalHold,
    status_code=201,
    responses=PROBLEM_RESPONSES,
)
def create_audit_legal_hold(
    project_id: str,
    command: CreateAuditLegalHoldRequest,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    governance: GovernanceDependency,
) -> AuditLegalHold:
    _no_store(response)
    return governance.create_hold(
        auth=auth,
        scope=_scope(organization_id, project_id, region_code),
        reason=command.reason,
        occurred_from=command.occurred_from,
        occurred_to=command.occurred_to,
        request_id=_request_id(request),
    )


@router.post(
    "/legal-holds/{hold_id}:release",
    operation_id="releaseAuditLegalHold",
    response_model=AuditLegalHold,
    responses=PROBLEM_RESPONSES,
)
def release_audit_legal_hold(
    project_id: str,
    hold_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    governance: GovernanceDependency,
) -> AuditLegalHold:
    _no_store(response)
    return governance.release_hold(
        auth=auth,
        scope=_scope(organization_id, project_id, region_code),
        hold_id=hold_id,
        request_id=_request_id(request),
    )


@router.post(
    "/exports",
    operation_id="createAuditExport",
    response_model=AuditExportJob,
    status_code=202,
    responses=PROBLEM_RESPONSES,
)
def create_audit_export(
    project_id: str,
    command: CreateAuditExportRequest,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    governance: GovernanceDependency,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=256),
    ],
) -> AuditExportJob:
    _no_store(response)
    scope = _scope(organization_id, project_id, region_code)
    request_id = _request_id(request)
    job = governance.create_export(
        auth=auth,
        scope=scope,
        occurred_from=command.occurred_from,
        occurred_to=command.occurred_to,
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    response.headers["Location"] = f"/api/v1/projects/{project_id}/audit/exports/{job.job_id}"
    return job


@router.get(
    "/exports/{job_id}",
    operation_id="getAuditExport",
    response_model=AuditExportJob,
    responses=PROBLEM_RESPONSES,
)
def get_audit_export(
    project_id: str,
    job_id: str,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    governance: GovernanceDependency,
) -> AuditExportJob:
    _no_store(response)
    return governance.get_export(
        auth=auth,
        scope=_scope(organization_id, project_id, region_code),
        job_id=job_id,
    )


@router.post(
    "/exports/{job_id}:cancel",
    operation_id="cancelAuditExport",
    response_model=AuditExportJob,
    responses=PROBLEM_RESPONSES,
)
def cancel_audit_export(
    project_id: str,
    job_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    governance: GovernanceDependency,
) -> AuditExportJob:
    _no_store(response)
    return governance.cancel_export(
        auth=auth,
        scope=_scope(organization_id, project_id, region_code),
        job_id=job_id,
        request_id=_request_id(request),
    )


@router.post(
    "/exports/{job_id}:retry",
    operation_id="retryAuditExport",
    response_model=AuditExportJob,
    status_code=202,
    responses=PROBLEM_RESPONSES,
)
def retry_audit_export(
    project_id: str,
    job_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    governance: GovernanceDependency,
) -> AuditExportJob:
    _no_store(response)
    scope = _scope(organization_id, project_id, region_code)
    request_id = _request_id(request)
    job = governance.retry_export(
        auth=auth,
        scope=scope,
        job_id=job_id,
        request_id=request_id,
    )
    return job


@router.get(
    "/exports/{job_id}/download",
    operation_id="authorizeAuditExportDownload",
    response_model=AuditExportDownloadAuthorization,
    responses=PROBLEM_RESPONSES,
)
def authorize_audit_export_download(
    project_id: str,
    job_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    governance: GovernanceDependency,
) -> AuditExportDownloadAuthorization:
    _no_store(response)
    return governance.authorize_download(
        auth=auth,
        scope=_scope(organization_id, project_id, region_code),
        job_id=job_id,
        request_id=_request_id(request),
    )
