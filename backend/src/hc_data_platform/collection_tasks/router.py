from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response

from hc_data_platform.security.auth import AuthContext, Permission
from hc_data_platform.security.http import VerifiedAuth, authorize_scope

from .models import (
    CollectionTask,
    CollectionTaskPage,
    CollectionTaskProgress,
    CollectionTaskStatus,
    CreateCollectionTask,
    Identifier,
    UpdateCollectionTask,
)
from .service import CollectionTaskService, CommandResult

router = APIRouter(prefix="/api/v1", tags=["collection-tasks"])

_service = CollectionTaskService()


def configure_collection_tasks(service: CollectionTaskService) -> None:
    global _service
    _service = service


def get_collection_task_service() -> CollectionTaskService:
    return _service


Service = Annotated[CollectionTaskService, Depends(get_collection_task_service)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=1, max_length=128)]
RegionCode = Annotated[
    str,
    Header(alias="X-Region-Code", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$"),
]


def _authorize_read(auth: AuthContext, project_id: str) -> None:
    authorize_scope(auth, project_id, Permission.READ)


def _authorize_manage(auth: AuthContext, project_id: str) -> None:
    authorize_scope(auth, project_id, Permission.UPLOAD)


def _command_headers(
    response: Response,
    service: CollectionTaskService,
    result: CommandResult,
) -> None:
    response.headers["ETag"] = service.etag(result.record)
    response.headers["Idempotency-Replayed"] = "true" if result.replayed else "false"


@router.post(
    "/projects/{project_id}/collection-tasks",
    response_model=CollectionTask,
    status_code=201,
    operation_id="createCollectionTask",
)
def create_collection_task(
    project_id: Identifier,
    command: CreateCollectionTask,
    response: Response,
    service: Service,
    auth: VerifiedAuth,
    idempotency_key: IdempotencyKey,
) -> CollectionTask:
    _authorize_manage(auth, project_id)
    result = service.create(
        project_id=project_id,
        command=command,
        idempotency_key=idempotency_key,
    )
    _command_headers(response, service, result)
    response.headers["Location"] = (
        f"/api/v1/projects/{project_id}/collection-tasks/{result.record.collection_task_id}"
    )
    return result.record.public()


@router.get(
    "/projects/{project_id}/collection-tasks",
    response_model=CollectionTaskPage,
    operation_id="listCollectionTasks",
)
def list_collection_tasks(
    project_id: Identifier,
    service: Service,
    auth: VerifiedAuth,
    status: Annotated[CollectionTaskStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(min_length=1, max_length=16_384)] = None,
) -> CollectionTaskPage:
    _authorize_read(auth, project_id)
    return service.list(project_id=project_id, status=status, limit=limit, cursor=cursor)


@router.get(
    "/projects/{project_id}/collection-tasks/{collection_task_id}",
    response_model=CollectionTask,
    operation_id="getCollectionTask",
)
def get_collection_task(
    project_id: Identifier,
    collection_task_id: Identifier,
    response: Response,
    service: Service,
    auth: VerifiedAuth,
) -> CollectionTask:
    _authorize_read(auth, project_id)
    record = service.detail(project_id, collection_task_id)
    response.headers["ETag"] = service.etag(record)
    return record.public()


@router.patch(
    "/projects/{project_id}/collection-tasks/{collection_task_id}",
    response_model=CollectionTask,
    operation_id="updateCollectionTask",
)
def update_collection_task(
    project_id: Identifier,
    collection_task_id: Identifier,
    command: UpdateCollectionTask,
    response: Response,
    service: Service,
    auth: VerifiedAuth,
    if_match: IfMatch,
) -> CollectionTask:
    _authorize_manage(auth, project_id)
    record = service.update(
        project_id=project_id,
        collection_task_id=collection_task_id,
        command=command,
        if_match=if_match,
    )
    response.headers["ETag"] = service.etag(record)
    return record.public()


@router.post(
    "/projects/{project_id}/collection-tasks/{collection_task_id}:close",
    response_model=CollectionTask,
    operation_id="closeCollectionTask",
)
def close_collection_task(
    project_id: Identifier,
    collection_task_id: Identifier,
    response: Response,
    service: Service,
    auth: VerifiedAuth,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
) -> CollectionTask:
    _authorize_manage(auth, project_id)
    result = service.close(
        project_id=project_id,
        collection_task_id=collection_task_id,
        if_match=if_match,
        idempotency_key=idempotency_key,
    )
    _command_headers(response, service, result)
    return result.record.public()


@router.get(
    "/projects/{project_id}/collection-tasks/{collection_task_id}/progress",
    response_model=CollectionTaskProgress,
    operation_id="getCollectionTaskProgress",
)
def get_collection_task_progress(
    project_id: Identifier,
    collection_task_id: Identifier,
    region_code: RegionCode,
    service: Service,
    auth: VerifiedAuth,
) -> CollectionTaskProgress:
    authorize_scope(auth, project_id, Permission.READ, region_code)
    return service.progress(project_id, collection_task_id, region_code)
