"""Verified tenant/capability boundary for immutable Episode data versions."""

from contextlib import contextmanager
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import problem
from hc_data_platform.security.http import VerifiedAuth, authorize_scope

from .download import complete_archive
from .models import DataVersionRef
from .postgres import PackageConflict
from .previews import EpisodePreviewGrant
from .quality import EpisodeQualityView, read_episode_quality
from .reader import PackageStepWindow
from .review_models import (
    EpisodeDataReview,
    EpisodeDataReviewAction,
    EpisodeDataReviewPage,
    SubmitEpisodeDataReview,
)
from .service import EpisodePackageService

BASE = (
    "/projects/{project_id}/regions/{region_code}/datasets/{dataset_id}"
    "/episodes/{episode_id}/data-versions"
)
VERSION = BASE + "/{data_version_id}"
router = APIRouter(prefix="/api/v1", tags=["episode_data"])
_service: EpisodePackageService | None = None


def configure_episode_packages(service: EpisodePackageService):
    global _service
    if _service is not None and _service is not service:
        _service.close()
    _service = service


@contextmanager
def authorized(auth, project_id, region_code, dataset_id, episode_id, *, download=False):
    organization = current_request_context().organization_id
    if not organization:
        raise problem(
            status=422,
            code="ORGANIZATION_SCOPE_REQUIRED",
            title="Organization scope required",
            detail="Select an organization before reading Episode data.",
        )
    authorize_scope(
        auth,
        project_id,
        "export.download" if download else "dataset_version.read",
        region_code,
        organization,
    )
    if _service is None:
        raise problem(
            status=503,
            code="EPISODE_PACKAGES_UNAVAILABLE",
            title="Episode data unavailable",
            detail="Episode package service is not configured.",
        )
    try:
        identity = _service.repository.identity(dataset_id, episode_id)
        yield _service, identity
    except KeyError as exc:
        raise problem(
            status=404,
            code="EPISODE_DATA_NOT_FOUND",
            title="Episode data not found",
            detail="The requested Episode version or modality is unavailable in this scope.",
        ) from exc
    except PermissionError as exc:
        raise problem(
            status=403,
            code="EPISODE_DATA_FORBIDDEN",
            title="Episode data forbidden",
            detail="This data reference is outside the authorized scope.",
        ) from exc
    except PackageConflict as exc:
        raise problem(
            status=409,
            code="EPISODE_PACKAGE_INTEGRITY_FAILED",
            title="Episode package integrity failed",
            detail="The committed package files could not be verified.",
        ) from exc
    except ValueError as exc:
        raise problem(
            status=422,
            code="EPISODE_DATA_REQUEST_INVALID",
            title="Invalid Episode request",
            detail="The requested range, feature or unit is invalid for this data version.",
        ) from exc


@router.get(BASE, response_model=list[DataVersionRef])
def versions(
    project_id: str,
    region_code: str,
    dataset_id: str,
    episode_id: str,
    auth: VerifiedAuth,
    before: int | None = Query(None, ge=1),
    limit: int = Query(100, ge=1, le=100),
):
    with authorized(auth, project_id, region_code, dataset_id, episode_id) as (service, identity):
        return service.repository.list_versions(identity, before=before, limit=limit)


@router.get(VERSION)
def detail(
    project_id: str,
    region_code: str,
    dataset_id: str,
    episode_id: str,
    data_version_id: str,
    auth: VerifiedAuth,
):
    with authorized(auth, project_id, region_code, dataset_id, episode_id) as (service, identity):
        package = service.files.open(identity, data_version_id)
        return {
            "data": package.receipt.data,
            "info": package.document("meta/info.json"),
            **{
                name: package.document(f"hc/{name}.json")
                for name in ("source", "modalities", "calibration", "quality", "conversion_report")
            },
            "download": {
                "kind": "complete_episode",
                "includes_collected_depth": True,
                "excluded_modalities": [],
            },
        }


@router.get(VERSION + "/steps", response_model=PackageStepWindow)
def steps(
    project_id: str,
    region_code: str,
    dataset_id: str,
    episode_id: str,
    data_version_id: str,
    auth: VerifiedAuth,
    start_step: int = Query(ge=0),
    end_step: int = Query(ge=0),
    columns: Annotated[list[str] | None, Query()] = None,
):
    with authorized(auth, project_id, region_code, dataset_id, episode_id) as (service, identity):
        return service.steps.read_steps(
            identity, data_version_id, start_step, end_step, columns=columns
        )


@router.get(VERSION + "/quality", response_model=EpisodeQualityView)
def fixed_quality(
    project_id: str,
    region_code: str,
    dataset_id: str,
    episode_id: str,
    data_version_id: str,
    auth: VerifiedAuth,
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    with authorized(auth, project_id, region_code, dataset_id, episode_id) as (service, identity):
        return read_episode_quality(service.files.open(identity, data_version_id))


@router.get(VERSION + "/data-reviews", response_model=EpisodeDataReviewPage)
def data_review_history(
    project_id: str,
    region_code: str,
    dataset_id: str,
    episode_id: str,
    data_version_id: str,
    auth: VerifiedAuth,
    response: Response,
    before: int | None = Query(None, ge=1),
    limit: int = Query(25, ge=1, le=100),
):
    response.headers["Cache-Control"] = "no-store"
    with authorized(auth, project_id, region_code, dataset_id, episode_id) as (service, identity):
        page = service.data_reviews.list(identity, data_version_id, before=before, limit=limit)
        actions = []
        for action, capabilities in (
            ("SUBMIT_DATA_REVIEW", ("dataset_version.review",)),
            ("LINK_ANNOTATION_TAG", ("dataset_version.review", "annotation_task.read")),
        ):
            allowed = all(
                auth.has_capability(capability, project_id, identity.organization_id)
                for capability in capabilities
            )
            actions.append(
                EpisodeDataReviewAction(
                    action=action,
                    allowed=allowed,
                    required_capabilities=capabilities,
                    denied_reason=None if allowed else "MISSING_CAPABILITY",
                )
            )
        return page.model_copy(update={"allowed_actions": tuple(actions)})


@router.post(VERSION + "/data-reviews", response_model=EpisodeDataReview, status_code=201)
def submit_data_review(
    project_id: str,
    region_code: str,
    dataset_id: str,
    episode_id: str,
    data_version_id: str,
    command: SubmitEpisodeDataReview,
    auth: VerifiedAuth,
    response: Response,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=256)],
):
    response.headers["Cache-Control"] = "no-store"
    with authorized(auth, project_id, region_code, dataset_id, episode_id) as (service, identity):
        authorize_scope(
            auth, project_id, "dataset_version.review", region_code, identity.organization_id
        )
        if any(location.tag is not None for location in command.locations):
            authorize_scope(
                auth, project_id, "annotation_task.read", region_code, identity.organization_id
            )
        return service.data_reviews.submit(
            identity,
            data_version_id,
            command,
            actor_id=auth.subject_id,
            idempotency_key=idempotency_key,
        )


@router.post(VERSION + "/media/{feature}/authorize", response_model=EpisodePreviewGrant)
def authorize_preview(
    project_id: str,
    region_code: str,
    dataset_id: str,
    episode_id: str,
    data_version_id: str,
    feature: str,
    auth: VerifiedAuth,
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    with authorized(auth, project_id, region_code, dataset_id, episode_id) as (service, identity):
        return service.previews.authorize(
            identity, data_version_id, feature, actor_id=auth.subject_id
        )


@router.get(VERSION + "/depth/{feature}/{frame_index}")
def depth(
    project_id: str,
    region_code: str,
    dataset_id: str,
    episode_id: str,
    data_version_id: str,
    feature: str,
    frame_index: int,
    auth: VerifiedAuth,
    unit: Literal["m", "mm"] = "mm",
    representation: Literal["metadata", "npz", "png"] = "metadata",
):
    with authorized(auth, project_id, region_code, dataset_id, episode_id) as (service, identity):
        frame = service.depth.read(identity, data_version_id, feature, frame_index, unit=unit)
        if representation == "metadata":
            return frame.metadata()
        content = frame.binary() if representation == "npz" else frame.preview_png()
        return Response(
            content,
            media_type="application/x-npz" if representation == "npz" else "image/png",
            headers={
                "Cache-Control": "no-store",
                "X-HC-Depth-Unit": unit,
                "X-HC-Depth-Quantized": "true",
            },
        )


@router.get(VERSION + "/download")
def download(
    project_id: str,
    region_code: str,
    dataset_id: str,
    episode_id: str,
    data_version_id: str,
    auth: VerifiedAuth,
):
    with authorized(auth, project_id, region_code, dataset_id, episode_id, download=True) as (
        service,
        identity,
    ):
        package = service.files.open(identity, data_version_id)
        path, temporary = complete_archive(package, service.directory / "downloads")
        return FileResponse(
            path,
            media_type="application/zip",
            filename=f"episode-{package.receipt.data.data_version_id}.zip",
            headers={"Cache-Control": "no-store"},
            background=BackgroundTask(temporary.cleanup),
        )
