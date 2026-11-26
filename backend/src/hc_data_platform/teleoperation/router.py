import base64
import hashlib
import hmac
import time
from typing import cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response

from hc_data_platform.collection_tasks.router import Service as TaskService
from hc_data_platform.core.context import select_organization_scope
from hc_data_platform.core.errors import ProblemException, problem
from hc_data_platform.robot_ingest.router import RobotToken, Service
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.http import VerifiedAuth, authorize_scope

from . import capture
from .models import (
    Assignment,
    CaptureCommand,
    CaptureView,
    Endpoint,
    IceServer,
    OperatorState,
    OperatorSync,
    RobotPresence,
    RobotSync,
    Session,
)
from .store import LEASE, PresenceStore


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(prefix="/api/v1", tags=["teleoperation"], dependencies=[Depends(no_store)])
ROBOTS = "/organizations/{organization_id}/teleoperation/robots"
SESSIONS = ROBOTS + "/{robot_id}/sessions"


def store(request: Request) -> PresenceStore:
    return cast(PresenceStore, request.app.state.teleoperation)


def conflict(detail: str) -> ProblemException:
    return problem(
        status=409, code="TELEOPERATION_CONFLICT", title="Teleoperation conflict", detail=detail
    )


def authorize(auth: AuthContext, org: str, capability: str) -> None:
    auth.require_organization_capability(org, capability)
    select_organization_scope(org)


def authorize_robot(auth: AuthContext, org: str, robot: str) -> None:
    from hc_data_platform.security.capabilities import resource_capability

    exact = resource_capability("teleoperation.connect", robot)
    authorize(
        auth,
        org,
        exact if auth.has_organization_capability(org, exact) else "teleoperation.connect",
    )


def ice_servers(request: Request, session_id: UUID) -> list[IceServer]:
    settings = request.app.state.settings
    servers = [IceServer(urls=url) for url in settings.teleoperation_stun_urls]
    if settings.teleoperation_turn_secret:
        name = f"{int(time.time()) + 3600}:{session_id}"
        key = settings.teleoperation_turn_secret.get_secret_value().encode()
        credential = base64.b64encode(hmac.digest(key, name.encode(), hashlib.sha1)).decode()
        servers.extend(
            IceServer(urls=url, username=name, credential=credential)
            for url in settings.teleoperation_turn_urls
        )
    return servers


@router.get(ROBOTS, operation_id="listTeleoperationRobots")
def list_robots(organization_id: str, auth: VerifiedAuth, request: Request) -> list[RobotPresence]:
    """List endpoints with a current VR/WebRTC heartbeat in this organization."""
    authorize(auth, organization_id, "robot.read")
    return store(request).online(organization_id, time.time())


@router.post(
    SESSIONS,
    operation_id="connectTeleoperationRobot",
    status_code=201,
    response_model_exclude_none=True,
)
def connect(
    organization_id: str, robot_id: str, auth: VerifiedAuth, request: Request
) -> Assignment:
    """Reserve one online robot for this operator. Synchronize before the 20 s lease expires."""
    authorize_robot(auth, organization_id, robot_id)
    with store(request).edit(organization_id, robot_id) as row:
        now = time.time()
        if not row or row.seen <= now - LEASE:
            raise conflict("机器人已离线")
        if row.session and row.session.expires > now:
            raise conflict("机器人正在被占用")
        row.session = Session(id=uuid4(), owner=auth.subject_id, expires=now + LEASE)
        return Assignment(
            session_id=row.session.id, ice_servers=ice_servers(request, row.session.id)
        )


def owned(row: Endpoint | None, session_id: UUID, owner: str) -> Session:
    now = time.time()
    if (
        not row
        or row.seen <= now - LEASE
        or not row.session
        or row.session.id != session_id
        or row.session.owner != owner
        or row.session.expires <= now
    ):
        raise conflict("会话已结束或机器人已离线，请重新连接")
    return row.session


@router.post(SESSIONS + "/{session_id}/sync", operation_id="syncTeleoperationOperator")
def sync_operator(
    organization_id: str,
    robot_id: str,
    session_id: UUID,
    command: OperatorSync,
    auth: VerifiedAuth,
    request: Request,
) -> OperatorState:
    """Publish a complete (non-trickle ICE) SDP offer once and renew the operator lease."""
    authorize_robot(auth, organization_id, robot_id)
    with store(request).edit(organization_id, robot_id) as row:
        session = owned(row, session_id, auth.subject_id)
        if command.offer:
            if session.offer and command.offer != session.offer:
                raise conflict("会话中不可替换 SDP offer")
            session.offer = command.offer
        session.expires = time.time() + LEASE
        return OperatorState(answer=session.answer)


@router.delete(
    SESSIONS + "/{session_id}",
    operation_id="releaseTeleoperationRobot",
    response_model=None,
    status_code=204,
)
def disconnect(
    organization_id: str, robot_id: str, session_id: UUID, auth: VerifiedAuth, request: Request
) -> None:
    authorize_robot(auth, organization_id, robot_id)
    with store(request).edit(organization_id, robot_id) as row:
        owned(row, session_id, auth.subject_id)
        assert row is not None
        row.session = None


CAPTURE = ROBOTS + "/{robot_id}/capture"


@router.get(CAPTURE, operation_id="getTeleoperationCapture")
def get_capture(
    organization_id: str, robot_id: str, project_id: str, auth: VerifiedAuth, request: Request
) -> CaptureView:
    authorize_robot(auth, organization_id, robot_id)
    authorize_scope(auth, project_id, "collection_task.read", organization_id=organization_id)
    select_organization_scope(organization_id)
    with store(request).edit(organization_id, robot_id) as row:
        return capture.view(row, auth.subject_id, project_id, time.time()) if row else CaptureView()


@router.post(CAPTURE, operation_id="commandTeleoperationCapture")
def issue_capture(
    organization_id: str,
    robot_id: str,
    command: CaptureCommand,
    auth: VerifiedAuth,
    request: Request,
    tasks: TaskService,
) -> CaptureView:
    authorize_robot(auth, organization_id, robot_id)
    authorize_scope(
        auth, command.project_id, "collection_task.read", organization_id=organization_id
    )
    authorize_scope(auth, command.project_id, "ingest.import", organization_id=organization_id)
    if command.action == "start":
        task = tasks.detail(organization_id, command.project_id, command.task_id).public()
        if task.status.value != "ACTIVE":
            raise conflict("任务已关闭或取消")
    select_organization_scope(organization_id)
    with store(request).edit(organization_id, robot_id) as row:
        owned(row, command.session_id, auth.subject_id)
        assert row is not None
        now = time.time()
        try:
            capture.enqueue(row, command, auth.subject_id, now)
        except ValueError as exc:
            raise conflict(str(exc)) from exc
        return capture.view(row, auth.subject_id, command.project_id, now)


@router.post(
    "/robot-ingest/teleoperation/sync",
    operation_id="syncTeleoperationRobot",
    response_model_exclude_none=True,
)
def sync_robot(
    command: RobotSync, token: RobotToken, service: Service, request: Request
) -> Assignment:
    """Advertise a VR-capable robot, poll for offers and publish the matching SDP answer."""
    auth = service.authenticate(token)
    org, robot = auth.organization_id, auth.authenticated_robot_id
    select_organization_scope(org)
    initial = Endpoint(instance_id=command.instance_id, name=command.name, seen=time.time())
    with store(request).edit(org, robot, initial=initial) as row:
        assert row is not None
        now = time.time()
        if row.seen <= now - LEASE:
            row.session = None
            row.instance_id = command.instance_id
        if row.instance_id != command.instance_id:
            raise conflict("该机器人已有在线端点")
        row.name, row.seen = command.name, now
        if row.session and row.session.expires <= now:
            row.session = None
        row.capture_project = command.capture_project
        row.capture_tasks = command.capture_tasks
        session = row.session
        if command.capture_result:
            if not row.capture or command.session_id != row.capture.command.session_id:
                raise conflict("录制回执不属于该会话")
            try:
                capture.acknowledge(row, command.capture_result)
            except ValueError as exc:
                raise conflict(str(exc)) from exc
        if command.answer or command.release:
            if not session or session.id != command.session_id:
                raise conflict("机器人会话已变更")
            if command.release:
                row.session = None
                return Assignment()
            if not session.offer or (session.answer and session.answer != command.answer):
                raise conflict("当前会话无法接受该 SDP answer")
            session.answer = command.answer
        if not session:
            return Assignment()
        slot = row.capture
        pending = None
        if (
            slot
            and not slot.receipt
            and slot.command.session_id == session.id
            and slot.instance_id == command.instance_id
            and slot.expires > now
            and row.capture_project == slot.command.project_id
            and row.capture_tasks is not None
        ):
            slot.delivered = True
            pending = slot.command
        return Assignment(
            capture_command=pending,
            session_id=session.id,
            offer=None if session.answer else session.offer,
            ice_servers=ice_servers(request, session.id) if not session.answer else [],
        )
