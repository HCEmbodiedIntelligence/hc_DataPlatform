"""App-owned task-to-logical selection; no global or parent router dependency."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response

from hc_data_platform.core.errors import problem
from hc_data_platform.integrations.annotation_logical import AnnotationLogicalService
from hc_data_platform.logical_data.models import AnnotationLogicalSnapshot
from hc_data_platform.security.http import VerifiedAuth, authorize_scope

router = APIRouter(tags=["logical_data"])


def application_service(request: Request) -> AnnotationLogicalService:
    service = getattr(request.app.state, "annotation_logical", None)
    if not isinstance(service, AnnotationLogicalService):
        raise problem(
            status=503,
            code="ANNOTATION_LOGICAL_UNAVAILABLE",
            title="Annotation logical source unavailable",
            detail="The application task-to-logical reader is not configured.",
        )
    return service


@router.get(
    "/projects/{project_id}/regions/{region_code}/annotation-tasks/{task_id}/logical",
    operation_id="getAnnotationLogicalDataSnapshot",
    response_model=AnnotationLogicalSnapshot,
)
def snapshot(
    project_id: str,
    region_code: str,
    task_id: str,
    auth: VerifiedAuth,
    organization_id: Annotated[
        str, Header(alias="X-Organization-Id", min_length=1, max_length=128)
    ],
    service: Annotated[AnnotationLogicalService, Depends(application_service)],
    response: Response,
) -> AnnotationLogicalSnapshot:
    authorize_scope(auth, project_id, "annotation_task.read", region_code, organization_id)
    response.headers["Cache-Control"] = "no-store"
    try:
        return service.snapshot(
            task_id,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            auth=auth,
        )
    except KeyError as exc:
        raise problem(
            status=404,
            code="ANNOTATION_LOGICAL_NOT_FOUND",
            title="Annotation logical source unavailable",
            detail="The fixed annotation source is unavailable.",
        ) from exc
