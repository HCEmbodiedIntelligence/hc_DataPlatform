"""Formal P10 read-only CleaningDraft routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from hc_data_platform.security.http import VerifiedAuth

from .models import (
    CleaningDraftDetailEnvelope,
    CleaningDraftEventsEnvelope,
    CleaningDraftListEnvelope,
    CleaningDraftScopeFilter,
    CleaningDraftSort,
    CleaningDraftStatusFilter,
    CleaningDraftSummaryEnvelope,
)
from .repository import CleaningDraftFilters
from .service import CleaningDraftService

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/regions/{region_code}/cleaning-drafts",
    tags=["cleaning-drafts"],
)
_service = CleaningDraftService.in_memory()

PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {
        "description": "The CleaningDraft read request could not be completed.",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    }
    for status in (400, 401, 403, 404, 409, 422, 500)
}


def configure_cleaning_drafts(service: CleaningDraftService) -> None:
    global _service
    _service = service


def get_cleaning_draft_service() -> CleaningDraftService:
    return _service


ServiceDependency = Annotated[CleaningDraftService, Depends(get_cleaning_draft_service)]
OrganizationHeader = Annotated[str, Header(alias="X-Organization-Id", min_length=1, max_length=128)]
OptionalQueryText = Annotated[str | None, Query(min_length=1, max_length=256)]
OptionalCursor = Annotated[str | None, Query(min_length=16, max_length=16_384)]
RepeatedCode = Annotated[list[str] | None, Query()]


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) and value else "request-id-unavailable"


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _filters(
    *,
    scope: CleaningDraftScopeFilter,
    status: CleaningDraftStatusFilter,
    q: str | None,
    dataset_id: str | None,
    base_version_id: str | None,
    episode_id: str | None,
    robot_id: str | None,
    creator_id: str | None,
    updated_from: datetime | None,
    updated_to: datetime | None,
    preview_status: list[str] | None,
    commit_status: list[str] | None,
    version_review_status: list[str] | None,
    finding_type: list[str] | None,
    finding_severity: list[str] | None,
) -> CleaningDraftFilters:
    return CleaningDraftFilters(
        scope=scope,
        status=status,
        query=q,
        dataset_id=dataset_id,
        base_version_id=base_version_id,
        episode_id=episode_id,
        robot_id=robot_id,
        creator_id=creator_id,
        updated_from=updated_from,
        updated_to=updated_to,
        preview_statuses=tuple(preview_status or ()),
        commit_statuses=tuple(commit_status or ()),
        version_review_statuses=tuple(version_review_status or ()),
        finding_types=tuple(finding_type or ()),
        finding_severities=tuple(finding_severity or ()),
    )


@router.get(
    "",
    operation_id="listCleaningDrafts",
    response_model=CleaningDraftListEnvelope,
    responses=PROBLEM_RESPONSES,
)
def list_cleaning_drafts(
    project_id: str,
    region_code: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
    scope: CleaningDraftScopeFilter = "all",
    status: CleaningDraftStatusFilter = "active",
    q: OptionalQueryText = None,
    dataset_id: OptionalQueryText = None,
    base_version_id: OptionalQueryText = None,
    episode_id: OptionalQueryText = None,
    robot_id: OptionalQueryText = None,
    creator_id: OptionalQueryText = None,
    updated_from: datetime | None = None,
    updated_to: datetime | None = None,
    preview_status: RepeatedCode = None,
    commit_status: RepeatedCode = None,
    version_review_status: RepeatedCode = None,
    finding_type: RepeatedCode = None,
    finding_severity: RepeatedCode = None,
    sort: CleaningDraftSort = "updated_at:desc,id:desc",
    after: OptionalCursor = None,
    before: OptionalCursor = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> CleaningDraftListEnvelope:
    _no_store(response)
    return service.list_drafts(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        filters=_filters(
            scope=scope,
            status=status,
            q=q,
            dataset_id=dataset_id,
            base_version_id=base_version_id,
            episode_id=episode_id,
            robot_id=robot_id,
            creator_id=creator_id,
            updated_from=updated_from,
            updated_to=updated_to,
            preview_status=preview_status,
            commit_status=commit_status,
            version_review_status=version_review_status,
            finding_type=finding_type,
            finding_severity=finding_severity,
        ),
        sort=sort,
        after=after,
        before=before,
        limit=limit,
        request_id=_request_id(request),
    )


@router.get(
    ":summary",
    operation_id="getCleaningDraftSummary",
    response_model=CleaningDraftSummaryEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_cleaning_draft_summary(
    project_id: str,
    region_code: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
    scope: CleaningDraftScopeFilter = "all",
    status: CleaningDraftStatusFilter = "active",
    q: OptionalQueryText = None,
    dataset_id: OptionalQueryText = None,
    base_version_id: OptionalQueryText = None,
    episode_id: OptionalQueryText = None,
    robot_id: OptionalQueryText = None,
    creator_id: OptionalQueryText = None,
    updated_from: datetime | None = None,
    updated_to: datetime | None = None,
    preview_status: RepeatedCode = None,
    commit_status: RepeatedCode = None,
    version_review_status: RepeatedCode = None,
    finding_type: RepeatedCode = None,
    finding_severity: RepeatedCode = None,
) -> CleaningDraftSummaryEnvelope:
    _no_store(response)
    return service.summary(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        filters=_filters(
            scope=scope,
            status=status,
            q=q,
            dataset_id=dataset_id,
            base_version_id=base_version_id,
            episode_id=episode_id,
            robot_id=robot_id,
            creator_id=creator_id,
            updated_from=updated_from,
            updated_to=updated_to,
            preview_status=preview_status,
            commit_status=commit_status,
            version_review_status=version_review_status,
            finding_type=finding_type,
            finding_severity=finding_severity,
        ),
        request_id=_request_id(request),
    )


@router.get(
    "/{draft_id}/summary",
    operation_id="getCleaningDraftDetail",
    response_model=CleaningDraftDetailEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_cleaning_draft_detail(
    project_id: str,
    region_code: str,
    draft_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
) -> CleaningDraftDetailEnvelope:
    _no_store(response)
    return service.detail(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        draft_id=draft_id,
        request_id=_request_id(request),
    )


@router.get(
    "/{draft_id}/events",
    operation_id="listCleaningDraftEvents",
    response_model=CleaningDraftEventsEnvelope,
    responses=PROBLEM_RESPONSES,
)
def list_cleaning_draft_events(
    project_id: str,
    region_code: str,
    draft_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
    after: OptionalCursor = None,
    before: OptionalCursor = None,
    limit: int = Query(default=10, ge=1, le=100),
) -> CleaningDraftEventsEnvelope:
    _no_store(response)
    return service.events(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        draft_id=draft_id,
        after=after,
        before=before,
        limit=limit,
        request_id=_request_id(request),
    )
