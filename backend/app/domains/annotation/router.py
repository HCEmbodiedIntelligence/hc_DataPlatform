"""HTTP surface for annotation tasks, resolver, drafts, submit, review and rebase."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext, require
from app.core.db import get_session
from app.core.pagination import CursorParams, build_page

from . import models, schemas
from .repository import scope_predicate
from .service import (
    CONTRACT_VERSION,
    AnnotationService,
    ManualIssueGatePort,
    MissingManualIssueGatePort,
    _iso,
    _scope,
    _task_projection,
    envelope,
)

router = APIRouter(prefix="/api/v1/projects/{project_id}/regions/{region_code}")
Session = Annotated[AsyncSession, Depends(get_session)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)]
ReadEpisode = Annotated[RequestContext, Depends(require("episode.read"))]
ReadTask = Annotated[RequestContext, Depends(require("annotation_task.read"))]
CreateTask = Annotated[RequestContext, Depends(require("annotation_task.create"))]
ClaimTask = Annotated[RequestContext, Depends(require("annotation_task.claim"))]
AssignTask = Annotated[RequestContext, Depends(require("annotation_task.assign"))]
EditDraft = Annotated[RequestContext, Depends(require("annotation_draft.edit"))]
SubmitTask = Annotated[RequestContext, Depends(require("annotation.submit"))]
RebaseTask = Annotated[RequestContext, Depends(require("annotation_task.rebase"))]
ReviewTask = Annotated[RequestContext, Depends(require("annotation.review"))]
ReadSet = Annotated[RequestContext, Depends(require("annotation_set.read"))]


def get_manual_issue_gate_port() -> ManualIssueGatePort:
    return cast(ManualIssueGatePort, MissingManualIssueGatePort())


IssueGate = Annotated[ManualIssueGatePort, Depends(get_manual_issue_gate_port)]


async def annotation_page(
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after: Annotated[str | None, Query(min_length=1, max_length=2048)] = None,
    before: Annotated[str | None, Query(min_length=1, max_length=2048)] = None,
) -> CursorParams:
    return CursorParams(limit=limit, after=after, before=before)


Page = Annotated[CursorParams, Depends(annotation_page)]


@router.get(
    "/episode-revisions/{revision_id}/annotation-task-entry-resolution",
    operation_id="resolveAnnotationTaskEntry",
    include_in_schema=False,
)
async def resolve_annotation_task_entry(
    revision_id: str,
    dataset_id: Annotated[str, Query()],
    dataset_version_id: Annotated[str, Query()],
    episode_id: Annotated[str, Query()],
    stream_id: Annotated[list[str], Query()],
    start_ns: Annotated[str, Query(pattern=r"^(0|[1-9][0-9]*)$")],
    end_ns: Annotated[str, Query(pattern=r"^(0|[1-9][0-9]*)$")],
    ontology_id: Annotated[str, Query()],
    ontology_version: Annotated[str, Query()],
    ontology_hash: Annotated[str, Query()],
    session: Session,
    ctx: ReadEpisode,
) -> dict:
    source = schemas.TaskSourceBinding(
        dataset_id=dataset_id,
        dataset_version_id=dataset_version_id,
        episode_id=episode_id,
        base_revision_id=revision_id,
        stream_ids=stream_id,
        start_ns=start_ns,
        end_ns=end_ns,
    ).model_dump(mode="json")
    ontology = schemas.OntologyBinding(
        ontology_id=ontology_id, ontology_version=ontology_version, ontology_hash=ontology_hash
    ).model_dump(mode="json")
    return await AnnotationService(session).resolve_entry(
        ctx, source, ontology, "annotation_task.create" in ctx.capabilities
    )


@router.post(
    "/annotation-task-entry-resolutions/{resolution_id}:materialize",
    operation_id="materializeAnnotationTaskFromEntryResolution",
    status_code=201,
    include_in_schema=False,
)
async def materialize_annotation_task_from_entry_resolution(
    resolution_id: str,
    command: schemas.MaterializeAnnotationTaskRequest,
    session: Session,
    ctx: CreateTask,
    idempotency_key: IdempotencyKey,
) -> dict:
    return await AnnotationService(session).materialize(
        ctx, idempotency_key, resolution_id, command
    )


@router.get("/annotation-tasks", operation_id="listAnnotationTasks")
async def list_annotation_tasks(
    session: Session,
    ctx: ReadTask,
    page: Page,
    view: Annotated[str, Query()] = "assigned_to_me",
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> dict:
    stmt = select(models.AnnotationTask).where(scope_predicate(models.AnnotationTask, ctx))
    if view == "assigned_to_me":
        stmt = stmt.where(models.AnnotationTask.assignee_id == ctx.actor_id)
    elif view == "available":
        stmt = stmt.where(
            models.AnnotationTask.workflow_status == "QUEUED",
            models.AnnotationTask.assignee_id.is_(None),
        )
    elif view == "stale":
        stmt = stmt.where(models.AnnotationTask.source_status == "STALE")
    if status_filter:
        stmt = stmt.where(models.AnnotationTask.workflow_status == status_filter)
    rows = list(
        (
            await session.scalars(
                stmt.order_by(
                    models.AnnotationTask.updated_at.desc(), models.AnnotationTask.task_id.desc()
                ).limit(page.limit + 1)
            )
        ).all()
    )
    result = build_page(rows, page, ("updated_at", "task_id"))
    result["items"] = [_task_projection(row, ctx) for row in result["items"]]
    result["snapshot_id"] = "annotation-snapshot"
    result.update(scope=_scope(ctx), request_id=ctx.request_id, contract_version=CONTRACT_VERSION)
    return result


@router.post("/annotation-tasks", operation_id="createAnnotationTask", status_code=201)
async def create_annotation_task(
    command: schemas.CreateAnnotationTaskRequest,
    session: Session,
    ctx: CreateTask,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict:
    result = await AnnotationService(session).create_task(ctx, idempotency_key, command)
    response.headers["ETag"] = result["data"]["etag"]
    return result


@router.get("/annotation-tasks/{task_id}", operation_id="getAnnotationTask")
async def get_annotation_task(
    task_id: str, session: Session, ctx: ReadTask, response: Response
) -> dict:
    result = await AnnotationService(session).task_detail(ctx, task_id)
    response.headers["ETag"] = result["data"]["task"]["etag"]
    return result


@router.post("/annotation-tasks/{task_id}:claim", operation_id="claimAnnotationTask")
async def claim_annotation_task(
    task_id: str,
    command: schemas.ClaimAnnotationTaskRequest,
    request: Request,
    session: Session,
    ctx: ClaimTask,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict:
    result = await AnnotationService(session).claim(ctx, idempotency_key, task_id, request, command)
    response.headers["ETag"] = result["data"]["etag"]
    return result


@router.post("/annotation-tasks/{task_id}:assign", operation_id="assignAnnotationTask")
async def assign_annotation_task(
    task_id: str,
    command: schemas.AssignAnnotationTaskRequest,
    request: Request,
    session: Session,
    ctx: AssignTask,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict:
    result = await AnnotationService(session).assign(
        ctx, idempotency_key, task_id, request, command
    )
    response.headers["ETag"] = result["data"]["etag"]
    return result


@router.put("/annotation-tasks/{task_id}/draft", operation_id="saveAnnotationDraft")
async def save_annotation_draft(
    task_id: str,
    command: schemas.SaveAnnotationDraftRequest,
    request: Request,
    session: Session,
    ctx: EditDraft,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict:
    result = await AnnotationService(session).save_draft(
        ctx, idempotency_key, task_id, request, command
    )
    response.headers["ETag"] = result["data"]["draft"]["etag"]
    return result


@router.post(
    "/annotation-tasks/{task_id}:submit-preflight", operation_id="preflightAnnotationSubmit"
)
async def preflight_annotation_submit(
    task_id: str,
    command: schemas.SubmitPreflightRequest,
    request: Request,
    session: Session,
    ctx: SubmitTask,
    idempotency_key: IdempotencyKey,
    gate: IssueGate,
) -> dict:
    return await AnnotationService(session, gate).preflight(
        ctx, idempotency_key, task_id, request, command
    )


@router.post(
    "/annotation-tasks/{task_id}:submit", operation_id="submitAnnotationTask", status_code=201
)
async def submit_annotation_task(
    task_id: str,
    command: schemas.SubmitAnnotationTaskRequest,
    request: Request,
    session: Session,
    ctx: SubmitTask,
    idempotency_key: IdempotencyKey,
    gate: IssueGate,
) -> dict:
    return await AnnotationService(session, gate).submit(
        ctx, idempotency_key, task_id, request, command
    )


@router.post(
    "/annotation-tasks/{task_id}/submissions/{submission_id}:review",
    operation_id="reviewAnnotationSubmission",
    include_in_schema=False,
)
async def review_annotation_submission(
    task_id: str,
    submission_id: str,
    command: schemas.ReviewAnnotationSubmissionRequest,
    request: Request,
    session: Session,
    ctx: ReviewTask,
    idempotency_key: IdempotencyKey,
) -> dict:
    return await AnnotationService(session).review_submission(
        ctx, idempotency_key, task_id, submission_id, request, command
    )


@router.post(
    "/annotation-tasks/{task_id}:rebase", operation_id="rebaseAnnotationTask", status_code=201
)
async def rebase_annotation_task(
    task_id: str,
    command: schemas.RebaseAnnotationTaskRequest,
    request: Request,
    session: Session,
    ctx: RebaseTask,
    idempotency_key: IdempotencyKey,
) -> dict:
    return await AnnotationService(session).rebase(ctx, idempotency_key, task_id, request, command)


@router.get(
    "/annotation-tasks/{task_id}/manual-issues", operation_id="listAnnotationTaskManualIssues"
)
async def list_annotation_task_manual_issues(
    task_id: str, session: Session, ctx: ReadTask, gate: IssueGate
) -> dict:
    task = await AnnotationService(session).repo.task(ctx, task_id)
    result = await gate.evaluate_annotation_submission(
        ctx=ctx,
        source={
            "dataset_id": task.dataset_id,
            "dataset_version_id": task.dataset_version_id,
            "episode_id": task.episode_id,
            "base_revision_id": task.base_revision_id,
            "stream_ids": task.stream_ids,
            "start_ns": str(task.start_ns),
            "end_ns": str(task.end_ns),
        },
    )
    return envelope({"items": result.get("issues", []), "submission_gate": result}, ctx)


def _set_projection(row: models.AnnotationSet, ctx: RequestContext) -> dict:
    return {
        "annotation_set_id": row.annotation_set_id,
        "task_id": row.task_id,
        "submission_id": row.submission_id,
        "scope": _scope(ctx),
        "source": {"base_revision_id": row.base_revision_id},
        "ontology": {
            "ontology_id": row.ontology_id,
            "ontology_version": row.ontology_version,
            "ontology_hash": row.ontology_hash,
        },
        "entries": row.entries,
        "content_hash": row.content_hash,
        "provenance": row.provenance,
        "submitted_by": row.submitted_by,
        "submitted_at": _iso(row.submitted_at),
        "effective_state": row.effective_state,
        "supersedes_annotation_set_id": row.supersedes_annotation_set_id,
        "superseded_by_annotation_set_id": row.superseded_by_annotation_set_id,
    }


@router.get("/annotation-sets/{annotation_set_id}", operation_id="getAnnotationSet")
async def get_annotation_set(annotation_set_id: str, session: Session, ctx: ReadSet) -> dict:
    row = await session.get(models.AnnotationSet, annotation_set_id)
    if row is None or (row.organization_id, row.project_id, row.region_code) != (
        ctx.organization_id,
        ctx.project_id,
        ctx.region_code,
    ):
        from app.core.errors import NotFoundError

        raise NotFoundError(code="ANNOTATION_SET_NOT_FOUND")
    return envelope(_set_projection(row, ctx), ctx)


@router.get(
    "/episode-revisions/{revision_id}/annotation-sets", operation_id="listRevisionAnnotationSets"
)
async def list_revision_annotation_sets(
    revision_id: str, session: Session, ctx: ReadSet, page: Page, include_superseded: bool = False
) -> dict:
    stmt = select(models.AnnotationSet).where(
        models.AnnotationSet.organization_id == ctx.organization_id,
        models.AnnotationSet.project_id == ctx.project_id,
        models.AnnotationSet.region_code == ctx.region_code,
        models.AnnotationSet.base_revision_id == revision_id,
    )
    if not include_superseded:
        stmt = stmt.where(models.AnnotationSet.effective_state == "ACTIVE")
    rows = list(
        (
            await session.scalars(
                stmt.order_by(
                    models.AnnotationSet.submitted_at.desc(),
                    models.AnnotationSet.annotation_set_id.desc(),
                ).limit(page.limit + 1)
            )
        ).all()
    )
    result = build_page(rows, page, ("submitted_at", "annotation_set_id"))
    result["items"] = [_set_projection(row, ctx) for row in result["items"]]
    result["snapshot_id"] = "annotation-set-snapshot"
    result.update(scope=_scope(ctx), request_id=ctx.request_id, contract_version=CONTRACT_VERSION)
    return result
