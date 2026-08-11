"""Canonical cross-domain asynchronous job polling endpoint."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Path, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext, get_context
from app.core.db import get_session
from app.core.errors import ForbiddenError, NotFoundError
from app.core.etag import compute_etag
from app.core.idempotency import IdempotencyRecord

router = APIRouter(prefix="/api/v1", tags=["Platform Jobs"])
Session = Annotated[AsyncSession, Depends(get_session)]
JobId = Annotated[str, Path(alias="jobId", min_length=1, max_length=256)]


def _scope_matches(scope: str, ctx: RequestContext) -> bool:
    try:
        value = json.loads(scope)
    except json.JSONDecodeError:
        value = scope
    if isinstance(value, dict):
        expected = {
            "organization_id": ctx.organization_id,
            "project_id": ctx.project_id,
            "region_code": ctx.region_code,
        }
        return all(value.get(key) == item for key, item in expected.items())
    rendered = str(value)
    return all(item in rendered for item in (ctx.organization_id, ctx.project_id, ctx.region_code))


def _find_job(value: Any, job_id: str) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if value.get("job_id") == job_id:
            return value
        for nested in value.values():
            found = _find_job(nested, job_id)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_job(nested, job_id)
            if found is not None:
                return found
    return None


def _read_capabilities(kind: str) -> frozenset[str]:
    normalized = kind.casefold()
    rules = (
        (("audit",), {"audit.read"}),
        (("calibration",), {"calibration.read"}),
        (("schema",), {"data_schema.read"}),
        (("robot_model",), {"robot_model.read"}),
        (("robot",), {"robot.read"}),
        (("cleaning",), {"cleaning.read"}),
        (("upload", "verification", "ingest"), {"upload.read"}),
        (("storage", "lifecycle", "restore", "multipart"), {"storage.lifecycle.read"}),
        (("dataset", "version", "export"), {"dataset.read"}),
    )
    return frozenset(
        capability
        for needles, capabilities in rules
        if any(needle in normalized for needle in needles)
        for capability in capabilities
    )


def _canonical_job(
    job: dict[str, Any], record: IdempotencyRecord, ctx: RequestContext
) -> dict[str, Any]:
    kind = str(job.get("kind") or job.get("job_type") or "")
    allowed = _read_capabilities(kind)
    if not allowed or not (allowed & ctx.capabilities):
        raise ForbiddenError(code="JOB_RESOURCE_READ_REQUIRED")
    created_at = job.get("created_at") or record.created_at
    updated_at = job.get("updated_at") or record.completed_at or record.created_at
    status_value = str(job.get("status", "QUEUED"))
    etag = str(job.get("etag") or compute_etag(str(updated_at)))
    resource_type = str(job.get("resource_type") or f"{kind.casefold()}.job")
    resource_id = str(job.get("resource_id") or job["job_id"])
    return {
        "job_id": str(job["job_id"]),
        "kind": kind,
        "status": status_value,
        "stage": str(job.get("stage") or status_value),
        "progress": job.get("progress"),
        "result_ref": job.get("result_ref"),
        "error": job.get("error"),
        "scope": {
            "organization_id": ctx.organization_id,
            "project_id": ctx.project_id,
            "region_code": ctx.region_code,
        },
        "resource_ref": {
            "resource_type": resource_type.replace("_", ".").casefold(),
            "resource_id": resource_id,
            "version": None,
            "etag": None,
        },
        "created_at": created_at,
        "updated_at": updated_at,
        "etag": etag,
        "cancellable": status_value in {"QUEUED", "RUNNING"},
        "retry_of_job_id": job.get("retry_of_job_id"),
    }


@router.get("/jobs/{jobId}", operation_id="getAsyncJob", response_model=None)
async def get_async_job(
    job_id: JobId,
    session: Session,
    response: Response,
    ctx: Annotated[RequestContext, Depends(get_context)],
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> dict[str, Any] | Response:
    records = list((await session.scalars(select(IdempotencyRecord))).all())
    match = next(
        (
            (record, job)
            for record in records
            if _scope_matches(record.scope, ctx)
            and (job := _find_job(record.response_body_or_resource_ref, job_id)) is not None
        ),
        None,
    )
    if match is None:
        raise NotFoundError(code="ASYNC_JOB_NOT_FOUND")
    value = _canonical_job(match[1], match[0], ctx)
    etag = value["etag"]
    if if_none_match and etag in {part.strip() for part in if_none_match.split(",")}:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
    response.headers["ETag"] = etag
    if value["status"] in {"QUEUED", "RUNNING", "CANCELLING"}:
        response.headers["Retry-After"] = "2"
    return value
