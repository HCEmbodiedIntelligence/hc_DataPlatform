"""Formal P05 dataset-page aggregate routes."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from hc_data_platform.security.http import VerifiedAuth

from .models import (
    CreateDatasetCommand,
    DatasetPageApproveReviewCommand,
    DatasetPageApproveReviewResult,
    DatasetPageBootstrapEnvelope,
    DatasetPageCapabilitiesEnvelope,
    DatasetPageDatasetEnvelope,
    DatasetPageDeletionPreflight,
    DatasetPageDeletionPreflightCommand,
    DatasetPageDiffJobCommand,
    DatasetPageEpisodeListEnvelope,
    DatasetPageEpisodeRevisionEnvelope,
    DatasetPageEpisodeRevisionHistoryEnvelope,
    DatasetPageFacetsEnvelope,
    DatasetPageJobAccepted,
    DatasetPageListEnvelope,
    DatasetPageOperationalInventoryEnvelope,
    DatasetPageRequiredStorageEnvelope,
    DatasetPageReturnReviewCommand,
    DatasetPageReturnReviewData,
    DatasetPageReturnReviewEnvelope,
    DatasetPageReturnReviewOutputVersion,
    DatasetPageReviewChecksCommand,
    DatasetPageReviewChecksEnvelope,
    DatasetPageSourceProvenanceListEnvelope,
    DatasetPageSummaryEnvelope,
    DatasetPageVersionBootstrapEnvelope,
    DatasetPageVersionCapacityEnvelope,
    DatasetPageVersionListEnvelope,
    DatasetPageVersionManifestEnvelope,
    DatasetPageVersionSchemaEnvelope,
    DatasetPageVersionSchemaSummaryEnvelope,
)
from .repository import (
    DatasetPageEpisodeFilters,
    DatasetPageFilters,
    DatasetPageSourceProvenanceFilters,
    DatasetPageVersionFilters,
)
from .service import DatasetPageService

router = APIRouter(prefix="/api/v1/projects/{project_id}/datasets", tags=["datasets"])
_service = DatasetPageService.in_memory()

PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {
        "description": "The dataset-page request could not be completed.",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    }
    for status in (400, 401, 403, 404, 409, 412, 422, 500)
}


def configure_dataset_page(service: DatasetPageService) -> None:
    global _service
    _service = service


def get_dataset_page_service() -> DatasetPageService:
    return _service


ServiceDependency = Annotated[DatasetPageService, Depends(get_dataset_page_service)]
OrganizationHeader = Annotated[str, Header(alias="X-Organization-Id", min_length=1, max_length=128)]
RegionHeader = Annotated[str, Header(alias="X-Region-Code", min_length=1, max_length=64)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=256)]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=3, max_length=256)]
OptionalQueryText = Annotated[str | None, Query(min_length=1, max_length=256)]
OptionalDate = Annotated[date | None, Query()]
OptionalDateTime = Annotated[datetime | None, Query()]
OptionalCursor = Annotated[str | None, Query(min_length=1, max_length=16_384)]
OptionalSnapshotToken = Annotated[str | None, Query(min_length=16, max_length=2048)]
SnapshotToken = Annotated[str, Query(min_length=16, max_length=2048)]
OperationalRevision = Annotated[str, Query(min_length=1, max_length=256)]
RepeatedQuery = Annotated[list[str] | None, Query()]
DatasetSort = Literal[
    "activity_at:desc,dataset_id:desc",
    "created_at:desc,dataset_id:desc",
    "name:asc,dataset_id:asc",
]
ChannelMatch = Literal["all", "any"]
DatasetVersionSort = Literal[
    "created_at:desc,version_id:desc",
    "created_at:asc,version_id:asc",
    "display_version:desc,version_id:desc",
    "display_version:asc,version_id:asc",
]
DatasetSourceProvenanceSort = Literal[
    "registered_at:desc,provenance_id:desc",
    "registered_at:asc,provenance_id:asc",
    "source_display_name:asc,provenance_id:asc",
]
DatasetEpisodeSort = Literal[
    "ordinal:asc,episode_id:asc",
    "started_at_ns:desc,episode_id:desc",
    "started_at_ns:asc,episode_id:asc",
]
RequiredStorageSort = Literal["role:asc,object_id:asc"]
OperationalInventorySort = Literal["created_at:desc,inventory_id:desc"]


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) and value else "request-id-unavailable"


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _filters(
    *,
    q: str | None,
    robot_model_id: str | None,
    robot_id: str | None,
    task: str | None,
    scene: str | None,
    asset_state: str | None,
    storage_class: str | None,
    channels: list[str] | None,
    channel_match: ChannelMatch,
    created_from: date | None,
    created_to: date | None,
) -> DatasetPageFilters:
    return DatasetPageFilters(
        query=q,
        robot_model_id=robot_model_id,
        robot_id=robot_id,
        task=task,
        scene=scene,
        asset_state=asset_state,
        storage_class=storage_class,
        channels=tuple(channels or ()),
        channel_match=channel_match,
        created_from=created_from,
        created_to=created_to,
    )


@router.get(
    "",
    operation_id="listDatasets",
    response_model=DatasetPageListEnvelope,
    responses=PROBLEM_RESPONSES,
)
def list_datasets(
    project_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    service: ServiceDependency,
    q: OptionalQueryText = None,
    robot_model_id: OptionalQueryText = None,
    robot_id: OptionalQueryText = None,
    task: OptionalQueryText = None,
    scene: OptionalQueryText = None,
    asset_state: OptionalQueryText = None,
    storage_class: OptionalQueryText = None,
    channels: RepeatedQuery = None,
    channel_match: ChannelMatch = "all",
    created_from: OptionalDate = None,
    created_to: OptionalDate = None,
    sort: DatasetSort = "activity_at:desc,dataset_id:desc",
    after: OptionalCursor = None,
    before: OptionalCursor = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> DatasetPageListEnvelope:
    _no_store(response)
    return service.list_datasets(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        filters=_filters(
            q=q,
            robot_model_id=robot_model_id,
            robot_id=robot_id,
            task=task,
            scene=scene,
            asset_state=asset_state,
            storage_class=storage_class,
            channels=channels,
            channel_match=channel_match,
            created_from=created_from,
            created_to=created_to,
        ),
        sort=sort,
        after=after,
        before=before,
        limit=limit,
        request_id=_request_id(request),
    )


@router.get(
    ":page-capabilities",
    operation_id="getDatasetsPageCapabilities",
    response_model=DatasetPageCapabilitiesEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_page_capabilities(
    project_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    service: ServiceDependency,
) -> DatasetPageCapabilitiesEnvelope:
    _no_store(response)
    return service.capabilities(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        request_id=_request_id(request),
    )


@router.get(
    ":summary",
    operation_id="getDatasetSummary",
    response_model=DatasetPageSummaryEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_dataset_summary(
    project_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    service: ServiceDependency,
    q: OptionalQueryText = None,
    robot_model_id: OptionalQueryText = None,
    robot_id: OptionalQueryText = None,
    task: OptionalQueryText = None,
    scene: OptionalQueryText = None,
    asset_state: OptionalQueryText = None,
    storage_class: OptionalQueryText = None,
    channels: RepeatedQuery = None,
    channel_match: ChannelMatch = "all",
    created_from: OptionalDate = None,
    created_to: OptionalDate = None,
) -> DatasetPageSummaryEnvelope:
    _no_store(response)
    return service.summary(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        filters=_filters(
            q=q,
            robot_model_id=robot_model_id,
            robot_id=robot_id,
            task=task,
            scene=scene,
            asset_state=asset_state,
            storage_class=storage_class,
            channels=channels,
            channel_match=channel_match,
            created_from=created_from,
            created_to=created_to,
        ),
        request_id=_request_id(request),
    )


@router.get(
    ":facets",
    operation_id="getDatasetFacets",
    response_model=DatasetPageFacetsEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_dataset_facets(
    project_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    service: ServiceDependency,
    q: OptionalQueryText = None,
    robot_model_id: OptionalQueryText = None,
    robot_id: OptionalQueryText = None,
    task: OptionalQueryText = None,
    scene: OptionalQueryText = None,
    asset_state: OptionalQueryText = None,
    storage_class: OptionalQueryText = None,
    channels: RepeatedQuery = None,
    channel_match: ChannelMatch = "all",
    created_from: OptionalDate = None,
    created_to: OptionalDate = None,
) -> DatasetPageFacetsEnvelope:
    _no_store(response)
    return service.facets(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        filters=_filters(
            q=q,
            robot_model_id=robot_model_id,
            robot_id=robot_id,
            task=task,
            scene=scene,
            asset_state=asset_state,
            storage_class=storage_class,
            channels=channels,
            channel_match=channel_match,
            created_from=created_from,
            created_to=created_to,
        ),
        request_id=_request_id(request),
    )


@router.post(
    "",
    operation_id="createDataset",
    response_model=DatasetPageDatasetEnvelope,
    status_code=201,
    responses=PROBLEM_RESPONSES,
)
def create_dataset(
    project_id: str,
    command: CreateDatasetCommand,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
) -> DatasetPageDatasetEnvelope:
    outcome = service.create_dataset(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        command=command,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["ETag"] = outcome.record.dataset.etag
    response.headers["Idempotency-Replayed"] = "true" if outcome.replayed else "false"
    return service.dataset_envelope(
        record=outcome.record.dataset,
        auth=auth,
        request_id=_request_id(request),
    )


@router.get(
    "/{dataset_id}/bootstrap",
    operation_id="getDatasetBootstrap",
    response_model=DatasetPageBootstrapEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_dataset_bootstrap(
    project_id: str,
    dataset_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    service: ServiceDependency,
) -> DatasetPageBootstrapEnvelope:
    _no_store(response)
    return service.bootstrap(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id=dataset_id,
        request_id=_request_id(request),
    )


@router.get(
    "/{dataset_id}/versions",
    operation_id="listDatasetVersions",
    response_model=DatasetPageVersionListEnvelope,
    responses=PROBLEM_RESPONSES,
)
def list_dataset_versions(
    project_id: str,
    dataset_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    service: ServiceDependency,
    q: OptionalQueryText = None,
    version_kind: OptionalQueryText = None,
    version_status: OptionalQueryText = None,
    sort: DatasetVersionSort = "created_at:desc,version_id:desc",
    after: OptionalCursor = None,
    before: OptionalCursor = None,
    limit: int = Query(default=20, ge=1, le=50),
) -> DatasetPageVersionListEnvelope:
    _no_store(response)
    return service.list_versions(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id=dataset_id,
        filters=DatasetPageVersionFilters(query=q, kind=version_kind, status=version_status),
        sort=sort,
        after=after,
        before=before,
        limit=limit,
        request_id=_request_id(request),
    )


@router.get(
    "/{dataset_id}/versions/{version_id}/schema-summary",
    operation_id="getDatasetVersionSchemaSummary",
    response_model=DatasetPageVersionSchemaSummaryEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_dataset_version_schema_summary(
    project_id: str,
    dataset_id: str,
    version_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    service: ServiceDependency,
) -> DatasetPageVersionSchemaSummaryEnvelope:
    _no_store(response)
    return service.schema_summary(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id=dataset_id,
        version_id=version_id,
        request_id=_request_id(request),
    )


@router.get(
    "/{dataset_id}/versions/{version_id}/source-provenance",
    operation_id="listDatasetVersionSourceProvenance",
    response_model=DatasetPageSourceProvenanceListEnvelope,
    responses=PROBLEM_RESPONSES,
)
def list_dataset_version_source_provenance(
    project_id: str,
    dataset_id: str,
    version_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    service: ServiceDependency,
    q: OptionalQueryText = None,
    source_id: OptionalQueryText = None,
    sort: DatasetSourceProvenanceSort = "registered_at:desc,provenance_id:desc",
    after: OptionalCursor = None,
    before: OptionalCursor = None,
    limit: int = Query(default=20, ge=1, le=50),
) -> DatasetPageSourceProvenanceListEnvelope:
    _no_store(response)
    return service.list_source_provenance(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id=dataset_id,
        version_id=version_id,
        filters=DatasetPageSourceProvenanceFilters(query=q, source_id=source_id),
        sort=sort,
        after=after,
        before=before,
        limit=limit,
        request_id=_request_id(request),
    )


@router.get(
    "/{dataset_id}/versions/{version_id}/capacity-facts",
    operation_id="getDatasetVersionCapacityFacts",
    response_model=DatasetPageVersionCapacityEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_dataset_version_capacity_facts(
    project_id: str,
    dataset_id: str,
    version_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    service: ServiceDependency,
) -> DatasetPageVersionCapacityEnvelope:
    _no_store(response)
    return service.capacity(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id=dataset_id,
        version_id=version_id,
        request_id=_request_id(request),
    )


@router.get(
    "/{dataset_id}/versions/{version_id}/episodes",
    operation_id="listVersionEpisodes",
    response_model=DatasetPageEpisodeListEnvelope,
    responses=PROBLEM_RESPONSES,
)
def list_version_episodes(
    project_id: str,
    dataset_id: str,
    version_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    service: ServiceDependency,
    snapshot_token: OptionalSnapshotToken = None,
    q: OptionalQueryText = None,
    task: OptionalQueryText = None,
    robot_id: OptionalQueryText = None,
    success_state: OptionalQueryText = None,
    started_from: OptionalDateTime = None,
    started_to: OptionalDateTime = None,
    included: bool | None = None,
    review_status: RepeatedQuery = None,
    has_finding: bool | None = None,
    change_type: RepeatedQuery = None,
    sort: DatasetEpisodeSort = "ordinal:asc,episode_id:asc",
    after: OptionalCursor = None,
    before: OptionalCursor = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> DatasetPageEpisodeListEnvelope:
    _no_store(response)
    return service.list_episodes(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id=dataset_id,
        version_id=version_id,
        filters=DatasetPageEpisodeFilters(
            query=q,
            task=task,
            robot_id=robot_id,
            success_state=success_state,
            started_from=started_from,
            started_to=started_to,
            included=included,
            review_statuses=tuple(review_status or ()),
            has_finding=has_finding,
            change_types=tuple(change_type or ()),
        ),
        sort=sort,
        after=after,
        before=before,
        limit=limit,
        snapshot_token=snapshot_token,
        request_id=_request_id(request),
    )


@router.get(
    "/{dataset_id}/episodes/{episode_id}/revision-history",
    operation_id="listEpisodeRevisionHistory",
    response_model=DatasetPageEpisodeRevisionHistoryEnvelope,
    responses=PROBLEM_RESPONSES,
)
def list_episode_revision_history(
    project_id: str,
    dataset_id: str,
    episode_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    service: ServiceDependency,
    after: OptionalCursor = None,
    before: OptionalCursor = None,
    limit: int = Query(default=20, ge=1, le=50),
) -> DatasetPageEpisodeRevisionHistoryEnvelope:
    _no_store(response)
    return service.episode_revision_history(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id=dataset_id,
        episode_id=episode_id,
        after=after,
        before=before,
        limit=limit,
        request_id=_request_id(request),
    )


@router.get(
    "/{dataset_id}/versions/{version_id}/bootstrap",
    operation_id="getDatasetVersionBootstrap",
    response_model=DatasetPageVersionBootstrapEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_dataset_version_bootstrap(
    project_id: str,
    dataset_id: str,
    version_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    service: ServiceDependency,
) -> DatasetPageVersionBootstrapEnvelope:
    _no_store(response)
    return service.version_bootstrap(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id=dataset_id,
        version_id=version_id,
        request_id=_request_id(request),
    )


@router.get(
    "/{dataset_id}/versions/{version_id}/episode-revisions/{revision_id}",
    operation_id="getEpisodeRevision",
    response_model=DatasetPageEpisodeRevisionEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_episode_revision(
    project_id: str,
    dataset_id: str,
    version_id: str,
    revision_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    service: ServiceDependency,
    snapshot_token: SnapshotToken,
) -> DatasetPageEpisodeRevisionEnvelope:
    _no_store(response)
    return service.episode_revision(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id=dataset_id,
        version_id=version_id,
        revision_id=revision_id,
        snapshot_token=snapshot_token,
        request_id=_request_id(request),
    )


@router.get(
    "/{dataset_id}/versions/{version_id}/manifest",
    operation_id="getVersionManifest",
    response_model=DatasetPageVersionManifestEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_version_manifest(
    project_id: str,
    dataset_id: str,
    version_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    service: ServiceDependency,
    after: OptionalCursor = None,
    before: OptionalCursor = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> DatasetPageVersionManifestEnvelope:
    _no_store(response)
    return service.manifest(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id=dataset_id,
        version_id=version_id,
        after=after,
        before=before,
        limit=limit,
        request_id=_request_id(request),
    )


@router.get(
    "/{dataset_id}/versions/{version_id}/schema",
    operation_id="getVersionSchema",
    response_model=DatasetPageVersionSchemaEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_version_schema(
    project_id: str,
    dataset_id: str,
    version_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    service: ServiceDependency,
    snapshot_token: SnapshotToken,
) -> DatasetPageVersionSchemaEnvelope:
    _no_store(response)
    return service.version_schema(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id=dataset_id,
        version_id=version_id,
        snapshot_token=snapshot_token,
        request_id=_request_id(request),
    )


@router.get(
    "/{dataset_id}/versions/{version_id}/required-storage",
    operation_id="listRequiredStorage",
    response_model=DatasetPageRequiredStorageEnvelope,
    responses=PROBLEM_RESPONSES,
)
def list_required_storage(
    project_id: str,
    dataset_id: str,
    version_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    service: ServiceDependency,
    snapshot_token: SnapshotToken,
    sort: RequiredStorageSort = "role:asc,object_id:asc",
    after: OptionalCursor = None,
    before: OptionalCursor = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> DatasetPageRequiredStorageEnvelope:
    del sort
    _no_store(response)
    return service.required_storage(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id=dataset_id,
        version_id=version_id,
        snapshot_token=snapshot_token,
        after=after,
        before=before,
        limit=limit,
        request_id=_request_id(request),
    )


@router.get(
    "/{dataset_id}/versions/{version_id}/operational-inventory",
    operation_id="listOperationalInventory",
    response_model=DatasetPageOperationalInventoryEnvelope,
    responses=PROBLEM_RESPONSES,
)
def list_operational_inventory(
    project_id: str,
    dataset_id: str,
    version_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    service: ServiceDependency,
    operational_revision: OperationalRevision,
    sort: OperationalInventorySort = "created_at:desc,inventory_id:desc",
    after: OptionalCursor = None,
    before: OptionalCursor = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> DatasetPageOperationalInventoryEnvelope:
    del sort
    _no_store(response)
    return service.operational_inventory(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id=dataset_id,
        version_id=version_id,
        operational_revision=operational_revision,
        after=after,
        before=before,
        limit=limit,
        request_id=_request_id(request),
    )


@router.post(
    "/{dataset_id}/versions/{version_id}/review-checks",
    operation_id="runVersionReviewChecks",
    response_model=DatasetPageReviewChecksEnvelope,
    responses=PROBLEM_RESPONSES,
)
def run_version_review_checks(
    project_id: str,
    dataset_id: str,
    version_id: str,
    command: DatasetPageReviewChecksCommand,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    if_match: IfMatch,
    service: ServiceDependency,
) -> DatasetPageReviewChecksEnvelope:
    _no_store(response)
    return service.review_checks(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id=dataset_id,
        version_id=version_id,
        command=command,
        if_match=if_match,
        request_id=_request_id(request),
    )


@router.post(
    "/{dataset_id}/versions/{version_id}:approve",
    operation_id="approveVersionReview",
    response_model=DatasetPageApproveReviewResult,
    status_code=202,
    responses=PROBLEM_RESPONSES,
)
def approve_version_review(
    project_id: str,
    dataset_id: str,
    version_id: str,
    command: DatasetPageApproveReviewCommand,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
) -> DatasetPageApproveReviewResult:
    outcome = service.approve_review(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id=dataset_id,
        version_id=version_id,
        command=command,
        if_match=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    _no_store(response)
    response.headers["Idempotency-Replayed"] = "true" if outcome.replayed else "false"
    return DatasetPageApproveReviewResult(
        **outcome.record.model_dump(),
        request_id=_request_id(request),
    )


@router.post(
    "/{dataset_id}/versions/{version_id}:return",
    operation_id="returnVersionReview",
    response_model=DatasetPageReturnReviewEnvelope,
    responses=PROBLEM_RESPONSES,
)
def return_version_review(
    project_id: str,
    dataset_id: str,
    version_id: str,
    command: DatasetPageReturnReviewCommand,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
) -> DatasetPageReturnReviewEnvelope:
    outcome = service.return_review(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id=dataset_id,
        version_id=version_id,
        command=command,
        if_match=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    _no_store(response)
    response.headers["Idempotency-Replayed"] = "true" if outcome.replayed else "false"
    record = outcome.record
    return DatasetPageReturnReviewEnvelope(
        data=DatasetPageReturnReviewData(
            scope=record.scope,
            review_decision=record.review_decision,
            findings=record.findings,
            review_finding_ids=record.review_finding_ids,
            output_version=DatasetPageReturnReviewOutputVersion(
                id=record.output_version_id,
                version_token=record.version_token,
            ),
            successor_draft_id=record.successor_draft_id,
            supersedes_draft_id=record.supersedes_draft_id,
            returned_from_version_id=record.returned_from_version_id,
            returned_from_review_decision_id=record.returned_from_review_decision_id,
        ),
        meta=service.mutation_meta(request_id=_request_id(request)),
    )


@router.post(
    "/{dataset_id}/versions/{version_id}/diff-jobs",
    operation_id="createVersionDiffJob",
    response_model=DatasetPageJobAccepted,
    status_code=202,
    responses=PROBLEM_RESPONSES,
)
def create_version_diff_job(
    project_id: str,
    dataset_id: str,
    version_id: str,
    command: DatasetPageDiffJobCommand,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
) -> DatasetPageJobAccepted:
    outcome = service.create_diff_job(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id=dataset_id,
        version_id=version_id,
        command=command,
        if_match=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    _no_store(response)
    response.headers["Idempotency-Replayed"] = "true" if outcome.replayed else "false"
    return DatasetPageJobAccepted(
        job=outcome.record.job,
        scope=outcome.record.scope,
        request_id=_request_id(request),
    )


@router.post(
    "/{dataset_id}/deletion-checks",
    operation_id="preflightDatasetDeletion",
    response_model=DatasetPageDeletionPreflight,
    responses=PROBLEM_RESPONSES,
)
def preflight_dataset_deletion(
    project_id: str,
    dataset_id: str,
    command: DatasetPageDeletionPreflightCommand,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
) -> DatasetPageDeletionPreflight:
    _no_store(response)
    return service.deletion_preflight(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id=dataset_id,
        version_id=None,
        command=command,
        if_match=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.post(
    "/{dataset_id}/versions/{version_id}/deletion-checks",
    operation_id="preflightVersionDeletion",
    response_model=DatasetPageDeletionPreflight,
    responses=PROBLEM_RESPONSES,
)
def preflight_version_deletion(
    project_id: str,
    dataset_id: str,
    version_id: str,
    command: DatasetPageDeletionPreflightCommand,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    region_code: RegionHeader,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
) -> DatasetPageDeletionPreflight:
    _no_store(response)
    return service.deletion_preflight(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id=dataset_id,
        version_id=version_id,
        command=command,
        if_match=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
