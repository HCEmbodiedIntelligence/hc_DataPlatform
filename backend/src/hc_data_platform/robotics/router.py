"""Formal P15 robot-directory router."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from hc_data_platform.core.context import select_request_scope
from hc_data_platform.security.http import VerifiedAuth
from hc_data_platform.security.scope import ScopeGuard

from .models import (
    ChannelPage,
    ComponentLifecycleTransitionRequest,
    ComponentPage,
    CreateMaintenanceRecordRequest,
    CreateRobotComponentRequest,
    CreateRobotRequest,
    FramePage,
    GlobalRobotSearchPage,
    MaintenanceRecordEnvelope,
    MaintenanceRecordPage,
    RobotBootstrapEnvelope,
    RobotComponentMutationEnvelope,
    RobotLifecycleTransitionRequest,
    RobotPage,
    UpdateRobotComponentRequest,
    UpdateRobotRequest,
)
from .service import RoboticsService

router = APIRouter(prefix="/api/v1/projects/{project_id}/regions/{region_code}", tags=["robotics"])
_service = RoboticsService.in_memory()
PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {
        "description": "The robot-directory request could not be completed.",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    }
    for status in (401, 403, 404, 409, 412, 422)
}


def configure_robotics(service: RoboticsService) -> None:
    global _service
    _service = service


def get_robotics_service() -> RoboticsService:
    return _service


ServiceDependency = Annotated[RoboticsService, Depends(get_robotics_service)]
OrganizationHeader = Annotated[str, Header(alias="X-Organization-Id", min_length=1, max_length=128)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=256)]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=1, max_length=256)]
SearchCursor = Annotated[str | None, Query(max_length=16_384)]
SearchLimit = Annotated[int, Query(ge=1, le=20)]


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) and value else "request-id-unavailable"


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


def _select_robotics_scope(
    auth: VerifiedAuth,
    project_id: str,
    region_code: str,
    organization_id: str,
) -> None:
    """Bind PostgreSQL RLS to the authenticated robot-directory scope."""

    ScopeGuard.require(auth, project_id, region_code, organization_id)
    auth.require_capability("robot.read", project_id, organization_id)
    select_request_scope(project_id, region_code, organization_id=organization_id)


def _select_robotics_manage_scope(
    auth: VerifiedAuth,
    project_id: str,
    region_code: str,
    organization_id: str,
) -> None:
    ScopeGuard.require(auth, project_id, region_code, organization_id)
    auth.require_capability("robot.manage", project_id, organization_id)
    select_request_scope(project_id, region_code, organization_id=organization_id)


@router.get(
    "/search",
    operation_id="searchRobots",
    response_model=GlobalRobotSearchPage,
    responses=PROBLEM_RESPONSES,
)
def search_robots(
    project_id: str,
    region_code: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
    q: str = Query(min_length=1, max_length=256),
    cursor: SearchCursor = None,
    limit: SearchLimit = 10,
) -> GlobalRobotSearchPage:
    """Bounded robot discovery for the shell-wide search entry point."""

    _select_robotics_scope(auth, project_id, region_code, organization_id)
    _no_store(response)
    return service.search_robots(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        query=q,
        cursor=cursor,
        limit=limit,
        request_id=_request_id(request),
    )


@router.get(
    "/robots", operation_id="listRobots", response_model=RobotPage, responses=PROBLEM_RESPONSES
)
def list_robots(
    project_id: str,
    region_code: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
    q: str | None = Query(default=None, min_length=1, max_length=256),
    lifecycle_status: Literal["DRAFT", "ACTIVE", "MAINTENANCE", "DISABLED", "RETIRED"]
    | None = Query(default=None),
    connectivity_state: Literal["ONLINE", "OFFLINE", "DEGRADED"] | None = Query(default=None),
) -> RobotPage:
    _select_robotics_scope(auth, project_id, region_code, organization_id)
    _no_store(response)
    return service.list_robots(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        query=q,
        lifecycle_status=lifecycle_status,
        connectivity_state=connectivity_state,
        request_id=_request_id(request),
    )


@router.post(
    "/robots",
    operation_id="createRobot",
    response_model=RobotBootstrapEnvelope,
    responses=PROBLEM_RESPONSES,
    status_code=status.HTTP_201_CREATED,
)
def create_robot(
    project_id: str,
    region_code: str,
    command: CreateRobotRequest,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
    idempotency_key: IdempotencyKey,
) -> RobotBootstrapEnvelope:
    _select_robotics_manage_scope(auth, project_id, region_code, organization_id)
    _no_store(response)
    result = service.create_robot(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        command=command,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = result.data.etag
    response.headers["Location"] = (
        f"/api/v1/projects/{project_id}/regions/{region_code}/robots/{result.data.robot.id}/bootstrap"
    )
    return result


@router.patch(
    "/robots/{robot_id}",
    operation_id="updateRobot",
    response_model=RobotBootstrapEnvelope,
    responses=PROBLEM_RESPONSES,
)
def update_robot(
    project_id: str,
    region_code: str,
    robot_id: str,
    command: UpdateRobotRequest,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
) -> RobotBootstrapEnvelope:
    _select_robotics_manage_scope(auth, project_id, region_code, organization_id)
    _no_store(response)
    result = service.update_robot(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        robot_id=robot_id,
        expected_etag=if_match,
        command=command,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = result.data.etag
    return result


@router.post(
    "/robots/{robot_id}:transition",
    operation_id="transitionRobotLifecycle",
    response_model=RobotBootstrapEnvelope,
    responses=PROBLEM_RESPONSES,
)
def transition_robot_lifecycle(
    project_id: str,
    region_code: str,
    robot_id: str,
    command: RobotLifecycleTransitionRequest,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
) -> RobotBootstrapEnvelope:
    _select_robotics_manage_scope(auth, project_id, region_code, organization_id)
    _no_store(response)
    result = service.transition_robot(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        robot_id=robot_id,
        expected_etag=if_match,
        command=command,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = result.data.etag
    return result


@router.get(
    "/robots/{robot_id}/maintenance-records",
    operation_id="listRobotMaintenanceRecords",
    response_model=MaintenanceRecordPage,
    responses=PROBLEM_RESPONSES,
)
def list_robot_maintenance_records(
    project_id: str,
    region_code: str,
    robot_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
) -> MaintenanceRecordPage:
    _select_robotics_scope(auth, project_id, region_code, organization_id)
    _no_store(response)
    return service.maintenance_records(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        robot_id=robot_id,
        request_id=_request_id(request),
    )


@router.post(
    "/robots/{robot_id}/maintenance-records",
    operation_id="createRobotMaintenanceRecord",
    response_model=MaintenanceRecordEnvelope,
    responses=PROBLEM_RESPONSES,
    status_code=status.HTTP_201_CREATED,
)
def create_robot_maintenance_record(
    project_id: str,
    region_code: str,
    robot_id: str,
    command: CreateMaintenanceRecordRequest,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
    idempotency_key: IdempotencyKey,
) -> MaintenanceRecordEnvelope:
    _select_robotics_manage_scope(auth, project_id, region_code, organization_id)
    _no_store(response)
    return service.create_maintenance_record(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        robot_id=robot_id,
        command=command,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.post(
    "/robots/{robot_id}/components",
    operation_id="createRobotComponent",
    response_model=RobotComponentMutationEnvelope,
    responses=PROBLEM_RESPONSES,
    status_code=status.HTTP_201_CREATED,
)
def create_robot_component(
    project_id: str,
    region_code: str,
    robot_id: str,
    command: CreateRobotComponentRequest,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
) -> RobotComponentMutationEnvelope:
    _select_robotics_manage_scope(auth, project_id, region_code, organization_id)
    _no_store(response)
    result = service.create_component(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        robot_id=robot_id,
        expected_robot_etag=if_match,
        command=command,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = result.data.robot_etag
    return result


@router.patch(
    "/components/{component_id}",
    operation_id="updateRobotComponent",
    response_model=RobotComponentMutationEnvelope,
    responses=PROBLEM_RESPONSES,
)
def update_robot_component(
    project_id: str,
    region_code: str,
    component_id: str,
    command: UpdateRobotComponentRequest,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
) -> RobotComponentMutationEnvelope:
    _select_robotics_manage_scope(auth, project_id, region_code, organization_id)
    _no_store(response)
    result = service.update_component(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        component_id=component_id,
        expected_robot_etag=if_match,
        command=command,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = result.data.robot_etag
    return result


@router.post(
    "/components/{component_id}:transition",
    operation_id="transitionRobotComponentLifecycle",
    response_model=RobotComponentMutationEnvelope,
    responses=PROBLEM_RESPONSES,
)
def transition_robot_component_lifecycle(
    project_id: str,
    region_code: str,
    component_id: str,
    command: ComponentLifecycleTransitionRequest,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
) -> RobotComponentMutationEnvelope:
    _select_robotics_manage_scope(auth, project_id, region_code, organization_id)
    _no_store(response)
    result = service.transition_component(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        component_id=component_id,
        expected_robot_etag=if_match,
        command=command,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = result.data.robot_etag
    return result


@router.get(
    "/robots/{robot_id}/bootstrap",
    operation_id="getRobotBootstrap",
    response_model=RobotBootstrapEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_robot_bootstrap(
    project_id: str,
    region_code: str,
    robot_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
) -> RobotBootstrapEnvelope:
    _select_robotics_scope(auth, project_id, region_code, organization_id)
    _no_store(response)
    result = service.robot_bootstrap(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        robot_id=robot_id,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = result.data.etag
    return result


@router.get(
    "/robots/{robot_id}/components",
    operation_id="listRobotComponents",
    response_model=ComponentPage,
    responses=PROBLEM_RESPONSES,
)
def list_robot_components(
    project_id: str,
    region_code: str,
    robot_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
) -> ComponentPage:
    _select_robotics_scope(auth, project_id, region_code, organization_id)
    _no_store(response)
    return service.components(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        robot_id=robot_id,
        request_id=_request_id(request),
    )


@router.get(
    "/components/{component_id}/frames",
    operation_id="listComponentFrames",
    response_model=FramePage,
    responses=PROBLEM_RESPONSES,
)
def list_component_frames(
    project_id: str,
    region_code: str,
    component_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
) -> FramePage:
    _select_robotics_scope(auth, project_id, region_code, organization_id)
    _no_store(response)
    return service.frames(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        component_id=component_id,
        request_id=_request_id(request),
    )


@router.get(
    "/components/{component_id}/channels",
    operation_id="listComponentChannels",
    response_model=ChannelPage,
    responses=PROBLEM_RESPONSES,
)
def list_component_channels(
    project_id: str,
    region_code: str,
    component_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
) -> ChannelPage:
    _select_robotics_scope(auth, project_id, region_code, organization_id)
    _no_store(response)
    return service.channels(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        component_id=component_id,
        request_id=_request_id(request),
    )
