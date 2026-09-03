"""P11 server-owned CleaningDraft workbench routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response

from hc_data_platform.core.errors import problem
from hc_data_platform.security.http import VerifiedAuth

from .models import (
    CleaningDraftBootstrapEnvelope,
    CommitAcceptedEnvelope,
    CommitCleaningDraftCommand,
    CreateCleaningPreviewCommand,
    PreviewAcceptedEnvelope,
    ReviewFindingsEnvelope,
    SaveCleaningEdlCommand,
    SaveCleaningEdlEnvelope,
)
from .service import CleaningWorkbenchService

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/regions/{region_code}/cleaning-drafts",
    tags=["cleaning-workbench"],
)
_service = CleaningWorkbenchService.in_memory()

PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {
        "description": "The CleaningDraft workbench request could not be completed.",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    }
    for status in (400, 401, 403, 404, 409, 412, 422, 428, 500)
}


def configure_cleaning_workbench(service: CleaningWorkbenchService) -> None:
    global _service
    _service = service


def get_cleaning_workbench_service() -> CleaningWorkbenchService:
    return _service


ServiceDependency = Annotated[CleaningWorkbenchService, Depends(get_cleaning_workbench_service)]
OrganizationHeader = Annotated[str, Header(alias="X-Organization-Id", min_length=1, max_length=128)]
IfMatch = Annotated[str | None, Header(alias="If-Match", min_length=3, max_length=256)]
IdempotencyKey = Annotated[
    str | None, Header(alias="Idempotency-Key", min_length=1, max_length=256)
]


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) and value else "request-id-unavailable"


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _if_match(value: str | None) -> str:
    if value is None:
        raise problem(
            status=428,
            code="IF_MATCH_REQUIRED",
            title="Precondition required",
            detail="This CleaningDraft mutation requires an If-Match header.",
        )
    return value


def _idempotency_key(value: str | None) -> str:
    if value is None:
        raise problem(
            status=400,
            code="IDEMPOTENCY_KEY_REQUIRED",
            title="Idempotency key required",
            detail="This CleaningDraft command requires an Idempotency-Key header.",
        )
    return value


@router.get(
    "/{draft_id}/bootstrap",
    operation_id="getCleaningDraftBootstrap",
    response_model=CleaningDraftBootstrapEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_cleaning_draft_bootstrap(
    project_id: str,
    region_code: str,
    draft_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
) -> CleaningDraftBootstrapEnvelope:
    _no_store(response)
    result = service.bootstrap(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        draft_id=draft_id,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = result.data.draft.etag
    return result


@router.put(
    "/{draft_id}/edl",
    operation_id="saveCleaningEdl",
    response_model=SaveCleaningEdlEnvelope,
    responses=PROBLEM_RESPONSES,
)
def save_cleaning_edl(
    project_id: str,
    region_code: str,
    draft_id: str,
    command: SaveCleaningEdlCommand,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    if_match: IfMatch,
    service: ServiceDependency,
) -> SaveCleaningEdlEnvelope:
    _no_store(response)
    result = service.save_edl(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        draft_id=draft_id,
        if_match=_if_match(if_match),
        command=command,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = result.envelope.data.draft.etag
    if result.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return result.envelope


@router.post(
    "/{draft_id}/previews",
    operation_id="createCleaningPreview",
    response_model=PreviewAcceptedEnvelope,
    status_code=202,
    responses=PROBLEM_RESPONSES,
)
def create_cleaning_preview(
    project_id: str,
    region_code: str,
    draft_id: str,
    command: CreateCleaningPreviewCommand,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
) -> PreviewAcceptedEnvelope:
    _no_store(response)
    result = service.create_preview(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        draft_id=draft_id,
        if_match=_if_match(if_match),
        idempotency_key=_idempotency_key(idempotency_key),
        command=command,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = result.envelope.job.etag
    if result.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return result.envelope


@router.post(
    "/{draft_id}/commits",
    operation_id="commitCleaningDraft",
    response_model=CommitAcceptedEnvelope,
    status_code=202,
    responses=PROBLEM_RESPONSES,
)
def commit_cleaning_draft(
    project_id: str,
    region_code: str,
    draft_id: str,
    command: CommitCleaningDraftCommand,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
) -> CommitAcceptedEnvelope:
    _no_store(response)
    result = service.commit(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        draft_id=draft_id,
        if_match=_if_match(if_match),
        idempotency_key=_idempotency_key(idempotency_key),
        command=command,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = result.envelope.job.etag
    if result.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return result.envelope


@router.get(
    "/{draft_id}/review-findings",
    operation_id="getCleaningReviewFindings",
    response_model=ReviewFindingsEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_cleaning_review_findings(
    project_id: str,
    region_code: str,
    draft_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
) -> ReviewFindingsEnvelope:
    _no_store(response)
    return service.review_findings(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        draft_id=draft_id,
        request_id=_request_id(request),
    )
