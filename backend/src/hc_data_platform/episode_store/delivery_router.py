"""Complete-package commands require the same exact scope on every cache access."""

from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, Header, Query, Response

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import problem
from hc_data_platform.security.http import VerifiedAuth, authorize_scope

from .delivery_models import (
    EpisodeDeliveryDownload,
    EpisodeDeliveryGeneration,
    EpisodeDeliveryJob,
    RequestEpisodeDelivery,
)
from .postgres import PackageConflict

router = APIRouter(tags=["episode_data"])
BASE = "/projects/{project_id}/regions/{region_code}/episode-deliveries"


@contextmanager
def delivery_service(auth, project_id, region_code):
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
    if _service is None:
        raise problem(
            status=503,
            code="EPISODE_PACKAGES_UNAVAILABLE",
            title="Episode service unavailable",
            detail="Package service is not configured.",
        )
    try:
        yield _service.deliveries
    except KeyError as exc:
        raise problem(
            status=404,
            code="EPISODE_DATA_NOT_FOUND",
            title="Episode not found",
            detail="A selected data version is unavailable.",
        ) from exc
    except PermissionError as exc:
        raise problem(
            status=403,
            code="EPISODE_DATA_FORBIDDEN",
            title="Episode forbidden",
            detail="Selection differs from the authorized scope.",
        ) from exc
    except PackageConflict as exc:
        raise problem(
            status=409,
            code="EPISODE_DELIVERY_INTEGRITY_FAILED",
            title="Episode integrity failed",
            detail="A selected package or archive failed verification.",
        ) from exc
    except ValueError as exc:
        raise problem(
            status=422,
            code="EPISODE_DELIVERY_SELECTION_INVALID",
            title="Invalid selection",
            detail=str(exc),
        ) from exc


@router.post(BASE, response_model=EpisodeDeliveryJob)
def request_episode_delivery(
    project_id: str,
    region_code: str,
    body: RequestEpisodeDelivery,
    auth: VerifiedAuth,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=160)],
):
    response.headers["Cache-Control"] = "no-store"
    with delivery_service(auth, project_id, region_code) as service:
        return service.request(body, idempotency_key, auth)


@router.get(BASE, response_model=tuple[EpisodeDeliveryJob, ...])
def list_episode_deliveries(
    project_id: str,
    region_code: str,
    auth: VerifiedAuth,
    response: Response,
    data_version_id: str | None = Query(None, max_length=256),
    limit: int = Query(25, ge=1, le=100),
    before: str | None = Query(None, pattern=r"^ed_[0-9a-f]{64}$"),
):
    response.headers["Cache-Control"] = "no-store"
    with delivery_service(auth, project_id, region_code) as service:
        return service.list(auth, data_version_id=data_version_id, limit=limit, before=before)


@router.get(BASE + "/{job_id}", response_model=EpisodeDeliveryJob)
def get_episode_delivery(
    project_id: str, region_code: str, job_id: str, auth: VerifiedAuth, response: Response
):
    response.headers["Cache-Control"] = "no-store"
    with delivery_service(auth, project_id, region_code) as service:
        return service.status(job_id, auth)


@router.post(BASE + "/{job_id}/retry", response_model=EpisodeDeliveryJob)
def retry_episode_delivery(
    project_id: str,
    region_code: str,
    job_id: str,
    body: EpisodeDeliveryGeneration,
    auth: VerifiedAuth,
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    with delivery_service(auth, project_id, region_code) as service:
        return service.command(job_id, body.expected_generation, auth, retry=True)


@router.post(BASE + "/{job_id}/cancel", response_model=EpisodeDeliveryJob)
def cancel_episode_delivery(
    project_id: str,
    region_code: str,
    job_id: str,
    body: EpisodeDeliveryGeneration,
    auth: VerifiedAuth,
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    with delivery_service(auth, project_id, region_code) as service:
        return service.command(job_id, body.expected_generation, auth, retry=False)


@router.post(BASE + "/{job_id}/download", response_model=EpisodeDeliveryDownload)
def download_episode_delivery(
    project_id: str, region_code: str, job_id: str, auth: VerifiedAuth, response: Response
):
    response.headers["Cache-Control"] = "no-store"
    with delivery_service(auth, project_id, region_code) as service:
        return service.download(job_id, auth)
