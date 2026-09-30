"""Scoped explicit primary creation; ordinary annotation saves remain separate."""

from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, Header, Query, Response

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import problem
from hc_data_platform.dataset_registry.ingest_projection import DatasetIngestProjectionConflict
from hc_data_platform.security.http import VerifiedAuth, authorize_scope

from .derivation_jobs import (
    RequestTagEpisode,
    TagDerivationGeneration,
    TagDerivationView,
    tag_job_view,
)
from .postgres import PackageConflict

router = APIRouter(tags=["episode_data"])
BASE = "/projects/{project_id}/regions/{region_code}/annotation-tasks/{task_id}/tag-episodes"


@contextmanager
def derivations(auth, project_id, region_code):
    from .router import _service

    organization = current_request_context().organization_id
    if not organization:
        raise problem(
            status=422,
            code="ORGANIZATION_SCOPE_REQUIRED",
            title="Organization required",
            detail="Select an organization.",
        )
    authorize_scope(auth, project_id, "dataset_version.read", region_code, organization)
    if _service is None or _service.tag_derivations is None:
        raise problem(
            status=503,
            code="TAG_EPISODE_UNAVAILABLE",
            title="Tag creation unavailable",
            detail="The Tag creation service is not configured.",
        )
    try:
        yield _service.tag_derivations
    except KeyError as exc:
        raise problem(
            status=404,
            code="TAG_EPISODE_DATA_NOT_FOUND",
            title="Tag data unavailable",
            detail="The selected parent data or annotation is unavailable.",
        ) from exc
    except PermissionError as exc:
        raise problem(
            status=403,
            code="TAG_EPISODE_FORBIDDEN",
            title="Tag creation forbidden",
            detail="Selection differs from the authorized scope.",
        ) from exc
    except (PackageConflict, DatasetIngestProjectionConflict) as exc:
        raise problem(
            status=409,
            code="TAG_EPISODE_PARENT_INTEGRITY_FAILED",
            title="Parent integrity failed",
            detail="The fixed parent package or product projection failed verification.",
        ) from exc
    except ValueError as exc:
        raise problem(
            status=422,
            code="TAG_EPISODE_SELECTION_INVALID",
            title="Invalid Tag selection",
            detail=str(exc),
        ) from exc


@router.post(BASE, response_model=TagDerivationView)
def create_tag_episode(
    project_id: str,
    region_code: str,
    task_id: str,
    body: RequestTagEpisode,
    auth: VerifiedAuth,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=160)],
):
    """Create a separate primary Episode from one fixed Tag revision/interval.

    Requires upload.manage, dataset_version.read, annotation_task.read and export.download.
    Original tags and media remain immutable; approvals are not inherited.
    """
    response.headers["Cache-Control"] = "no-store"
    with derivations(auth, project_id, region_code) as service:
        return tag_job_view(service.request(task_id, body, idempotency_key, auth))


@router.get(BASE, response_model=tuple[TagDerivationView, ...])
def list_tag_episodes(
    project_id: str,
    region_code: str,
    task_id: str,
    auth: VerifiedAuth,
    response: Response,
    limit: int = Query(25, ge=1, le=100),
    before: str | None = Query(None, pattern=r"^td_[0-9a-f]{64}$"),
):
    response.headers["Cache-Control"] = "no-store"
    with derivations(auth, project_id, region_code) as service:
        return tuple(
            tag_job_view(job) for job in service.list(task_id, auth, limit=limit, before=before)
        )


@router.get(BASE + "/{job_id}", response_model=TagDerivationView)
def get_tag_episode(
    project_id: str,
    region_code: str,
    task_id: str,
    job_id: str,
    auth: VerifiedAuth,
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    with derivations(auth, project_id, region_code) as service:
        return tag_job_view(service.status(task_id, job_id, auth))


@router.post(BASE + "/{job_id}/retry", response_model=TagDerivationView)
def retry_tag_episode(
    project_id: str,
    region_code: str,
    task_id: str,
    job_id: str,
    body: TagDerivationGeneration,
    auth: VerifiedAuth,
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    with derivations(auth, project_id, region_code) as service:
        return tag_job_view(
            service.command(task_id, job_id, body.expected_generation, auth, retry=True)
        )


@router.post(BASE + "/{job_id}/cancel", response_model=TagDerivationView)
def cancel_tag_episode(
    project_id: str,
    region_code: str,
    task_id: str,
    job_id: str,
    body: TagDerivationGeneration,
    auth: VerifiedAuth,
    response: Response,
):
    """Cancel only a never-started command; started primary data is never deleted."""
    response.headers["Cache-Control"] = "no-store"
    with derivations(auth, project_id, region_code) as service:
        return tag_job_view(
            service.command(task_id, job_id, body.expected_generation, auth, retry=False)
        )
