"""Authorized logical snapshot reads shared by native and historical data."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from hc_data_platform.core.errors import problem
from hc_data_platform.security.http import VerifiedAuth, authorize_scope

from .models import LogicalMediaGrant, LogicalSampleBatch, LogicalSnapshotView, SnapshotSelection
from .service import LogicalDataService

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/regions/{region_code}/datasets/{dataset_id}"
    "/versions/{version_id}/episode-revisions/{revision_id}/logical",
    tags=["logical_data"],
)


def application_reader(request: Request) -> LogicalDataService | None:
    return getattr(request.app.state, "logical_data", None)


Reader = Annotated[LogicalDataService | None, Depends(application_reader)]
Organization = Annotated[str, Header(alias="X-Organization-Id", min_length=1, max_length=128)]
SnapshotToken = Annotated[str, Query(min_length=16, max_length=2048)]


def _selection(auth, organization_id, project_id, region_code, dataset_id, version_id, revision_id):
    authorize_scope(
        auth,
        project_id,
        "dataset_version.read",
        region_code=region_code,
        organization_id=organization_id,
    )
    return SnapshotSelection(
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id=dataset_id,
        version_id=version_id,
        revision_id=revision_id,
    )


def _read(service, action):
    if service is None:
        raise problem(
            status=503,
            code="LOGICAL_DATA_UNAVAILABLE",
            title="Logical data unavailable",
            detail="The application's logical reader is not configured.",
        )
    try:
        return action(service)
    except KeyError as exc:
        raise problem(
            status=404,
            code="LOGICAL_DATA_NOT_FOUND",
            title="Logical data not found",
            detail="The requested stream or immutable samples are unavailable.",
        ) from exc
    except ValueError as exc:
        raise problem(
            status=422,
            code="LOGICAL_WINDOW_INVALID",
            title="Invalid logical sample window",
            detail=str(exc),
        ) from exc


@router.get("", operation_id="getLogicalDataSnapshot", response_model=LogicalSnapshotView)
def snapshot(
    project_id: str,
    region_code: str,
    dataset_id: str,
    version_id: str,
    revision_id: str,
    auth: VerifiedAuth,
    organization_id: Organization,
    snapshot_token: SnapshotToken,
    service: Reader,
    response: Response,
):
    selection = _selection(
        auth, organization_id, project_id, region_code, dataset_id, version_id, revision_id
    )
    response.headers["Cache-Control"] = "no-store"
    return _read(
        service,
        lambda reader: reader.snapshot(
            selection,
            auth=auth,
            snapshot_token=snapshot_token,
        ),
    )


@router.get(
    "/streams/{stream_id}/samples",
    operation_id="getLogicalDataSamples",
    response_model=LogicalSampleBatch,
)
def samples(
    project_id: str,
    region_code: str,
    dataset_id: str,
    version_id: str,
    revision_id: str,
    stream_id: str,
    auth: VerifiedAuth,
    organization_id: Organization,
    snapshot_token: SnapshotToken,
    service: Reader,
    response: Response,
    snapshot_id: str = Query(pattern=r"^snapshot_[0-9a-f]{64}$"),
    start: int = Query(ge=0),
    end: int = Query(ge=0),
):
    selection = _selection(
        auth, organization_id, project_id, region_code, dataset_id, version_id, revision_id
    )
    response.headers["Cache-Control"] = "no-store"
    return _read(
        service,
        lambda reader: reader.read_samples(
            selection,
            stream_id,
            start,
            end,
            auth=auth,
            snapshot_token=snapshot_token,
            snapshot_id=snapshot_id,
        ),
    )


@router.post(
    "/streams/{stream_id}/media/authorize",
    operation_id="authorizeLogicalDataMedia",
    response_model=LogicalMediaGrant,
)
def authorize_media(
    project_id: str,
    region_code: str,
    dataset_id: str,
    version_id: str,
    revision_id: str,
    stream_id: str,
    auth: VerifiedAuth,
    organization_id: Organization,
    snapshot_token: SnapshotToken,
    service: Reader,
    response: Response,
    snapshot_id: str = Query(pattern=r"^snapshot_[0-9a-f]{64}$"),
):
    selection = _selection(
        auth, organization_id, project_id, region_code, dataset_id, version_id, revision_id
    )
    response.headers["Cache-Control"] = "no-store"
    return _read(
        service,
        lambda reader: reader.authorize_media(
            selection,
            stream_id,
            auth=auth,
            snapshot_token=snapshot_token,
            snapshot_id=snapshot_id,
        ),
    )
