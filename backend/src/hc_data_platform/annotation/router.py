"""FastAPI boundary for scoped annotation, revision, and review workflows."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from hc_data_platform.core.context import select_request_scope
from hc_data_platform.core.errors import problem
from hc_data_platform.ingest.models import ManifestDiscoveryV1
from hc_data_platform.ingest.router import get_service as get_ingest_service
from hc_data_platform.ingest.service import UploadSessionService
from hc_data_platform.security import AuthContext, ScopeGuard

from .auto_jobs import (
    AutoAnnotationInputSelection,
    AutoAnnotationJob,
    AutoAnnotationJobService,
)
from .models import (
    AnnotationApprovedV1,
    AnnotationCurrent,
    AnnotationDraft,
    AnnotationHistory,
    AnnotationOperation,
    AnnotationReview,
    AnnotationRevision,
    AnnotationRevisionThreadPage,
    AnnotationStatus,
    AnnotationSubmission,
    AnnotationTag,
    AnnotationTask,
    AutoAnnotationCapability,
    ExclusionRange,
    ReviewDecision,
    RevisionOrigin,
    TagSchemaDocument,
    TagSchemaTarget,
    TagSchemaVersion,
)
from .ports import AutoAnnotationProvider
from .service import AnnotationService, DisabledAutoAnnotationProvider, InMemoryAnnotationService

router = APIRouter(prefix="/api/v1", tags=["annotation"])

_service: AnnotationService = InMemoryAnnotationService()
_auto_provider: AutoAnnotationProvider = DisabledAutoAnnotationProvider()
_auto_jobs: AutoAnnotationJobService | None = None


def configure_annotation(
    service: AnnotationService,
    *,
    auto_provider: AutoAnnotationProvider | None = None,
    auto_jobs: AutoAnnotationJobService | None = None,
) -> None:
    """Application composition hook for PostgreSQL, BE-02 auth, and feature providers."""

    global _auto_jobs, _auto_provider, _service
    _service = service
    _auto_provider = auto_provider or DisabledAutoAnnotationProvider()
    _auto_jobs = auto_jobs


def get_annotation_service() -> AnnotationService:
    return _service


def get_auto_annotation_job_service() -> AutoAnnotationJobService:
    if _auto_jobs is None:
        raise problem(
            status=503,
            code="AUTO_ANNOTATION_PROVIDER_UNAVAILABLE",
            title="Automatic annotation provider unavailable",
            detail="No automatic annotation provider is configured for this deployment.",
        )
    return _auto_jobs


async def get_annotation_auth(request: Request) -> AuthContext:
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
    path_region = request.path_params.get("region_code")
    header_region = request.headers.get("X-Region-Code")
    if path_region is not None and header_region is not None and path_region != header_region:
        raise problem(
            status=400,
            code="REGION_SCOPE_MISMATCH",
            title="Region scope mismatch",
            detail="X-Region-Code must match the region selected by the request path.",
        )
    region_code = path_region or header_region
    if (
        "/annotation-tasks" in request.url.path
        or "/approved-annotation" in request.url.path
        or "/annotations/revisions" in request.url.path
    ) and header_region is None:
        raise problem(
            status=400,
            code="REGION_SCOPE_REQUIRED",
            title="Region scope required",
            detail="X-Region-Code is required for annotation task resources.",
        )
    ScopeGuard.require(auth, project_id, region_code)
    select_request_scope(project_id, region_code)
    return auth


ServiceDependency = Annotated[AnnotationService, Depends(get_annotation_service)]
IngestServiceDependency = Annotated[UploadSessionService, Depends(get_ingest_service)]
AuthDependency = Annotated[AuthContext, Depends(get_annotation_auth)]
AutoJobDependency = Annotated[
    AutoAnnotationJobService,
    Depends(get_auto_annotation_job_service),
]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=1)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=256)]
StatusQuery = Annotated[AnnotationStatus | None, Query()]
RevisionQuery = Annotated[int | None, Query(ge=0)]
RevisionThreadAfter = Annotated[str | None, Query(max_length=16_384)]
RevisionThreadLimit = Annotated[int, Query(ge=1, le=100)]


class SaveDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    client_mutation_id: str = Field(min_length=1, max_length=256)
    tags: tuple[AnnotationTag, ...] | None = None
    operations: tuple[AnnotationOperation, ...]


class RestoreRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_revision: int = Field(ge=0)
    expected_revision: int = Field(ge=0)
    client_mutation_id: str = Field(min_length=1, max_length=256)


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=0)
    submission_id: str | None = Field(default=None, min_length=1)
    decision: ReviewDecision
    comment: str = Field(default="", max_length=10000)


class AutoAnnotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=0)
    provider: str | None = Field(default=None, min_length=1, max_length=128)
    model: str | None = Field(default=None, min_length=1, max_length=256)
    input_selection: AutoAnnotationInputSelection = Field(
        default_factory=AutoAnnotationInputSelection
    )


class ApplyAutoAnnotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    tags: tuple[AnnotationTag, ...] | None = None
    operations: tuple[AnnotationOperation, ...] | None = None


class CreateTagSchemaVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_id: str | None = Field(default=None, min_length=1, max_length=256)
    version: int | None = Field(default=None, ge=1)
    name: str = Field(min_length=1, max_length=256)
    document: TagSchemaDocument
    compatible_targets: tuple[TagSchemaTarget, ...] = ()


def _etag(response: Response, task: AnnotationTask) -> None:
    response.headers["ETag"] = task.etag


def _no_store(response: Response) -> None:
    """Keep mutable, tenant-scoped annotation evidence out of HTTP caches."""

    response.headers["Cache-Control"] = "no-store"


def _request_id(request: Request, *, default: str = "annotation-auto-job") -> str:
    # Production middleware writes its normalized correlation id on the
    # request. Do not read a context-var here: lightweight router tests and
    # background-adjacent calls can retain an unrelated ambient context.
    request_id = getattr(request.state, "request_id", None)
    if isinstance(request_id, str) and request_id:
        return request_id
    return request.headers.get("X-Request-ID", default)


@router.post(
    "/projects/{project_id}/tag-schemas",
    response_model=TagSchemaVersion,
    status_code=201,
)
def create_tag_schema_version(
    project_id: str,
    command: CreateTagSchemaVersionRequest,
    response: Response,
    service: ServiceDependency,
    auth: AuthDependency,
) -> TagSchemaVersion:
    _no_store(response)
    return service.create_tag_schema_version(
        project_id=project_id,
        name=command.name,
        document=command.document,
        compatible_targets=command.compatible_targets,
        schema_id=command.schema_id,
        version=command.version,
        actor=auth,
    )


@router.get(
    "/projects/{project_id}/tag-schemas/{schema_id}/versions",
    response_model=list[TagSchemaVersion],
)
def list_tag_schema_versions(
    project_id: str,
    schema_id: str,
    response: Response,
    service: ServiceDependency,
    auth: AuthDependency,
) -> tuple[TagSchemaVersion, ...]:
    _no_store(response)
    return service.list_tag_schema_versions(project_id=project_id, schema_id=schema_id, actor=auth)


@router.get(
    "/projects/{project_id}/tag-schemas/{schema_id}/versions/{version}",
    response_model=TagSchemaVersion,
)
def get_tag_schema_version(
    project_id: str,
    schema_id: str,
    version: int,
    response: Response,
    service: ServiceDependency,
    auth: AuthDependency,
) -> TagSchemaVersion:
    _no_store(response)
    return service.get_tag_schema_version(
        project_id=project_id,
        schema_id=schema_id,
        version=version,
        actor=auth,
    )


@router.post(
    "/projects/{project_id}/tag-schemas/{schema_id}/versions/{version}/publish",
    response_model=TagSchemaVersion,
)
def publish_tag_schema_version(
    project_id: str,
    schema_id: str,
    version: int,
    response: Response,
    service: ServiceDependency,
    auth: AuthDependency,
) -> TagSchemaVersion:
    _no_store(response)
    return service.publish_tag_schema_version(
        project_id=project_id,
        schema_id=schema_id,
        version=version,
        actor=auth,
    )


@router.get(
    "/projects/{project_id}/annotation-tasks",
    response_model=list[AnnotationTask],
)
def list_annotation_tasks(
    project_id: str,
    response: Response,
    service: ServiceDependency,
    auth: AuthDependency,
    status: StatusQuery = None,
) -> tuple[AnnotationTask, ...]:
    _no_store(response)
    return service.list_tasks(project_id=project_id, actor=auth, status=status)


@router.get(
    "/annotations/revisions",
    response_model=AnnotationRevisionThreadPage,
    responses={
        200: {
            "headers": {
                "Cache-Control": {
                    "required": True,
                    "schema": {"type": "string"},
                }
            }
        },
        400: {"description": "Scope headers or the cursor are invalid."},
    },
)
def list_annotation_revision_threads(
    request: Request,
    response: Response,
    service: ServiceDependency,
    auth: AuthDependency,
    status: StatusQuery = None,
    origin: Annotated[RevisionOrigin | None, Query()] = None,
    after: RevisionThreadAfter = None,
    limit: RevisionThreadLimit = 25,
) -> AnnotationRevisionThreadPage:
    """List current immutable revision threads for the selected project and region."""

    _no_store(response)
    request_id = _request_id(request, default="annotation-revision-list")
    project_id = request.headers.get("X-Project-ID")
    region_code = request.headers.get("X-Region-Code")
    if project_id is None or region_code is None:
        # get_annotation_auth rejects this first in normal composition; the explicit
        # check keeps dependency-overridden router tests fail-closed too.
        raise problem(
            status=400,
            code="PROJECT_SCOPE_REQUIRED",
            title="Project scope required",
            detail="X-Project-ID and X-Region-Code are required for revision threads.",
        )
    return service.list_revision_threads(
        project_id=project_id,
        region_code=region_code,
        actor=auth,
        request_id=request_id,
        status=status,
        origin=origin,
        after=after,
        limit=limit,
    )


@router.get("/annotation-tasks/{task_id}", response_model=AnnotationTask)
def get_annotation_task(
    task_id: str,
    response: Response,
    service: ServiceDependency,
    auth: AuthDependency,
) -> AnnotationTask:
    _no_store(response)
    task = service.read_task(task_id, auth)
    _etag(response, task)
    return task


@router.get(
    "/projects/{project_id}/regions/{region_code}/annotation-tasks/{task_id}/manifest-discovery",
    response_model=ManifestDiscoveryV1,
)
def get_annotation_task_manifest_discovery(
    project_id: str,
    region_code: str,
    task_id: str,
    response: Response,
    service: ServiceDependency,
    ingest_service: IngestServiceDependency,
    auth: AuthDependency,
) -> ManifestDiscoveryV1:
    """Return only the persisted discovery for this authorized task's rollout."""

    _no_store(response)
    task = service.read_task(task_id, auth)
    if task.project_id != project_id or task.region_code != region_code:
        raise problem(
            status=404,
            code="ANNOTATION_TASK_NOT_FOUND",
            title="Annotation task not found",
            detail="The task does not exist in the selected project and Region.",
        )
    return ingest_service.get_manifest_discovery_for_rollout(
        project_id=project_id,
        region_code=region_code,
        rollout_id=task.rollout_id,
    )


@router.post("/annotation-tasks/{task_id}/claim", response_model=AnnotationTask)
def claim_annotation_task(
    task_id: str,
    response: Response,
    service: ServiceDependency,
    auth: AuthDependency,
) -> AnnotationTask:
    _no_store(response)
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
    _no_store(response)
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
    _no_store(response)
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
    _no_store(response)
    revision = service.save_draft(
        task_id,
        auth,
        command.operations,
        tags=command.tags,
        expected_revision=command.expected_revision,
        if_match=if_match,
        client_mutation_id=command.client_mutation_id,
    )
    response.headers["ETag"] = service.read_task(task_id, auth).etag
    return revision


@router.post(
    "/annotation-tasks/{task_id}/revisions:restore",
    response_model=AnnotationRevision,
    status_code=201,
)
def restore_annotation_revision(
    task_id: str,
    command: RestoreRevisionRequest,
    response: Response,
    if_match: IfMatch,
    service: ServiceDependency,
    auth: AuthDependency,
) -> AnnotationRevision:
    """Create a new immutable annotation data revision from an earlier revision."""

    _no_store(response)
    revision = service.restore_revision(
        task_id,
        auth,
        target_revision=command.target_revision,
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
    response: Response,
    service: ServiceDependency,
    auth: AuthDependency,
) -> tuple[AnnotationRevision, ...]:
    _no_store(response)
    return service.list_revisions(task_id, auth)


@router.get(
    "/annotation-tasks/{task_id}/revisions/{revision}",
    response_model=AnnotationRevision,
)
def get_annotation_revision(
    task_id: str,
    revision: int,
    response: Response,
    service: ServiceDependency,
    auth: AuthDependency,
) -> AnnotationRevision:
    _no_store(response)
    return service.get_revision(task_id, revision, auth)


@router.post(
    "/annotation-tasks/{task_id}/submit",
    response_model=AnnotationSubmission,
    status_code=201,
)
def submit_annotation_revision(
    task_id: str,
    command: SubmitRequest,
    response: Response,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
    auth: AuthDependency,
) -> AnnotationSubmission:
    _no_store(response)
    submission = service.submit_for_review(
        task_id,
        auth,
        expected_revision=command.expected_revision,
        if_match=if_match,
        idempotency_key=idempotency_key,
    )
    task = service.read_task(task_id, auth)
    _etag(response, task)
    return submission


@router.get(
    "/annotation-tasks/{task_id}/submissions",
    response_model=list[AnnotationSubmission],
)
def list_annotation_submissions(
    task_id: str,
    response: Response,
    service: ServiceDependency,
    auth: AuthDependency,
) -> tuple[AnnotationSubmission, ...]:
    _no_store(response)
    return service.list_submissions(task_id, auth)


@router.get(
    "/annotation-tasks/{task_id}/submissions/{submission_id}",
    response_model=AnnotationSubmission,
)
def get_annotation_submission(
    task_id: str,
    submission_id: str,
    response: Response,
    service: ServiceDependency,
    auth: AuthDependency,
) -> AnnotationSubmission:
    _no_store(response)
    return service.get_submission(task_id, submission_id, auth)


@router.post("/annotation-tasks/{task_id}/reviews", response_model=AnnotationTask)
def review_annotation_revision(
    task_id: str,
    command: ReviewRequest,
    response: Response,
    if_match: IfMatch,
    service: ServiceDependency,
    auth: AuthDependency,
) -> AnnotationTask:
    _no_store(response)
    service.review(
        task_id,
        auth,
        command.decision,
        revision=command.revision,
        submission_id=command.submission_id,
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
    response: Response,
    service: ServiceDependency,
    auth: AuthDependency,
) -> tuple[AnnotationReview, ...]:
    _no_store(response)
    return service.list_reviews(task_id, auth)


@router.get("/annotation-tasks/{task_id}/history", response_model=AnnotationHistory)
def get_annotation_history(
    task_id: str,
    response: Response,
    service: ServiceDependency,
    auth: AuthDependency,
) -> AnnotationHistory:
    _no_store(response)
    return service.get_history(task_id, auth)


@router.get(
    "/annotation-tasks/{task_id}/exclusions",
    response_model=list[ExclusionRange],
)
def get_effective_exclusions(
    task_id: str,
    response: Response,
    service: ServiceDependency,
    auth: AuthDependency,
    revision: RevisionQuery = None,
) -> tuple[ExclusionRange, ...]:
    _no_store(response)
    return service.effective_exclusions(task_id, revision=revision, actor=auth)


@router.get(
    "/projects/{project_id}/rollouts/{rollout_id}/approved-annotation",
    response_model=AnnotationApprovedV1,
)
def get_approved_annotation(
    project_id: str,
    rollout_id: str,
    response: Response,
    service: ServiceDependency,
    auth: AuthDependency,
) -> AnnotationApprovedV1:
    _no_store(response)
    return service.approved_snapshot(project_id=project_id, rollout_id=rollout_id, actor=auth)


@router.get(
    "/capabilities/auto-annotation",
    response_model=AutoAnnotationCapability,
)
def get_auto_annotation_capability(response: Response) -> AutoAnnotationCapability:
    _no_store(response)
    return _auto_jobs.capability() if _auto_jobs is not None else _auto_provider.capability()


@router.post(
    "/annotation-tasks/{task_id}/auto-annotation",
    response_model=AutoAnnotationJob,
    status_code=202,
)
def request_auto_annotation(
    task_id: str,
    command: AutoAnnotationRequest,
    response: Response,
    request: Request,
    auth: AuthDependency,
    jobs: AutoJobDependency,
    idempotency_key: IdempotencyKey,
) -> AutoAnnotationJob:
    _no_store(response)
    capability = jobs.capability()
    descriptor = capability.providers[0] if capability.providers else None
    provider_name = command.provider or (None if descriptor is None else descriptor.provider)
    model = command.model or (None if descriptor is None else descriptor.models[0])
    if provider_name is None or model is None:
        raise problem(
            status=503,
            code="AUTO_ANNOTATION_PROVIDER_UNAVAILABLE",
            title="Automatic annotation provider unavailable",
            detail="No automatic annotation provider or model is configured.",
        )
    request_id = _request_id(request)
    job = jobs.create(
        auth=auth,
        task_id=task_id,
        source_revision=command.revision,
        provider_name=provider_name,
        model=model,
        input_selection=command.input_selection,
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    response.headers["Location"] = (
        f"/api/v1/annotation-tasks/{task_id}/auto-annotation-jobs/{job.job_id}"
    )
    return job


@router.get(
    "/annotation-tasks/{task_id}/auto-annotation-jobs/{job_id}",
    response_model=AutoAnnotationJob,
)
def get_auto_annotation_job(
    task_id: str,
    job_id: str,
    response: Response,
    auth: AuthDependency,
    jobs: AutoJobDependency,
) -> AutoAnnotationJob:
    _no_store(response)
    return jobs.get(auth=auth, task_id=task_id, job_id=job_id)


@router.post(
    "/annotation-tasks/{task_id}/auto-annotation-jobs/{job_id}:cancel",
    response_model=AutoAnnotationJob,
)
def cancel_auto_annotation_job(
    task_id: str,
    job_id: str,
    response: Response,
    request: Request,
    auth: AuthDependency,
    jobs: AutoJobDependency,
) -> AutoAnnotationJob:
    _no_store(response)
    return jobs.cancel(
        auth=auth,
        task_id=task_id,
        job_id=job_id,
        request_id=_request_id(request),
    )


@router.post(
    "/annotation-tasks/{task_id}/auto-annotation-jobs/{job_id}:retry",
    response_model=AutoAnnotationJob,
    status_code=202,
)
def retry_auto_annotation_job(
    task_id: str,
    job_id: str,
    response: Response,
    request: Request,
    auth: AuthDependency,
    jobs: AutoJobDependency,
) -> AutoAnnotationJob:
    _no_store(response)
    request_id = _request_id(request)
    job = jobs.retry(
        auth=auth,
        task_id=task_id,
        job_id=job_id,
        request_id=request_id,
    )
    return job


@router.post(
    "/annotation-tasks/{task_id}/auto-annotation-jobs/{job_id}:apply",
    response_model=AnnotationRevision,
)
def apply_auto_annotation_job(
    task_id: str,
    job_id: str,
    command: ApplyAutoAnnotationRequest,
    response: Response,
    request: Request,
    if_match: IfMatch,
    service: ServiceDependency,
    auth: AuthDependency,
    jobs: AutoJobDependency,
) -> AnnotationRevision:
    _no_store(response)
    revision = jobs.apply(
        auth=auth,
        task_id=task_id,
        job_id=job_id,
        expected_revision=command.expected_revision,
        if_match=if_match,
        tags=command.tags,
        operations=command.operations,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = service.read_task(task_id, auth).etag
    return revision
