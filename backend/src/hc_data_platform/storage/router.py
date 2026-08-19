from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from hc_data_platform.security.http import VerifiedAuth

from .models import (
    CapacityInventoryPage,
    CapacitySnapshot,
    CreateLifecyclePolicy,
    LifecycleAuditPage,
    LifecyclePolicy,
    LifecyclePolicyPage,
    UpdateLifecyclePolicy,
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


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) and value else "request-id-unavailable"


def _mutation_headers(response: Response, *, etag: str | None, replayed: bool) -> None:
    if etag is not None:
        response.headers["ETag"] = etag
    response.headers["Idempotency-Replayed"] = "true" if replayed else "false"
    response.headers["Cache-Control"] = "no-store"


@router.get(
    "/capacity",
    response_model=CapacitySnapshot,
    operation_id="getStorageCapacity",
)
def get_capacity_snapshot(
    project_id: str,
    auth: VerifiedAuth,
    service: ServiceDependency,
    snapshot_id: str | None = Query(default=None, min_length=1, max_length=256),
) -> CapacitySnapshot:
    return service.capacity_snapshot(
        project_id=project_id,
        actor=auth,
        snapshot_id=snapshot_id,
    )


@router.get(
    "/inventory",
    response_model=CapacityInventoryPage,
    operation_id="listStorageInventory",
)
def list_capacity_inventory(
    project_id: str,
    auth: VerifiedAuth,
    service: ServiceDependency,
    snapshot_id: str | None = Query(default=None, min_length=1, max_length=256),
    cursor: Cursor = None,
    limit: Limit = 50,
) -> CapacityInventoryPage:
    return service.inventory_page(
        project_id=project_id,
        actor=auth,
        snapshot_id=snapshot_id,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/lifecycle-policies",
    response_model=LifecyclePolicyPage,
    operation_id="listLifecyclePolicies",
)
def list_lifecycle_policies(
    project_id: str,
    auth: VerifiedAuth,
    service: ServiceDependency,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> LifecyclePolicyPage:
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
    "/lifecycle-audit",
    response_model=LifecycleAuditPage,
    operation_id="listLifecycleAudit",
)
def list_lifecycle_audit(
    project_id: str,
    auth: VerifiedAuth,
    service: ServiceDependency,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> LifecycleAuditPage:
    return service.list_audit(
        project_id=project_id,
        actor=auth,
        cursor=cursor,
        limit=limit,
    )
