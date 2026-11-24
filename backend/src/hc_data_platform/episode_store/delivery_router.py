"""Complete-package commands require the same exact scope on every cache access."""

from contextlib import contextmanager
from contextvars import copy_context
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import AwareDatetime, Field
from starlette.background import BackgroundTask

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import problem
from hc_data_platform.security.http import VerifiedAuth, authorize_scope

from hc_data_platform.lerobot_imports.source_archive import OriginalArchiveRequest
from hc_data_platform.storage.export_archive import publish_archive

from .models import FrozenModel
from .collection_delivery_models import EpisodeDeliveryCandidates, RequestCollectionDelivery
from .collection_export import CollectionIncompatible
from .delivery_models import (
    DownloadEpisodeBundle,
    PrepareEpisodePart,
    PrepareEpisodeParts,
    PreparedEpisodePart,
    EpisodeDeliveryDownload,
    EpisodeDeliveryGeneration,
    EpisodeDeliveryJob,
    RequestEpisodeDelivery,
)
from .dependencies import PackageServices
from .errors import PackageConflict
from .delivery_repository import scope_key
from .upload_candidates import ExportUploadBatch, UploadCandidatePage, UploadExportFilter, UploadExportStage, upload_batches

router = APIRouter(tags=["episode_data"])
BASE = "/projects/{project_id}/regions/{region_code}/episode-deliveries"


@contextmanager
def delivery_service(packages, auth, project_id, region_code, *, capability="dataset_version.read"):
    organization = current_request_context().organization_id
    if not organization:
        raise problem(
            status=422,
            code="ORGANIZATION_SCOPE_REQUIRED",
            title="Organization required",
            detail="Select an organization.",
        )
    authorize_scope(auth, project_id, capability, region_code, organization)
    if packages is None:
        raise problem(
            status=503,
            code="EPISODE_PACKAGES_UNAVAILABLE",
            title="Episode service unavailable",
            detail="Package service is not configured.",
        )
    try:
        yield packages.deliveries
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
    except CollectionIncompatible as exc:
        raise problem(
            status=422,
            code="EPISODE_COLLECTION_INCOMPATIBLE",
            title="Incompatible collection",
            detail=str(exc),
            details={"field": exc.field},
        ) from exc
    except ValueError as exc:
        raise problem(
            status=422,
            code="EPISODE_DELIVERY_SELECTION_INVALID",
            title="Invalid selection",
            detail=str(exc),
        ) from exc


@router.get(BASE + "/formats")
def list_export_formats(project_id: str, region_code: str, auth: VerifiedAuth,
                        packages: PackageServices):
    from .export_formats import formats
    with delivery_service(packages, auth, project_id, region_code) as service:
        service.authorize(auth)
        return formats()


@router.post(BASE, response_model=EpisodeDeliveryJob)
def request_episode_delivery(
    project_id: str,
    region_code: str,
    body: RequestEpisodeDelivery,
    auth: VerifiedAuth,
    packages: PackageServices,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=160)],
):
    response.headers["Cache-Control"] = "no-store"
    with delivery_service(packages, auth, project_id, region_code) as service:
        return service.request(body, idempotency_key, auth)


@router.post(BASE + "/collections", response_model=EpisodeDeliveryJob)
def request_collection_delivery(
    project_id: str,
    region_code: str,
    body: RequestCollectionDelivery,
    auth: VerifiedAuth,
    packages: PackageServices,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=160)],
):
    response.headers["Cache-Control"] = "no-store"
    with delivery_service(packages, auth, project_id, region_code) as service:
        return service.request_collection(body, idempotency_key, auth)


@router.get(BASE, response_model=tuple[EpisodeDeliveryJob, ...])
def list_episode_deliveries(
    project_id: str,
    region_code: str,
    auth: VerifiedAuth,
    packages: PackageServices,
    response: Response,
    data_version_id: str | None = Query(None, max_length=256),
    limit: int = Query(25, ge=1, le=100),
    before: str | None = Query(None, pattern=r"^ed_[0-9a-f]{64}$"),
    kind: Literal["independent", "collection"] | None = None,
):
    response.headers["Cache-Control"] = "no-store"
    with delivery_service(packages, auth, project_id, region_code, capability="export.read") as service:
        return service.list(
            auth, data_version_id=data_version_id, limit=limit, before=before, kind=kind
        )


@router.get(BASE + "/candidates", response_model=EpisodeDeliveryCandidates)
def episode_delivery_candidates(
    project_id: str,
    region_code: str,
    auth: VerifiedAuth,
    packages: PackageServices,
    response: Response,
    dataset_id: str = Query(min_length=1, max_length=256),
    collection_version_id: str | None = Query(None, max_length=256),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    data_stage: Literal["dataset", "annotated"] = "dataset",
):
    response.headers["Cache-Control"] = "no-store"
    with delivery_service(packages, auth, project_id, region_code) as service:
        return service.candidates(
            dataset_id,
            auth,
            collection_version_id=collection_version_id,
            offset=offset,
            limit=limit,
            data_stage=data_stage,
        )


@router.get(BASE + "/upload-batches", response_model=list[ExportUploadBatch])
def list_export_upload_batches(project_id: str, region_code: str, auth: VerifiedAuth,
                              packages: PackageServices, response: Response, before: int | None = Query(None, ge=1),
                              limit: int = Query(100, ge=1, le=100)):
    response.headers["Cache-Control"] = "no-store"
    with delivery_service(packages, auth, project_id, region_code) as service:
        service.authorize(auth)
        return upload_batches(service.repository, scope_key(), before, limit)


@router.get(BASE + "/upload-candidates", response_model=UploadCandidatePage)
def list_upload_candidates(
    project_id: str, region_code: str, auth: VerifiedAuth, packages: PackageServices,
    response: Response,
    stage: UploadExportStage,
    batch_number: list[Annotated[int, Query(ge=1)]] = Query(default=[]),
    all_batches: bool = False,
    uploaded_from: AwareDatetime | None = None, uploaded_before: AwareDatetime | None = None,
    dataset_id: list[str] = Query(default=[]), task_id: list[str] = Query(default=[]),
    after: str | None = Query(None, max_length=256), limit: int = Query(25, ge=1, le=100),
):
    response.headers["Cache-Control"] = "no-store"
    with delivery_service(packages, auth, project_id, region_code) as service:
        selection = UploadExportFilter(stage=stage, batch_numbers=tuple(batch_number), all_batches=all_batches,
                                       uploaded_from=uploaded_from, uploaded_before=uploaded_before)
        return service.filtered_candidates(selection, auth, datasets=dataset_id, tasks=task_id, after=after, limit=limit)


@router.post(BASE + "/bundle-parts", response_model=PreparedEpisodePart,
             responses={200: {"content": {"application/x-ndjson": {"schema": {"type": "string"}}}}})
def prepare_episode_part(project_id: str, region_code: str, body: PrepareEpisodePart | PrepareEpisodeParts,
                         auth: VerifiedAuth, packages: PackageServices):
    from .episode_bundle import prepare_part

    with delivery_service(packages, auth, project_id, region_code) as service:
        if isinstance(body, PrepareEpisodeParts):
            from .batch_preparation import batch_progress

            return StreamingResponse(batch_progress(service, body, auth), media_type="application/x-ndjson",
                                     headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
        return prepare_part(service, body, auth)


def bundle_archive(service, body, auth):
    from .bundle_archive import stream_episode_bundle
    from .episode_bundle import bundle_files

    records = {}
    from .export_file_names import record_files

    versions = {}
    parts = bundle_files(service, body, auth, records=records, versions=versions)
    outer = body.download_name or (
        f"Episodes-{body.download_date.isoformat()}.zip" if body.download_date else "episodes.zip"
    )
    context = copy_context()

    def indexed(files):
        pairs = [(name, versions[part]) for part, paths in files.items() for name in [outer, *paths]]
        context.run(record_files, service.repository, pairs)

    stream = stream_episode_bundle(service.storage, parts, records, service.packages.directory,
                                  on_files=indexed)
    return outer, stream


@router.post(BASE + "/bundle", response_class=StreamingResponse,
             responses={200: {"content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}}})
def download_episode_bundle(project_id: str, region_code: str, body: DownloadEpisodeBundle,
                            auth: VerifiedAuth, packages: PackageServices):
    with delivery_service(packages, auth, project_id, region_code) as service:
        outer, stream = bundle_archive(service, body, auth)
        return StreamingResponse(stream, media_type="application/zip",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no",
                     "Content-Disposition": f'attachment; filename="{outer}"'},
            background=BackgroundTask(stream.close))


class RawOssExport(OriginalArchiveRequest):
    kind: Literal["raw"]


class BundleOssExport(DownloadEpisodeBundle):
    kind: Literal["bundle"]


class JobOssExport(FrozenModel):
    kind: Literal["job"]
    job_id: str = Field(pattern=r"^ed_[0-9a-f]{64}$")
    export_filter: UploadExportFilter | None = None


@router.post(BASE + "/oss", response_class=StreamingResponse,
             responses={200: {"content": {"application/x-ndjson": {"schema": {"type": "string"}}}}})
def export_archive_to_oss(
    project_id: str, region_code: str, request: Request, auth: VerifiedAuth, packages: PackageServices,
    body: Annotated[RawOssExport | BundleOssExport | JobOssExport, Body(discriminator="kind")],
):
    organization = current_request_context().organization_id
    if not organization:
        raise problem(status=422, code="ORGANIZATION_SCOPE_REQUIRED", title="Organization required",
                      detail="Select an organization.")
    scope = (organization, project_id, region_code)
    authorize_scope(auth, project_id, "export.create", region_code, scope[0])
    if isinstance(body, RawOssExport):
        from hc_data_platform.lerobot_imports.router import get_service, original_archive

        uploads = get_service(request)
        chunks = original_archive(uploads, scope, body, auth)
        return publish_archive(uploads.storage, scope, body.download_name or "original-data.zip", chunks,
                               item_count=len(set(body.import_ids)))

    with delivery_service(packages, auth, project_id, region_code) as service:
        if isinstance(body, BundleOssExport):
            name, chunks = bundle_archive(service, body, auth)
            return publish_archive(service.storage, scope, name, chunks, item_count=len(body.part_ids))

        row, name = service.ready_archive(body.job_id, auth, body.export_filter)
        context = copy_context()
        return publish_archive(service.storage, scope, name,
                               service.storage.read_chunks(row["artifact_key"]),
                               item_count=len(row["plan"]["members"]), expected_sha256=row["artifact_sha256"],
                               on_complete=lambda: context.run(service.record_archive_access, row, name, auth))


@router.get(BASE + "/{job_id}", response_model=EpisodeDeliveryJob)
def get_episode_delivery(
    project_id: str,
    region_code: str,
    job_id: str,
    auth: VerifiedAuth,
    packages: PackageServices,
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    with delivery_service(packages, auth, project_id, region_code, capability="export.read") as service:
        return service.status(job_id, auth)


@router.post(BASE + "/{job_id}/retry", response_model=EpisodeDeliveryJob)
def retry_episode_delivery(
    project_id: str,
    region_code: str,
    job_id: str,
    body: EpisodeDeliveryGeneration,
    auth: VerifiedAuth,
    packages: PackageServices,
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    with delivery_service(packages, auth, project_id, region_code) as service:
        return service.command(job_id, body.expected_generation, auth, retry=True)


@router.post(BASE + "/{job_id}/cancel", response_model=EpisodeDeliveryJob)
def cancel_episode_delivery(
    project_id: str,
    region_code: str,
    job_id: str,
    body: EpisodeDeliveryGeneration,
    auth: VerifiedAuth,
    packages: PackageServices,
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    with delivery_service(packages, auth, project_id, region_code) as service:
        return service.command(job_id, body.expected_generation, auth, retry=False)


@router.post(BASE + "/{job_id}/download", response_model=EpisodeDeliveryDownload)
def download_episode_delivery(
    project_id: str,
    region_code: str,
    job_id: str,
    auth: VerifiedAuth,
    packages: PackageServices,
    response: Response,
    export_filter: UploadExportFilter | None = None,
):
    response.headers["Cache-Control"] = "no-store"
    with delivery_service(packages, auth, project_id, region_code) as service:
        return service.download(job_id, auth, export_filter)
