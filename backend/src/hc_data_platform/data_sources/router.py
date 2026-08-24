"""Formal P02 data-source HTTP routes."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BeforeValidator

from hc_data_platform.security.http import VerifiedAuth

from .models import (
    ConnectionTestJobEnvelope,
    CreateDataSourceCommand,
    DataSourceEnvelope,
    DataSourcePage,
    RotateCredentialCommand,
    SourceStateCommand,
    TestConnectionCommand,
    UpdateDataSourceCommand,
)
from .service import DataSourceService

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/regions/{region_code}/data-sources",
    tags=["data-sources"],
)
_service = DataSourceService.in_memory()

PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {
        "description": "The data-source request could not be completed.",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    }
    for status in (400, 401, 403, 404, 409, 412, 422, 500, 503)
}


def configure_data_sources(service: DataSourceService) -> None:
    global _service
    _service = service


def get_data_source_service() -> DataSourceService:
    return _service


ServiceDependency = Annotated[DataSourceService, Depends(get_data_source_service)]
OrganizationHeader = Annotated[str, Header(alias="X-Organization-Id", min_length=1, max_length=128)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=256)]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=1, max_length=256)]
SourceSort = Literal[
    "updated_at:desc,id:desc",
    "name:asc,id:asc",
    "last_test_at:desc,id:desc",
]
SourcePageLimit = Annotated[Literal[10, 20, 50], BeforeValidator(int)]
OptionalQueryText = Annotated[str | None, Query(min_length=1, max_length=256)]
OptionalCursor = Annotated[str | None, Query(min_length=1, max_length=16_384)]
RepeatedQuery = Annotated[list[str] | None, Query()]


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) and value else "request-id-unavailable"


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _mutation_headers(response: Response, *, etag: str, replayed: bool) -> None:
    _no_store(response)
    response.headers["ETag"] = etag
    response.headers["Idempotency-Replayed"] = "true" if replayed else "false"


@router.get(
    "/page",
    operation_id="getDataSourcesPage",
    response_model=DataSourcePage,
    responses=PROBLEM_RESPONSES,
)
def get_data_sources_page(
    project_id: str,
    region_code: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
    q: OptionalQueryText = None,
    source_type: RepeatedQuery = None,
    administrative_state: RepeatedQuery = None,
    connectivity_state: RepeatedQuery = None,
    credential_state: RepeatedQuery = None,
    sort: SourceSort = "updated_at:desc,id:desc",
    after: OptionalCursor = None,
    before: OptionalCursor = None,
    limit: Annotated[SourcePageLimit, Query()] = 20,
) -> DataSourcePage:
    _no_store(response)
    return service.list_sources(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        query=q,
        source_types=tuple(source_type or ()),
        administrative_states=tuple(administrative_state or ()),
        connectivity_states=tuple(connectivity_state or ()),
        credential_states=tuple(credential_state or ()),
        sort=sort,
        after=after,
        before=before,
        limit=limit,
        request_id=_request_id(request),
    )


@router.post(
    "",
    operation_id="createDataSource",
    response_model=DataSourceEnvelope,
    status_code=201,
    responses=PROBLEM_RESPONSES,
)
def create_data_source(
    project_id: str,
    region_code: str,
    command: CreateDataSourceCommand,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
) -> DataSourceEnvelope:
    outcome = service.create_source(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        command=command,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    source = outcome.record.source
    if source is None:
        raise RuntimeError("create data source returned no source")
    _mutation_headers(response, etag=source.etag, replayed=outcome.replayed)
    return service.envelope(
        outcome=outcome,
        auth=auth,
        scope=source.scope,
        request_id=_request_id(request),
    )


@router.get(
    "/{source_id}",
    operation_id="getDataSource",
    response_model=DataSourceEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_data_source(
    project_id: str,
    region_code: str,
    source_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: ServiceDependency,
) -> DataSourceEnvelope:
    _no_store(response)
    result = service.get_source(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        source_id=source_id,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = result.data.etag
    return result


@router.patch(
    "/{source_id}",
    operation_id="updateDataSource",
    response_model=DataSourceEnvelope,
    responses=PROBLEM_RESPONSES,
)
def update_data_source(
    project_id: str,
    region_code: str,
    source_id: str,
    command: UpdateDataSourceCommand,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
) -> DataSourceEnvelope:
    outcome = service.update_source(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        source_id=source_id,
        command=command,
        if_match=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    source = outcome.record.source
    if source is None:
        raise RuntimeError("update data source returned no source")
    _mutation_headers(response, etag=source.etag, replayed=outcome.replayed)
    return service.envelope(
        outcome=outcome,
        auth=auth,
        scope=source.scope,
        request_id=_request_id(request),
    )


@router.post(
    "/{source_id}:rotate-credential",
    operation_id="rotateDataSourceCredential",
    response_model=DataSourceEnvelope,
    responses=PROBLEM_RESPONSES,
)
def rotate_data_source_credential(
    project_id: str,
    region_code: str,
    source_id: str,
    command: RotateCredentialCommand,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
) -> DataSourceEnvelope:
    outcome = service.rotate_credential(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        source_id=source_id,
        command=command,
        if_match=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    source = outcome.record.source
    if source is None:
        raise RuntimeError("rotate credential returned no source")
    _mutation_headers(response, etag=source.etag, replayed=outcome.replayed)
    return service.envelope(
        outcome=outcome,
        auth=auth,
        scope=source.scope,
        request_id=_request_id(request),
    )


@router.post(
    "/{source_id}:test-connection",
    operation_id="testDataSourceConnection",
    response_model=ConnectionTestJobEnvelope,
    status_code=202,
    responses=PROBLEM_RESPONSES,
)
def test_data_source_connection(
    project_id: str,
    region_code: str,
    source_id: str,
    command: TestConnectionCommand,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
) -> ConnectionTestJobEnvelope:
    outcome = service.test_connection(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        source_id=source_id,
        command=command,
        if_match=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    source = outcome.record.source
    if source is None or outcome.record.connection_test is None:
        raise RuntimeError("connection test returned no source or job")
    _mutation_headers(response, etag=source.etag, replayed=outcome.replayed)
    response.headers["Location"] = f"/api/v1/jobs/{outcome.record.connection_test.id}"
    return service.connection_test_envelope(
        outcome=outcome,
        scope=source.scope,
        request_id=_request_id(request),
    )


def _set_state(
    *,
    project_id: str,
    region_code: str,
    source_id: str,
    desired_state: Literal["ENABLED", "DISABLED"],
    command: SourceStateCommand,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: str,
    if_match: str,
    idempotency_key: str,
    service: DataSourceService,
) -> DataSourceEnvelope:
    outcome = service.set_administrative_state(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        source_id=source_id,
        desired_state=desired_state,
        command=command,
        if_match=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    source = outcome.record.source
    if source is None:
        raise RuntimeError("state change returned no source")
    _mutation_headers(response, etag=source.etag, replayed=outcome.replayed)
    return service.envelope(
        outcome=outcome,
        auth=auth,
        scope=source.scope,
        request_id=_request_id(request),
    )


@router.post(
    "/{source_id}:enable",
    operation_id="enableDataSource",
    response_model=DataSourceEnvelope,
    responses=PROBLEM_RESPONSES,
)
def enable_data_source(
    project_id: str,
    region_code: str,
    source_id: str,
    command: SourceStateCommand,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
) -> DataSourceEnvelope:
    return _set_state(
        project_id=project_id,
        region_code=region_code,
        source_id=source_id,
        desired_state="ENABLED",
        command=command,
        response=response,
        request=request,
        auth=auth,
        organization_id=organization_id,
        if_match=if_match,
        idempotency_key=idempotency_key,
        service=service,
    )


@router.post(
    "/{source_id}:disable",
    operation_id="disableDataSource",
    response_model=DataSourceEnvelope,
    responses=PROBLEM_RESPONSES,
)
def disable_data_source(
    project_id: str,
    region_code: str,
    source_id: str,
    command: SourceStateCommand,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
) -> DataSourceEnvelope:
    return _set_state(
        project_id=project_id,
        region_code=region_code,
        source_id=source_id,
        desired_state="DISABLED",
        command=command,
        response=response,
        request=request,
        auth=auth,
        organization_id=organization_id,
        if_match=if_match,
        idempotency_key=idempotency_key,
        service=service,
    )
