"""One row per organization robot, independent permissions and observations."""

from datetime import datetime, timedelta, timezone

from hc_data_platform.core.errors import problem

from .models import CredentialState
from .onboarding_models import (
    ConnectionState,
    CredentialAvailability,
    OnboardingFacts,
    OnboardingFault,
    OnboardingPage,
    OnboardingRobot,
    OnboardingSummary,
    PermissionState,
    RobotSegment,
    RobotSort,
)

ONLINE_TIMEOUT_SECONDS = 300
ACTIVITY_LEASE_SECONDS = 900


def project_robot(facts: OnboardingFacts, now: datetime) -> OnboardingRobot:
    identity = facts.identity
    permission: PermissionState = "NOT_ENABLED" if identity is None else identity.state.value
    valid = any(
        credential.state is CredentialState.ACTIVE
        and (credential.expires_at is None or credential.expires_at > now)
        for credential in facts.credentials
    )
    availability: CredentialAvailability = (
        "AVAILABLE"
        if valid
        else "UNAVAILABLE"
        if facts.credentials or (identity and identity.credential_revision)
        else "NOT_ISSUED"
    )
    seen = identity.last_seen_at if identity else None
    state: ConnectionState
    if seen is not None:
        state = (
            "UNKNOWN"
            if seen > now
            else "ONLINE"
            if now - seen < timedelta(seconds=ONLINE_TIMEOUT_SECONDS)
            else "OFFLINE"
        )
    else:
        state = (
            "UNKNOWN"
            if identity and identity.credential_revision and identity.observation_started_at is None
            else "UNVERIFIED"
        )
    disabled = permission == "DISABLED" or facts.lifecycle_status in {
        "DISABLED",
        "RETIRED",
        "MAINTENANCE",
    }
    pending = not disabled and (
        identity is None
        or not facts.model_version_id
        or facts.lifecycle_status != "ACTIVE"
        or seen is None
    )
    faults: list[OnboardingFault] = []
    if identity and not disabled and not pending:
        if not valid:
            times = [c.revoked_at or c.expires_at or c.issued_at for c in facts.credentials]
            faults.append(
                OnboardingFault(
                    code="NO_USABLE_CREDENTIAL",
                    reason="没有未过期且未撤销的上传凭据",
                    occurred_at=max(times) if times else identity.updated_at,
                    action="生成或更换上传凭据，并重新配置采集端",
                )
            )
        if identity.last_auth_failure_at and (seen is None or identity.last_auth_failure_at > seen):
            faults.append(
                OnboardingFault(
                    code=identity.last_auth_failure_code or "AUTHENTICATION_FAILED",
                    reason="设备使用的上传凭据认证失败",
                    occurred_at=identity.last_auth_failure_at,
                    action="检查采集端凭据；成功认证后自动恢复",
                )
            )
        if (
            state == "OFFLINE"
            and identity.activity_expires_at
            and seen
            and identity.activity_expires_at > seen + timedelta(seconds=ONLINE_TIMEOUT_SECONDS)
        ):
            faults.append(
                OnboardingFault(
                    code="ACTIVE_COMMUNICATION_TIMEOUT",
                    reason="设备报告采集或上传中，通信已超时",
                    occurred_at=seen + timedelta(seconds=ONLINE_TIMEOUT_SECONDS),
                    action="检查采集端网络和进程；恢复有效设备通信后自动清除",
                )
            )
    return OnboardingRobot(
        **facts.model_dump(),
        upload_permission=permission,
        credential_availability=availability,
        connection_state=state,
        last_communication_at=seen,
        pending=pending,
        faults=tuple(faults),
    )


def robot_page(
    facts: tuple[OnboardingFacts, ...],
    now: datetime,
    *,
    query: str = "",
    connection: ConnectionState | None = None,
    permission: PermissionState | None = None,
    credential: CredentialAvailability | None = None,
    segment: RobotSegment | None = None,
    sort: RobotSort = "updated",
    page: int = 1,
    page_size: int = 20,
) -> OnboardingPage:
    robots = tuple(project_robot(item, now) for item in facts)
    unknown = sum(item.connection_state == "UNKNOWN" for item in robots)
    summary = OnboardingSummary(
        total_count=len(robots),
        online_count=None if unknown else sum(item.connection_state == "ONLINE" for item in robots),
        pending_count=sum(item.pending for item in robots),
        attention_count=sum(bool(item.faults) for item in robots),
        unknown_count=unknown,
    )
    search = query.strip().casefold()
    matches = [
        item
        for item in robots
        if (
            not search
            or search in f"{item.display_name} {item.serial_no} {item.robot_id}".casefold()
        )
        and (connection is None or item.connection_state == connection)
        and (permission is None or item.upload_permission == permission)
        and (credential is None or item.credential_availability == credential)
        and (segment != "online" or item.connection_state == "ONLINE")
        and (segment != "pending" or item.pending)
        and (segment != "attention" or bool(item.faults))
    ]
    if sort == "name":
        matches.sort(key=lambda item: (item.display_name.casefold(), item.robot_id))
    elif sort == "communication":
        matches.sort(
            key=lambda item: (
                item.last_communication_at or datetime.min.replace(tzinfo=timezone.utc),
                item.robot_id,
            ),
            reverse=True,
        )
    else:
        matches.sort(
            key=lambda item: (
                max(item.updated_at, item.identity.updated_at)
                if item.identity
                else item.updated_at,
                item.robot_id,
            ),
            reverse=True,
        )
    return OnboardingPage(
        items=tuple(matches[(page - 1) * page_size : page * page_size]),
        total=len(matches),
        page=page,
        page_size=page_size,
        summary=summary,
        observed_at=now,
        online_timeout_seconds=ONLINE_TIMEOUT_SECONDS,
    )


def require_active_robot(lifecycle: str) -> None:
    if lifecycle != "ACTIVE":
        raise problem(
            status=409,
            code="ROBOT_ONBOARDING_LIFECYCLE",
            title="机器人尚未激活或已停用",
            detail="请在机器人实例详情确认生命周期；接入操作不会自动激活设备。",
        )
