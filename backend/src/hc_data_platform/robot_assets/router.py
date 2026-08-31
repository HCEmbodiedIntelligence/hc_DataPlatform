from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from hc_data_platform.core.context import select_organization_scope
from hc_data_platform.robotics.models import CreateRobotRequest
from hc_data_platform.security.http import VerifiedAuth

from .models import (
    BindOrganizationRobotModelRequest,
    OrganizationRobotBootstrapEnvelope,
    OrganizationRobotModelBinding,
    OrganizationRobotModelBindingPage,
    OrganizationRobotPage,
)
from .service import OrganizationRobotAssetService

router = APIRouter(
    prefix="/api/v1/organizations/{organization_id}/robots",
    tags=["organization-robot-assets"],
)
_service = OrganizationRobotAssetService.in_memory()

PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    code: {"description": "The organization robot asset request could not be completed."}
    for code in (401, 403, 404, 409, 412, 422)
}


def configure_organization_robot_assets(service: OrganizationRobotAssetService) -> None:
    global _service
    _service = service


def get_organization_robot_asset_service() -> OrganizationRobotAssetService:
    return _service


ServiceDependency = Annotated[
    OrganizationRobotAssetService, Depends(get_organization_robot_asset_service)
]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=256)]


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) and value else "request-id-unavailable"


def _prepare(organization_id: str, response: Response) -> None:
    select_organization_scope(organization_id)
    response.headers["Cache-Control"] = "private, no-store"


@router.get("", operation_id="listOrganizationRobots", response_model=OrganizationRobotPage)
def list_organization_robots(
    organization_id: str,
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    service: ServiceDependency,
    q: str | None = Query(default=None, min_length=1, max_length=256),
    lifecycle_status: Literal["DRAFT", "ACTIVE", "MAINTENANCE", "DISABLED", "RETIRED"]
    | None = Query(default=None),
    connectivity_state: Literal["ONLINE", "OFFLINE", "DEGRADED"]
    | None = Query(default=None),
) -> OrganizationRobotPage:
    _prepare(organization_id, response)
    return service.list_robots(
        auth=auth,
        organization_id=organization_id,
        query=q,
        lifecycle_status=lifecycle_status,
        connectivity_state=connectivity_state,
        request_id=_request_id(request),
    )


@router.post(
    "",
    operation_id="createOrganizationRobot",
    response_model=OrganizationRobotBootstrapEnvelope,
    status_code=status.HTTP_201_CREATED,
    responses=PROBLEM_RESPONSES,
)
def create_organization_robot(
    organization_id: str,
    command: CreateRobotRequest,
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
) -> OrganizationRobotBootstrapEnvelope:
    _prepare(organization_id, response)
    result = service.create_robot(
        auth=auth,
        organization_id=organization_id,
        command=command,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = result.data.etag
    response.headers["Location"] = (
        f"/api/v1/organizations/{organization_id}/robots/{result.data.robot.id}/bootstrap"
    )
    return result


@router.get(
    "/model-bindings",
    operation_id="listOrganizationRobotModelBindings",
    response_model=OrganizationRobotModelBindingPage,
    responses=PROBLEM_RESPONSES,
)
def list_organization_robot_model_bindings(
    organization_id: str,
    version_id: str,
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> OrganizationRobotModelBindingPage:
    _prepare(organization_id, response)
    return service.list_model_bindings(
        auth=auth,
        organization_id=organization_id,
        version_id=version_id,
        request_id=_request_id(request),
    )


@router.get(
    "/{robot_id}/bootstrap",
    operation_id="getOrganizationRobotBootstrap",
    response_model=OrganizationRobotBootstrapEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_organization_robot_bootstrap(
    organization_id: str,
    robot_id: str,
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> OrganizationRobotBootstrapEnvelope:
    _prepare(organization_id, response)
    result = service.get_robot(
        auth=auth,
        organization_id=organization_id,
        robot_id=robot_id,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = result.data.etag
    return result


@router.post(
    "/{robot_id}/model-bindings",
    operation_id="bindOrganizationRobotModel",
    response_model=OrganizationRobotModelBinding,
    responses=PROBLEM_RESPONSES,
)
def bind_organization_robot_model(
    organization_id: str,
    robot_id: str,
    command: BindOrganizationRobotModelRequest,
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
) -> OrganizationRobotModelBinding:
    _prepare(organization_id, response)
    return service.bind_model(
        auth=auth,
        organization_id=organization_id,
        robot_id=robot_id,
        command=command,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
