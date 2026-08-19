from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from hc_data_platform.core.context import select_request_scope
from hc_data_platform.security.http import VerifiedAuth
from hc_data_platform.security.scope import ScopeGuard

from .models import (
    DashboardActivityResponse,
    DashboardCoverageResponse,
    DashboardIdentifier,
    DashboardPendingItemsResponse,
    DashboardSnapshotResponse,
)
from .repository import DASHBOARD_CAPABILITY
from .service import DashboardService

router = APIRouter(prefix="/api/v1/projects/{project_id}/dashboard", tags=["dashboard"])

_service = DashboardService()


def configure_dashboard(service: DashboardService) -> None:
    global _service
    _service = service


def get_dashboard_service() -> DashboardService:
    return _service


Service = Annotated[DashboardService, Depends(get_dashboard_service)]
RegionCode = Annotated[
    str,
    Query(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="Exact authorized region; cross-region aggregation is not enabled.",
    ),
]
RangeStart = Annotated[datetime, Query(alias="from")]
RangeEnd = Annotated[datetime, Query(alias="to")]
TimezoneName = Annotated[
    str,
    Query(
        alias="timezone",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)*$",
    ),
]
Cursor = Annotated[str | None, Query(min_length=1, max_length=16_384)]
Limit = Annotated[int, Query(ge=1, le=100)]


def _authorize(auth: VerifiedAuth, project_id: str, region_code: str) -> None:
    ScopeGuard.require(auth, project_id, region_code)
    auth.require_capability(DASHBOARD_CAPABILITY, project_id)
    select_request_scope(project_id, region_code)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.get(
    "/snapshot",
    response_model=DashboardSnapshotResponse,
    operation_id="getDashboardSnapshot",
)
def get_dashboard_snapshot(
    project_id: DashboardIdentifier,
    region_code: RegionCode,
    range_start: RangeStart,
    range_end: RangeEnd,
    timezone_name: TimezoneName,
    response: Response,
    auth: VerifiedAuth,
    service: Service,
) -> DashboardSnapshotResponse:
    _authorize(auth, project_id, region_code)
    _no_store(response)
    return service.snapshot(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        range_start=range_start,
        range_end=range_end,
        timezone_name=timezone_name,
    )


@router.get(
    "/activity",
    response_model=DashboardActivityResponse,
    operation_id="getDashboardActivity",
)
def get_dashboard_activity(
    project_id: DashboardIdentifier,
    region_code: RegionCode,
    range_start: RangeStart,
    range_end: RangeEnd,
    timezone_name: TimezoneName,
    response: Response,
    auth: VerifiedAuth,
    service: Service,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> DashboardActivityResponse:
    _authorize(auth, project_id, region_code)
    _no_store(response)
    return service.activity(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        range_start=range_start,
        range_end=range_end,
        timezone_name=timezone_name,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/coverage",
    response_model=DashboardCoverageResponse,
    operation_id="getDashboardCoverage",
)
def get_dashboard_coverage(
    project_id: DashboardIdentifier,
    region_code: RegionCode,
    range_start: RangeStart,
    range_end: RangeEnd,
    timezone_name: TimezoneName,
    response: Response,
    auth: VerifiedAuth,
    service: Service,
) -> DashboardCoverageResponse:
    _authorize(auth, project_id, region_code)
    _no_store(response)
    return service.coverage(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        range_start=range_start,
        range_end=range_end,
        timezone_name=timezone_name,
    )


@router.get(
    "/pending-items",
    response_model=DashboardPendingItemsResponse,
    operation_id="getDashboardPendingItems",
)
def get_dashboard_pending_items(
    project_id: DashboardIdentifier,
    region_code: RegionCode,
    range_start: RangeStart,
    range_end: RangeEnd,
    timezone_name: TimezoneName,
    response: Response,
    auth: VerifiedAuth,
    service: Service,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> DashboardPendingItemsResponse:
    _authorize(auth, project_id, region_code)
    _no_store(response)
    return service.pending_items(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        range_start=range_start,
        range_end=range_end,
        timezone_name=timezone_name,
        cursor=cursor,
        limit=limit,
    )
