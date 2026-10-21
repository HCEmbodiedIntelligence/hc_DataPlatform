"""Short lived device pairing and minimal machine task discovery.

Tickets store digests, never bearer secrets. A durable claim fixes the request,
local configuration and credential ID before issuance. Issuance has its own
unique receipt, so a crash between the two writes is safely recoverable.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta
from typing import Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import Field

from hc_data_platform.capture_schemes.ports import RecordingPlans
from hc_data_platform.core.context import select_organization_scope
from hc_data_platform.core.errors import ProblemException, problem

from .models import (
    CredentialState,
    Identifier,
    IdentityState,
    RobotCredentialSummary,
    StrictModel,
    UploadTarget,
)


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def reject(code: str, detail: str, status: int = 409):
    raise problem(status=status, code=code, title=detail, detail=detail)


class LocalRobot(StrictModel):
    local_robot_id: Identifier
    profile_id: Identifier
    config_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    serial_no: str | None = Field(default=None, max_length=128)
    model_version_id: Identifier | None = None


class RedeemConnection(StrictModel):
    ticket: str = Field(min_length=32, max_length=256, repr=False)
    request_id: str = Field(min_length=16, max_length=128)
    local_robot: LocalRobot


class TaskSelection(StrictModel):
    require_processing_config: bool = False
    source_format: Literal["MCAP", "LEROBOT_V3", "CAPTURE_BUNDLE"]
    local_robot: LocalRobot | None = None


class ConnectionTicket(StrictModel):
    ticket: str = Field(repr=False)
    expires_at: datetime
    expires_in_seconds: int


class RobotConnectionInfo(StrictModel):
    organization_id: Identifier
    robot_id: Identifier
    ingest_identity_id: Identifier
    robot_name: str
    platform_name: str
    allowed_formats: tuple[str, ...]
    model_version_id: Identifier
    local_robot: LocalRobot | None


class RedeemedConnection(RobotConnectionInfo):
    token: str = Field(repr=False)


class TaskOwnership(StrictModel):
    organization_id: Identifier
    project_id: Identifier
    dataset_id: Identifier
    region_code: Identifier
    collection_task_id: Identifier


class SelectableTask(StrictModel):
    processing_config: dict | None = None
    capture_plan: dict | None = None
    collection_task_id: Identifier
    project_id: Identifier
    name: str
    task_code: str
    description: str
    project_name: str
    selectable: bool
    reason: str | None
    ownership: TaskOwnership


class TaskProject(StrictModel):
    id: Identifier
    name: str


class RobotTaskPage(StrictModel):
    items: tuple[SelectableTask, ...]
    total: int
    page: int
    page_size: int
    projects: tuple[TaskProject, ...]


def policy(identity) -> str:
    return digest(
        {
            "formats": identity.allowed_formats,
            "transports": identity.allowed_transports,
            "policy": identity.upload_policy.model_dump(mode="json"),
        }
    )


def robot_info(service, identity):
    select_organization_scope(identity.organization_id)
    facts = next(
        (
            r
            for r in service.repository.onboarding_facts(identity.organization_id)
            if r.robot_id == identity.robot_id
        ),
        None,
    )
    if facts is None or facts.lifecycle_status != "ACTIVE":
        reject("ROBOT_IDENTITY_DISABLED", "机器人已停用或不存在")
    if not facts.model_version_id:
        reject("ROBOT_MODEL_REQUIRED", "请先在平台为机器人绑定已发布模型")
    if not service.connections.store.model_ready(identity.organization_id, facts.model_version_id):
        reject("ROBOT_MODEL_UNAVAILABLE", "机器人模型尚未发布或模型资产不可用，请检查平台配置")
    return facts


class Connections:
    def __init__(self, service, *, capture_plans: RecordingPlans | None = None):
        from .connection_repository import ConnectionStore

        self.service = service
        self.store = ConnectionStore(service.repository)
        self.capture_plans = capture_plans

    def issue(self, auth, organization_id, project_id, identity_id):
        s = self.service
        s._authorize_admin(auth, organization_id, project_id)
        identity = s._require_identity(organization_id, identity_id)
        if identity.state is not IdentityState.ENABLED:
            reject("ROBOT_IDENTITY_DISABLED", "请先恢复机器人上传权限")
        facts = robot_info(s, identity)
        ticket = secrets.token_urlsafe(48)
        now = s._clock()
        expires = now + timedelta(minutes=10)
        self.store.insert(
            digest(ticket),
            {
                "ticket_id": uuid4().hex,
                "organization_id": organization_id,
                "identity_id": identity_id,
                "robot_id": identity.robot_id,
                "policy": policy(identity),
                "model_version_id": facts.model_version_id,
                "expires_at": expires.isoformat(),
                "claim": None,
            },
        )
        return {"ticket": ticket, "expires_at": expires, "expires_in_seconds": 600}

    def redeem(self, command: RedeemConnection):
        s = self.service
        now = s._clock()
        local = command.local_robot.model_dump(mode="json")
        with self.store.locked(digest(command.ticket)) as ticket:
            if ticket is None:
                reject("CONNECTION_TICKET_INVALID", "连接链接无效", 401)
            select_organization_scope(ticket["organization_id"])
            identity = s._require_identity(ticket["organization_id"], ticket["identity_id"])
            facts = robot_info(s, identity)
            if (
                identity.state is not IdentityState.ENABLED
                or policy(identity) != ticket["policy"]
                or facts.model_version_id != ticket["model_version_id"]
            ):
                reject("CONNECTION_PERMISSION_CHANGED", "上传权限或模型已变化，请重新生成连接链接")
            if (local["serial_no"] and local["serial_no"] != facts.serial_no) or (
                local["model_version_id"] and local["model_version_id"] != facts.model_version_id
            ):
                reject("LOCAL_ROBOT_MISMATCH", "实际机器人配置与平台设备不匹配，请检查机器人配置")
            claim = ticket["claim"]
            if claim:
                if claim["request_id"] != command.request_id or claim["local_robot"] != local:
                    reject("CONNECTION_TICKET_USED", "连接链接已被使用，请重新生成")
                # Lost-response recovery is bound to the exact original request,
                # including its unguessable request ID; it cannot issue a second credential.
                if now > datetime.fromisoformat(claim["issued_at"]) + timedelta(hours=24):
                    reject("CONNECTION_RETRY_EXPIRED", "连接恢复期限已过，请重新生成链接", 410)
            else:
                if now >= datetime.fromisoformat(ticket["expires_at"]):
                    reject("CONNECTION_TICKET_EXPIRED", "连接链接已过期，请重新生成", 410)
                self.store.bind_local(ticket, local)
                claim = {
                    "request_id": command.request_id,
                    "local_robot": local,
                    "issued_at": now.isoformat(),
                }
                ticket["claim"] = claim
        # This deterministic secret is never stored in the ticket table. Both
        # issuance and all exact retries return the same credential after restart.
        seed = ticket["ticket_id"] + ":" + command.request_id
        credential_uuid = uuid5(NAMESPACE_URL, "hc-robot-connection:" + seed).hex
        secret = hmac.new(
            s._credential_key, ("connection-v1:" + seed).encode(), hashlib.sha256
        ).hexdigest()
        token = f"hcri_{credential_uuid}.{secret}"
        issued_at = datetime.fromisoformat(claim["issued_at"])
        summary = RobotCredentialSummary(
            credential_id="ric-" + credential_uuid,
            credential_version=identity.credential_revision + 1,
            state=CredentialState.ACTIVE,
            token_prefix=f"hcri_{credential_uuid}.{secret[:8]}",
            issued_at=issued_at,
        )
        s.repository.issue_credential(
            identity=identity,
            summary=summary,
            token_digest=s._token_digest(token),
            revoke_previous=False,
            request_key="connection:" + ticket["ticket_id"],
            expected_policy=ticket["policy"],
        )
        # Disabled/revoked credentials are never resurrected by retrying a ticket.
        machine = s.authenticate(token)
        return {"token": token, **self.me(machine), "local_robot": local}

    def me(self, auth):
        s = self.service
        select_organization_scope(auth.organization_id)
        identity = s._require_identity(auth.organization_id, auth.ingest_identity_id)
        facts = robot_info(s, identity)
        return {
            "organization_id": auth.organization_id,
            "robot_id": auth.authenticated_robot_id,
            "ingest_identity_id": auth.ingest_identity_id,
            "robot_name": facts.display_name,
            "platform_name": "HC DataPlatform",
            "allowed_formats": auth.allowed_formats,
            "model_version_id": facts.model_version_id,
            "local_robot": self.store.local(auth.organization_id, auth.ingest_identity_id),
        }

    def task(self, auth, task_id, source_format, local_robot=None, require_processing_config=False):
        info = self.me(auth)
        if (
            local_robot is not None
            and info["local_robot"]
            and info["local_robot"] != local_robot.model_dump(mode="json")
        ):
            reject("LOCAL_ROBOT_MISMATCH", "本地配置与已连接设备的配置不一致，请检查机器人配置")
        target = self.service.repository.resolve_upload_target(task_id)
        if target is None or target.organization_id != auth.organization_id:
            reject("UPLOAD_TARGET_NOT_FOUND", "任务不存在或不可访问", 404)
        if target.task_status != "ACTIVE":
            reject("COLLECTION_TASK_INACTIVE", "任务已关闭或取消，请保留数据并检查任务")
        if source_format not in auth.allowed_formats or "HTTPS" not in auth.allowed_transports:
            reject("SOURCE_FORMAT_NOT_ALLOWED", "机器人上传权限不允许当前采集格式", 403)
        row = self.store.task_row(auth.organization_id, task_id)
        if row is None or not row["ready"]:
            reject("TASK_NOT_READY", "任务的数据集、区域或处理配置尚未就绪")
        result = task_item(row, ownership(target))
        self._task_capture_plan(auth, result, source_format)
        if require_processing_config and not result["selectable"]:
            reject(result["reason"], "任务采集方案或实例映射尚未就绪")
        return result

    def tasks(self, auth, source_format, q="", project_id=None, page=1, page_size=20):
        self.me(auth)
        rows = self.store.task_rows(auth.organization_id)
        projects = {}
        items = []
        for row in rows:
            if row["task_status"] != "ACTIVE":
                continue
            projects[row["project_id"]] = row["project_name"]
            if project_id and row["project_id"] != project_id:
                continue
            if q.casefold() not in (row["name"] + " " + row["task_code"]).casefold():
                continue
            if (
                row["ready"]
                and source_format in auth.allowed_formats
                and "HTTPS" in auth.allowed_transports
            ):
                snapshot = {
                    key: row[key]
                    for key in (
                        "organization_id",
                        "project_id",
                        "dataset_id",
                        "region_code",
                        "collection_task_id",
                    )
                }
                item = task_item(row, snapshot)
                self._task_capture_plan(auth, item, source_format)
                items.append(item)
        items.sort(key=lambda item: (item["name"], item["collection_task_id"]))
        return {
            "items": items[(page - 1) * page_size : page * page_size],
            "total": len(items),
            "page": page,
            "page_size": page_size,
            "projects": [{"id": k, "name": v} for k, v in sorted(projects.items())],
        }

    def _task_capture_plan(self, auth, item, source_format):
        if source_format not in {"MCAP", "LEROBOT_V3", "CAPTURE_BUNDLE"}:
            return
        if self.capture_plans is None:
            item.update(
                selectable=False,
                reason="CAPTURE_STORE_UNAVAILABLE",
                capture_plan=None,
                processing_config={"error": "CAPTURE_STORE_UNAVAILABLE"},
            )
            return
        from hc_data_platform.core.context import select_request_scope

        ownership = item["ownership"]
        scope = (auth.organization_id, ownership["project_id"], ownership["region_code"])
        select_request_scope(scope[1], scope[2], organization_id=scope[0])
        try:
            preview = self.capture_plans.preview(
                scope, ownership["collection_task_id"], auth.authenticated_robot_id, source_format
            )
        except ProblemException as exc:
            item.update(
                selectable=False,
                reason=exc.problem.code,
                capture_plan=None,
                processing_config={"error": exc.problem.code},
            )
            return
        item["capture_plan"] = preview.plan
        item["processing_config"] = preview.processing_config

    def prepare_recording(self, auth, task_id, command):
        from hc_data_platform.core.context import select_request_scope

        self.me(auth)
        target = self.service.repository.resolve_upload_target(task_id)
        if target is None or target.organization_id != auth.organization_id:
            reject("UPLOAD_TARGET_NOT_FOUND", "任务不存在或不可访问", 404)
        if (
            command.source_format not in auth.allowed_formats
            or "HTTPS" not in auth.allowed_transports
        ):
            reject("SOURCE_FORMAT_NOT_ALLOWED", "机器人上传权限不允许当前采集格式", 403)
        if self.capture_plans is None:
            reject("CAPTURE_STORE_UNAVAILABLE", "录制准备需要持久化的任务计划服务", 503)
        scope = (target.organization_id, target.project_id, target.region_code)
        select_request_scope(scope[1], scope[2], organization_id=scope[0])
        return self.capture_plans.prepare(
            scope, task_id, auth.authenticated_robot_id, command, "robot:" + auth.ingest_identity_id
        )


def ownership(target: UploadTarget) -> dict:
    return {
        k: getattr(target, k)
        for k in (
            "organization_id",
            "project_id",
            "dataset_id",
            "region_code",
            "collection_task_id",
        )
    }


def task_item(row, snapshot):
    return {
        **{key: row[key] for key in ("name", "task_code", "description", "project_name")},
        "collection_task_id": snapshot["collection_task_id"],
        "project_id": snapshot["project_id"],
        "selectable": True,
        "reason": None,
        "ownership": snapshot,
    }
