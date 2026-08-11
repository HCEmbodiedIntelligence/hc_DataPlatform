"""FastAPI routes for the 22-operation storage lifecycle contract."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Path, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.context import RequestContext, get_context, require
from app.core.db import get_session
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.idempotency import with_idempotency
from app.core.outbox import emit_event
from app.core.pagination import CursorParams, build_page, cursor_params, decode_cursor

from .models import LifecyclePolicyVersionModel, MultipartUploadProjectionModel
from .repository import ScopeTuple
from .schemas import (
    AbortMultipartRequest,
    CreateLifecyclePolicyRequest,
    CreateSimulationRequest,
    EnableLifecyclePolicyRequest,
    InventoryRefreshRequest,
    PauseLifecyclePolicyRequest,
    PlanMultipartAbortRequest,
    RestoreCommitRequest,
    RestorePreflightRequest,
    RestoreRequest,
    UpdateLifecyclePolicyRequest,
)
from .service import StorageService, job_projection, object_projection, policy_projection

router = APIRouter(tags=["storage-lifecycle"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ProjectId = Annotated[str, Path(alias="projectId", min_length=1, max_length=160)]
RegionCode = Annotated[str, Query(alias="region_code", min_length=1, max_length=32)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=3, max_length=256)]


def _scope(ctx: RequestContext, project_id: str, region_code: str) -> ScopeTuple:
    if ctx.project_id != project_id or ctx.region_code != region_code:
        raise ForbiddenError(code="SCOPE_MISMATCH")
    return ctx.organization_id, project_id, region_code


def _scope_body(scope: ScopeTuple) -> dict[str, str]:
    return {
        "organization_id": scope[0],
        "project_id": scope[1],
        "region_code": scope[2],
        "timezone": "UTC",
    }


def _envelope(data: Any, scope: ScopeTuple, ctx: RequestContext) -> dict[str, Any]:
    return {
        "data": data,
        "scope": _scope_body(scope),
        "request_id": ctx.request_id,
        "contract_version": "v1",
    }


def _page(
    rows: list[Any],
    params: CursorParams,
    fields: tuple[str, str],
    scope: ScopeTuple,
    ctx: RequestContext,
) -> dict[str, Any]:
    page = build_page(rows, params, fields)
    return {
        **page,
        "scope": _scope_body(scope),
        "request_id": ctx.request_id,
        "contract_version": "v1",
    }


def _cursor_value(value: str | None, date_field: str, id_field: str):
    if value is None:
        return None
    parts = decode_cursor(value)
    try:
        date_value = datetime.fromisoformat(str(parts[date_field]).replace("Z", "+00:00"))
        if date_value.tzinfo is None:
            date_value = date_value.replace(tzinfo=UTC)
        return date_value, str(parts[id_field])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError(code="INVALID_CURSOR") from exc


def _multipart_projection(row: MultipartUploadProjectionModel) -> dict[str, Any]:
    projection = dict(row.projection)
    projection.update(
        {
            "id": row.upload_id,
            "upload_id": row.upload_id,
            "upload_version": row.upload_version,
            "etag": row.etag,
            "status": row.status,
            "uploaded_bytes": str(row.uploaded_bytes),
            "expected_bytes": str(row.expected_bytes) if row.expected_bytes is not None else None,
            "uploaded_part_count": str(row.uploaded_part_count),
            "last_activity_at": row.last_activity_at,
            "protection_state": row.protection_state,
            "protected_reasons": row.protected_reasons,
        }
    )
    for forbidden in ("provider_bucket", "provider_object_key", "provider_etag"):
        projection.pop(forbidden, None)
    return projection


async def _record(
    session: AsyncSession,
    ctx: RequestContext,
    event_name: str,
    target_type: str,
    target_id: str,
    detail: dict[str, Any] | None = None,
) -> str:
    safe = detail or {}
    event_id = await emit_event(session, event_name, target_type, target_id, safe, ctx)
    await write_audit(session, event_name, target_type, target_id, "SUCCEEDED", ctx, safe)
    return event_id


@router.get("/projects/{projectId}/storage/overview", operation_id="getStorageOverview")
async def get_storage_overview(
    project_id: ProjectId,
    region_code: RegionCode,
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("storage.overview.read"))],
    months: Annotated[int, Query(ge=1, le=24)] = 6,
) -> dict[str, Any]:
    scope = _scope(ctx, project_id, region_code)
    snapshot = await StorageService(session).require_snapshot(scope)
    data = dict(snapshot.overview_projection)
    data.update(
        {
            "snapshot_id": snapshot.snapshot_id,
            "as_of": snapshot.as_of,
            "freshness": snapshot.freshness,
            "formula_version": snapshot.formula_version,
            "data_completeness": snapshot.data_completeness,
            "watermarks": snapshot.watermarks,
            "reconciliation": snapshot.reconciliation,
        }
    )
    if isinstance(data.get("growth"), list):
        data["growth"] = data["growth"][-months:]
    return _envelope(data, scope, ctx)


@router.get("/projects/{projectId}/storage/objects", operation_id="listStorageObjects")
async def list_storage_objects(
    project_id: ProjectId,
    region_code: RegionCode,
    session: SessionDep,
    params: Annotated[CursorParams, Depends(cursor_params)],
    ctx: Annotated[RequestContext, Depends(require("storage.object.read"))],
    snapshot_id: Annotated[str | None, Query()] = None,
    object_role: Annotated[str | None, Query()] = None,
    storage_class: Annotated[str | None, Query()] = None,
    object_status: Annotated[str | None, Query(alias="status")] = None,
) -> dict[str, Any]:
    scope = _scope(ctx, project_id, region_code)
    snapshot = await StorageService(session).require_snapshot(scope, snapshot_id)
    rows = await StorageService(session).repo.list_objects(
        scope,
        limit=params.limit,
        after=_cursor_value(params.after, "created_at", "object_id"),
        before=_cursor_value(params.before, "created_at", "object_id"),
        object_role=object_role,
        storage_class=storage_class,
        status=object_status,
    )
    page = _page(rows, params, ("created_at", "object_id"), scope, ctx)
    page["items"] = [object_projection(row) for row in page["items"]]
    page["snapshot_id"] = snapshot.snapshot_id
    return page


@router.get("/projects/{projectId}/storage/objects/{objectId}", operation_id="getStorageObject")
async def get_storage_object(
    project_id: ProjectId,
    object_id: Annotated[str, Path(alias="objectId")],
    region_code: RegionCode,
    snapshot_id: Annotated[str, Query()],
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("storage.object.read"))],
) -> dict[str, Any]:
    scope = _scope(ctx, project_id, region_code)
    await StorageService(session).require_snapshot(scope, snapshot_id)
    service = StorageService(session)
    row = await service.repo.get_object(scope, object_id)
    if row is None:
        raise NotFoundError(code="STORAGE_OBJECT_NOT_FOUND")
    references = await service.repo.object_references(object_id)
    protection = await service.repo.current_protection(object_id)
    data = object_projection(row)
    data["references"] = [
        {
            "resource_type": item.resource_type,
            "resource_id": item.resource_id,
            "resource_version": item.resource_version,
            "relation": item.relation,
        }
        for item in references
    ]
    data["protection"] = (
        {
            "reference_state": protection.reference_state,
            "reference_count": str(protection.reference_count)
            if protection.reference_count is not None
            else None,
            "retention_state": protection.retention_state,
            "retain_until": protection.retain_until,
            "legal_hold_state": protection.legal_hold_state,
            "legal_hold_count": str(protection.legal_hold_count)
            if protection.legal_hold_count is not None
            else None,
            "provider_lock_state": protection.provider_lock_state,
            "evaluated_at": protection.evaluated_at,
        }
        if protection
        else {"reference_state": "UNKNOWN", "retention_state": "UNKNOWN"}
    )
    await _record(session, ctx, "storage.object.viewed", "storage.object", object_id)
    return _envelope(data, scope, ctx)


@router.get("/projects/{projectId}/storage/multipart", operation_id="listStorageMultipart")
async def list_storage_multipart(
    project_id: ProjectId,
    region_code: RegionCode,
    session: SessionDep,
    params: Annotated[CursorParams, Depends(cursor_params)],
    ctx: Annotated[RequestContext, Depends(require("storage.object.read"))],
    snapshot_id: Annotated[str | None, Query()] = None,
    multipart_status: Annotated[list[str] | None, Query(alias="status")] = None,
) -> dict[str, Any]:
    scope = _scope(ctx, project_id, region_code)
    snapshot = await StorageService(session).require_snapshot(scope, snapshot_id)
    rows = await StorageService(session).repo.list_multipart(
        scope,
        limit=params.limit,
        statuses=multipart_status or (),
        after=_cursor_value(params.after, "last_activity_at", "upload_id"),
        before=_cursor_value(params.before, "last_activity_at", "upload_id"),
    )
    page = _page(rows, params, ("last_activity_at", "upload_id"), scope, ctx)
    page["items"] = [_multipart_projection(row) for row in page["items"]]
    page["snapshot_id"] = snapshot.snapshot_id
    return page


@router.get("/projects/{projectId}/storage/cost-breakdown", operation_id="getStorageCostBreakdown")
async def get_storage_cost_breakdown(
    project_id: ProjectId,
    region_code: RegionCode,
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("storage.cost.read"))],
    billing_period: Annotated[str | None, Query(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")] = None,
    currency: Annotated[str | None, Query(pattern=r"^[A-Z]{3}$")] = None,
) -> dict[str, Any]:
    scope = _scope(ctx, project_id, region_code)
    row = await StorageService(session).repo.current_cost(scope, billing_period, currency)
    data = (
        dict(row.projection)
        if row
        else {"availability": "NOT_SETTLED", "currency": currency, "formula_version": None}
    )
    return _envelope(data, scope, ctx)


@router.post(
    "/projects/{projectId}/storage/inventory-jobs",
    operation_id="createStorageInventoryJob",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_storage_inventory_job(
    project_id: ProjectId,
    region_code: RegionCode,
    body: InventoryRefreshRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    ctx: Annotated[RequestContext, Depends(require("storage.inventory.refresh"))],
) -> dict[str, Any]:
    scope = _scope(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        job = await StorageService(session).create_inventory_job(scope, body.expected_snapshot_id)
        await _record(
            session,
            ctx,
            "storage.inventory_refresh.requested",
            "storage.inventory_snapshot",
            job.resource_id,
            {"job_id": job.job_id},
        )
        data = job_projection(job)
        return {
            "inventory_job": data,
            "job": data,
            "scope": _scope_body(scope),
            "request_id": ctx.request_id,
            "contract_version": "v1",
        }

    return await with_idempotency(
        session, idempotency_key, (scope, "createStorageInventoryJob"), command
    )


@router.get("/projects/{projectId}/storage/lifecycle-page", operation_id="getLifecyclePage")
async def get_lifecycle_page(
    project_id: ProjectId,
    region_code: RegionCode,
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("storage.lifecycle.read"))],
) -> dict[str, Any]:
    scope = _scope(ctx, project_id, region_code)
    service = StorageService(session)
    snapshot = await service.require_snapshot(scope)
    policy_set = await service.get_or_create_policy_set(scope)
    rows = await service.repo.list_policies(scope, limit=100)
    data = {
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_at": snapshot.as_of,
        "freshness": snapshot.freshness,
        "policy_set_version": policy_set.version,
        "policy_set_etag": policy_set.etag,
        "policies": [policy_projection(row) for row in rows[:100]],
        "allowed_actions": ["CREATE_POLICY", "SIMULATE"],
        "blocked_reasons": [],
    }
    return _envelope(data, scope, ctx)


@router.get(
    "/projects/{projectId}/storage/lifecycle-policies", operation_id="listLifecyclePolicies"
)
async def list_lifecycle_policies(
    project_id: ProjectId,
    region_code: RegionCode,
    session: SessionDep,
    params: Annotated[CursorParams, Depends(cursor_params)],
    ctx: Annotated[RequestContext, Depends(require("storage.lifecycle.read"))],
    policy_status: Annotated[str | None, Query(alias="status")] = None,
) -> dict[str, Any]:
    scope = _scope(ctx, project_id, region_code)
    service = StorageService(session)
    rows = await service.repo.list_policies(
        scope,
        limit=params.limit,
        status=policy_status,
        after=_cursor_value(params.after, "updated_at", "policy_id"),
        before=_cursor_value(params.before, "updated_at", "policy_id"),
    )
    page = _page(rows, params, ("updated_at", "policy_id"), scope, ctx)
    page["items"] = [policy_projection(row) for row in page["items"]]
    policy_set = await service.get_or_create_policy_set(scope)
    page["policy_set_version"] = policy_set.version
    page["policy_set_etag"] = policy_set.etag
    return page


def _policy_mutation_response(
    row: LifecyclePolicyVersionModel, policy_set: Any, scope: ScopeTuple, ctx: RequestContext
) -> dict[str, Any]:
    return {
        "policy": policy_projection(row),
        "policy_set_version": policy_set.version,
        "policy_set_etag": policy_set.etag,
        "scope": _scope_body(scope),
        "request_id": ctx.request_id,
        "contract_version": "v1",
    }


@router.post(
    "/projects/{projectId}/storage/lifecycle-policies",
    operation_id="createLifecyclePolicy",
    status_code=status.HTTP_201_CREATED,
)
async def create_lifecycle_policy(
    project_id: ProjectId,
    region_code: RegionCode,
    body: CreateLifecyclePolicyRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("storage.lifecycle.manage"))],
) -> dict[str, Any]:
    scope = _scope(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        row, policy_set = await StorageService(session).create_policy(scope, body, if_match)
        await _record(
            session,
            ctx,
            "storage.lifecycle_policy.created",
            "storage.lifecycle_policy",
            row.policy_id,
            {"policy_version": row.version, "policy_set_version": policy_set.version},
        )
        return _policy_mutation_response(row, policy_set, scope, ctx)

    return await with_idempotency(
        session, idempotency_key, (scope, "createLifecyclePolicy"), command
    )


@router.get(
    "/projects/{projectId}/storage/lifecycle-policies/{policyId}",
    operation_id="getLifecyclePolicy",
)
async def get_lifecycle_policy(
    project_id: ProjectId,
    policy_id: Annotated[str, Path(alias="policyId")],
    region_code: RegionCode,
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("storage.lifecycle.read"))],
) -> dict[str, Any]:
    scope = _scope(ctx, project_id, region_code)
    service = StorageService(session)
    row = await service.repo.current_policy(scope, policy_id)
    if row is None:
        raise NotFoundError(code="LIFECYCLE_POLICY_NOT_FOUND")
    policy_set = await service.get_or_create_policy_set(scope)
    return {
        "policy": policy_projection(row),
        "policy_set_version": policy_set.version,
        "conflict_summary": {"conflicting_policy_ids": [], "overlapping_scope_labels": []},
        "scope": _scope_body(scope),
        "request_id": ctx.request_id,
        "contract_version": "v1",
    }


@router.patch(
    "/projects/{projectId}/storage/lifecycle-policies/{policyId}",
    operation_id="updateLifecyclePolicy",
)
async def update_lifecycle_policy(
    project_id: ProjectId,
    policy_id: Annotated[str, Path(alias="policyId")],
    region_code: RegionCode,
    body: UpdateLifecyclePolicyRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("storage.lifecycle.manage"))],
) -> dict[str, Any]:
    scope = _scope(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        row, policy_set = await StorageService(session).update_policy(
            scope, policy_id, body, if_match
        )
        await _record(
            session,
            ctx,
            "storage.lifecycle_policy.updated",
            "storage.lifecycle_policy",
            policy_id,
            {"policy_version": row.version, "policy_set_version": policy_set.version},
        )
        return _policy_mutation_response(row, policy_set, scope, ctx)

    return await with_idempotency(
        session, idempotency_key, (scope, "updateLifecyclePolicy", policy_id), command
    )


@router.post(
    "/projects/{projectId}/storage/lifecycle-policies/{policyId}:enable",
    operation_id="enableLifecyclePolicy",
)
async def enable_lifecycle_policy(
    project_id: ProjectId,
    policy_id: Annotated[str, Path(alias="policyId")],
    region_code: RegionCode,
    body: EnableLifecyclePolicyRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("storage.lifecycle.manage"))],
) -> dict[str, Any]:
    scope = _scope(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        row, policy_set = await StorageService(session).enable_policy(
            scope, policy_id, body, if_match
        )
        await _record(
            session,
            ctx,
            "storage.lifecycle_policy.enabled",
            "storage.lifecycle_policy",
            policy_id,
            {"policy_version": row.version, "policy_set_version": policy_set.version},
        )
        return _policy_mutation_response(row, policy_set, scope, ctx)

    return await with_idempotency(
        session, idempotency_key, (scope, "enableLifecyclePolicy", policy_id), command
    )


@router.post(
    "/projects/{projectId}/storage/lifecycle-policies/{policyId}:pause",
    operation_id="pauseLifecyclePolicy",
)
async def pause_lifecycle_policy(
    project_id: ProjectId,
    policy_id: Annotated[str, Path(alias="policyId")],
    region_code: RegionCode,
    body: PauseLifecyclePolicyRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("storage.lifecycle.manage"))],
) -> dict[str, Any]:
    scope = _scope(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        row, policy_set = await StorageService(session).pause_policy(
            scope, policy_id, body, if_match
        )
        await _record(
            session,
            ctx,
            "storage.lifecycle_policy.paused",
            "storage.lifecycle_policy",
            policy_id,
            {"policy_version": row.version, "policy_set_version": policy_set.version},
        )
        return _policy_mutation_response(row, policy_set, scope, ctx)

    return await with_idempotency(
        session, idempotency_key, (scope, "pauseLifecyclePolicy", policy_id), command
    )


@router.post(
    "/projects/{projectId}/storage/lifecycle-simulations",
    operation_id="createLifecycleSimulation",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_lifecycle_simulation(
    project_id: ProjectId,
    region_code: RegionCode,
    body: CreateSimulationRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    ctx: Annotated[RequestContext, Depends(require("storage.lifecycle.simulate"))],
) -> dict[str, Any]:
    scope = _scope(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        simulation, job = await StorageService(session).create_simulation(scope, body)
        event_id = await _record(
            session,
            ctx,
            "storage.lifecycle_simulation.created",
            "storage.lifecycle_simulation",
            simulation.simulation_id,
            {"job_id": job.job_id, "snapshot_id": simulation.snapshot_id},
        )
        simulation.audit_event_id = event_id
        data = dict(simulation.result_projection)
        data["audit_ref"] = {"audit_event_id": event_id, "request_id": ctx.request_id}
        return {
            "simulation": data,
            "job": job_projection(job),
            "scope": _scope_body(scope),
            "request_id": ctx.request_id,
            "contract_version": "v1",
        }

    return await with_idempotency(
        session, idempotency_key, (scope, "createLifecycleSimulation"), command
    )


@router.get(
    "/projects/{projectId}/storage/lifecycle-simulations/{simulationId}",
    operation_id="getLifecycleSimulation",
)
async def get_lifecycle_simulation(
    project_id: ProjectId,
    simulation_id: Annotated[str, Path(alias="simulationId")],
    region_code: RegionCode,
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("storage.lifecycle.read"))],
) -> dict[str, Any]:
    scope = _scope(ctx, project_id, region_code)
    service = StorageService(session)
    simulation = await service.repo.get_simulation(scope, simulation_id)
    if simulation is None:
        raise NotFoundError(code="LIFECYCLE_SIMULATION_NOT_FOUND")
    job = await service.repo.get_job(simulation.job_id)
    return {
        "simulation": simulation.result_projection,
        "job": job_projection(job) if job else None,
        "scope": _scope_body(scope),
        "request_id": ctx.request_id,
        "contract_version": "v1",
    }


@router.get(
    "/projects/{projectId}/storage/lifecycle-executions",
    operation_id="listLifecycleExecutions",
)
async def list_lifecycle_executions(
    project_id: ProjectId,
    region_code: RegionCode,
    session: SessionDep,
    params: Annotated[CursorParams, Depends(cursor_params)],
    ctx: Annotated[RequestContext, Depends(require("storage.lifecycle.read"))],
    execution_status: Annotated[str | None, Query(alias="status")] = None,
    started_from: Annotated[datetime | None, Query()] = None,
    started_to: Annotated[datetime | None, Query()] = None,
) -> dict[str, Any]:
    scope = _scope(ctx, project_id, region_code)
    if started_from and started_to and started_from > started_to:
        raise ValidationError(code="INVALID_TIME_RANGE")
    rows = await StorageService(session).repo.list_executions(
        scope,
        limit=params.limit,
        status=execution_status,
        started_from=started_from,
        started_to=started_to,
        after=_cursor_value(params.after, "started_at", "execution_id"),
        before=_cursor_value(params.before, "started_at", "execution_id"),
    )
    page = _page(rows, params, ("started_at", "execution_id"), scope, ctx)
    page["items"] = [row.projection for row in page["items"]]
    return page


@router.get(
    "/projects/{projectId}/storage/lifecycle-executions/{executionId}",
    operation_id="getLifecycleExecution",
)
async def get_lifecycle_execution(
    project_id: ProjectId,
    execution_id: Annotated[str, Path(alias="executionId")],
    region_code: RegionCode,
    session: SessionDep,
    ctx: Annotated[RequestContext, Depends(require("storage.lifecycle.read"))],
) -> dict[str, Any]:
    scope = _scope(ctx, project_id, region_code)
    row = await StorageService(session).repo.get_execution(scope, execution_id)
    if row is None:
        raise NotFoundError(code="LIFECYCLE_EXECUTION_NOT_FOUND")
    return _envelope(row.projection, scope, ctx)


@router.get("/projects/{projectId}/storage/restore-tasks", operation_id="listRestoreTasks")
async def list_restore_tasks(
    project_id: ProjectId,
    region_code: RegionCode,
    session: SessionDep,
    params: Annotated[CursorParams, Depends(cursor_params)],
    ctx: Annotated[RequestContext, Depends(require("storage.restore.read"))],
    restore_status: Annotated[str | None, Query(alias="status")] = None,
) -> dict[str, Any]:
    scope = _scope(ctx, project_id, region_code)
    rows = await StorageService(session).repo.list_restore_tasks(
        scope,
        limit=params.limit,
        status=restore_status,
        after=_cursor_value(params.after, "requested_at", "restore_task_id"),
        before=_cursor_value(params.before, "requested_at", "restore_task_id"),
    )
    page = _page(rows, params, ("requested_at", "restore_task_id"), scope, ctx)
    page["items"] = [row.projection for row in page["items"]]
    return page


@router.post("/projects/{projectId}/storage/restore-tasks", operation_id="createRestoreTask")
async def create_restore_task(
    project_id: ProjectId,
    region_code: RegionCode,
    body: RestoreRequest,
    response: Response,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    ctx: Annotated[RequestContext, Depends(get_context)],
) -> dict[str, Any]:
    scope = _scope(ctx, project_id, region_code)
    required = "storage.restore.read" if body.mode == "PREFLIGHT" else "storage.restore.request"
    if required not in ctx.capabilities:
        raise ForbiddenError(code="CAPABILITY_REQUIRED")

    async def command() -> dict[str, Any]:
        service = StorageService(session)
        if isinstance(body, RestorePreflightRequest):
            data = await service.restore_preflight(scope, ctx.actor_id, body)
            return {
                **data,
                "scope": _scope_body(scope),
                "request_id": ctx.request_id,
                "contract_version": "v1",
            }
        assert isinstance(body, RestoreCommitRequest)
        task, job = await service.restore_commit(scope, ctx.actor_id, body)
        response.status_code = status.HTTP_202_ACCEPTED
        event_id = await _record(
            session,
            ctx,
            "storage.restore.requested",
            "storage.restore_task",
            task.restore_task_id,
            {"job_id": job.job_id},
        )
        task.projection["audit_ref"] = {"audit_event_id": event_id, "request_id": ctx.request_id}
        return {
            "mode": "COMMIT",
            "restore_task": task.projection,
            "job": job_projection(job),
            "audit_ref": {"audit_event_id": event_id, "request_id": ctx.request_id},
            "scope": _scope_body(scope),
            "request_id": ctx.request_id,
            "contract_version": "v1",
        }

    return await with_idempotency(session, idempotency_key, (scope, "createRestoreTask"), command)


@router.get(
    "/projects/{projectId}/storage/multipart-lifecycle", operation_id="listMultipartLifecycle"
)
async def list_multipart_lifecycle(
    project_id: ProjectId,
    region_code: RegionCode,
    session: SessionDep,
    params: Annotated[CursorParams, Depends(cursor_params)],
    ctx: Annotated[RequestContext, Depends(require("storage.multipart.read"))],
    multipart_status: Annotated[list[str] | None, Query(alias="status")] = None,
) -> dict[str, Any]:
    scope = _scope(ctx, project_id, region_code)
    rows = await StorageService(session).repo.list_multipart(
        scope,
        limit=params.limit,
        statuses=multipart_status or (),
        after=_cursor_value(params.after, "last_activity_at", "upload_id"),
        before=_cursor_value(params.before, "last_activity_at", "upload_id"),
    )
    page = _page(rows, params, ("last_activity_at", "upload_id"), scope, ctx)
    page["items"] = [_multipart_projection(row) for row in page["items"]]
    return page


@router.post(
    "/projects/{projectId}/storage/multipart/{uploadId}:plan-abort",
    operation_id="planMultipartAbort",
)
async def plan_multipart_abort(
    project_id: ProjectId,
    upload_id: Annotated[str, Path(alias="uploadId")],
    region_code: RegionCode,
    body: PlanMultipartAbortRequest,
    response: Response,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("storage.multipart.abort"))],
) -> dict[str, Any]:
    scope = _scope(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        data, plan = await StorageService(session).plan_abort(scope, upload_id, body, if_match)
        response.headers["ETag"] = plan.etag
        return {
            **data,
            "scope": _scope_body(scope),
            "request_id": ctx.request_id,
            "contract_version": "v1",
        }

    return await with_idempotency(
        session, idempotency_key, (scope, "planMultipartAbort", upload_id), command
    )


@router.post(
    "/projects/{projectId}/storage/multipart/{uploadId}:abort",
    operation_id="abortMultipart",
    status_code=status.HTTP_202_ACCEPTED,
)
async def abort_multipart(
    project_id: ProjectId,
    upload_id: Annotated[str, Path(alias="uploadId")],
    region_code: RegionCode,
    body: AbortMultipartRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    ctx: Annotated[RequestContext, Depends(require("storage.multipart.abort"))],
) -> dict[str, Any]:
    scope = _scope(ctx, project_id, region_code)

    async def command() -> dict[str, Any]:
        _abort, job, upload = await StorageService(session).commit_abort(
            scope, upload_id, body, if_match
        )
        event_id = await _record(
            session,
            ctx,
            "storage.multipart_abort.requested",
            "storage.multipart_upload",
            upload_id,
            {"job_id": job.job_id, "upload_version": upload.upload_version},
        )
        return {
            "upload_id": upload_id,
            "upload_version": upload.upload_version,
            "status": upload.status,
            "job": job_projection(job),
            "audit_ref": {"audit_event_id": event_id, "request_id": ctx.request_id},
            "scope": _scope_body(scope),
            "request_id": ctx.request_id,
            "contract_version": "v1",
        }

    return await with_idempotency(
        session, idempotency_key, (scope, "abortMultipart", upload_id), command
    )
