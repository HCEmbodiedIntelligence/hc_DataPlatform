from __future__ import annotations

import inspect
import os
from collections.abc import Awaitable
from functools import lru_cache
from typing import TypeVar, cast

from fastapi import APIRouter

from hc_data_platform.core.config import get_settings
from hc_data_platform.core.errors import problem
from hc_data_platform.security import Permission
from hc_data_platform.security.http import VerifiedAuth, authorize_read, authorize_scope

from .models import JobListV1, JobRecord, JobStatus
from .service import InMemoryWorkflowLauncher, TemporalWorkflowLauncher
from .worker import DEFAULT_TASK_QUEUE

router = APIRouter(prefix="/api/v1", tags=["jobs"])
_T = TypeVar("_T")


@lru_cache(maxsize=1)
def get_launcher() -> InMemoryWorkflowLauncher | TemporalWorkflowLauncher:
    settings = get_settings()
    configured_backend = os.getenv("HC_WORKFLOW_BACKEND")
    backend = configured_backend or (
        "temporal" if settings.runtime_backend == "production" else "memory"
    )
    if backend.lower() == "temporal":
        return TemporalWorkflowLauncher(
            settings.temporal_target,
            namespace=os.getenv("HC_TEMPORAL_NAMESPACE", "default"),
            task_queue=os.getenv("HC_TEMPORAL_TASK_QUEUE", DEFAULT_TASK_QUEUE),
        )
    return InMemoryWorkflowLauncher()


async def _resolve(value: _T | Awaitable[_T]) -> _T:
    if inspect.isawaitable(value):
        return await cast(Awaitable[_T], value)
    return value


@router.get("/jobs", response_model=JobListV1)
async def list_jobs(
    auth: VerifiedAuth,
    project_id: str | None = None,
    status: JobStatus | None = None,
) -> JobListV1:
    selected_project = project_id
    if selected_project is not None:
        authorize_read(auth, selected_project)
    elif "admin" not in auth.roles:
        if len(auth.project_ids) != 1:
            raise problem(
                status=400,
                code="PROJECT_SCOPE_REQUIRED",
                title="Project scope required",
                detail="Select project_id when the access token has zero or multiple projects.",
            )
        selected_project = next(iter(auth.project_ids))
        authorize_read(auth, selected_project)
    else:
        auth.require_permission(Permission.READ)
    result = get_launcher().list(project_id=selected_project, status=status)
    return await _resolve(result)


@router.get("/jobs/{job_id}", response_model=JobRecord)
async def get_job(job_id: str, auth: VerifiedAuth) -> JobRecord:
    job = await _resolve(get_launcher().get(job_id))
    authorize_read(auth, job.project_id)
    return job


@router.post("/jobs/{job_id}:cancel", response_model=JobRecord)
async def cancel_job(job_id: str, auth: VerifiedAuth) -> JobRecord:
    job = await _resolve(get_launcher().get(job_id))
    authorize_scope(auth, job.project_id, Permission.ADMINISTER)
    return await _resolve(get_launcher().cancel(job_id))
