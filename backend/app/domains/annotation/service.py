"""Annotation state machines, schema validation, and resolver coordination."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Any, Protocol

from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.context import RequestContext
from app.core.errors import (
    ForbiddenError,
    NotFoundError,
    ServerError,
    ValidationError,
    VersionConflictError,
)
from app.core.etag import check_if_match, compute_etag
from app.core.idempotency import with_idempotency
from app.core.ids import new_id
from app.core.outbox import emit_event

from . import models, schemas
from .repository import AnnotationRepository
from .schemas import AnnotationEntry


class AnnotationWorkflowState(StrEnum):
    QUEUED = "QUEUED"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    SUBMITTED = "SUBMITTED"
    RETURNED = "RETURNED"
    APPROVED = "APPROVED"
    CANCELLED = "CANCELLED"


WORKFLOW_TRANSITIONS: dict[AnnotationWorkflowState, frozenset[AnnotationWorkflowState]] = {
    AnnotationWorkflowState.QUEUED: frozenset(
        {AnnotationWorkflowState.ASSIGNED, AnnotationWorkflowState.CANCELLED}
    ),
    AnnotationWorkflowState.ASSIGNED: frozenset(
        {
            AnnotationWorkflowState.IN_PROGRESS,
            AnnotationWorkflowState.BLOCKED,
            AnnotationWorkflowState.CANCELLED,
        }
    ),
    AnnotationWorkflowState.IN_PROGRESS: frozenset(
        {
            AnnotationWorkflowState.SUBMITTED,
            AnnotationWorkflowState.BLOCKED,
            AnnotationWorkflowState.CANCELLED,
        }
    ),
    AnnotationWorkflowState.BLOCKED: frozenset(
        {
            AnnotationWorkflowState.ASSIGNED,
            AnnotationWorkflowState.IN_PROGRESS,
            AnnotationWorkflowState.CANCELLED,
        }
    ),
    AnnotationWorkflowState.SUBMITTED: frozenset(
        {AnnotationWorkflowState.APPROVED, AnnotationWorkflowState.RETURNED}
    ),
    AnnotationWorkflowState.RETURNED: frozenset(
        {AnnotationWorkflowState.IN_PROGRESS, AnnotationWorkflowState.CANCELLED}
    ),
    AnnotationWorkflowState.APPROVED: frozenset(),
    AnnotationWorkflowState.CANCELLED: frozenset(),
}


class AnnotationSourcePort(Protocol):
    async def resolve_annotation_context(
        self,
        *,
        scope_key: str,
        revision_id: str,
        stream_ids: Sequence[str],
        start_ns: int,
        end_ns: int,
    ) -> dict[str, Any]: ...


class AnnotationResolverRepository(Protocol):
    async def lock_active_by_coverage(self, coverage_key_hash: str) -> Any | None: ...
    async def insert_task_and_empty_draft(self, context: dict[str, Any]) -> Any: ...


@dataclass(frozen=True, slots=True)
class StableFieldError:
    path: str
    code: str
    message: str


_ENTRY_ADAPTER = TypeAdapter(list[AnnotationEntry])
_ENTRY_FIELDS = {"annotation_id", "semantic_type", "label_code", "attributes", "anchor"}
_ANCHOR_BY_SEMANTIC = {
    "ACTION": "TIME_RANGE",
    "PHASE": "TIME_RANGE",
    "OBJECT": "OBJECT_TRACK",
    "EVENT": "TIME_POINT",
    "KEYFRAME": "TIME_POINT",
}


def _escape_json_pointer(value: object) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def validate_annotation_entries(entries: list[dict[str, Any]]) -> list[AnnotationEntry]:
    """Validate the strict semantic union and report stable JSON Pointer paths."""

    field_errors: list[StableFieldError] = []
    for index, entry in enumerate(entries):
        base = f"/entries/{index}"
        unknown = set(entry) - _ENTRY_FIELDS
        for field in sorted(unknown):
            field_errors.append(
                StableFieldError(
                    f"{base}/{_escape_json_pointer(field)}",
                    "UNKNOWN_FIELD",
                    "field is not defined by data-annotation-schemas.json",
                )
            )
        semantic_type = entry.get("semantic_type")
        if semantic_type not in _ANCHOR_BY_SEMANTIC:
            field_errors.append(
                StableFieldError(
                    f"{base}/semantic_type",
                    "UNKNOWN_SEMANTIC_TYPE",
                    "semantic_type is not supported",
                )
            )
            continue
        anchor = entry.get("anchor")
        if (
            isinstance(anchor, dict)
            and anchor.get("anchor_type") != _ANCHOR_BY_SEMANTIC[semantic_type]
        ):
            field_errors.append(
                StableFieldError(
                    f"{base}/anchor/anchor_type",
                    "ANCHOR_TYPE_MISMATCH",
                    f"{semantic_type} requires {_ANCHOR_BY_SEMANTIC[semantic_type]}",
                )
            )
    if field_errors:
        raise ValidationError(
            "annotation entries failed schema validation",
            field_errors=[asdict(error) for error in field_errors],
        )
    try:
        return _ENTRY_ADAPTER.validate_python(entries)
    except PydanticValidationError as exc:
        converted = []
        for error in exc.errors(include_url=False):
            location = [part for part in error["loc"] if part not in _ANCHOR_BY_SEMANTIC]
            pointer = "/entries/" + "/".join(_escape_json_pointer(part) for part in location)
            converted.append(
                {"path": pointer, "code": str(error["type"]).upper(), "message": error["msg"]}
            )
        raise ValidationError(
            "annotation entries failed schema validation", field_errors=converted
        ) from exc


def canonical_entries_hash(entries: Sequence[AnnotationEntry]) -> str:
    payload = [entry.model_dump(mode="json") for entry in entries]
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return "sha256:" + sha256(encoded).hexdigest()


def coverage_key_hash(source: dict[str, Any], ontology: dict[str, Any]) -> str:
    key = {
        "dataset_id": source["dataset_id"],
        "dataset_version_id": source["dataset_version_id"],
        "episode_id": source["episode_id"],
        "base_revision_id": source["base_revision_id"],
        "stream_ids": sorted(source["stream_ids"]),
        "start_ns": str(source["start_ns"]),
        "end_ns": str(source["end_ns"]),
        "ontology_id": ontology["ontology_id"],
        "ontology_version": ontology["ontology_version"],
        "ontology_hash": ontology["ontology_hash"],
    }
    encoded = json.dumps(key, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + sha256(encoded).hexdigest()


def assert_workflow_transition(current: str, target: str) -> None:
    try:
        source = AnnotationWorkflowState(current)
        destination = AnnotationWorkflowState(target)
    except ValueError as exc:
        raise ValidationError("unknown annotation workflow state") from exc
    if destination not in WORKFLOW_TRANSITIONS[source]:
        raise VersionConflictError(f"illegal annotation transition {source}->{destination}")


def assert_task_editable(*, workflow_status: str, source_status: str, draft_state: str) -> None:
    if source_status == "STALE" or draft_state == "STALE_READ_ONLY":
        raise VersionConflictError("ANNOTATION_TASK_STALE")
    if workflow_status not in {"ASSIGNED", "IN_PROGRESS", "RETURNED"} or draft_state != "ACTIVE":
        raise VersionConflictError("ANNOTATION_DRAFT_NOT_EDITABLE")


CONTRACT_VERSION = "data-annotation.v1"
EMPTY_ENTRIES_HASH = "sha256:" + sha256(b"[]").hexdigest()


class ManualIssueGatePort(Protocol):
    async def evaluate_annotation_submission(
        self,
        *,
        ctx: RequestContext,
        source: dict[str, Any],
    ) -> dict[str, Any]: ...


class MissingManualIssueGatePort:
    async def evaluate_annotation_submission(self, **_: Any) -> dict[str, Any]:
        return {
            "status": "UNAVAILABLE",
            "policy_version": "unavailable",
            "issue_watermark": "unavailable",
            "blocking_issue_ids": [],
            "advisory_issue_ids": [],
            "issues": [],
            "blocked_reasons": [
                {"code": "ISSUE_GATE_UNAVAILABLE", "message": "ManualIssue gate is unavailable."}
            ],
        }


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(UTC).isoformat().replace("+00:00", "Z")


def _scope(ctx: RequestContext) -> dict[str, str]:
    return {
        "organization_id": ctx.organization_id,
        "project_id": ctx.project_id,
        "region_code": ctx.region_code,
    }


def envelope(data: Any, ctx: RequestContext) -> dict[str, Any]:
    return {
        "data": data,
        "scope": _scope(ctx),
        "request_id": ctx.request_id,
        "contract_version": CONTRACT_VERSION,
    }


def _source(row: models.AnnotationTask) -> dict[str, Any]:
    return {
        "dataset_id": row.dataset_id,
        "dataset_version_id": row.dataset_version_id,
        "episode_id": row.episode_id,
        "base_revision_id": row.base_revision_id,
        "stream_ids": row.stream_ids,
        "start_ns": str(row.start_ns),
        "end_ns": str(row.end_ns),
    }


def _ontology(row: models.AnnotationTask) -> dict[str, Any]:
    return {
        "ontology_id": row.ontology_id,
        "ontology_version": row.ontology_version,
        "ontology_hash": row.ontology_hash,
    }


def _task_projection(row: models.AnnotationTask, ctx: RequestContext) -> dict[str, Any]:
    return {
        "task_id": row.task_id,
        "scope": _scope(ctx),
        "task_source": row.task_source,
        "workflow_status": row.workflow_status,
        "source_status": row.source_status,
        "block_source": row.block_source,
        "block_reason": row.block_reason,
        "source": _source(row),
        "ontology": _ontology(row),
        "assignment": {
            "mode": row.assignment_mode,
            "assignee_id": row.assignee_id,
            "assigned_by": row.assigned_by,
            "assigned_at": _iso(row.assigned_at) if row.assigned_at else None,
        },
        "priority": row.priority,
        "current_draft_revision": row.current_draft_revision,
        "current_draft_hash": row.current_draft_hash,
        "current_submission_id": row.current_submission_id,
        "submitted_annotation_set_id": row.submitted_annotation_set_id,
        "correction_of_annotation_set_id": row.correction_of_annotation_set_id,
        "predecessor_task_id": row.predecessor_task_id,
        "successor_task_id": row.successor_task_id,
        "latest_review_id": row.latest_review_id,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "etag": row.etag,
        "allowed_actions": [],
        "action_reasons": [],
    }


async def _draft_projection(session: AsyncSession, draft: models.AnnotationDraft) -> dict[str, Any]:
    revision = await session.get(
        models.AnnotationDraftRevision,
        {"task_id": draft.task_id, "draft_revision": draft.current_revision},
    )
    return {
        "task_id": draft.task_id,
        "draft_revision": draft.current_revision,
        "state": draft.state,
        "entries": revision.entries if revision else [],
        "content_hash": draft.current_content_hash,
        "saved_by": revision.saved_by if revision else "system",
        "saved_at": _iso(revision.saved_at) if revision else _iso(),
        "etag": draft.etag,
    }


def _digest(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


class AnnotationService:
    def __init__(
        self, session: AsyncSession, issue_gate: ManualIssueGatePort | None = None
    ) -> None:
        self.session = session
        self.repo = AnnotationRepository(session)
        self.issue_gate = issue_gate or MissingManualIssueGatePort()

    def _idem_scope(self, ctx: RequestContext, operation_id: str) -> dict[str, str]:
        return {**_scope(ctx), "actor_id": ctx.actor_id, "operation_id": operation_id}

    async def _write(
        self,
        *,
        ctx: RequestContext,
        key: str,
        operation_id: str,
        request_value: Any,
        target_type: str,
        target_id: str | Callable[[], str],
        events: Sequence[str],
        action: Callable[[], Awaitable[Any]],
    ) -> Any:
        request_hash = _digest(request_value)

        async def unit() -> Any:
            value = await action()
            resolved = target_id() if callable(target_id) else target_id
            for event in events:
                await write_audit(
                    self.session,
                    event,
                    target_type,
                    resolved,
                    "SUCCEEDED",
                    ctx,
                    {"operation_id": operation_id},
                )
                await emit_event(
                    self.session,
                    event,
                    target_type,
                    resolved,
                    {"operation_id": operation_id, "request_id": ctx.request_id},
                    ctx,
                )
            stable = deepcopy(value)
            if isinstance(stable, dict):
                stable["_idempotency_request_hash"] = request_hash
            return stable

        result = await with_idempotency(
            self.session, key, self._idem_scope(ctx, operation_id), unit
        )
        if isinstance(result, dict):
            prior = result.pop("_idempotency_request_hash", None)
            if prior is not None and prior != request_hash:
                raise VersionConflictError(code="IDEMPOTENCY_KEY_REUSED")
        return result

    async def _insert_task(
        self,
        ctx: RequestContext,
        command: schemas.CreateAnnotationTaskRequest | dict[str, Any],
        *,
        task_source: str | None = None,
        predecessor_task_id: str | None = None,
    ) -> tuple[models.AnnotationTask, models.AnnotationDraft]:
        value = command.model_dump(mode="json") if hasattr(command, "model_dump") else command
        source = value["source"]
        ontology = value["ontology"]
        key_hash = coverage_key_hash(source, ontology)
        existing = await self.repo.active_by_coverage(ctx, key_hash, lock=True)
        if existing:
            return existing, await self.repo.draft(existing.task_id)
        task_id = new_id("annotation_task")
        now = _now()
        assignee = value.get("assignee_id")
        workflow = "ASSIGNED" if assignee else "QUEUED"
        mode = "DIRECT" if assignee else "UNASSIGNED"
        task = models.AnnotationTask(
            task_id=task_id,
            organization_id=ctx.organization_id,
            project_id=ctx.project_id,
            region_code=ctx.region_code,
            coverage_key_hash=key_hash,
            task_source=task_source or value["task_source"],
            workflow_status=workflow,
            source_status="CURRENT",
            dataset_id=source["dataset_id"],
            dataset_version_id=source["dataset_version_id"],
            episode_id=source["episode_id"],
            base_revision_id=source["base_revision_id"],
            stream_ids=source["stream_ids"],
            start_ns=int(source["start_ns"]),
            end_ns=int(source["end_ns"]),
            ontology_id=ontology["ontology_id"],
            ontology_version=ontology["ontology_version"],
            ontology_hash=ontology["ontology_hash"],
            assignment_mode=mode,
            assignee_id=assignee,
            assigned_by=ctx.actor_id if assignee else None,
            assigned_at=now if assignee else None,
            priority=int(value.get("priority", 0)),
            current_draft_revision=0,
            current_draft_hash=EMPTY_ENTRIES_HASH,
            correction_of_annotation_set_id=value.get("correction_of_annotation_set_id"),
            predecessor_task_id=predecessor_task_id,
            resource_version=1,
            etag=compute_etag(1),
            created_at=now,
            updated_at=now,
        )
        draft = models.AnnotationDraft(
            task_id=task_id,
            current_revision=0,
            current_content_hash=EMPTY_ENTRIES_HASH,
            state="ACTIVE",
            resource_version=1,
            etag=compute_etag(1),
        )
        revision = models.AnnotationDraftRevision(
            task_id=task_id,
            draft_revision=0,
            entries=[],
            content_hash=EMPTY_ENTRIES_HASH,
            saved_by=ctx.actor_id,
            saved_at=now,
            client_mutation_id="server-baseline",
        )
        try:
            async with self.session.begin_nested():
                self.session.add_all([task, draft, revision])
                await self.session.flush()
        except IntegrityError:
            existing = await self.repo.active_by_coverage(ctx, key_hash, lock=True)
            if existing is None:
                raise
            return existing, await self.repo.draft(existing.task_id)
        return task, draft

    async def create_task(
        self, ctx: RequestContext, key: str, command: schemas.CreateAnnotationTaskRequest
    ) -> dict[str, Any]:
        target = []

        async def action() -> dict[str, Any]:
            task, draft = await self._insert_task(ctx, command)
            target.append(task.task_id)
            return envelope(_task_projection(task, ctx), ctx)

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="createAnnotationTask",
            request_value=command,
            target_type="annotation_task",
            target_id=lambda: target[0],
            events=("annotation.task.created",),
            action=action,
        )

    async def resolve_entry(
        self,
        ctx: RequestContext,
        source: dict[str, Any],
        ontology: dict[str, Any],
        can_create: bool,
    ) -> dict[str, Any]:
        key_hash = coverage_key_hash(source, ontology)
        task = await self.repo.active_by_coverage(ctx, key_hash)
        state = "CAN_CREATE"
        next_action = "CREATE_TASK"
        task_id = None
        task_etag = None
        token = new_id("resolution_token") + "-" * 32
        if task:
            task_id = task.task_id
            task_etag = task.etag
            token = None
            if task.assignee_id == ctx.actor_id:
                state = "OPEN_EXISTING"
                next_action = "OPEN_TASK"
            elif task.workflow_status == "QUEUED" and task.assignee_id is None:
                state = "CLAIMABLE"
                next_action = "CLAIM_TASK"
            else:
                state = "ASSIGNED_TO_OTHER"
                next_action = "NONE"
        elif not can_create:
            state = "FORBIDDEN"
            next_action = "NONE"
            token = None
        resolution_id = new_id("entry_resolution")
        now = _now()
        resolved = {"source": source, "ontology": ontology, "coverage_key_hash": key_hash}
        row = models.AnnotationEntryResolution(
            resolution_id=resolution_id,
            organization_id=ctx.organization_id,
            project_id=ctx.project_id,
            region_code=ctx.region_code,
            principal_id=ctx.actor_id,
            coverage_key_hash=key_hash,
            state=state,
            resolved_context=resolved if state not in {"ASSIGNED_TO_OTHER", "FORBIDDEN"} else None,
            task_id=task_id,
            token_hash=sha256(token.encode()).hexdigest() if token else None,
            expires_at=now + timedelta(minutes=5),
            created_at=now,
        )
        self.session.add(row)
        await self.session.flush()
        return envelope(
            {
                "resolution_id": resolution_id,
                "state": state,
                "resolved_context": row.resolved_context,
                "task_id": task_id,
                "task_etag": task_etag,
                "entry_resolution_token": token,
                "next_action": next_action,
                "blocked_reason": None,
                "expires_at": _iso(row.expires_at),
            },
            ctx,
        )

    async def materialize(
        self,
        ctx: RequestContext,
        key: str,
        resolution_id: str,
        command: schemas.MaterializeAnnotationTaskRequest,
    ) -> dict[str, Any]:
        target = []

        async def action() -> dict[str, Any]:
            row = await self.session.get(
                models.AnnotationEntryResolution, resolution_id, with_for_update=True
            )
            if row is None or row.principal_id != ctx.actor_id:
                raise NotFoundError(code="ENTRY_RESOLUTION_NOT_FOUND")
            if row.expires_at < _now():
                from app.core.errors import GoneError

                raise GoneError(code="ENTRY_RESOLUTION_EXPIRED")
            if row.token_hash != sha256(command.entry_resolution_token.encode()).hexdigest():
                raise ForbiddenError(code="ENTRY_RESOLUTION_TOKEN_INVALID")
            existing = await self.repo.active_by_coverage(ctx, row.coverage_key_hash, lock=True)
            if existing:
                task = existing
                draft = await self.repo.draft(task.task_id)
                disposition = (
                    "OPEN_EXISTING" if task.assignee_id == ctx.actor_id else "CLAIMED_EXISTING"
                )
                task.assignee_id = ctx.actor_id
                task.assignment_mode = "CLAIMED"
                task.workflow_status = "ASSIGNED"
            else:
                value = {
                    "task_source": "COVERAGE_GAP",
                    **row.resolved_context,
                    "priority": 0,
                    "assignee_id": ctx.actor_id,
                    "correction_of_annotation_set_id": None,
                }
                task, draft = await self._insert_task(ctx, value)
                disposition = "CREATED"
            row.task_id = task.task_id
            row.consumed_at = _now()
            target.append(task.task_id)
            await self.session.flush()
            return envelope(
                {
                    "disposition": disposition,
                    "task": _task_projection(task, ctx),
                    "draft": await _draft_projection(self.session, draft),
                },
                ctx,
            )

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="materializeAnnotationTaskFromEntryResolution",
            request_value=command,
            target_type="annotation_task",
            target_id=lambda: target[0],
            events=("annotation.task.created",),
            action=action,
        )

    async def claim(
        self,
        ctx: RequestContext,
        key: str,
        task_id: str,
        request: Any,
        command: schemas.ClaimAnnotationTaskRequest,
    ) -> dict[str, Any]:
        async def action() -> dict[str, Any]:
            row = await self.repo.task(ctx, task_id, lock=True)
            check_if_match(request, row.etag)
            if row.workflow_status != "QUEUED" or row.assignee_id:
                raise VersionConflictError(code="ANNOTATION_TASK_NOT_CLAIMABLE")
            row.assignee_id = ctx.actor_id
            row.assigned_by = ctx.actor_id
            row.assigned_at = _now()
            row.assignment_mode = "CLAIMED"
            row.workflow_status = "ASSIGNED"
            row.resource_version += 1
            row.etag = compute_etag(row.resource_version)
            row.updated_at = _now()
            await self.session.flush()
            return envelope(_task_projection(row, ctx), ctx)

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="claimAnnotationTask",
            request_value=command,
            target_type="annotation_task",
            target_id=task_id,
            events=("annotation.task.claimed",),
            action=action,
        )

    async def assign(
        self,
        ctx: RequestContext,
        key: str,
        task_id: str,
        request: Any,
        command: schemas.AssignAnnotationTaskRequest,
    ) -> dict[str, Any]:
        async def action() -> dict[str, Any]:
            row = await self.repo.task(ctx, task_id, lock=True)
            check_if_match(request, row.etag)
            if row.workflow_status in {"APPROVED", "CANCELLED"}:
                raise VersionConflictError(code="ANNOTATION_TASK_TERMINAL")
            row.assignee_id = command.assignee_id
            row.assigned_by = ctx.actor_id
            row.assigned_at = _now()
            row.assignment_mode = "DIRECT"
            row.workflow_status = (
                "ASSIGNED" if row.workflow_status == "QUEUED" else row.workflow_status
            )
            row.resource_version += 1
            row.etag = compute_etag(row.resource_version)
            row.updated_at = _now()
            await self.session.flush()
            return envelope(_task_projection(row, ctx), ctx)

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="assignAnnotationTask",
            request_value=command,
            target_type="annotation_task",
            target_id=task_id,
            events=("annotation.task.assigned",),
            action=action,
        )

    async def save_draft(
        self,
        ctx: RequestContext,
        key: str,
        task_id: str,
        request: Any,
        command: schemas.SaveAnnotationDraftRequest,
    ) -> dict[str, Any]:
        async def action() -> dict[str, Any]:
            task = await self.repo.task(ctx, task_id, lock=True)
            draft = await self.repo.draft(task_id, lock=True)
            check_if_match(request, draft.etag)
            assert_task_editable(
                workflow_status=task.workflow_status,
                source_status=task.source_status,
                draft_state=draft.state,
            )
            if draft.current_revision != command.expected_draft_revision:
                raise VersionConflictError(code="DRAFT_REVISION_CONFLICT")
            raw = [entry.model_dump(mode="json") for entry in command.entries]
            entries = validate_annotation_entries(raw)
            content_hash = canonical_entries_hash(entries)
            revision = draft.current_revision + 1
            now = _now()
            self.session.add(
                models.AnnotationDraftRevision(
                    task_id=task_id,
                    draft_revision=revision,
                    entries=raw,
                    content_hash=content_hash,
                    saved_by=ctx.actor_id,
                    saved_at=now,
                    client_mutation_id=command.client_mutation_id,
                )
            )
            draft.current_revision = revision
            draft.current_content_hash = content_hash
            draft.resource_version += 1
            draft.etag = compute_etag(draft.resource_version)
            task.current_draft_revision = revision
            task.current_draft_hash = content_hash
            events = ["annotation.draft.saved"]
            if task.workflow_status in {"ASSIGNED", "RETURNED"}:
                task.workflow_status = "IN_PROGRESS"
                events.append("annotation.task.started")
            task.resource_version += 1
            task.etag = compute_etag(task.resource_version)
            task.updated_at = now
            await self.session.flush()
            return envelope(
                {
                    "task": _task_projection(task, ctx),
                    "draft": await _draft_projection(self.session, draft),
                    "_events": events,
                },
                ctx,
            )

        result = await self._write(
            ctx=ctx,
            key=key,
            operation_id="saveAnnotationDraft",
            request_value=command,
            target_type="annotation_task",
            target_id=task_id,
            events=("annotation.draft.saved",),
            action=action,
        )
        return result

    async def preflight(
        self,
        ctx: RequestContext,
        key: str,
        task_id: str,
        request: Any,
        command: schemas.SubmitPreflightRequest,
    ) -> dict[str, Any]:
        preflight_id = new_id("annotation_preflight")

        async def action() -> dict[str, Any]:
            task = await self.repo.task(ctx, task_id, lock=True)
            draft = await self.repo.draft(task_id)
            check_if_match(request, task.etag)
            if task.source_status == "STALE":
                raise VersionConflictError(code="ANNOTATION_TASK_STALE")
            if (
                draft.current_revision != command.expected_draft_revision
                or draft.current_content_hash != command.expected_content_hash
            ):
                raise VersionConflictError(code="DRAFT_REVISION_CONFLICT")
            gate = await self.issue_gate.evaluate_annotation_submission(
                ctx=ctx, source=_source(task)
            )
            valid = gate.get("status") in {"PASS", "ADVISORY"}
            now = _now()
            row = models.AnnotationSubmitPreflight(
                preflight_id=preflight_id,
                task_id=task_id,
                draft_revision=draft.current_revision,
                content_hash=draft.current_content_hash,
                issue_watermark=str(gate.get("issue_watermark", "unknown")),
                valid=valid,
                validation_errors=[],
                expires_at=now + timedelta(minutes=5),
                created_at=now,
            )
            self.session.add(row)
            await self.session.flush()
            return envelope(
                {
                    "preflight_id": preflight_id,
                    "task_id": task_id,
                    "draft_revision": draft.current_revision,
                    "content_hash": draft.current_content_hash,
                    "valid": valid,
                    "submission_gate": gate,
                    "validation_errors": [],
                    "expires_at": _iso(row.expires_at),
                },
                ctx,
            )

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="preflightAnnotationSubmit",
            request_value=command,
            target_type="annotation_task",
            target_id=task_id,
            events=("annotation.submit.preflighted",),
            action=action,
        )

    async def submit(
        self,
        ctx: RequestContext,
        key: str,
        task_id: str,
        request: Any,
        command: schemas.SubmitAnnotationTaskRequest,
    ) -> dict[str, Any]:
        submission_id = new_id("annotation_submission")
        set_id = new_id("annotation_set")

        async def action() -> dict[str, Any]:
            task = await self.repo.task(ctx, task_id, lock=True)
            draft = await self.repo.draft(task_id, lock=True)
            check_if_match(request, task.etag)
            assert_task_editable(
                workflow_status=task.workflow_status,
                source_status=task.source_status,
                draft_state=draft.state,
            )
            if (
                draft.current_revision != command.expected_draft_revision
                or draft.current_content_hash != command.expected_content_hash
            ):
                raise VersionConflictError(code="DRAFT_REVISION_CONFLICT")
            preflight = await self.session.get(
                models.AnnotationSubmitPreflight, command.preflight_id
            )
            if (
                preflight is None
                or preflight.task_id != task_id
                or preflight.expires_at < _now()
                or preflight.draft_revision != draft.current_revision
                or preflight.content_hash != draft.current_content_hash
            ):
                raise VersionConflictError(code="SUBMIT_PREFLIGHT_STALE")
            gate = await self.issue_gate.evaluate_annotation_submission(
                ctx=ctx, source=_source(task)
            )
            if gate.get("status") == "UNAVAILABLE":
                raise ServerError(code="ISSUE_GATE_UNAVAILABLE", retryable=True)
            if gate.get("status") == "BLOCKED":
                raise VersionConflictError(
                    code="ANNOTATION_SUBMIT_BLOCKED_BY_ISSUE",
                    blocked_reasons=gate.get("blocked_reasons", []),
                )
            revision = await self.session.get(
                models.AnnotationDraftRevision,
                {"task_id": task_id, "draft_revision": draft.current_revision},
            )
            now = _now()
            submission = models.AnnotationSubmission(
                submission_id=submission_id,
                task_id=task_id,
                annotation_set_id=set_id,
                draft_revision=draft.current_revision,
                draft_hash=draft.current_content_hash,
                submission_state="SUBMITTED",
                submitted_by=ctx.actor_id,
                submitted_at=now,
                resource_version=1,
                etag=compute_etag(1),
            )
            annotation_set = models.AnnotationSet(
                annotation_set_id=set_id,
                task_id=task_id,
                submission_id=submission_id,
                organization_id=ctx.organization_id,
                project_id=ctx.project_id,
                region_code=ctx.region_code,
                base_revision_id=task.base_revision_id,
                ontology_id=task.ontology_id,
                ontology_version=task.ontology_version,
                ontology_hash=task.ontology_hash,
                entries=revision.entries if revision else [],
                content_hash=draft.current_content_hash,
                provenance={
                    "submitted_draft_revision": draft.current_revision,
                    "submitted_draft_hash": draft.current_content_hash,
                    "issue_gate_policy_version": gate.get("policy_version"),
                    "issue_watermark": gate.get("issue_watermark"),
                    "advisory_issue_ids": gate.get("advisory_issue_ids", []),
                },
                submitted_by=ctx.actor_id,
                submitted_at=now,
                effective_state="ACTIVE",
                supersedes_annotation_set_id=task.correction_of_annotation_set_id,
            )
            self.session.add_all([submission, annotation_set])
            draft.state = "FROZEN_SUBMITTED"
            task.workflow_status = "SUBMITTED"
            task.current_submission_id = submission_id
            task.submitted_annotation_set_id = set_id
            task.resource_version += 1
            task.etag = compute_etag(task.resource_version)
            task.updated_at = now
            await self.session.flush()
            return envelope(
                {
                    "task": _task_projection(task, ctx),
                    "submission": {
                        "submission_id": submission_id,
                        "task_id": task_id,
                        "annotation_set_id": set_id,
                        "draft_revision": draft.current_revision,
                        "draft_hash": draft.current_content_hash,
                        "submission_state": "SUBMITTED",
                        "submitted_by": ctx.actor_id,
                        "submitted_at": _iso(now),
                        "etag": submission.etag,
                    },
                    "annotation_set": {
                        "annotation_set_id": set_id,
                        "task_id": task_id,
                        "submission_id": submission_id,
                        "entries": annotation_set.entries,
                        "content_hash": annotation_set.content_hash,
                        "effective_state": "ACTIVE",
                        "submitted_by": ctx.actor_id,
                        "submitted_at": _iso(now),
                    },
                },
                ctx,
            )

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="submitAnnotationTask",
            request_value=command,
            target_type="annotation_set",
            target_id=set_id,
            events=("annotation.set.submitted",),
            action=action,
        )

    async def rebase(
        self,
        ctx: RequestContext,
        key: str,
        task_id: str,
        request: Any,
        command: schemas.RebaseAnnotationTaskRequest,
    ) -> dict[str, Any]:
        successor = []

        async def action() -> dict[str, Any]:
            stale = await self.repo.task(ctx, task_id, lock=True)
            check_if_match(request, stale.etag)
            if stale.source_status != "STALE":
                raise VersionConflictError(code="ANNOTATION_TASK_NOT_STALE")
            source = _source(stale)
            source["base_revision_id"] = command.target_revision_id
            value = {
                "task_source": "REVISION_REBASE",
                "source": source,
                "ontology": _ontology(stale),
                "priority": stale.priority,
                "assignee_id": command.successor_assignee_id,
                "correction_of_annotation_set_id": None,
            }
            task, draft = await self._insert_task(
                ctx, value, task_source="REVISION_REBASE", predecessor_task_id=task_id
            )
            stale.successor_task_id = task.task_id
            successor.append(task.task_id)
            await self.session.flush()
            return envelope(
                {
                    "stale_task_id": task_id,
                    "successor_task": _task_projection(task, ctx),
                    "successor_draft": await _draft_projection(self.session, draft),
                    "migration_mode": "EMPTY",
                    "migrated_annotation_count": 0,
                },
                ctx,
            )

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="rebaseAnnotationTask",
            request_value=command,
            target_type="annotation_task",
            target_id=lambda: successor[0],
            events=("annotation.task.rebased",),
            action=action,
        )

    async def mark_revision_stale(self, ctx: RequestContext, revision_id: str) -> int:
        rows = list(
            (
                await self.session.scalars(
                    select(models.AnnotationTask)
                    .where(
                        models.AnnotationTask.organization_id == ctx.organization_id,
                        models.AnnotationTask.project_id == ctx.project_id,
                        models.AnnotationTask.region_code == ctx.region_code,
                        models.AnnotationTask.base_revision_id == revision_id,
                        models.AnnotationTask.workflow_status.not_in(("APPROVED", "CANCELLED")),
                        models.AnnotationTask.source_status == "CURRENT",
                    )
                    .with_for_update()
                )
            ).all()
        )
        for task in rows:
            task.source_status = "STALE"
            task.resource_version += 1
            task.etag = compute_etag(task.resource_version)
            task.updated_at = _now()
            draft = await self.repo.draft(task.task_id, lock=True)
            draft.state = "STALE_READ_ONLY"
            draft.resource_version += 1
            draft.etag = compute_etag(draft.resource_version)
        await self.session.flush()
        return len(rows)

    async def review_submission(
        self,
        ctx: RequestContext,
        key: str,
        task_id: str,
        submission_id: str,
        request: Any,
        command: schemas.ReviewAnnotationSubmissionRequest,
    ) -> dict[str, Any]:
        review_id = new_id("annotation_review")

        async def action() -> dict[str, Any]:
            task = await self.repo.task(ctx, task_id, lock=True)
            submission = await self.repo.submission(task_id, submission_id, lock=True)
            check_if_match(request, submission.etag)
            if task.workflow_status != "SUBMITTED" or submission.submission_state != "SUBMITTED":
                raise VersionConflictError(code="ANNOTATION_SUBMISSION_REVIEW_CONFLICT")
            now = _now()
            review = models.AnnotationReview(
                review_id=review_id,
                task_id=task_id,
                submission_id=submission_id,
                decision=command.decision,
                reason_codes=command.reason_codes,
                comment=command.comment,
                reviewed_by=ctx.actor_id,
                reviewed_at=now,
            )
            self.session.add(review)
            submission.submission_state = command.decision
            submission.latest_review_id = review_id
            submission.resource_version += 1
            submission.etag = compute_etag(submission.resource_version)
            task.workflow_status = command.decision
            task.latest_review_id = review_id
            task.resource_version += 1
            task.etag = compute_etag(task.resource_version)
            task.updated_at = now
            active_draft = None
            if command.decision == "RETURNED":
                old = await self.repo.draft(task_id, lock=True)
                revision = old.current_revision + 1
                old.state = "ACTIVE"
                old.current_revision = revision
                old.current_content_hash = EMPTY_ENTRIES_HASH
                old.resource_version += 1
                old.etag = compute_etag(old.resource_version)
                self.session.add(
                    models.AnnotationDraftRevision(
                        task_id=task_id,
                        draft_revision=revision,
                        entries=[],
                        content_hash=EMPTY_ENTRIES_HASH,
                        saved_by=ctx.actor_id,
                        saved_at=now,
                        client_mutation_id="review-return-baseline",
                    )
                )
                task.current_draft_revision = revision
                task.current_draft_hash = EMPTY_ENTRIES_HASH
                active_draft = await _draft_projection(self.session, old)
            await self.session.flush()
            return envelope(
                {
                    "task": _task_projection(task, ctx),
                    "submission": {
                        "submission_id": submission_id,
                        "submission_state": submission.submission_state,
                        "etag": submission.etag,
                    },
                    "review": {
                        "review_id": review_id,
                        "task_id": task_id,
                        "submission_id": submission_id,
                        "decision": command.decision,
                        "reason_codes": command.reason_codes,
                        "comment": command.comment,
                        "reviewed_by": ctx.actor_id,
                        "reviewed_at": _iso(now),
                    },
                    "active_draft": active_draft,
                },
                ctx,
            )

        # The canonical registry has no review-specific event yet. task.started is a
        # registered fail-closed placeholder until the dependency request is resolved.
        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="reviewAnnotationSubmission",
            request_value=command,
            target_type="annotation_submission",
            target_id=submission_id,
            events=("annotation.task.started",),
            action=action,
        )

    async def task_detail(self, ctx: RequestContext, task_id: str) -> dict[str, Any]:
        task = await self.repo.task(ctx, task_id)
        draft = await self.repo.draft(task_id)
        return envelope(
            {
                "task": _task_projection(task, ctx),
                "draft": await _draft_projection(self.session, draft),
                "latest_submission": None,
                "annotation_reviews": [],
                "manual_issues": [],
                "submission_gate": {
                    "status": "UNAVAILABLE",
                    "policy_version": "unavailable",
                    "issue_watermark": "unavailable",
                    "evaluated_at": _iso(),
                    "blocking_issue_ids": [],
                    "advisory_issue_ids": [],
                    "issues": [],
                    "blocked_reasons": [],
                },
                "reference_annotation_sets": [],
            },
            ctx,
        )
