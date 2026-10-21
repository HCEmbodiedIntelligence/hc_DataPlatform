from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from hc_data_platform.core.context import select_organization_scope
from hc_data_platform.core.errors import problem
from hc_data_platform.robot_assets.models import BindOrganizationRobotModelRequest
from hc_data_platform.robot_assets.router import get_organization_robot_asset_service
from hc_data_platform.robot_assets.service import OrganizationRobotAssetService
from hc_data_platform.robotics.models import CreateRobotRequest
from hc_data_platform.security.http import VerifiedAuth, authorize_scope

from .models import CreateRobotIngestIdentity, IssueCredentialCommand
from .onboarding import (
    ACTIVITY_LEASE_SECONDS,
    ONLINE_TIMEOUT_SECONDS,
    project_robot,
    require_active_robot,
    robot_page,
)
from .onboarding_models import (
    ConnectionState,
    CredentialAvailability,
    OnboardingFacts,
    OnboardingPage,
    OnboardRobotCommand,
    OnboardRobotResult,
    PermissionState,
    RobotHeartbeat,
    RobotHeartbeatReceipt,
    RobotSegment,
    RobotSort,
    SuccessfulUploadVolume,
)
from .router import OrganizationId, RegionCode, RobotToken, Service, _no_store

router = APIRouter(prefix="/api/v1", tags=["robot-onboarding"])

Assets = Annotated[OrganizationRobotAssetService, Depends(get_organization_robot_asset_service)]


@router.get(
    "/projects/{project_id}/robot-ingest/onboarding",
    response_model=OnboardingPage,
    operation_id="listRobotOnboarding",
)
def list_onboarding(
    project_id: str,
    auth: VerifiedAuth,
    organization_id: OrganizationId,
    response: Response,
    service: Service,
    region_code: Annotated[str | None, Header(alias="X-Region-Code", max_length=128)] = None,
    q: Annotated[str, Query(max_length=200)] = "",
    connection: ConnectionState | None = None,
    permission: PermissionState | None = None,
    credential: CredentialAvailability | None = None,
    segment: RobotSegment | None = None,
    sort: RobotSort = "updated",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=10, le=50, json_schema_extra={"enum": [10, 20, 50]})] = 20,
) -> OnboardingPage:
    service._authorize_admin(auth, organization_id, project_id, manage=False)
    if page_size not in (10, 20, 50):
        raise problem(
            status=422,
            code="INVALID_PAGE_SIZE",
            title="每页条数无效",
            detail="请选择 10、20 或 50 条。",
        )
    _no_store(response)
    legacy_scopes: set[tuple[str, str]] = set()
    for org, project, granted_region in auth.organization_scope_triples:
        if org == organization_id and auth.has_capability("ingest_source.read", project, org):
            for region in (
                (granted_region,) if granted_region else (*auth.region_codes, region_code)
            ):
                if region:
                    legacy_scopes.add((project, region))
    return robot_page(
        service.repository.onboarding_facts(
            organization_id,
            legacy_scopes=tuple(sorted(legacy_scopes)),
        ),
        service._clock(),
        query=q,
        connection=connection,
        permission=permission,
        credential=credential,
        segment=segment,
        sort=sort,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/projects/{project_id}/robot-ingest/onboarding",
    response_model=OnboardRobotResult,
    operation_id="onboardRobot",
)
def onboard_robot(
    project_id: str,
    command: OnboardRobotCommand,
    auth: VerifiedAuth,
    organization_id: OrganizationId,
    request: Request,
    response: Response,
    service: Service,
    assets: Assets,
) -> OnboardRobotResult:
    service._authorize_admin(auth, organization_id, project_id)
    _no_store(response)
    request_id = getattr(request.state, "request_id", "robot-onboarding")
    if command.robot_id:
        robot_id = command.robot_id
    else:
        created = assets.create_robot(
            auth=auth,
            organization_id=organization_id,
            command=CreateRobotRequest(
                display_name=command.display_name or "",
                serial_no=command.serial_no or "",
                lifecycle_status="ACTIVE",
            ),
            idempotency_key=command.idempotency_key,
            request_id=request_id,
        )
        robot_id = created.data.robot.id
    # A replayed creation receipt may contain the pre-binding revision. Read current
    # facts before continuing, and never replay a lifecycle or identity enable command.
    bootstrap = assets.get_robot(
        auth=auth, organization_id=organization_id, robot_id=robot_id, request_id=request_id
    ).data
    existing = service.repository.find_identity(organization_id, robot_id)
    if existing is None:
        require_active_robot(bootstrap.robot.lifecycle_status)
    if existing is None and bootstrap.effective_model_binding is None:
        if not command.model_version_id:
            raise problem(
                status=409,
                code="ROBOT_MODEL_REQUIRED",
                title="机器人尚未绑定模型",
                detail=f"机器人 {robot_id} 已保留。请选择已发布模型后继续配置。",
            )
        assets.bind_model(
            auth=auth,
            organization_id=organization_id,
            robot_id=robot_id,
            command=BindOrganizationRobotModelRequest(
                version_id=command.model_version_id, robot_etag=bootstrap.etag
            ),
            idempotency_key=command.idempotency_key + ":model",
            request_id=request_id,
        )
        bootstrap = assets.get_robot(
            auth=auth, organization_id=organization_id, robot_id=robot_id, request_id=request_id
        ).data
    identity = service.create_identity(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        command=CreateRobotIngestIdentity(
            robot_id=robot_id,
            display_name=bootstrap.robot.display_name,
            allowed_formats=command.allowed_formats,
            allowed_transports=command.allowed_transports,
            upload_policy=command.upload_policy,
        ),
    )
    if command.generate_credential:
        identity = service.issue_credential(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            ingest_identity_id=identity.data.ingest_identity_id,
            command=IssueCredentialCommand(idempotency_key=command.idempotency_key),
            only_if_missing=True,
        )
    facts = OnboardingFacts(
        robot_id=robot_id,
        display_name=bootstrap.robot.display_name,
        serial_no=bootstrap.robot.serial_no,
        lifecycle_status=bootstrap.robot.lifecycle_status,
        model_version_id=(
            bootstrap.effective_model_binding.robot_model_version_id
            if bootstrap.effective_model_binding
            else None
        ),
        updated_at=identity.data.updated_at,
        identity=identity.data,
        credentials=service.list_credentials(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            ingest_identity_id=identity.data.ingest_identity_id,
        ),
    )
    return OnboardRobotResult(robot=project_robot(facts, service._clock()), identity=identity)


@router.post(
    "/robot-ingest/heartbeat",
    response_model=RobotHeartbeatReceipt,
    operation_id="recordRobotHeartbeat",
)
def robot_heartbeat(
    command: RobotHeartbeat, token: RobotToken, response: Response, service: Service
) -> RobotHeartbeatReceipt:
    auth = service.authenticate(token)
    select_organization_scope(auth.organization_id)
    now = service._clock()
    expires = (
        now + timedelta(seconds=ACTIVITY_LEASE_SECONDS) if command.activity != "IDLE" else None
    )
    service.repository.heartbeat(auth.organization_id, auth.ingest_identity_id, now, expires)
    _no_store(response)
    return RobotHeartbeatReceipt(
        robot_id=auth.authenticated_robot_id,
        observed_at=now,
        online_timeout_seconds=ONLINE_TIMEOUT_SECONDS,
        activity_expires_at=expires,
    )


@router.get(
    "/projects/{project_id}/robot-ingest/successful-volume",
    response_model=SuccessfulUploadVolume,
    operation_id="getSuccessfulUploadVolume",
)
def successful_volume(
    project_id: str,
    auth: VerifiedAuth,
    organization_id: OrganizationId,
    region_code: RegionCode,
    response: Response,
    service: Service,
    day: date | None = None,
    tz: str = "Asia/Shanghai",
    robot_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> SuccessfulUploadVolume:
    authorize_scope(auth, project_id, "upload.read", region_code, organization_id)
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise problem(
            status=422,
            code="INVALID_TIMEZONE",
            title="时区无效",
            detail="请使用 IANA 时区，例如 Asia/Shanghai。",
        ) from exc
    selected = day or service._clock().astimezone(zone).date()
    start = datetime.combine(selected, time.min, zone).astimezone(timezone.utc)
    end = datetime.combine(selected + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
    size, count = service.repository.successful_volume(
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        start=start,
        end=end,
        robot_id=robot_id,
    )
    _no_store(response)
    return SuccessfulUploadVolume(
        day=selected,
        timezone=tz,
        from_inclusive=start,
        to_exclusive=end,
        raw_bytes=size,
        upload_count=count,
    )
