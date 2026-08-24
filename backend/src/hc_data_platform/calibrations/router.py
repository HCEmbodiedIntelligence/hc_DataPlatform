"""Formal P16 calibration router."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from hc_data_platform.core.context import select_request_scope
from hc_data_platform.security.http import VerifiedAuth
from hc_data_platform.security.scope import ScopeGuard

from .models import (
    CalibrationDatasetAssociationEnvelope,
    CalibrationDatasetAssociationPage,
    CalibrationDatasetAssociationRequest,
    CalibrationPublishPreflightEnvelope,
    CalibrationPublishPreflightRequest,
    CalibrationPublishRequest,
    CalibrationSetEnvelope,
    CalibrationSetPage,
    CalibrationValidationReportEnvelope,
    CalibrationVersionDocumentEnvelope,
    CalibrationVersionPage,
    CreateCalibrationSetRequest,
    RecalibrateCalibrationSetRequest,
)
from .service import CalibrationService

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/regions/{region_code}", tags=["calibrations"]
)
_service = CalibrationService.in_memory()
PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {
        "description": "The calibration request could not be completed.",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    }
    for status in (401, 403, 404, 409, 412, 422)
}


def configure_calibrations(service: CalibrationService) -> None:
    global _service
    _service = service


def get_calibration_service() -> CalibrationService:
    return _service


ServiceDependency = Annotated[CalibrationService, Depends(get_calibration_service)]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=1, max_length=256)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=256)]
OrganizationHeader = Annotated[str, Header(alias="X-Organization-Id", min_length=1, max_length=128)]


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) and value else "request-id-unavailable"


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


def _select_calibration_scope(
    auth: VerifiedAuth, project_id: str, region_code: str, organization_id: str
) -> None:
    """Bind PostgreSQL RLS to the authenticated calibration request scope."""

    ScopeGuard.require(auth, project_id, region_code, organization_id)
    auth.require_capability("calibration.read", project_id, organization_id)
    select_request_scope(project_id, region_code, organization_id=organization_id)


def _select_calibration_publish_scope(
    auth: VerifiedAuth, project_id: str, region_code: str, organization_id: str
) -> None:
    ScopeGuard.require(auth, project_id, region_code, organization_id)
    auth.require_capability("calibration.publish", project_id, organization_id)
    select_request_scope(project_id, region_code, organization_id=organization_id)


def _select_calibration_dataset_scope(
    auth: VerifiedAuth, project_id: str, region_code: str, organization_id: str, *, write: bool
) -> None:
    if write:
        _select_calibration_publish_scope(auth, project_id, region_code, organization_id)
    else:
        _select_calibration_scope(auth, project_id, region_code, organization_id)
    auth.require_capability("dataset.read", project_id, organization_id)


@router.get(
    "/calibration-sets",
    operation_id="listCalibrationSets",
    response_model=CalibrationSetPage,
    responses=PROBLEM_RESPONSES,
)
def list_calibration_sets(
    project_id: str,
    region_code: str,
    organization_id: OrganizationHeader,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    service: ServiceDependency,
    q: str | None = Query(default=None, min_length=1, max_length=256),
    robot_id: str | None = Query(default=None, min_length=1, max_length=128),
    component_id: str | None = Query(default=None, min_length=1, max_length=128),
) -> CalibrationSetPage:
    _select_calibration_scope(auth, project_id, region_code, organization_id)
    _no_store(response)
    return service.list_sets(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        query=q,
        robot_id=robot_id,
        component_id=component_id,
        request_id=_request_id(request),
    )


@router.post(
    "/calibration-sets",
    operation_id="createCalibrationSet",
    response_model=CalibrationSetEnvelope,
    responses=PROBLEM_RESPONSES,
    status_code=status.HTTP_201_CREATED,
)
def create_calibration_set(
    project_id: str,
    region_code: str,
    organization_id: OrganizationHeader,
    command: CreateCalibrationSetRequest,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    service: ServiceDependency,
    idempotency_key: IdempotencyKey,
) -> CalibrationSetEnvelope:
    _select_calibration_publish_scope(auth, project_id, region_code, organization_id)
    _no_store(response)
    result = service.create_set(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        command=command,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = result.data.etag
    response.headers["Location"] = (
        f"/api/v1/projects/{project_id}/regions/{region_code}/calibration-sets/{result.data.id}"
    )
    return result


@router.get(
    "/calibration-sets/{set_id}",
    operation_id="getCalibrationSet",
    response_model=CalibrationSetEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_calibration_set(
    project_id: str,
    region_code: str,
    organization_id: OrganizationHeader,
    set_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> CalibrationSetEnvelope:
    _select_calibration_scope(auth, project_id, region_code, organization_id)
    _no_store(response)
    result = service.get_set(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        set_id=set_id,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = result.data.etag
    return result


@router.get(
    "/calibration-sets/{set_id}/versions",
    operation_id="listCalibrationVersions",
    response_model=CalibrationVersionPage,
    responses=PROBLEM_RESPONSES,
)
def list_calibration_versions(
    project_id: str,
    region_code: str,
    organization_id: OrganizationHeader,
    set_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> CalibrationVersionPage:
    _select_calibration_scope(auth, project_id, region_code, organization_id)
    _no_store(response)
    return service.list_versions(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        set_id=set_id,
        request_id=_request_id(request),
    )


@router.post(
    "/calibration-sets/{set_id}/versions",
    operation_id="recalibrateCalibrationSet",
    response_model=CalibrationSetEnvelope,
    responses=PROBLEM_RESPONSES,
    status_code=status.HTTP_201_CREATED,
)
def recalibrate_calibration_set(
    project_id: str,
    region_code: str,
    organization_id: OrganizationHeader,
    set_id: str,
    command: RecalibrateCalibrationSetRequest,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    service: ServiceDependency,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
) -> CalibrationSetEnvelope:
    _select_calibration_publish_scope(auth, project_id, region_code, organization_id)
    _no_store(response)
    result = service.recalibrate_set(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        set_id=set_id,
        expected_etag=if_match,
        command=command,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = result.data.etag
    return result


@router.get(
    "/calibration-sets/{set_id}/versions/{version}/dataset-associations",
    operation_id="listCalibrationDatasetAssociations",
    response_model=CalibrationDatasetAssociationPage,
    responses=PROBLEM_RESPONSES,
)
def list_calibration_dataset_associations(
    project_id: str,
    region_code: str,
    organization_id: OrganizationHeader,
    set_id: str,
    version: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> CalibrationDatasetAssociationPage:
    _select_calibration_dataset_scope(auth, project_id, region_code, organization_id, write=False)
    _no_store(response)
    return service.list_dataset_associations(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        set_id=set_id,
        version=version,
        request_id=_request_id(request),
    )


@router.post(
    "/calibration-sets/{set_id}/versions/{version}/dataset-associations",
    operation_id="associateCalibrationDatasetVersion",
    response_model=CalibrationDatasetAssociationEnvelope,
    responses=PROBLEM_RESPONSES,
    status_code=status.HTTP_201_CREATED,
)
def associate_calibration_dataset_version(
    project_id: str,
    region_code: str,
    organization_id: OrganizationHeader,
    set_id: str,
    version: str,
    command: CalibrationDatasetAssociationRequest,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    service: ServiceDependency,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
) -> CalibrationDatasetAssociationEnvelope:
    _select_calibration_dataset_scope(auth, project_id, region_code, organization_id, write=True)
    _no_store(response)
    return service.associate_dataset(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        set_id=set_id,
        version=version,
        expected_etag=if_match,
        command=command,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get(
    "/calibration-sets/{set_id}/versions/{version}/document",
    operation_id="getCalibrationVersionDocument",
    response_model=CalibrationVersionDocumentEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_calibration_version_document(
    project_id: str,
    region_code: str,
    organization_id: OrganizationHeader,
    set_id: str,
    version: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> CalibrationVersionDocumentEnvelope:
    _select_calibration_scope(auth, project_id, region_code, organization_id)
    _no_store(response)
    return service.get_version_document(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        set_id=set_id,
        version=version,
        request_id=_request_id(request),
    )


@router.post(
    "/calibration-sets/{set_id}/versions/{version}:validate",
    operation_id="validateCalibrationVersion",
    response_model=CalibrationValidationReportEnvelope,
    responses=PROBLEM_RESPONSES,
)
def validate_calibration_version(
    project_id: str,
    region_code: str,
    organization_id: OrganizationHeader,
    set_id: str,
    version: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    service: ServiceDependency,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
) -> CalibrationValidationReportEnvelope:
    _select_calibration_publish_scope(auth, project_id, region_code, organization_id)
    _no_store(response)
    return service.validate_version(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        set_id=set_id,
        version=version,
        expected_etag=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.get(
    "/calibration-validation-reports/{report_id}",
    operation_id="getCalibrationValidationReport",
    response_model=CalibrationValidationReportEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_calibration_validation_report(
    project_id: str,
    region_code: str,
    organization_id: OrganizationHeader,
    report_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    service: ServiceDependency,
) -> CalibrationValidationReportEnvelope:
    _select_calibration_scope(auth, project_id, region_code, organization_id)
    _no_store(response)
    return service.get_validation_report(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        report_id=report_id,
        request_id=_request_id(request),
    )


@router.post(
    "/calibration-sets/{set_id}/versions/{version}:preflight-publish",
    operation_id="preflightCalibrationPublish",
    response_model=CalibrationPublishPreflightEnvelope,
    responses=PROBLEM_RESPONSES,
)
def preflight_calibration_publish(
    project_id: str,
    region_code: str,
    organization_id: OrganizationHeader,
    set_id: str,
    version: str,
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
    command: CalibrationPublishPreflightRequest,
) -> CalibrationPublishPreflightEnvelope:
    _select_calibration_publish_scope(auth, project_id, region_code, organization_id)
    _no_store(response)
    return service.preflight_publish(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        set_id=set_id,
        version=version,
        expected_etag=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
        command=command,
    )


@router.post(
    "/calibration-sets/{set_id}/versions/{version}:publish",
    operation_id="publishCalibrationVersion",
    response_model=CalibrationSetEnvelope,
    responses=PROBLEM_RESPONSES,
)
def publish_calibration_version(
    project_id: str,
    region_code: str,
    organization_id: OrganizationHeader,
    set_id: str,
    version: str,
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
    command: CalibrationPublishRequest,
) -> CalibrationSetEnvelope:
    _select_calibration_publish_scope(auth, project_id, region_code, organization_id)
    _no_store(response)
    result = service.publish(
        auth=auth,
        project_id=project_id,
        region_code=region_code,
        set_id=set_id,
        version=version,
        expected_etag=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
        command=command,
    )
    response.headers["ETag"] = result.data.etag
    return result
