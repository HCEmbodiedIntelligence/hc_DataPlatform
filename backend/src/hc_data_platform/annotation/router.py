"""FastAPI boundary for scoped annotation, revision, and review workflows."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from hc_data_platform.core.context import select_request_scope
from hc_data_platform.core.errors import problem
from hc_data_platform.security import AuthContext, ScopeGuard

from .models import (
    AnnotationApprovedV1,
    AnnotationCurrent,
    AnnotationDraft,
    AnnotationHistory,
    AnnotationOperation,
    AnnotationReview,
    AnnotationRevision,
    AnnotationStatus,
    AnnotationTask,
    AutoAnnotationCapability,
    ExclusionRange,
    ReviewDecision,
)
from .ports import AutoAnnotationProvider
from .service import AnnotationService, DisabledAutoAnnotationProvider, InMemoryAnnotationService

router = APIRouter(prefix="/api/v1", tags=["annotation"])

_service: AnnotationService = InMemoryAnnotationService()
_auto_provider: AutoAnnotationProvider = DisabledAutoAnnotationProvider()


def configure_annotation(
    service: AnnotationService,
    *,
    auto_provider: AutoAnnotationProvider | None = None,
) -> None:
    """Application composition hook for PostgreSQL, BE-02 auth, and feature providers."""

    global _auto_provider, _service
    _service = service
    _auto_provider = auto_provider or DisabledAutoAnnotationProvider()


def get_annotation_service() -> AnnotationService:
    return _service


def get_annotation_auth(request: Request) -> AuthContext:
    """Consume the verified BE-02 context installed by authentication middleware."""

    auth = getattr(request.state, "auth_context", None)
    if not isinstance(auth, AuthContext):
        raise problem(
            status=401,
            code="AUTHENTICATION_REQUIRED",
            title="Authentication required",
            detail="A verified BE-02 authentication context is required.",
        )
    path_project = request.path_params.get("project_id")
    header_project = request.headers.get("X-Project-ID")
    if path_project is not None and header_project is not None and path_project != header_project:
        raise problem(
            status=400,
            code="PROJECT_SCOPE_MISMATCH",
            title="Project scope mismatch",
            detail="X-Project-ID must match the project selected by the request path.",
        )
    project_id = path_project or header_project
    if project_id is None:
        raise problem(
            status=400,
            code="PROJECT_SCOPE_REQUIRED",
            title="Project scope required",
            detail="X-Project-ID is required for annotation task routes without a project path.",
        )
    region_code = request.headers.get("X-Region-Code")
    ScopeGuard.require(auth, project_id, region_code)
    select_request_scope(project_id, region_code)
    return auth


ServiceDependency = Annotated[AnnotationService, Depends(get_annotation_service)]
AuthDependency = Annotated[AuthContext, Depends(get_annotation_auth)]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=1)]
StatusQuery = Annotated[AnnotationStatus | None, Query()]
RevisionQuery = Annotated[int | None, Query(ge=0)]


class SaveDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    client_mutation_id: str = Field(min_length=1, max_length=256)
    operations: tuple[AnnotationOperation, ...]


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=0)
    decision: ReviewDecision
    comment: str = Field(default="", max_length=10000)


class AutoAnnotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=0)


def _etag(response: Response, task: AnnotationTask) -> None:
    response.headers["ETag"] = task.etag


@router.get(
    "/projects/{project_id}/annotation-tasks",
    response_model=list[AnnotationTask],
)
def list_annotation_tasks(
    project_id: str,
    service: ServiceDependency,
    auth: AuthDependency,
    status: StatusQuery = None,
) -> tuple[AnnotationTask, ...]:
    return service.list_tasks(project_id=project_id, actor=auth, status=status)


@router.get("/annotation-tasks/{task_id}", response_model=AnnotationTask)
def get_annotation_task(
    task_id: str,
    response: Response,
    service: ServiceDependency,
    auth: AuthDependency,
) -> AnnotationTask:
    task = service.read_task(task_id, auth)
    _etag(response, task)
    return task


@router.post("/annotation-tasks/{task_id}/claim", response_model=AnnotationTask)
def claim_annotation_task(
    task_id: str,
    response: Response,
    service: ServiceDependency,
    auth: AuthDependency,
) -> AnnotationTask:
    task = service.claim(task_id, auth)
    _etag(response, task)
    return task


@router.get("/annotation-tasks/{task_id}/draft", response_model=AnnotationDraft)
def get_annotation_draft(
    task_id: str,
    response: Response,
    service: ServiceDependency,
    auth: AuthDependency,
) -> AnnotationDraft:
    draft = service.get_draft(task_id, auth)
    response.headers["ETag"] = draft.etag
    return draft


@router.get("/annotation-tasks/{task_id}/current", response_model=AnnotationCurrent)
def get_annotation_current(
    task_id: str,
    response: Response,
    service: ServiceDependency,
    auth: AuthDependency,
) -> AnnotationCurrent:
    current = service.get_current(task_id, auth)
    response.headers["ETag"] = current.etag
    return current


@router.post(
    "/annotation-tasks/{task_id}/revisions",
    response_model=AnnotationRevision,
    status_code=201,
)
def save_annotation_draft(
    task_id: str,
    command: SaveDraftRequest,
    response: Response,
    if_match: IfMatch,
    service: ServiceDependency,
    auth: AuthDependency,
) -> AnnotationRevision:
    revision = service.save_draft(
        task_id,
        auth,
        command.operations,
        expected_revision=command.expected_revision,
        if_match=if_match,
        client_mutation_id=command.client_mutation_id,
    )
    response.headers["ETag"] = service.read_task(task_id, auth).etag
    return revision


@router.get(
    "/annotation-tasks/{task_id}/revisions",
    response_model=list[AnnotationRevision],
)
def list_annotation_revisions(
    task_id: str,
    service: ServiceDependency,
    auth: AuthDependency,
) -> tuple[AnnotationRevision, ...]:
    return service.list_revisions(task_id, auth)


@router.get(
    "/annotation-tasks/{task_id}/revisions/{revision}",
    response_model=AnnotationRevision,
)
def get_annotation_revision(
    task_id: str,
    revision: int,
    service: ServiceDependency,
    auth: AuthDependency,
) -> AnnotationRevision:
    return service.get_revision(task_id, revision, auth)


@router.post("/annotation-tasks/{task_id}/submit", response_model=AnnotationTask)
def submit_annotation_revision(
    task_id: str,
    command: SubmitRequest,
    response: Response,
    if_match: IfMatch,
    service: ServiceDependency,
    auth: AuthDependency,
) -> AnnotationTask:
    task = service.submit(
        task_id,
        auth,
        expected_revision=command.expected_revision,
        if_match=if_match,
    )
    _etag(response, task)
    return task


@router.post("/annotation-tasks/{task_id}/reviews", response_model=AnnotationTask)
def review_annotation_revision(
    task_id: str,
    command: ReviewRequest,
    response: Response,
    if_match: IfMatch,
    service: ServiceDependency,
    auth: AuthDependency,
) -> AnnotationTask:
    service.review(
        task_id,
        auth,
        command.decision,
        revision=command.revision,
        if_match=if_match,
        comment=command.comment,
    )
    task = service.read_task(task_id, auth)
    _etag(response, task)
    return task


@router.get(
    "/annotation-tasks/{task_id}/reviews",
    response_model=list[AnnotationReview],
)
def list_annotation_reviews(
    task_id: str,
    service: ServiceDependency,
    auth: AuthDependency,
) -> tuple[AnnotationReview, ...]:
    return service.list_reviews(task_id, auth)


@router.get("/annotation-tasks/{task_id}/history", response_model=AnnotationHistory)
def get_annotation_history(
    task_id: str,
    service: ServiceDependency,
    auth: AuthDependency,
) -> AnnotationHistory:
    return service.get_history(task_id, auth)


@router.get(
    "/annotation-tasks/{task_id}/exclusions",
    response_model=list[ExclusionRange],
)
def get_effective_exclusions(
    task_id: str,
    service: ServiceDependency,
    auth: AuthDependency,
    revision: RevisionQuery = None,
) -> tuple[ExclusionRange, ...]:
    return service.effective_exclusions(task_id, revision=revision, actor=auth)


@router.get(
    "/projects/{project_id}/rollouts/{rollout_id}/approved-annotation",
    response_model=AnnotationApprovedV1,
)
def get_approved_annotation(
    project_id: str,
    rollout_id: str,
    service: ServiceDependency,
    auth: AuthDependency,
) -> AnnotationApprovedV1:
    return service.approved_snapshot(project_id=project_id, rollout_id=rollout_id, actor=auth)


@router.get(
    "/capabilities/auto-annotation",
    response_model=AutoAnnotationCapability,
)
def get_auto_annotation_capability() -> AutoAnnotationCapability:
    return _auto_provider.capability()


@router.post("/annotation-tasks/{task_id}/auto-annotation")
def request_auto_annotation(
    task_id: str,
    command: AutoAnnotationRequest,
    service: ServiceDependency,
    auth: AuthDependency,
) -> str:
    service.read_task(task_id, auth)
    return _auto_provider.request(task_id=task_id, revision=command.revision)
