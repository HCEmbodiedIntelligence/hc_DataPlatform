"""Formal P09 ManualIssue routes."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from hc_data_platform.core.errors import problem
from hc_data_platform.security.http import VerifiedAuth

from .models import (
    CreateDraftFromIssueCommand,
    CreateManualIssueCommand,
    ManualIssueCreateDraftEnvelope,
    ManualIssueDetailEnvelope,
    ManualIssueListEnvelope,
    ManualIssuePageEnvelope,
    ResolveManualIssueCommand,
    TriageManualIssueCommand,
)
from .repository import ManualIssueFilters
from .service import ManualIssueService

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/regions/{region_code}/manual-issues",
    tags=["manual-cleaning"],
)
_service = ManualIssueService.in_memory()

PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {
        "description": "The ManualIssue request could not be completed.",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    }
    for status in (400, 401, 403, 404, 409, 412, 422, 428, 500)
}


def configure_manual_issues(service: ManualIssueService) -> None:
    global _service
    _service = service


def get_manual_issue_service() -> ManualIssueService:
    return _service


ServiceDependency = Annotated[ManualIssueService, Depends(get_manual_issue_service)]
OrganizationHeader = Annotated[str, Header(alias="X-Organization-Id", min_length=1, max_length=128)]
IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=1, max_length=256),
]
IfMatch = Annotated[str | None, Header(alias="If-Match", min_length=3, max_length=256)]
OptionalQueryText = Annotated[str | None, Query(min_length=1, max_length=256)]
OptionalCursor = Annotated[str | None, Query(min_length=16, max_length=16_384)]
ManualIssueSort = Literal[
    "updated_at:desc,id:desc",
    "updated_at:asc,id:asc",
    "severity:desc,updated_at:desc,id:desc",
    "created_at:desc,id:desc",
]
ManualIssueStatus = Literal["OPEN", "IN_PROGRESS", "RESOLVED"]
ManualIssueType = Literal[
    "POSE_JITTER",
    "TIMESTAMP_DRIFT",
    "MISSING_FRAME",
    "STREAM_GAP",
    "CALIBRATION_MISMATCH",
    "INVALID_MASK",
    "OTHER",
]
ManualIssueSeverity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
RepeatedStatus = Annotated[list[ManualIssueStatus] | None, Query()]
RepeatedIssueType = Annotated[list[ManualIssueType] | None, Query()]
RepeatedSeverity = Annotated[list[ManualIssueSeverity] | None, Query()]


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) and value else "request-id-unavailable"


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _idempotency_key(value: str | None) -> str:
    if value is None:
        raise problem(
            status=400,
            code="IDEMPOTENCY_KEY_REQUIRED",
            title="Idempotency key required",
            detail="This ManualIssue mutation requires an Idempotency-Key header.",
        )
    return value


def _if_match(value: str | None) -> str:
    if value is None:
        raise problem(
            status=428,
            code="IF_MATCH_REQUIRED",
            title="Precondition required",
            detail="This ManualIssue mutation requires an If-Match header.",
        )
    return value


def _filters(
    *,
    q: str | None,
    dataset_id: str | None,
    version_id: str | None,
    episode_id: str | None,
    status: list[ManualIssueStatus] | None,
    issue_type: list[ManualIssueType] | None,
    severity: list[ManualIssueSeverity] | None,
    assignee_id: str | None,
) -> ManualIssueFilters:
    return ManualIssueFilters(
        query=q,
        dataset_id=dataset_id,
        version_id=version_id,
        episode_id=episode_id,
        statuses=tuple(status or ()),
        issue_types=tuple(issue_type or ()),
        severities=tuple(severity or ()),
        assignee_id=assignee_id,
    )


@router.get(
    ":page",
    operation_id="getManualIssuesPage",
    response_model=ManualIssuePageEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_manual_issues_page(
    project_id: str,
    region_code: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
    q: OptionalQueryText = None,
    dataset_id: OptionalQueryText = None,
    version_id: OptionalQueryText = None,
    episode_id: OptionalQueryText = None,
    status: RepeatedStatus = None,
    issue_type: RepeatedIssueType = None,
    severity: RepeatedSeverity = None,
    assignee_id: OptionalQueryText = None,
) -> ManualIssuePageEnvelope:
    _no_store(response)
    return service.page(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        filters=_filters(
            q=q,
            dataset_id=dataset_id,
            version_id=version_id,
            episode_id=episode_id,
            status=status,
            issue_type=issue_type,
            severity=severity,
            assignee_id=assignee_id,
        ),
        request_id=_request_id(request),
    )


@router.get(
    "",
    operation_id="listManualIssues",
    response_model=ManualIssueListEnvelope,
    responses=PROBLEM_RESPONSES,
)
def list_manual_issues(
    project_id: str,
    region_code: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
    q: OptionalQueryText = None,
    dataset_id: OptionalQueryText = None,
    version_id: OptionalQueryText = None,
    episode_id: OptionalQueryText = None,
    status: RepeatedStatus = None,
    issue_type: RepeatedIssueType = None,
    severity: RepeatedSeverity = None,
    assignee_id: OptionalQueryText = None,
    sort: ManualIssueSort = "updated_at:desc,id:desc",
    after: OptionalCursor = None,
    before: OptionalCursor = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> ManualIssueListEnvelope:
    _no_store(response)
    return service.list_issues(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        filters=_filters(
            q=q,
            dataset_id=dataset_id,
            version_id=version_id,
            episode_id=episode_id,
            status=status,
            issue_type=issue_type,
            severity=severity,
            assignee_id=assignee_id,
        ),
        sort=sort,
        after=after,
        before=before,
        limit=limit,
        request_id=_request_id(request),
    )


@router.post(
    "",
    operation_id="createManualIssue",
    response_model=ManualIssueDetailEnvelope,
    status_code=201,
    responses=PROBLEM_RESPONSES,
)
def create_manual_issue(
    project_id: str,
    region_code: str,
    command: CreateManualIssueCommand,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
    idempotency_key: IdempotencyKey = None,
) -> ManualIssueDetailEnvelope:
    outcome = service.create_issue(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        command=command,
        idempotency_key=_idempotency_key(idempotency_key),
        request_id=_request_id(request),
    )
    if outcome.replayed:
        response.status_code = 200
    response.headers["ETag"] = outcome.record.issue.etag
    response.headers["Idempotency-Replayed"] = "true" if outcome.replayed else "false"
    response.headers["Location"] = (
        f"/api/v1/projects/{project_id}/regions/{region_code}/manual-issues/{outcome.record.issue.id}"
    )
    _no_store(response)
    return service.detail_envelope(
        record=outcome.record.issue,
        auth=auth,
        request_id=_request_id(request),
    )


@router.get(
    "/{issue_id}",
    operation_id="getManualIssue",
    response_model=ManualIssueDetailEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_manual_issue(
    project_id: str,
    region_code: str,
    issue_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
) -> ManualIssueDetailEnvelope:
    record = service.get_issue(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        issue_id=issue_id,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = record.etag
    _no_store(response)
    return service.detail_envelope(record=record, auth=auth, request_id=_request_id(request))


@router.post(
    "/{issue_id}:triage",
    operation_id="triageManualIssue",
    response_model=ManualIssueDetailEnvelope,
    responses=PROBLEM_RESPONSES,
)
def triage_manual_issue(
    project_id: str,
    region_code: str,
    issue_id: str,
    command: TriageManualIssueCommand,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
) -> ManualIssueDetailEnvelope:
    outcome = service.triage_issue(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        issue_id=issue_id,
        command=command,
        if_match=_if_match(if_match),
        idempotency_key=_idempotency_key(idempotency_key),
        request_id=_request_id(request),
    )
    response.headers["ETag"] = outcome.record.issue.etag
    response.headers["Idempotency-Replayed"] = "true" if outcome.replayed else "false"
    _no_store(response)
    return service.detail_envelope(
        record=outcome.record.issue,
        auth=auth,
        request_id=_request_id(request),
    )


@router.post(
    "/{issue_id}/cleaning-drafts",
    operation_id="createCleaningDraftFromManualIssue",
    response_model=ManualIssueCreateDraftEnvelope,
    status_code=201,
    responses=PROBLEM_RESPONSES,
)
def create_cleaning_draft_from_manual_issue(
    project_id: str,
    region_code: str,
    issue_id: str,
    command: CreateDraftFromIssueCommand,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
) -> ManualIssueCreateDraftEnvelope:
    outcome = service.create_draft_from_issue(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        issue_id=issue_id,
        command=command,
        if_match=_if_match(if_match),
        idempotency_key=_idempotency_key(idempotency_key),
        request_id=_request_id(request),
    )
    if outcome.replayed or outcome.record.result.disposition != "CREATED":
        response.status_code = 200
    response.headers["ETag"] = outcome.record.issue_etag
    response.headers["Idempotency-Replayed"] = "true" if outcome.replayed else "false"
    _no_store(response)
    return service.draft_envelope(record=outcome.record, request_id=_request_id(request))


@router.post(
    "/{issue_id}:resolve",
    operation_id="resolveManualIssue",
    response_model=ManualIssueDetailEnvelope,
    responses=PROBLEM_RESPONSES,
)
def resolve_manual_issue(
    project_id: str,
    region_code: str,
    issue_id: str,
    command: ResolveManualIssueCommand,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
) -> ManualIssueDetailEnvelope:
    outcome = service.resolve_issue(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        issue_id=issue_id,
        command=command,
        if_match=_if_match(if_match),
        idempotency_key=_idempotency_key(idempotency_key),
        request_id=_request_id(request),
    )
    response.headers["ETag"] = outcome.record.issue.etag
    response.headers["Idempotency-Replayed"] = "true" if outcome.replayed else "false"
    _no_store(response)
    return service.detail_envelope(
        record=outcome.record.issue,
        auth=auth,
        request_id=_request_id(request),
    )
