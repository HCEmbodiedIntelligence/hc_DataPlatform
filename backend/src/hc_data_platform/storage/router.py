from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from hc_data_platform.core.context import current_request_context, select_request_scope
from hc_data_platform.security.http import VerifiedAuth
from hc_data_platform.security.scope import ScopeGuard

from .models import (
    AbortMultipartUploadRequest,
    ApproveLifecycleExecutionRequest,
    CancelLifecycleExecutionRequest,
    CapacityHistory,
    CapacityInventoryPage,
    CapacityPortfolio,
    CapacitySnapshot,
    CreateLifecyclePolicy,
    CreateLifecycleScheduleRequest,
    LifecycleAuditPage,
    LifecycleDryRunRequest,
    LifecycleExecution,
    LifecycleExecutionLogPage,
    LifecycleExecutionPage,
    LifecyclePolicy,
    LifecyclePolicyPage,
    LifecycleSchedule,
    LifecycleSchedulePage,
    ManagedMultipartUpload,
    ManagedStorageObject,
    ManagedStorageObjectPage,
    RestoreStorageObjectRequest,
    RetryLifecycleExecutionRequest,
    StartLifecycleExecutionRequest,
    StorageObjectDownloadGrant,
    TransitionStorageObjectRequest,
    TrashStorageObjectRequest,
    UpdateLifecyclePolicy,
    UpdateLifecycleScheduleRequest,
)
from .service import StorageGovernanceService

router = APIRouter(prefix="/api/v1/projects/{project_id}/storage", tags=["storage"])

_service = StorageGovernanceService.in_memory()


def configure_storage_governance(service: StorageGovernanceService) -> None:
    global _service
    _service = service


def get_storage_governance_service() -> StorageGovernanceService:
    return _service


ServiceDependency = Annotated[
    StorageGovernanceService,
    Depends(get_storage_governance_service),
]
IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=256),
]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=1, max_length=256)]
Cursor = Annotated[str | None, Query(max_length=16384)]
Limit = Annotated[int, Query(ge=1, le=100)]
HistoryStart = Annotated[datetime | None, Query(alias="from")]
HistoryEnd = Annotated[datetime | None, Query(alias="to")]


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) and value else "request-id-unavailable"


def _mutation_headers(response: Response, *, etag: str | None, replayed: bool) -> None:
    if etag is not None:
        response.headers["ETag"] = etag
    response.headers["Idempotency-Replayed"] = "true" if replayed else "false"
    response.headers["Cache-Control"] = "no-store"


def _select_project_scope(auth: VerifiedAuth, project_id: str) -> None:
    """Bind DB RLS to the verified path scope, never a caller-supplied header."""

    request_scope = current_request_context()
    ScopeGuard.require(
        auth,
        project_id,
        request_scope.region_code,
        request_scope.organization_id,
    )
    select_request_scope(
        project_id,
        request_scope.region_code,
        organization_id=request_scope.organization_id,
    )


def _read_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


@router.get(
    "/capacity",
    response_model=CapacitySnapshot,
    operation_id="getStorageCapacity",
)
def get_capacity_snapshot(
    project_id: str,
    response: Response,
    auth: VerifiedAuth,
    service: ServiceDependency,
    snapshot_id: str | None = Query(default=None, min_length=1, max_length=256),
) -> CapacitySnapshot:
    _select_project_scope(auth, project_id)
    _read_headers(response)
    return service.capacity_snapshot(
        project_id=project_id,
        actor=auth,
        snapshot_id=snapshot_id,
    )


@router.get(
    "/capacity/history",
    response_model=CapacityHistory,
    operation_id="getStorageCapacityHistory",
)
def get_capacity_history(
    project_id: str,
    response: Response,
    auth: VerifiedAuth,
    service: ServiceDependency,
    window_start: HistoryStart = None,
    window_end: HistoryEnd = None,
) -> CapacityHistory:
    _select_project_scope(auth, project_id)
    _read_headers(response)
    return service.capacity_history(
        project_id=project_id,
        actor=auth,
        window_start=window_start,
        window_end=window_end,
    )


@router.get(
    "/capacity/portfolio",
    response_model=CapacityPortfolio,
    operation_id="getStorageCapacityPortfolio",
)
def get_capacity_portfolio(
    project_id: str,
    response: Response,
    auth: VerifiedAuth,
    service: ServiceDependency,
    project_ids: Annotated[list[str] | None, Query(alias="project_id")] = None,
) -> CapacityPortfolio:
    _select_project_scope(auth, project_id)
    _read_headers(response)
    return service.capacity_portfolio(
        project_ids=project_ids or [project_id],
        actor=auth,
    )


@router.get(
    "/inventory",
    response_model=CapacityInventoryPage,
    operation_id="listStorageInventory",
)
def list_capacity_inventory(
    project_id: str,
    response: Response,
    auth: VerifiedAuth,
    service: ServiceDependency,
    snapshot_id: str | None = Query(default=None, min_length=1, max_length=256),
    cursor: Cursor = None,
    limit: Limit = 50,
) -> CapacityInventoryPage:
    _select_project_scope(auth, project_id)
    _read_headers(response)
    return service.inventory_page(
        project_id=project_id,
        actor=auth,
        snapshot_id=snapshot_id,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/objects",
    response_model=ManagedStorageObjectPage,
    operation_id="listManagedStorageObjects",
)
def list_managed_storage_objects(
    project_id: str,
    response: Response,
    auth: VerifiedAuth,
    service: ServiceDependency,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> ManagedStorageObjectPage:
    _select_project_scope(auth, project_id)
    _read_headers(response)
    return service.list_managed_objects(
        project_id=project_id,
        actor=auth,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/objects/{object_id}",
    response_model=ManagedStorageObject,
    operation_id="getManagedStorageObject",
)
def get_managed_storage_object(
    project_id: str,
    object_id: str,
    response: Response,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> ManagedStorageObject:
    _select_project_scope(auth, project_id)
    _read_headers(response)
    value = service.get_managed_object(
        project_id=project_id,
        object_id=object_id,
        actor=auth,
    )
    response.headers["ETag"] = value.etag
    return value


@router.post(
    "/objects/{object_id}:download",
    response_model=StorageObjectDownloadGrant,
    operation_id="authorizeManagedStorageObjectDownload",
)
def authorize_managed_storage_object_download(
    project_id: str,
    object_id: str,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> StorageObjectDownloadGrant:
    _select_project_scope(auth, project_id)
    value = service.authorize_object_download(
        project_id=project_id,
        object_id=object_id,
        actor=auth,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    _mutation_headers(response, etag=None, replayed=False)
    return value


@router.post(
    "/objects/{object_id}:trash",
    response_model=ManagedStorageObject,
    operation_id="trashManagedStorageObject",
)
def trash_managed_storage_object(
    project_id: str,
    object_id: str,
    command: TrashStorageObjectRequest,
    request: Request,
    response: Response,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> ManagedStorageObject:
    _select_project_scope(auth, project_id)
    value = service.trash_object(
        project_id=project_id,
        object_id=object_id,
        command=command,
        actor=auth,
        if_match=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    _mutation_headers(response, etag=value.etag, replayed=False)
    return value


@router.post(
    "/objects/{object_id}:restore",
    response_model=ManagedStorageObject,
    operation_id="restoreManagedStorageObject",
)
def restore_managed_storage_object(
    project_id: str,
    object_id: str,
    command: RestoreStorageObjectRequest,
    request: Request,
    response: Response,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> ManagedStorageObject:
    _select_project_scope(auth, project_id)
    value = service.restore_object(
        project_id=project_id,
        object_id=object_id,
        command=command,
        actor=auth,
        if_match=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    _mutation_headers(response, etag=value.etag, replayed=False)
    return value


@router.post(
    "/objects/{object_id}:transition",
    response_model=ManagedStorageObject,
    operation_id="transitionManagedStorageObject",
)
def transition_managed_storage_object(
    project_id: str,
    object_id: str,
    command: TransitionStorageObjectRequest,
    request: Request,
    response: Response,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> ManagedStorageObject:
    _select_project_scope(auth, project_id)
    value = service.transition_object(
        project_id=project_id,
        object_id=object_id,
        command=command,
        actor=auth,
        if_match=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    _mutation_headers(response, etag=value.etag, replayed=False)
    return value


@router.post(
    "/multipart-uploads/{multipart_id}:abort",
    response_model=ManagedMultipartUpload,
    operation_id="abortManagedStorageMultipartUpload",
)
def abort_managed_storage_multipart_upload(
    project_id: str,
    multipart_id: str,
    command: AbortMultipartUploadRequest,
    request: Request,
    response: Response,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> ManagedMultipartUpload:
    _select_project_scope(auth, project_id)
    value = service.abort_multipart(
        project_id=project_id,
        multipart_id=multipart_id,
        reason=command.reason,
        actor=auth,
        if_match=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    _mutation_headers(response, etag=value.etag, replayed=False)
    return value


@router.get(
    "/lifecycle-policies",
    response_model=LifecyclePolicyPage,
    operation_id="listLifecyclePolicies",
)
def list_lifecycle_policies(
    project_id: str,
    response: Response,
    auth: VerifiedAuth,
    service: ServiceDependency,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> LifecyclePolicyPage:
    _select_project_scope(auth, project_id)
    _read_headers(response)
    return service.list_policies(
        project_id=project_id,
        actor=auth,
        cursor=cursor,
        limit=limit,
    )


@router.post(
    "/lifecycle-policies",
    response_model=LifecyclePolicy,
    status_code=201,
    operation_id="createLifecyclePolicy",
)
def create_lifecycle_policy(
    project_id: str,
    command: CreateLifecyclePolicy,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> LifecyclePolicy:
    _select_project_scope(auth, project_id)
    outcome = service.create_policy(
        project_id=project_id,
        command=command,
        actor=auth,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    assert outcome.policy is not None
    _mutation_headers(response, etag=outcome.policy.etag, replayed=outcome.replayed)
    return outcome.policy


@router.get(
    "/lifecycle-policies/{policy_id}",
    response_model=LifecyclePolicy,
    operation_id="getLifecyclePolicy",
)
def get_lifecycle_policy(
    project_id: str,
    policy_id: str,
    response: Response,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> LifecyclePolicy:
    _select_project_scope(auth, project_id)
    _read_headers(response)
    policy = service.get_policy(project_id=project_id, policy_id=policy_id, actor=auth)
    response.headers["ETag"] = policy.etag
    return policy


@router.put(
    "/lifecycle-policies/{policy_id}",
    response_model=LifecyclePolicy,
    operation_id="updateLifecyclePolicy",
)
def update_lifecycle_policy(
    project_id: str,
    policy_id: str,
    command: UpdateLifecyclePolicy,
    request: Request,
    response: Response,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> LifecyclePolicy:
    _select_project_scope(auth, project_id)
    outcome = service.update_policy(
        project_id=project_id,
        policy_id=policy_id,
        command=command,
        actor=auth,
        if_match=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    assert outcome.policy is not None
    _mutation_headers(response, etag=outcome.policy.etag, replayed=outcome.replayed)
    return outcome.policy


@router.post(
    "/lifecycle-policies/{policy_id}/enable",
    response_model=LifecyclePolicy,
    operation_id="enableLifecyclePolicy",
)
def enable_lifecycle_policy(
    project_id: str,
    policy_id: str,
    request: Request,
    response: Response,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> LifecyclePolicy:
    _select_project_scope(auth, project_id)
    outcome = service.enable_policy(
        project_id=project_id,
        policy_id=policy_id,
        actor=auth,
        if_match=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    assert outcome.policy is not None
    _mutation_headers(response, etag=outcome.policy.etag, replayed=outcome.replayed)
    return outcome.policy


@router.post(
    "/lifecycle-policies/{policy_id}/pause",
    response_model=LifecyclePolicy,
    operation_id="pauseLifecyclePolicy",
)
def pause_lifecycle_policy(
    project_id: str,
    policy_id: str,
    request: Request,
    response: Response,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> LifecyclePolicy:
    _select_project_scope(auth, project_id)
    outcome = service.pause_policy(
        project_id=project_id,
        policy_id=policy_id,
        actor=auth,
        if_match=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    assert outcome.policy is not None
    _mutation_headers(response, etag=outcome.policy.etag, replayed=outcome.replayed)
    return outcome.policy


@router.delete(
    "/lifecycle-policies/{policy_id}",
    status_code=204,
    operation_id="deleteLifecyclePolicy",
)
def delete_lifecycle_policy(
    project_id: str,
    policy_id: str,
    request: Request,
    response: Response,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> None:
    _select_project_scope(auth, project_id)
    outcome = service.delete_policy(
        project_id=project_id,
        policy_id=policy_id,
        actor=auth,
        if_match=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    _mutation_headers(response, etag=None, replayed=outcome.replayed)


@router.get(
    "/lifecycle-schedules",
    response_model=LifecycleSchedulePage,
    operation_id="listLifecycleSchedules",
)
def list_lifecycle_schedules(
    project_id: str,
    response: Response,
    auth: VerifiedAuth,
    service: ServiceDependency,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> LifecycleSchedulePage:
    _select_project_scope(auth, project_id)
    _read_headers(response)
    return service.list_lifecycle_schedules(
        project_id=project_id,
        actor=auth,
        cursor=cursor,
        limit=limit,
    )


@router.post(
    "/lifecycle-schedules",
    response_model=LifecycleSchedule,
    status_code=201,
    operation_id="createLifecycleSchedule",
)
def create_lifecycle_schedule(
    project_id: str,
    command: CreateLifecycleScheduleRequest,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> LifecycleSchedule:
    _select_project_scope(auth, project_id)
    outcome = service.create_lifecycle_schedule_command(
        project_id=project_id,
        command=command,
        actor=auth,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    assert outcome.schedule is not None
    _mutation_headers(
        response,
        etag=outcome.schedule.etag,
        replayed=outcome.replayed,
    )
    return outcome.schedule


@router.get(
    "/lifecycle-schedules/{schedule_id}",
    response_model=LifecycleSchedule,
    operation_id="getLifecycleSchedule",
)
def get_lifecycle_schedule(
    project_id: str,
    schedule_id: str,
    response: Response,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> LifecycleSchedule:
    _select_project_scope(auth, project_id)
    _read_headers(response)
    schedule = service.get_lifecycle_schedule(
        project_id=project_id,
        schedule_id=schedule_id,
        actor=auth,
    )
    response.headers["ETag"] = schedule.etag
    return schedule


@router.put(
    "/lifecycle-schedules/{schedule_id}",
    response_model=LifecycleSchedule,
    operation_id="updateLifecycleSchedule",
)
def update_lifecycle_schedule(
    project_id: str,
    schedule_id: str,
    command: UpdateLifecycleScheduleRequest,
    request: Request,
    response: Response,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> LifecycleSchedule:
    _select_project_scope(auth, project_id)
    outcome = service.update_lifecycle_schedule_command(
        project_id=project_id,
        schedule_id=schedule_id,
        command=command,
        actor=auth,
        if_match=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    assert outcome.schedule is not None
    _mutation_headers(
        response,
        etag=outcome.schedule.etag,
        replayed=outcome.replayed,
    )
    return outcome.schedule


@router.delete(
    "/lifecycle-schedules/{schedule_id}",
    status_code=204,
    operation_id="deleteLifecycleSchedule",
)
def delete_lifecycle_schedule(
    project_id: str,
    schedule_id: str,
    request: Request,
    response: Response,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> None:
    _select_project_scope(auth, project_id)
    outcome = service.delete_lifecycle_schedule_command(
        project_id=project_id,
        schedule_id=schedule_id,
        actor=auth,
        if_match=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    _mutation_headers(response, etag=None, replayed=outcome.replayed)


@router.post(
    "/lifecycle-schedules/{schedule_id}/enable",
    response_model=LifecycleSchedule,
    operation_id="enableLifecycleSchedule",
)
def enable_lifecycle_schedule(
    project_id: str,
    schedule_id: str,
    request: Request,
    response: Response,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> LifecycleSchedule:
    _select_project_scope(auth, project_id)
    outcome = service.enable_lifecycle_schedule_command(
        project_id=project_id,
        schedule_id=schedule_id,
        actor=auth,
        if_match=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    assert outcome.schedule is not None
    _mutation_headers(
        response,
        etag=outcome.schedule.etag,
        replayed=outcome.replayed,
    )
    return outcome.schedule


@router.post(
    "/lifecycle-schedules/{schedule_id}/pause",
    response_model=LifecycleSchedule,
    operation_id="pauseLifecycleSchedule",
)
def pause_lifecycle_schedule(
    project_id: str,
    schedule_id: str,
    request: Request,
    response: Response,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> LifecycleSchedule:
    _select_project_scope(auth, project_id)
    outcome = service.pause_lifecycle_schedule_command(
        project_id=project_id,
        schedule_id=schedule_id,
        actor=auth,
        if_match=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    assert outcome.schedule is not None
    _mutation_headers(
        response,
        etag=outcome.schedule.etag,
        replayed=outcome.replayed,
    )
    return outcome.schedule


@router.get(
    "/lifecycle-audit",
    response_model=LifecycleAuditPage,
    operation_id="listLifecycleAudit",
)
def list_lifecycle_audit(
    project_id: str,
    response: Response,
    auth: VerifiedAuth,
    service: ServiceDependency,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> LifecycleAuditPage:
    _select_project_scope(auth, project_id)
    _read_headers(response)
    return service.list_audit(
        project_id=project_id,
        actor=auth,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/lifecycle-executions",
    response_model=LifecycleExecutionPage,
    operation_id="listLifecycleExecutions",
)
def list_lifecycle_executions(
    project_id: str,
    response: Response,
    auth: VerifiedAuth,
    service: ServiceDependency,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> LifecycleExecutionPage:
    _select_project_scope(auth, project_id)
    _read_headers(response)
    return service.list_lifecycle_executions(
        project_id=project_id,
        actor=auth,
        cursor=cursor,
        limit=limit,
    )


@router.post(
    "/lifecycle-executions:dry-run",
    response_model=LifecycleExecution,
    status_code=201,
    operation_id="createLifecycleExecutionDryRun",
)
def create_lifecycle_execution_dry_run(
    project_id: str,
    command: LifecycleDryRunRequest,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> LifecycleExecution:
    _select_project_scope(auth, project_id)
    outcome = service.create_lifecycle_dry_run_command(
        project_id=project_id,
        command=command,
        actor=auth,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    _mutation_headers(response, etag=None, replayed=outcome.replayed)
    return outcome.execution


@router.get(
    "/lifecycle-executions/{execution_id}",
    response_model=LifecycleExecution,
    operation_id="getLifecycleExecution",
)
def get_lifecycle_execution(
    project_id: str,
    execution_id: str,
    response: Response,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> LifecycleExecution:
    _select_project_scope(auth, project_id)
    _read_headers(response)
    return service.get_lifecycle_execution(
        project_id=project_id,
        execution_id=execution_id,
        actor=auth,
    )


@router.get(
    "/lifecycle-executions/{execution_id}/logs",
    response_model=LifecycleExecutionLogPage,
    operation_id="listLifecycleExecutionLogs",
)
def list_lifecycle_execution_logs(
    project_id: str,
    execution_id: str,
    response: Response,
    auth: VerifiedAuth,
    service: ServiceDependency,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> LifecycleExecutionLogPage:
    _select_project_scope(auth, project_id)
    _read_headers(response)
    return service.list_lifecycle_execution_logs(
        project_id=project_id,
        execution_id=execution_id,
        actor=auth,
        cursor=cursor,
        limit=limit,
    )


@router.post(
    "/lifecycle-executions/{execution_id}:approve",
    response_model=LifecycleExecution,
    operation_id="approveLifecycleExecution",
)
def approve_lifecycle_execution(
    project_id: str,
    execution_id: str,
    command: ApproveLifecycleExecutionRequest,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> LifecycleExecution:
    _select_project_scope(auth, project_id)
    outcome = service.approve_lifecycle_execution_command(
        project_id=project_id,
        execution_id=execution_id,
        command=command,
        actor=auth,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    _mutation_headers(response, etag=None, replayed=outcome.replayed)
    return outcome.execution


@router.post(
    "/lifecycle-executions/{execution_id}:start",
    response_model=LifecycleExecution,
    status_code=202,
    operation_id="startLifecycleExecution",
)
def start_lifecycle_execution(
    project_id: str,
    execution_id: str,
    command: StartLifecycleExecutionRequest,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> LifecycleExecution:
    _select_project_scope(auth, project_id)
    outcome = service.start_lifecycle_execution_command(
        project_id=project_id,
        execution_id=execution_id,
        command=command,
        actor=auth,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    _mutation_headers(response, etag=None, replayed=outcome.replayed)
    return outcome.execution


@router.post(
    "/lifecycle-executions/{execution_id}:cancel",
    response_model=LifecycleExecution,
    operation_id="cancelLifecycleExecution",
)
def cancel_lifecycle_execution(
    project_id: str,
    execution_id: str,
    command: CancelLifecycleExecutionRequest,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> LifecycleExecution:
    _select_project_scope(auth, project_id)
    outcome = service.cancel_lifecycle_execution_command(
        project_id=project_id,
        execution_id=execution_id,
        command=command,
        actor=auth,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    _mutation_headers(response, etag=None, replayed=outcome.replayed)
    return outcome.execution


@router.post(
    "/lifecycle-executions/{execution_id}:retry",
    response_model=LifecycleExecution,
    status_code=202,
    operation_id="retryLifecycleExecution",
)
def retry_lifecycle_execution(
    project_id: str,
    execution_id: str,
    command: RetryLifecycleExecutionRequest,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> LifecycleExecution:
    _select_project_scope(auth, project_id)
    outcome = service.retry_lifecycle_execution_command(
        project_id=project_id,
        execution_id=execution_id,
        command=command,
        actor=auth,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    _mutation_headers(response, etag=None, replayed=outcome.replayed)
    return outcome.execution
