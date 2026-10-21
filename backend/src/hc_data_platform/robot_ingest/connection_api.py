from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response

from hc_data_platform.capture_schemes.references import PrepareRecording
from hc_data_platform.capture_schemes.responses import CaptureRecordingSnapshot
from hc_data_platform.security.http import VerifiedAuth

from .connections import (
    ConnectionTicket,
    RedeemConnection,
    RedeemedConnection,
    RobotConnectionInfo,
    RobotTaskPage,
    SelectableTask,
    TaskSelection,
)
from .router import OrganizationId, RobotToken, Service, _no_store

router = APIRouter(prefix="/api/v1", tags=["robot-connections"])


@router.post(
    "/robot-ingest/tasks/{task_id}:prepare-recording",
    operation_id="prepareRobotTaskRecording",
    response_model=CaptureRecordingSnapshot,
    response_model_exclude_unset=True,
)
def prepare_task_recording(
    task_id: str,
    command: PrepareRecording,
    token: RobotToken,
    service: Service,
    response: Response,
) -> dict:
    _no_store(response)
    return service.connections.prepare_recording(service.authenticate(token), task_id, command)


@router.post(
    "/projects/{project_id}/robot-ingest/identities/{identity_id}/connections",
    response_model=ConnectionTicket,
)
def issue_connection(
    project_id: str,
    identity_id: str,
    auth: VerifiedAuth,
    organization_id: OrganizationId,
    service: Service,
    response: Response,
):
    _no_store(response)
    return service.connections.issue(auth, organization_id, project_id, identity_id)


@router.post("/robot-ingest/connections:redeem", response_model=RedeemedConnection)
def redeem_connection(command: RedeemConnection, service: Service, response: Response):
    _no_store(response)
    return service.connections.redeem(command)


@router.get("/robot-ingest/me", response_model=RobotConnectionInfo)
def machine_identity(token: RobotToken, service: Service, response: Response):
    _no_store(response)
    return service.connections.me(service.authenticate(token))


@router.get("/robot-ingest/tasks", response_model=RobotTaskPage)
def machine_tasks(
    token: RobotToken,
    service: Service,
    response: Response,
    source_format: Literal["MCAP", "LEROBOT_V3", "CAPTURE_BUNDLE"] = "MCAP",
    q: Annotated[str, Query(max_length=200)] = "",
    project_id: Annotated[str | None, Query(max_length=128)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
):
    _no_store(response)
    return service.connections.tasks(
        service.authenticate(token), source_format, q, project_id, page, page_size
    )


@router.post("/robot-ingest/tasks/{task_id}:preflight", response_model=SelectableTask)
def machine_task(
    task_id: str, command: TaskSelection, token: RobotToken, service: Service, response: Response
):
    _no_store(response)
    return service.connections.task(
        service.authenticate(token),
        task_id,
        command.source_format,
        command.local_robot,
        command.require_processing_config,
    )
