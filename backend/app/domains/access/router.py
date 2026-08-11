"""FastAPI routes for all 26 access/audit operations in the domain OpenAPI."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Path, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.context import RequestContext, require
from app.core.db import get_session
from app.core.errors import NotFoundError, ValidationError
from app.core.etag import compute_etag
from app.core.idempotency import with_idempotency
from app.core.outbox import emit_event
from app.core.pagination import (
    CursorParams,
    build_page,
    cursor_params,
    decode_cursor,
)

from .models import AuditExportJobModel, InvitationModel, MemberModel
from .schemas import (
    AccessChangePreflightRequest,
    AuditExportCreateCommand,
    AuditExportPreflightRequest,
    AuditScope,
    InvitationPreflightRequest,
    PreflightCommitRequest,
)
from .service import (
    AUDIT_SORT,
    EVENT_CATALOG_VERSION,
    POLICY_VERSION,
    REGISTRY,
    ROLE_VERSION,
    AccessService,
    AuditService,
    as_utc,
    project_audit_record,
    project_scope_key,
    utc_now,
)

router = APIRouter(tags=["access-audit"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ProjectId = Annotated[str, Path(alias="projectId", min_length=1, max_length=256)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=3, max_length=256)]


def _project_scope(ctx: RequestContext, project_id: str) -> dict[str, str]:
    if ctx.project_id != project_id:
        raise ValidationError(
            code="SCOPE_MISMATCH", message="Project path and context do not match"
        )
    return {
        "type": "PROJECT",
        "organization_id": ctx.organization_id,
        "project_id": project_id,
    }


def _audit_scope(ctx: RequestContext, project_id: str) -> AuditScope:
    _project_scope(ctx, project_id)
    return AuditScope(
        organization_id=ctx.organization_id,
        project_id=project_id,
        region_code=ctx.region_code,
    )


def _envelope(data: Any, scope: dict[str, Any], ctx: RequestContext) -> dict[str, Any]:
    return {"data": data, "scope": scope, "request_id": ctx.request_id, "contract_version": "v1"}


def _cursor_value(
    cursor: str | None, time_field: str, id_field: str
) -> tuple[datetime, str] | None:
    if cursor is None:
        return None
    parts = decode_cursor(cursor)
    try:
        raw_time = str(parts[time_field]).replace("Z", "+00:00")
        return datetime.fromisoformat(raw_time), str(parts[id_field])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError(
            code="INVALID_CURSOR", message="Cursor does not match this resource"
        ) from exc


def _member_dict(row: MemberModel, can_manage: bool) -> dict[str, Any]:
    allowed = []
    if can_manage:
        allowed = ["CHANGE_ROLE", "ADD_SCOPE_GRANT"]
        allowed.append("DISABLE_MEMBERSHIP" if row.status == "ACTIVE" else "ENABLE_MEMBERSHIP")
    return {
        "member_id": row.member_id,
        "principal": row.principal_snapshot,
        "organization_id": row.organization_id,
        "project_id": row.project_id,
        "status": row.status,
        "role_id": row.role_id,
        "role_version": row.role_version,
        "joined_at": row.joined_at,
        "last_active_at": row.last_active_at,
        "etag": row.etag,
        "allowed_actions": allowed,
        "blocked_reasons": []
        if allowed
        else [
            {
                "action": "CHANGE_ROLE",
                "code": "CAPABILITY_REQUIRED",
                "message_key": "access.capability_required",
            }
        ],
    }


def _invitation_dict(row: InvitationModel, can_manage: bool) -> dict[str, Any]:
    allowed: list[str] = []
    if can_manage and row.status == "PENDING":
        allowed = ["RESEND_INVITATION", "REVOKE_INVITATION"]
    return {
        "invitation_id": row.invitation_id,
        "project_id": row.project_id,
        "target_display": row.target_display,
        "status": row.status,
        "intended_role_id": row.intended_role_id,
        "intended_role_version": row.intended_role_version,
        "intended_scope": row.intended_scope,
        "policy_revision": row.policy_revision,
        "expires_at": row.expires_at,
        "superseded_by_invitation_id": row.superseded_by_invitation_id,
        "etag": row.etag,
        "allowed_actions": allowed,
        "blocked_reasons": [],
    }


def _audit_export_dict(row: AuditExportJobModel) -> dict[str, Any]:
    artifact = None
    if row.artifact_ref is not None:
        availability = "ACTIVE"
        if row.artifact_expires_at and as_utc(row.artifact_expires_at) <= utc_now():
            availability = "EXPIRED"
        artifact = {
            "availability": availability,
            "expires_at": row.artifact_expires_at,
            "row_count": str(row.succeeded_count),
            "sha256": row.artifact_sha256,
        }
    return {
        "export_id": row.export_id,
        "scope": row.scope,
        "requester_principal_id": row.requester_principal_id,
        "snapshot_at": row.snapshot_at,
        "normalized_filter_hash": row.normalized_filter_hash,
        "format": row.format,
        "field_profile": row.field_profile,
        "job_id": row.job_id,
        "logical_status": row.status,
        "counts": {
            "succeeded": str(row.succeeded_count),
            "failed": str(row.failed_count),
            "omitted": str(row.omitted_count),
        },
        "artifact": artifact,
        "create_authorization_policy_version": row.create_authorization_policy_version,
    }


async def _write_triplet(
    session: AsyncSession,
    ctx: RequestContext,
    event_name: str,
    target_type: str,
    target_id: str,
    payload: dict[str, Any],
) -> None:
    await write_audit(session, event_name, target_type, target_id, "SUCCEEDED", ctx, payload)
    await emit_event(session, event_name, target_type, target_id, payload, ctx)


@router.get("/projects/{projectId}/access/bootstrap", operation_id="getAccessBootstrap")
async def get_access_bootstrap(
    project_id: ProjectId,
    response: Response,
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("access.read"))],
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Any:
    scope = _project_scope(ctx, project_id)
    policy_etag = compute_etag(POLICY_VERSION)
    if if_none_match == policy_etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED)
    response.headers["ETag"] = policy_etag
    # Counts stay authoritative in PostgreSQL and are not inferred from a page.
    rows = await AccessService(session).repo.list_members(project_id, limit=100)
    active = [row for row in rows if row.status == "ACTIVE"]
    data = {
        "scope": scope,
        "current_actor": {
            "principal_id": ctx.actor_id,
            "capability_keys": sorted(ctx.capabilities),
        },
        "role_version": ROLE_VERSION,
        "capability_catalog_version": REGISTRY.catalog_version,
        "policy_revision": POLICY_VERSION,
        "project_policy_etag": policy_etag,
        "summary": {
            "active_members": str(len(active)),
            "admins": str(sum(row.role_id == "PROJECT_ADMIN" for row in active)),
            "project_developers": str(sum(row.role_id == "PROJECT_DEVELOPER" for row in active)),
            "project_data_processors": str(
                sum(row.role_id == "PROJECT_DATA_PROCESSOR" for row in active)
            ),
            "pending_invitations": "0",
            "as_of": utc_now(),
        },
        "allowed_actions": ["INVITE_MEMBER", "CHANGE_ROLE", "ADD_SCOPE_GRANT"]
        if "access.manage" in ctx.capabilities
        else [],
        "blocked_reasons": [],
    }
    return _envelope(data, scope, ctx)


@router.get("/projects/{projectId}/members", operation_id="listProjectMembers")
async def list_project_members(
    project_id: ProjectId,
    session: SessionDep,
    params: Annotated[CursorParams, Depends(cursor_params)],
    ctx: Annotated[RequestContext, Depends(require("access.read"))],
    role_id: Annotated[list[str] | None, Query(alias="role_id[]")] = None,
    member_status: Annotated[list[str] | None, Query(alias="status[]")] = None,
) -> dict[str, Any]:
    scope = _project_scope(ctx, project_id)
    after = _cursor_value(params.after, "joined_at", "member_id")
    before = _cursor_value(params.before, "joined_at", "member_id")
    rows = await AccessService(session).repo.list_members(
        project_id,
        limit=params.limit,
        after=after,
        before=before,
        role_ids=role_id or (),
        statuses=member_status or (),
    )
    page = build_page(rows, params, ("joined_at", "member_id"))
    page["items"] = [
        _member_dict(row, "access.manage" in ctx.capabilities) for row in page["items"]
    ]
    return {**page, "scope": scope, "request_id": ctx.request_id, "contract_version": "v1"}


@router.get("/projects/{projectId}/members/{membershipId}", operation_id="getProjectMember")
async def get_project_member(
    project_id: ProjectId,
    membership_id: Annotated[str, Path(alias="membershipId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("access.read"))],
) -> dict[str, Any]:
    scope = _project_scope(ctx, project_id)
    row = await AccessService(session).repo.get_member(project_id, membership_id)
    if row is None:
        raise NotFoundError(code="MEMBER_NOT_FOUND")
    return _envelope(_member_dict(row, "access.manage" in ctx.capabilities), scope, ctx)


@router.get(
    "/projects/{projectId}/members/{membershipId}/effective-access",
    operation_id="getMemberEffectiveAccess",
)
async def get_member_effective_access(
    project_id: ProjectId,
    membership_id: Annotated[str, Path(alias="membershipId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("access.read"))],
) -> dict[str, Any]:
    scope = _project_scope(ctx, project_id)
    service = AccessService(session)
    row = await service.repo.get_member(project_id, membership_id)
    if row is None:
        raise NotFoundError(code="MEMBER_NOT_FOUND")
    capabilities = set(REGISTRY.role_capabilities[row.role_id]) if row.status == "ACTIVE" else set()
    grants = await service.repo.list_scope_grants(project_id, limit=100, member_id=membership_id)
    denied = {
        key
        for grant in grants
        if grant.status == "ACTIVE" and grant.effect == "DENY"
        for key in grant.capability_keys
    }
    capabilities -= denied
    now = utc_now()
    data = {
        "principal_id": row.principal_id,
        "scope": scope,
        "capability_keys": sorted(capabilities),
        "denied_capability_keys": sorted(denied),
        "evidence": [],
        "catalog_version": REGISTRY.catalog_version,
        "role_version": row.role_version,
        "policy_version": POLICY_VERSION,
        "evaluated_at": now,
        "expires_at": now,
        "refresh_state": "CURRENT",
    }
    return _envelope(data, scope, ctx)


@router.get("/projects/{projectId}/roles", operation_id="listProjectRoles")
async def list_project_roles(
    project_id: ProjectId,
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("access.read"))],
) -> dict[str, Any]:
    scope = _project_scope(ctx, project_id)
    return {
        "items": AccessService(session).roles(),
        "scope": scope,
        "request_id": ctx.request_id,
        "contract_version": "v1",
    }


@router.get("/authorization/capabilities", operation_id="getCapabilityCatalog")
async def get_capability_catalog(
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("access.read"))],
) -> dict[str, Any]:
    scope = _project_scope(ctx, ctx.project_id)
    return _envelope(AccessService(session).capability_catalog(), scope, ctx)


@router.get("/projects/{projectId}/role-assignments", operation_id="listRoleAssignments")
async def list_role_assignments(
    project_id: ProjectId,
    session: SessionDep,
    params: Annotated[CursorParams, Depends(cursor_params)],
    ctx: Annotated[RequestContext, Depends(require("access.read"))],
    membership_id: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    scope = _project_scope(ctx, project_id)
    rows = await AccessService(session).repo.list_role_assignments(
        project_id,
        limit=params.limit,
        member_id=membership_id,
        after=_cursor_value(params.after, "valid_from", "role_assignment_id"),
        before=_cursor_value(params.before, "valid_from", "role_assignment_id"),
    )
    page = build_page(rows, params, ("valid_from", "role_assignment_id"))
    page["items"] = [
        {
            "role_assignment_id": row.role_assignment_id,
            "subject": row.subject,
            "role_id": row.role_id,
            "role_version": row.role_version,
            "scope": row.scope,
            "inherit": row.inherit,
            "valid_from": row.valid_from,
            "valid_to": row.valid_to,
            "status": row.status,
            "etag": row.etag,
        }
        for row in page["items"]
    ]
    return {**page, "scope": scope, "request_id": ctx.request_id, "contract_version": "v1"}


@router.get("/projects/{projectId}/access-grants", operation_id="listAccessGrants")
async def list_access_grants(
    project_id: ProjectId,
    session: SessionDep,
    params: Annotated[CursorParams, Depends(cursor_params)],
    ctx: Annotated[RequestContext, Depends(require("access.read"))],
    membership_id: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    scope = _project_scope(ctx, project_id)
    rows = await AccessService(session).repo.list_scope_grants(
        project_id,
        limit=params.limit,
        member_id=membership_id,
        after=_cursor_value(params.after, "valid_from", "scope_grant_id"),
        before=_cursor_value(params.before, "valid_from", "scope_grant_id"),
    )
    page = build_page(rows, params, ("valid_from", "scope_grant_id"))
    page["items"] = [
        {
            "scope_grant_id": row.scope_grant_id,
            "subject": row.subject,
            "scope": row.scope,
            "effect": row.effect,
            "capability_keys": row.capability_keys,
            "ceiling_version": row.ceiling_version,
            "inherit": row.inherit,
            "valid_from": row.valid_from,
            "valid_to": row.valid_to,
            "status": row.status,
            "etag": row.etag,
        }
        for row in page["items"]
    ]
    return {**page, "scope": scope, "request_id": ctx.request_id, "contract_version": "v1"}


@router.get("/projects/{projectId}/invitations", operation_id="listInvitations")
async def list_invitations(
    project_id: ProjectId,
    session: SessionDep,
    params: Annotated[CursorParams, Depends(cursor_params)],
    ctx: Annotated[RequestContext, Depends(require("access.read"))],
) -> dict[str, Any]:
    scope = _project_scope(ctx, project_id)
    rows = await AccessService(session).repo.list_invitations(
        project_id,
        limit=params.limit,
        after=_cursor_value(params.after, "created_at", "invitation_id"),
        before=_cursor_value(params.before, "created_at", "invitation_id"),
    )
    page = build_page(rows, params, ("created_at", "invitation_id"))
    page["items"] = [
        _invitation_dict(row, "access.manage" in ctx.capabilities) for row in page["items"]
    ]
    return {**page, "scope": scope, "request_id": ctx.request_id, "contract_version": "v1"}


@router.post("/projects/{projectId}/invitations:preflight", operation_id="preflightInvitation")
async def preflight_invitation(
    project_id: ProjectId,
    body: InvitationPreflightRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("access.manage"))],
) -> dict[str, Any]:
    scope = _project_scope(ctx, project_id)
    if if_match != body.project_policy_etag:
        raise ValidationError(
            code="PRECONDITION_FAILED", message="If-Match must equal project_policy_etag"
        )

    async def command() -> dict[str, Any]:
        data = await AccessService(session).preflight_invitation(project_id, ctx.actor_id, body)
        return _envelope(data, scope, ctx)

    return await with_idempotency(session, idempotency_key, (scope, "preflightInvitation"), command)


@router.post(
    "/projects/{projectId}/invitations",
    operation_id="createInvitation",
    status_code=status.HTTP_201_CREATED,
)
async def create_invitation(
    project_id: ProjectId,
    body: PreflightCommitRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    _if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("access.manage"))],
) -> dict[str, Any]:
    scope = _project_scope(ctx, project_id)

    async def command() -> dict[str, Any]:
        row = await AccessService(session).commit_invitation(project_id, body.preflight_token)
        payload = {"invitation_id": row.invitation_id, "role_id": row.intended_role_id}
        await _write_triplet(
            session,
            ctx,
            "access.invitation.created",
            "access.invitation",
            row.invitation_id,
            payload,
        )
        return _envelope(
            {"invitation": _invitation_dict(row, True), "request_id": ctx.request_id}, scope, ctx
        )

    return await with_idempotency(session, idempotency_key, (scope, "createInvitation"), command)


@router.get("/projects/{projectId}/invitations/{invitationId}", operation_id="getInvitation")
async def get_invitation(
    project_id: ProjectId,
    invitation_id: Annotated[str, Path(alias="invitationId")],
    response: Response,
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("access.read"))],
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Any:
    scope = _project_scope(ctx, project_id)
    row = await AccessService(session).repo.get_invitation(project_id, invitation_id)
    if row is None:
        raise NotFoundError(code="INVITATION_NOT_FOUND")
    if if_none_match == row.etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED)
    response.headers["ETag"] = row.etag
    return _envelope(_invitation_dict(row, "access.manage" in ctx.capabilities), scope, ctx)


async def _commit_existing_invitation(
    event_name: str,
    operation_id: str,
    project_id: str,
    invitation_id: str,
    body: PreflightCommitRequest,
    session: AsyncSession,
    idempotency_key: str,
    ctx: RequestContext,
) -> dict[str, Any]:
    scope = _project_scope(ctx, project_id)

    async def command() -> dict[str, Any]:
        row = await AccessService(session).commit_invitation(project_id, body.preflight_token)
        await _write_triplet(
            session,
            ctx,
            event_name,
            "access.invitation",
            invitation_id,
            {"invitation_id": invitation_id, "result_invitation_id": row.invitation_id},
        )
        return _envelope(
            {"invitation": _invitation_dict(row, True), "request_id": ctx.request_id}, scope, ctx
        )

    return await with_idempotency(session, idempotency_key, (scope, operation_id), command)


@router.post(
    "/projects/{projectId}/invitations/{invitationId}:resend",
    operation_id="resendInvitation",
    status_code=status.HTTP_201_CREATED,
)
async def resend_invitation(
    project_id: ProjectId,
    invitation_id: Annotated[str, Path(alias="invitationId")],
    body: PreflightCommitRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    _if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("access.manage"))],
) -> dict[str, Any]:
    return await _commit_existing_invitation(
        "access.invitation.resent",
        "resendInvitation",
        project_id,
        invitation_id,
        body,
        session,
        idempotency_key,
        ctx,
    )


@router.post(
    "/projects/{projectId}/invitations/{invitationId}:revoke",
    operation_id="revokeInvitation",
)
async def revoke_invitation(
    project_id: ProjectId,
    invitation_id: Annotated[str, Path(alias="invitationId")],
    body: PreflightCommitRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    _if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("access.manage"))],
) -> dict[str, Any]:
    return await _commit_existing_invitation(
        "access.invitation.revoked",
        "revokeInvitation",
        project_id,
        invitation_id,
        body,
        session,
        idempotency_key,
        ctx,
    )


@router.post("/projects/{projectId}/access-changes:preflight", operation_id="preflightAccessChange")
async def preflight_access_change(
    project_id: ProjectId,
    body: AccessChangePreflightRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("access.manage"))],
) -> dict[str, Any]:
    scope = _project_scope(ctx, project_id)
    if if_match != body.project_policy_etag:
        raise ValidationError(
            code="PRECONDITION_FAILED", message="If-Match must equal project_policy_etag"
        )

    async def command() -> dict[str, Any]:
        data = await AccessService(session).preflight_access_change(project_id, ctx.actor_id, body)
        return _envelope(data, scope, ctx)

    return await with_idempotency(
        session, idempotency_key, (scope, "preflightAccessChange"), command
    )


@router.post("/projects/{projectId}/access-changes", operation_id="commitAccessChange")
async def commit_access_change(
    project_id: ProjectId,
    body: PreflightCommitRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    _if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("access.manage"))],
) -> dict[str, Any]:
    scope = _project_scope(ctx, project_id)

    async def command() -> dict[str, Any]:
        data = await AccessService(session).commit_access_change(project_id, body.preflight_token)
        events = data.pop("_audit_events")
        for index, event_name in enumerate(events):
            target_id = data["affected_membership_ids"][
                min(index, len(data["affected_membership_ids"]) - 1)
            ]
            await _write_triplet(
                session,
                ctx,
                event_name,
                "access.member",
                target_id,
                {"result_id": data["result_id"]},
            )
        return _envelope(data, scope, ctx)

    return await with_idempotency(session, idempotency_key, (scope, "commitAccessChange"), command)


@router.get("/projects/{projectId}/audit/bootstrap", operation_id="getAuditBootstrap")
async def get_audit_bootstrap(
    project_id: ProjectId,
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("audit.read"))],
) -> dict[str, Any]:
    scope = _audit_scope(ctx, project_id)
    scope_key = project_scope_key(scope.organization_id, scope.project_id, scope.region_code)
    now = utc_now()
    counts = await AuditService(session).repo.counts(
        scope_key, now.replace(hour=0, minute=0, second=0, microsecond=0)
    )
    data = {
        "scope": scope.model_dump(mode="json"),
        "metrics": {key: str(value) for key, value in counts.items()},
        "as_of": now,
        "catalog_version": EVENT_CATALOG_VERSION,
        "policy_version": POLICY_VERSION,
        "integrity": "UNKNOWN",
        "allowed_actions": ["VIEW", "VIEW_RESOURCE"]
        + (["EXPORT"] if "audit.export" in ctx.capabilities else []),
        "blocked_reasons": [],
    }
    return _envelope(data, scope.model_dump(mode="json"), ctx)


@router.get("/projects/{projectId}/audit/events/facets", operation_id="getAuditFacets")
async def get_audit_facets(
    project_id: ProjectId,
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("audit.read"))],
) -> dict[str, Any]:
    scope = _audit_scope(ctx, project_id)
    data = await AuditService(session).repo.facet_values(
        project_scope_key(scope.organization_id, scope.project_id, scope.region_code)
    )
    return _envelope(data, scope.model_dump(mode="json"), ctx)


@router.get("/projects/{projectId}/audit/events", operation_id="listAuditEvents")
async def list_audit_events(
    project_id: ProjectId,
    session: SessionDep,
    params: Annotated[CursorParams, Depends(cursor_params)],
    ctx: Annotated[RequestContext, Depends(require("audit.read"))],
    sort: Annotated[str, Query()] = AUDIT_SORT,
    occurred_from: Annotated[datetime | None, Query()] = None,
    occurred_to: Annotated[datetime | None, Query()] = None,
    actor_id: Annotated[list[str] | None, Query(alias="actor_id[]")] = None,
    event_name: Annotated[list[str] | None, Query(alias="event_name[]")] = None,
    resource_type: Annotated[list[str] | None, Query(alias="resource_type[]")] = None,
    outcome: Annotated[list[str] | None, Query(alias="outcome[]")] = None,
    risk_level: Annotated[list[str] | None, Query(alias="risk_level[]")] = None,
    request_id: Annotated[str | None, Query()] = None,
    region_code: Annotated[list[str] | None, Query(alias="region_code[]")] = None,
) -> dict[str, Any]:
    if sort != AUDIT_SORT:
        raise ValidationError(code="AUDIT_SORT_UNSUPPORTED")
    scope = _audit_scope(ctx, project_id)
    after = _cursor_value(params.after, "occurred_at", "event_id")
    before = _cursor_value(params.before, "occurred_at", "event_id")
    rows = await AuditService(session).repo.list_events(
        project_scope_key(scope.organization_id, scope.project_id, scope.region_code),
        limit=params.limit,
        after=after,
        before=before,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        actor_ids=actor_id or (),
        event_names=event_name or (),
        resource_types=resource_type or (),
        outcomes=outcome or (),
        risk_levels=risk_level or (),
        request_id=request_id,
        region_codes=region_code or (),
    )
    page = build_page(rows, params, ("occurred_at", "event_id"))
    page["items"] = [project_audit_record(row, set(ctx.capabilities)) for row in page["items"]]
    page["redaction"] = {
        "policy_version": "audit-redaction-v1",
        "omitted_field_classes": ["credentials", "network_exact", "raw_payload"],
    }
    return {
        **page,
        "scope": scope.model_dump(mode="json"),
        "request_id": ctx.request_id,
        "contract_version": "v1",
    }


@router.get("/projects/{projectId}/audit/events/{eventId}", operation_id="getAuditEvent")
async def get_audit_event(
    project_id: ProjectId,
    event_id: Annotated[str, Path(alias="eventId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("audit.read"))],
) -> dict[str, Any]:
    scope = _audit_scope(ctx, project_id)
    data = await AuditService(session).get_projected_event(scope, event_id, set(ctx.capabilities))
    return _envelope(data, scope.model_dump(mode="json"), ctx)


@router.get(
    "/projects/{projectId}/audit/events/{eventId}/related",
    operation_id="listRelatedAuditEvents",
)
async def list_related_audit_events(
    project_id: ProjectId,
    event_id: Annotated[str, Path(alias="eventId")],
    session: SessionDep,
    params: Annotated[CursorParams, Depends(cursor_params)],
    ctx: Annotated[RequestContext, Depends(require("audit.read"))],
) -> dict[str, Any]:
    scope = _audit_scope(ctx, project_id)
    repo = AuditService(session).repo
    original = await repo.get_event(
        project_scope_key(scope.organization_id, scope.project_id, scope.region_code), event_id
    )
    if original is None:
        raise NotFoundError(code="AUDIT_EVENT_NOT_FOUND")
    rows = await repo.list_related_events(
        original.scope_key,
        event_id,
        limit=params.limit,
        after=_cursor_value(params.after, "occurred_at", "event_id"),
        before=_cursor_value(params.before, "occurred_at", "event_id"),
    )
    page = build_page(rows, params, ("occurred_at", "event_id"))
    page["items"] = [project_audit_record(row, set(ctx.capabilities)) for row in page["items"]]
    page["redaction"] = {"policy_version": "audit-redaction-v1", "omitted_field_classes": []}
    return {
        **page,
        "scope": scope.model_dump(mode="json"),
        "request_id": ctx.request_id,
        "contract_version": "v1",
    }


@router.get("/projects/{projectId}/audit/retention-policy", operation_id="getAuditRetentionPolicy")
async def get_audit_retention_policy(
    project_id: ProjectId,
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("audit.read"))],
) -> dict[str, Any]:
    scope = _audit_scope(ctx, project_id)
    row = await AuditService(session).repo.get_retention_policy()
    data = {
        "policy_version": row.policy_version if row else "audit-retention-v1-conditional",
        "classes": row.classes if row else [],
        "legal_hold_explanation": "Legal Hold prevents ordinary purge and never rewrites an event.",
        "writes_enabled": False,
    }
    return _envelope(data, scope.model_dump(mode="json"), ctx)


@router.post("/projects/{projectId}/audit-exports:preflight", operation_id="preflightAuditExport")
async def preflight_audit_export(
    project_id: ProjectId,
    body: AuditExportPreflightRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    ctx: Annotated[RequestContext, Depends(require("audit.export"))],
) -> dict[str, Any]:
    scope = _audit_scope(ctx, project_id)

    async def command() -> dict[str, Any]:
        data = await AuditService(session).preflight_export(scope, body)
        return _envelope(data, scope.model_dump(mode="json"), ctx)

    return await with_idempotency(
        session, idempotency_key, (scope.model_dump(), "preflightAuditExport"), command
    )


@router.post(
    "/projects/{projectId}/audit-exports",
    operation_id="createAuditExport",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_audit_export(
    project_id: ProjectId,
    body: AuditExportCreateCommand,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    ctx: Annotated[RequestContext, Depends(require("audit.export"))],
) -> dict[str, Any]:
    scope = _audit_scope(ctx, project_id)

    async def command() -> dict[str, Any]:
        row = await AuditService(session).create_export(scope, ctx.actor_id, body)
        await _write_triplet(
            session,
            ctx,
            "audit.export.requested",
            "audit.export",
            row.export_id,
            {"job_id": row.job_id},
        )
        now = utc_now()
        return {
            "audit_export": _audit_export_dict(row),
            "job": {
                "job_id": row.job_id,
                "kind": "AUDIT_EXPORT",
                "status": row.status,
                "created_at": now,
                "updated_at": now,
                "resource_version": "1",
            },
            "allowed_actions": [],
            "blocked_reasons": [],
            "scope": scope.model_dump(mode="json"),
            "request_id": ctx.request_id,
            "contract_version": "v1",
        }

    return await with_idempotency(
        session, idempotency_key, (scope.model_dump(), "createAuditExport"), command
    )


@router.get("/projects/{projectId}/audit-exports/{exportId}", operation_id="getAuditExport")
async def get_audit_export(
    project_id: ProjectId,
    export_id: Annotated[str, Path(alias="exportId")],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("audit.export"))],
) -> dict[str, Any]:
    scope = _audit_scope(ctx, project_id)
    row = await AuditService(session).repo.get_export(
        project_scope_key(scope.organization_id, scope.project_id, scope.region_code), export_id
    )
    if row is None:
        raise NotFoundError(code="AUDIT_EXPORT_NOT_FOUND")
    now = utc_now()
    return {
        "audit_export": _audit_export_dict(row),
        "job": {
            "job_id": row.job_id,
            "kind": "AUDIT_EXPORT",
            "status": row.status,
            "created_at": row.created_at,
            "updated_at": now,
            "resource_version": "1",
        },
        "allowed_actions": ["DOWNLOAD"] if row.status == "SUCCEEDED" else [],
        "blocked_reasons": [],
        "scope": scope.model_dump(mode="json"),
        "request_id": ctx.request_id,
        "contract_version": "v1",
    }


@router.post(
    "/projects/{projectId}/audit-exports/{exportId}:authorize-download",
    operation_id="authorizeAuditExportDownload",
)
async def authorize_audit_export_download(
    project_id: ProjectId,
    export_id: Annotated[str, Path(alias="exportId")],
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    ctx: Annotated[RequestContext, Depends(require("audit.export"))],
) -> dict[str, Any]:
    scope = _audit_scope(ctx, project_id)

    async def command() -> dict[str, Any]:
        data = await AuditService(session).authorize_export_download(
            scope, export_id, can_download="audit.export" in ctx.capabilities
        )
        await _write_triplet(session, ctx, "audit.export.downloaded", "audit.export", export_id, {})
        return _envelope(data, scope.model_dump(mode="json"), ctx)

    return await with_idempotency(
        session,
        idempotency_key,
        (scope.model_dump(), "authorizeAuditExportDownload", export_id),
        command,
    )
