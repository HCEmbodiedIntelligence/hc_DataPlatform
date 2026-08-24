from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from hc_data_platform.core.context import select_request_scope
from hc_data_platform.security.http import VerifiedAuth
from hc_data_platform.security.scope import ScopeGuard

from .models import (
    CreateStreamSchemaRequest,
    DataSchemaDatasetReferenceEnvelope,
    DataSchemaDatasetReferencePage,
    DataSchemaDatasetReferenceRequest,
    DataSchemaEnvelope,
    DataSchemaPage,
    DataSchemaPublishPreflightEnvelope,
    DataSchemaPublishPreflightRequest,
    DataSchemaPublishRequest,
    DataSchemaRouteEnvelope,
    DataSchemaValidationReportEnvelope,
    UpdateStreamSchemaDraftRequest,
)
from .service import DataSchemaService

router = APIRouter(tags=["data-schemas"])
_service = DataSchemaService.in_memory()
PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {
        "description": "The data-schema request could not be completed.",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    }
    for status in (401, 403, 404, 409, 412, 422, 428)
}
ProjectScope = Annotated[str, Header(alias="X-Project-ID", min_length=1, max_length=128)]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=1, max_length=256)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=256)]


def configure_data_schemas(service: DataSchemaService) -> None:
    global _service
    _service = service


def get_data_schema_service() -> DataSchemaService:
    return _service


ServiceDependency = Annotated[DataSchemaService, Depends(get_data_schema_service)]


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) and value else "request-id-unavailable"


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


def _select_organization_scope(auth: VerifiedAuth, project_id: str) -> None:
    """Bind project-scoped organization joins to the verified PostgreSQL context."""

    ScopeGuard.require(auth, project_id)
    auth.require_capability("data_schema.read", project_id)
    select_request_scope(project_id)


def _select_organization_mutation_scope(
    auth: VerifiedAuth, project_id: str, capability: str
) -> None:
    ScopeGuard.require(auth, project_id)
    auth.require_capability(capability, project_id)
    select_request_scope(project_id)


def _select_route_scope(auth: VerifiedAuth, project_id: str, region_code: str) -> None:
    """Bind the exact P15 component relationship scope before PostgreSQL access."""

    ScopeGuard.require(auth, project_id, region_code)
    auth.require_capability("data_schema.read", project_id)
    select_request_scope(project_id, region_code)


def _select_dataset_reference_scope(
    auth: VerifiedAuth, project_id: str, region_code: str, *, write: bool
) -> None:
    ScopeGuard.require(auth, project_id, region_code)
    auth.require_capability("data_schema.read", project_id)
    auth.require_capability("datasets.read", project_id)
    if write:
        auth.require_capability("data_schema.publish", project_id)
    select_request_scope(project_id, region_code)


@router.get(
    "/api/v1/organizations/{organization_id}/stream-schemas",
    operation_id="listStreamSchemas",
    response_model=DataSchemaPage,
    responses=PROBLEM_RESPONSES,
)
def list_stream_schemas(
    organization_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    project_id: ProjectScope,
    service: ServiceDependency,
    q: str | None = Query(default=None, min_length=1, max_length=256),
    schema_status: str | None = Query(
        default=None,
        alias="status",
        pattern=r"^(DRAFT|PUBLISHED)$",
    ),
    logical_type: str | None = Query(default=None, min_length=1, max_length=128),
    after: str | None = Query(default=None, min_length=1, max_length=16_384),
    before: str | None = Query(default=None, min_length=1, max_length=16_384),
    limit: int = Query(default=20, ge=1, le=100),
) -> DataSchemaPage:
    _select_organization_scope(auth, project_id)
    _no_store(response)
    return service.list_versions(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        query=q,
        status=schema_status,
        logical_type=logical_type,
        after=after,
        before=before,
        limit=limit,
        request_id=_request_id(request),
    )


@router.post(
    "/api/v1/organizations/{organization_id}/stream-schemas",
    operation_id="createStreamSchema",
    response_model=DataSchemaEnvelope,
    responses=PROBLEM_RESPONSES,
    status_code=status.HTTP_201_CREATED,
)
def create_stream_schema(
    organization_id: str,
    command: CreateStreamSchemaRequest,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    project_id: ProjectScope,
    service: ServiceDependency,
    idempotency_key: IdempotencyKey,
) -> DataSchemaEnvelope:
    _select_organization_mutation_scope(auth, project_id, "data_schema.create")
    _no_store(response)
    result = service.create_version(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        command=command,
        source="MANUAL",
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = result.data.etag
    response.headers["Location"] = (
        f"/api/v1/organizations/{organization_id}/stream-schemas/{result.data.schema_id}"
        f"/versions/{result.data.schema_version}"
    )
    return result


@router.post(
    "/api/v1/organizations/{organization_id}/stream-schemas:import",
    operation_id="importStreamSchema",
    response_model=DataSchemaEnvelope,
    responses=PROBLEM_RESPONSES,
    status_code=status.HTTP_201_CREATED,
)
def import_stream_schema(
    organization_id: str,
    command: CreateStreamSchemaRequest,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    project_id: ProjectScope,
    service: ServiceDependency,
    idempotency_key: IdempotencyKey,
) -> DataSchemaEnvelope:
    _select_organization_mutation_scope(auth, project_id, "data_schema.import")
    _no_store(response)
    result = service.create_version(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        command=command,
        source="IMPORT",
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = result.data.etag
    response.headers["Location"] = (
        f"/api/v1/organizations/{organization_id}/stream-schemas/{result.data.schema_id}"
        f"/versions/{result.data.schema_version}"
    )
    return result


@router.get(
    "/api/v1/organizations/{organization_id}/stream-schemas/{schema_id}/versions/{schema_version}",
    operation_id="getStreamSchemaVersion",
    response_model=DataSchemaEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_stream_schema_version(
    organization_id: str,
    schema_id: str,
    schema_version: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    project_id: ProjectScope,
    service: ServiceDependency,
) -> DataSchemaEnvelope:
    _select_organization_scope(auth, project_id)
    _no_store(response)
    result = service.get_version(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        schema_id=schema_id,
        schema_version=schema_version,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = result.data.etag
    return result


@router.get(
    "/api/v1/organizations/{organization_id}/projects/{project_id}/regions/{region_code}"
    "/stream-schemas/{schema_id}/versions/{schema_version}/dataset-references",
    operation_id="listDataSchemaDatasetReferences",
    response_model=DataSchemaDatasetReferencePage,
    responses=PROBLEM_RESPONSES,
)
def list_data_schema_dataset_references(
    organization_id: str,
    project_id: str,
    region_code: str,
    schema_id: str,
    schema_version: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> DataSchemaDatasetReferencePage:
    _select_dataset_reference_scope(auth, project_id, region_code, write=False)
    _no_store(response)
    return service.list_dataset_references(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        schema_id=schema_id,
        schema_version=schema_version,
        request_id=_request_id(request),
    )


@router.post(
    "/api/v1/organizations/{organization_id}/projects/{project_id}/regions/{region_code}"
    "/stream-schemas/{schema_id}/versions/{schema_version}/dataset-references",
    operation_id="associateDataSchemaDatasetReference",
    response_model=DataSchemaDatasetReferenceEnvelope,
    responses=PROBLEM_RESPONSES,
    status_code=status.HTTP_201_CREATED,
)
def associate_data_schema_dataset_reference(
    organization_id: str,
    project_id: str,
    region_code: str,
    schema_id: str,
    schema_version: str,
    command: DataSchemaDatasetReferenceRequest,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    service: ServiceDependency,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
) -> DataSchemaDatasetReferenceEnvelope:
    _select_dataset_reference_scope(auth, project_id, region_code, write=True)
    _no_store(response)
    return service.associate_dataset_reference(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        schema_id=schema_id,
        schema_version=schema_version,
        expected_etag=if_match,
        command=command,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.patch(
    "/api/v1/organizations/{organization_id}/stream-schemas/{schema_id}/versions/{schema_version}",
    operation_id="updateStreamSchemaDraft",
    response_model=DataSchemaEnvelope,
    responses=PROBLEM_RESPONSES,
)
def update_stream_schema_draft(
    organization_id: str,
    schema_id: str,
    schema_version: str,
    command: UpdateStreamSchemaDraftRequest,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    project_id: ProjectScope,
    service: ServiceDependency,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
) -> DataSchemaEnvelope:
    _select_organization_mutation_scope(auth, project_id, "data_schema.create")
    _no_store(response)
    result = service.update_draft(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        schema_id=schema_id,
        schema_version=schema_version,
        expected_etag=if_match,
        command=command,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = result.data.etag
    return result


@router.post(
    "/api/v1/organizations/{organization_id}/stream-schemas/{schema_id}/versions/{schema_version}:validate",
    operation_id="validateStreamSchemaVersion",
    response_model=DataSchemaValidationReportEnvelope,
    responses=PROBLEM_RESPONSES,
)
def validate_stream_schema_version(
    organization_id: str,
    schema_id: str,
    schema_version: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    project_id: ProjectScope,
    service: ServiceDependency,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
) -> DataSchemaValidationReportEnvelope:
    _select_organization_mutation_scope(auth, project_id, "data_schema.validate")
    _no_store(response)
    return service.validate_version(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        schema_id=schema_id,
        schema_version=schema_version,
        expected_etag=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.post(
    "/api/v1/organizations/{organization_id}/stream-schemas/{schema_id}/versions/{schema_version}:preflight-publish",
    operation_id="preflightDataSchemaPublish",
    response_model=DataSchemaPublishPreflightEnvelope,
    responses=PROBLEM_RESPONSES,
)
def preflight_data_schema_publish(
    organization_id: str,
    schema_id: str,
    schema_version: str,
    request: Request,
    command: DataSchemaPublishPreflightRequest,
    response: Response,
    auth: VerifiedAuth,
    project_id: ProjectScope,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
) -> DataSchemaPublishPreflightEnvelope:
    _select_organization_mutation_scope(auth, project_id, "data_schema.publish")
    _no_store(response)
    return service.preflight_publish(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        schema_id=schema_id,
        schema_version=schema_version,
        expected_etag=if_match,
        command=command,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.post(
    "/api/v1/organizations/{organization_id}/stream-schemas/{schema_id}/versions/{schema_version}:publish",
    operation_id="publishDataSchemaVersion",
    response_model=DataSchemaEnvelope,
    responses=PROBLEM_RESPONSES,
)
def publish_data_schema_version(
    organization_id: str,
    schema_id: str,
    schema_version: str,
    request: Request,
    command: DataSchemaPublishRequest,
    response: Response,
    auth: VerifiedAuth,
    project_id: ProjectScope,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
) -> DataSchemaEnvelope:
    _select_organization_mutation_scope(auth, project_id, "data_schema.publish")
    _no_store(response)
    result = service.publish_version(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        schema_id=schema_id,
        schema_version=schema_version,
        expected_etag=if_match,
        command=command,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = result.data.etag
    return result


@router.get(
    "/api/v1/projects/{project_id}/regions/{region_code}/route-resolutions/p15-to-p17",
    operation_id="resolveP15DataSchemaRoute",
    response_model=DataSchemaRouteEnvelope,
    responses=PROBLEM_RESPONSES,
)
def resolve_p15_data_schema_route(
    project_id: str,
    region_code: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    service: ServiceDependency,
    schema_id: str = Query(alias="schema_id", min_length=1, max_length=128),
    schema_version: str = Query(alias="schema_version", pattern=r"^[1-9]\d*$"),
    component_id: str = Query(alias="component_id", min_length=1, max_length=128),
    detail_tab: str = Query(
        alias="detail_tab", pattern=r"^(fields|encoding|compatibility|references)$"
    ),
) -> DataSchemaRouteEnvelope:
    _select_route_scope(auth, project_id, region_code)
    _no_store(response)
    return service.resolve_route(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        schema_id=schema_id,
        schema_version=schema_version,
        component_id=component_id,
        detail_tab=detail_tab,
        request_id=_request_id(request),
    )
