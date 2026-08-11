"""Manual-cleaning state machines, transaction services, and time mapping.

The state tables stay near the top by design: unresolved product vocabulary is
centralized and cannot drift between handlers.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Any, Protocol

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.context import RequestContext
from app.core.errors import (
    NotFoundError,
    ServerError,
    ValidationError,
    VersionConflictError,
)
from app.core.etag import check_if_match, compute_etag
from app.core.idempotency import with_idempotency
from app.core.ids import new_id
from app.core.outbox import emit_event
from app.platform.ports import DatasetVersionPort

from . import models, schemas
from .repository import CleaningRepository


class ManualIssueState(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"


MANUAL_ISSUE_TRANSITIONS: dict[ManualIssueState, frozenset[ManualIssueState]] = {
    ManualIssueState.OPEN: frozenset({ManualIssueState.OPEN, ManualIssueState.IN_PROGRESS}),
    ManualIssueState.IN_PROGRESS: frozenset(
        {ManualIssueState.OPEN, ManualIssueState.IN_PROGRESS, ManualIssueState.RESOLVED}
    ),
    ManualIssueState.RESOLVED: frozenset(),
}


class CleaningDraftState(StrEnum):
    EDITING = "EDITING"
    COMMITTED = "COMMITTED"


CLEANING_DRAFT_TRANSITIONS: dict[CleaningDraftState, frozenset[CleaningDraftState]] = {
    CleaningDraftState.EDITING: frozenset({CleaningDraftState.COMMITTED}),
    CleaningDraftState.COMMITTED: frozenset(),
}


class AuthoritativeIssuePort(Protocol):
    """Temporary local port pending addition to ``app.platform.ports``."""

    async def get_issue_source(self, *, issue_id: str, scope_key: str, for_update: bool) -> Any: ...


@dataclass(frozen=True, slots=True)
class MappingSegment:
    source_start_ns: int
    source_end_ns: int
    output_revision_index: int
    output_start_ns: int
    output_end_ns: int

    def __post_init__(self) -> None:
        if self.source_start_ns >= self.source_end_ns:
            raise ValueError("source range must be non-empty")
        if self.output_start_ns >= self.output_end_ns:
            raise ValueError("output range must be non-empty")


def _subtract(intervals: Sequence[tuple[int, int]], cut: tuple[int, int]) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    cut_start, cut_end = cut
    for start, end in intervals:
        if cut_end <= start or cut_start >= end:
            result.append((start, end))
            continue
        if start < cut_start:
            result.append((start, cut_start))
        if cut_end < end:
            result.append((cut_end, end))
    return result


def build_time_mapping(
    source_start_ns: int,
    source_end_ns: int,
    operations: Iterable[dict[str, Any]],
) -> tuple[MappingSegment, ...]:
    """Build the pure source↔output map for a V1 EDL.

    TRIM selects the source domain, EXCLUDE_RANGE removes time, and SPLIT starts a
    new output Revision whose coordinate system begins at zero. Other operations
    do not change the global time coordinate. All intervals are ``[start, end)``.
    """

    if source_start_ns < 0 or source_start_ns >= source_end_ns:
        raise ValueError("source range must be a non-empty non-negative interval")
    enabled = [op for op in operations if op.get("enabled", True)]
    trims = [op for op in enabled if op.get("type") == "TRIM"]
    if len(trims) > 1:
        raise ValueError("at most one TRIM operation is allowed")
    domain_start, domain_end = source_start_ns, source_end_ns
    if trims:
        domain_start = int(trims[0]["start_ns"])
        domain_end = int(trims[0]["end_ns"])
        if (
            domain_start < source_start_ns
            or domain_end > source_end_ns
            or domain_start >= domain_end
        ):
            raise ValueError("TRIM lies outside the source domain")

    intervals: list[tuple[int, int]] = [(domain_start, domain_end)]
    for op in enabled:
        if op.get("type") != "EXCLUDE_RANGE":
            continue
        cut = (int(op["start_ns"]), int(op["end_ns"]))
        if cut[0] < domain_start or cut[1] > domain_end or cut[0] >= cut[1]:
            raise ValueError("EXCLUDE_RANGE lies outside the trimmed domain")
        intervals = _subtract(intervals, cut)

    split_points = sorted({int(op["at_ns"]) for op in enabled if op.get("type") == "SPLIT"})
    for point in split_points:
        if not domain_start < point < domain_end:
            raise ValueError("SPLIT must be strictly inside the trimmed domain")
        if not any(start <= point <= end for start, end in intervals):
            raise ValueError("SPLIT cannot lie inside an excluded range")

    pieces: list[tuple[int, int, int]] = []
    for start, end in intervals:
        boundaries = [start, *(p for p in split_points if start < p < end), end]
        for left, right in zip(boundaries, boundaries[1:], strict=True):
            revision_index = sum(1 for point in split_points if point <= left)
            pieces.append((left, right, revision_index))

    if not pieces:
        raise ValueError("EDL excludes the complete source domain")

    output_offsets: dict[int, int] = {}
    result: list[MappingSegment] = []
    for start, end, revision_index in pieces:
        output_start = output_offsets.get(revision_index, 0)
        output_end = output_start + end - start
        result.append(MappingSegment(start, end, revision_index, output_start, output_end))
        output_offsets[revision_index] = output_end
    return tuple(result)


def source_to_output_ns(
    segments: Sequence[MappingSegment], source_ns: int
) -> tuple[int, int] | None:
    """Return ``(output_revision_index, output_ns)`` or ``None`` for removed time."""

    for segment in segments:
        if segment.source_start_ns <= source_ns < segment.source_end_ns:
            return (
                segment.output_revision_index,
                segment.output_start_ns + source_ns - segment.source_start_ns,
            )
    return None


def output_to_source_ns(
    segments: Sequence[MappingSegment], output_revision_index: int, output_ns: int
) -> int | None:
    for segment in segments:
        if (
            segment.output_revision_index == output_revision_index
            and segment.output_start_ns <= output_ns < segment.output_end_ns
        ):
            return segment.source_start_ns + output_ns - segment.output_start_ns
    return None


def canonical_operation_hash(operations: Sequence[dict[str, Any]]) -> str:
    encoded = json.dumps(
        operations, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return "sha256:" + sha256(encoded).hexdigest()


def assert_issue_transition(current: str, target: str, *, assignee_id: str | None) -> None:
    try:
        source = ManualIssueState(current)
        destination = ManualIssueState(target)
    except ValueError as exc:
        raise ValidationError("unknown ManualIssue state") from exc
    if destination not in MANUAL_ISSUE_TRANSITIONS[source]:
        raise VersionConflictError(f"illegal ManualIssue transition {source}->{destination}")
    if destination is ManualIssueState.IN_PROGRESS and not assignee_id:
        raise ValidationError("IN_PROGRESS requires assignee_id")


CONTRACT_VERSION = "manual-cleaning.v1"


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


def _digest(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _issue_etag(row: models.ManualIssue) -> str:
    return compute_etag(row.resource_version)


def _draft_etag(row: models.CleaningDraft) -> str:
    return compute_etag(row.resource_version)


def _issue_projection(row: models.ManualIssue, ctx: RequestContext) -> dict[str, Any]:
    return {
        "id": row.manual_issue_id,
        "etag": _issue_etag(row),
        "scope": _scope(ctx),
        "dataset_id": row.dataset_id,
        "origin_dataset_version_id": row.origin_dataset_version_id,
        "episode_id": row.episode_id,
        "episode_revision_id": row.episode_revision_id,
        "episode_stream_id": row.episode_stream_id,
        "context_status": "CURRENT",
        "schema_snapshot_id": row.schema_snapshot_id,
        "robot_model_version_id": row.robot_model_version_id,
        "calibration_set_id": row.calibration_set_id,
        "start_ns": str(row.start_ns),
        "end_ns": str(row.end_ns),
        "issue_type": row.issue_type,
        "severity": row.severity,
        "status": row.status,
        "note": row.note,
        "assignee": {"id": row.assignee_id, "display_name": row.assignee_id}
        if row.assignee_id
        else None,
        "related_drafts": [],
        "resolution_version": (
            {
                "version_id": row.resolution_version_id,
                "producer_draft_id": row.producer_draft_id,
                "root_issue_draft_id": row.root_issue_draft_id,
                "lineage_depth": row.lineage_depth,
                "resolved_at": _iso(row.resolved_at),
            }
            if row.resolution_version_id
            else None
        ),
        "resolution_note": row.resolution_note,
        "resolved_at": _iso(row.resolved_at) if row.resolved_at else None,
        "resolved_by": {"id": row.resolved_by, "display_name": row.resolved_by}
        if row.resolved_by
        else None,
        "allowed_actions": []
        if row.status == "RESOLVED"
        else ["VIEW_EPISODE", "TRIAGE", "PREVIEW_RANGE"],
        "blocked_reasons": [],
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _draft_projection(row: models.CleaningDraft) -> dict[str, Any]:
    return {
        "draft_id": row.draft_id,
        "etag": _draft_etag(row),
        "status": row.status,
        "origin_type": row.origin_type,
        "dataset_id": row.dataset_id,
        "base_version_id": row.base_version_id,
        "base_revision_id": row.base_revision_id,
        "episode_id": row.episode_id,
        "current_edl_revision": str(row.current_edl_revision),
        "operation_hash": row.current_operation_hash,
        "updated_at": _iso(row.updated_at),
        "allowed_actions": ["EDIT", "PREVIEW", "COMMIT"] if row.status == "EDITING" else [],
    }


class MissingDatasetVersionPort:
    async def get_version(self, dataset_id: str, version_id: str, ctx: RequestContext) -> None:
        del dataset_id, version_id, ctx
        raise ServerError(code="DATASET_VERSION_PORT_UNAVAILABLE", retryable=True)


class CleaningService:
    def __init__(
        self, session: AsyncSession, dataset_versions: DatasetVersionPort | None = None
    ) -> None:
        self.session = session
        self.repo = CleaningRepository(session)
        self.dataset_versions = dataset_versions or MissingDatasetVersionPort()

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

    async def create_issue(
        self, ctx: RequestContext, key: str, command: schemas.CreateManualIssueRequest
    ) -> dict[str, Any]:
        issue_id = new_id("manual_issue")

        async def action() -> dict[str, Any]:
            version = await self.dataset_versions.get_version(
                "", command.origin_dataset_version_id, ctx
            )
            if not version:
                raise NotFoundError(code="VERSION_NOT_FOUND")
            dataset_id = str(version.get("dataset_id") or "")
            if not dataset_id:
                raise ValidationError(code="VERSION_DATASET_MISSING")
            now = _now()
            row = models.ManualIssue(
                manual_issue_id=issue_id,
                organization_id=ctx.organization_id,
                project_id=ctx.project_id,
                region_code=ctx.region_code,
                dataset_id=dataset_id,
                origin_dataset_version_id=command.origin_dataset_version_id,
                episode_id=command.episode_id,
                episode_revision_id=command.episode_revision_id,
                episode_stream_id=command.episode_stream_id,
                schema_snapshot_id=str(version.get("schema_snapshot_id") or "schema-unknown"),
                robot_model_version_id=version.get("robot_model_version_id"),
                calibration_set_id=version.get("calibration_set_id"),
                start_ns=int(command.start_ns),
                end_ns=int(command.end_ns),
                issue_type=command.issue_type,
                severity=command.severity,
                assignee_id=None,
                status="OPEN",
                note=command.note,
                resource_version=1,
                created_at=now,
                created_by=ctx.actor_id,
                updated_at=now,
                updated_by=ctx.actor_id,
            )
            self.session.add(row)
            self.session.add(
                models.ManualIssueHistory(
                    history_id=new_id("issue_history"),
                    manual_issue_id=issue_id,
                    event_type="CREATED",
                    prior_resource_version=None,
                    next_resource_version=1,
                    before_facts=None,
                    after_facts={"status": "OPEN"},
                    reason=None,
                    actor_id=ctx.actor_id,
                    request_id=ctx.request_id,
                    created_at=now,
                )
            )
            await self.session.flush()
            return envelope(_issue_projection(row, ctx), ctx)

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="createManualIssue",
            request_value=command,
            target_type="manual_issue",
            target_id=issue_id,
            events=("manual_issue.created",),
            action=action,
        )

    async def triage_issue(
        self,
        ctx: RequestContext,
        key: str,
        issue_id: str,
        request: Request,
        command: schemas.TriageManualIssueRequest,
    ) -> dict[str, Any]:
        async def action() -> dict[str, Any]:
            row = await self.repo.issue(ctx, issue_id, lock=True)
            check_if_match(request, _issue_etag(row))
            assert_issue_transition(
                row.status, command.target_status, assignee_id=command.assignee_id
            )
            prior = {"status": row.status, "severity": row.severity, "assignee_id": row.assignee_id}
            prior_version = row.resource_version
            row.status = command.target_status
            row.severity = command.severity
            row.assignee_id = command.assignee_id
            row.resource_version += 1
            row.updated_at = _now()
            row.updated_by = ctx.actor_id
            event_type = (
                "WORK_STARTED"
                if prior["status"] == "OPEN" and row.status == "IN_PROGRESS"
                else "REQUEUED"
                if prior["status"] == "IN_PROGRESS" and row.status == "OPEN"
                else "TRIAGE_UPDATED"
            )
            self.session.add(
                models.ManualIssueHistory(
                    history_id=new_id("issue_history"),
                    manual_issue_id=issue_id,
                    event_type=event_type,
                    prior_resource_version=prior_version,
                    next_resource_version=row.resource_version,
                    before_facts=prior,
                    after_facts={
                        "status": row.status,
                        "severity": row.severity,
                        "assignee_id": row.assignee_id,
                    },
                    reason=command.reason,
                    actor_id=ctx.actor_id,
                    request_id=ctx.request_id,
                    created_at=row.updated_at,
                )
            )
            await self.session.flush()
            return envelope(_issue_projection(row, ctx), ctx)

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="triageManualIssue",
            request_value=command,
            target_type="manual_issue",
            target_id=issue_id,
            events=("manual_issue.triaged",),
            action=action,
        )

    async def create_draft_from_issue(
        self,
        ctx: RequestContext,
        key: str,
        issue_id: str,
        request: Request,
        command: schemas.CreateDraftFromIssueRequest,
    ) -> dict[str, Any]:
        created_id: list[str] = []

        async def action() -> dict[str, Any]:
            issue = await self.repo.issue(ctx, issue_id, lock=True)
            check_if_match(request, _issue_etag(issue))
            if issue.status == "RESOLVED":
                raise VersionConflictError(code="MANUAL_ISSUE_RESOLVED")
            linked = list(
                (
                    await self.session.scalars(
                        select(models.ManualIssueDraftLink).where(
                            models.ManualIssueDraftLink.manual_issue_id == issue_id
                        )
                    )
                ).all()
            )
            if isinstance(command, schemas.DraftFromIssueSelectionRequest):
                if command.candidate_draft_id not in {item.draft_id for item in linked}:
                    raise VersionConflictError(code="SELECTION_TOKEN_STALE")
                row = await self.repo.draft(ctx, command.candidate_draft_id)
                return envelope(
                    {
                        "disposition": "ALREADY_LINKED",
                        "draft_id": row.draft_id,
                        "draft": _draft_projection(row),
                    },
                    ctx,
                )
            editable = []
            for item in linked:
                row = await self.repo.draft(ctx, item.draft_id)
                if row.status == "EDITING":
                    editable.append(row)
            if len(editable) == 1:
                return envelope(
                    {
                        "disposition": "ALREADY_LINKED",
                        "draft_id": editable[0].draft_id,
                        "draft": _draft_projection(editable[0]),
                    },
                    ctx,
                )
            if len(editable) > 1:
                return envelope(
                    {
                        "disposition": "SELECTION_REQUIRED",
                        "draft_id": None,
                        "selection_token": new_id("selection"),
                        "candidates": [_draft_projection(row) for row in editable],
                    },
                    ctx,
                )
            draft_id = new_id("cleaning_draft")
            created_id.append(draft_id)
            now = _now()
            empty_hash = canonical_operation_hash([])
            draft = models.CleaningDraft(
                draft_id=draft_id,
                organization_id=ctx.organization_id,
                project_id=ctx.project_id,
                region_code=ctx.region_code,
                origin_type="ISSUE_DERIVED",
                dataset_id=issue.dataset_id,
                base_version_id=issue.origin_dataset_version_id,
                episode_id=issue.episode_id,
                base_revision_id=issue.episode_revision_id,
                status="EDITING",
                current_edl_revision=0,
                current_operation_hash=empty_hash,
                resource_version=1,
                created_at=now,
                created_by=ctx.actor_id,
                updated_at=now,
                updated_by=ctx.actor_id,
            )
            self.session.add(draft)
            self.session.add(
                models.ManualIssueDraftLink(
                    draft_id=draft_id, manual_issue_id=issue_id, created_at=now
                )
            )
            self.session.add(
                models.IssueDerivedDraftContext(
                    draft_id=draft_id,
                    schema_version=1,
                    dataset_id=issue.dataset_id,
                    base_version_id=issue.origin_dataset_version_id,
                    episode_id=issue.episode_id,
                    base_revision_id=issue.episode_revision_id,
                    selected_stream_id=issue.episode_stream_id,
                    selected_channel_path=issue.episode_stream_id,
                    start_ns=issue.start_ns,
                    end_ns=issue.end_ns,
                )
            )
            self.session.add(
                models.CleaningEdlRevision(
                    draft_id=draft_id,
                    edl_revision=0,
                    operation_hash=empty_hash,
                    canonical_document={"operations": []},
                    validation_result={"status": "PASSED", "issues": []},
                    calculated_summary={"output_segment_count": "1"},
                    author_id=ctx.actor_id,
                    created_at=now,
                )
            )
            self.session.add(
                models.CleaningDraftLineageClosure(
                    ancestor_draft_id=draft_id,
                    descendant_draft_id=draft_id,
                    depth=0,
                    created_at=now,
                )
            )
            await self.session.flush()
            return envelope(
                {"disposition": "CREATED", "draft_id": draft_id, "draft": _draft_projection(draft)},
                ctx,
            )

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="createCleaningDraftFromManualIssue",
            request_value={"issue_id": issue_id, "command": command},
            target_type="cleaning_draft",
            target_id=lambda: created_id[0] if created_id else issue_id,
            events=("cleaning.draft.created",),
            action=action,
        )

    async def save_edl(
        self,
        ctx: RequestContext,
        draft_id: str,
        request: Request,
        command: schemas.SaveCleaningEdlRequest,
    ) -> dict[str, Any]:
        key = command.client_mutation_id

        async def action() -> dict[str, Any]:
            row = await self.repo.draft(ctx, draft_id, lock=True)
            check_if_match(request, _draft_etag(row))
            if row.status != "EDITING":
                raise VersionConflictError(code="DRAFT_NOT_EDITABLE")
            if row.current_edl_revision != int(
                command.expected_edl_revision
            ) or row.current_operation_hash != (
                command.expected_operation_hash or row.current_operation_hash
            ):
                raise VersionConflictError(code="EDL_REVISION_CONFLICT")
            operations = [op.model_dump(mode="json") for op in command.operations]
            operation_hash = canonical_operation_hash(operations)
            revision = row.current_edl_revision + 1
            now = _now()
            self.session.add(
                models.CleaningEdlRevision(
                    draft_id=draft_id,
                    edl_revision=revision,
                    operation_hash=operation_hash,
                    canonical_document={"operations": operations},
                    validation_result={"status": "PASSED", "issues": []},
                    calculated_summary={
                        "output_segment_count": str(
                            1
                            + sum(
                                1
                                for op in operations
                                if op["type"] == "SPLIT" and op.get("enabled", True)
                            )
                        )
                    },
                    author_id=ctx.actor_id,
                    created_at=now,
                )
            )
            for op in operations:
                self.session.add(
                    models.CleaningOperation(
                        draft_id=draft_id,
                        edl_revision=revision,
                        operation_id=op["id"],
                        sequence_no=op["sequence_no"],
                        operation_type=op["type"],
                        operation=op,
                    )
                )
            row.current_edl_revision = revision
            row.current_operation_hash = operation_hash
            row.resource_version += 1
            row.updated_at = now
            row.updated_by = ctx.actor_id
            await self.session.flush()
            return envelope(
                {
                    "draft": _draft_projection(row),
                    "edl": {
                        "edl_revision": str(revision),
                        "etag": _draft_etag(row),
                        "operation_hash": operation_hash,
                        "operations": operations,
                        "validation": {"status": "PASSED", "issues": []},
                        "summary": {},
                        "updated_at": _iso(now),
                    },
                    "request_id": ctx.request_id,
                },
                ctx,
            )

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="saveCleaningEdl",
            request_value=command,
            target_type="cleaning_draft",
            target_id=draft_id,
            events=("cleaning.draft.updated",),
            action=action,
        )

    async def create_preview(
        self,
        ctx: RequestContext,
        key: str,
        draft_id: str,
        request: Request,
        command: schemas.CreateCleaningPreviewRequest,
    ) -> dict[str, Any]:
        preview_id = new_id("preview")
        job_id = new_id("job")

        async def action() -> dict[str, Any]:
            row = await self.repo.draft(ctx, draft_id, lock=True)
            check_if_match(request, _draft_etag(row))
            if (
                row.status != "EDITING"
                or row.base_revision_id != command.base_revision_id
                or row.current_edl_revision != int(command.edl_revision)
                or row.current_operation_hash != command.operation_hash
            ):
                raise VersionConflictError(code="PREVIEW_PRECONDITION_FAILED")
            now = _now()
            preview = models.CleaningPreview(
                preview_id=preview_id,
                draft_id=draft_id,
                base_revision_id=row.base_revision_id,
                edl_revision=row.current_edl_revision,
                operation_hash=row.current_operation_hash,
                status="QUEUED",
                job_id=job_id,
                created_at=now,
                expires_at=now + timedelta(hours=1),
            )
            self.session.add(preview)
            row.active_preview_id = preview_id
            await self.session.flush()
            preview_data = {
                "preview_id": preview_id,
                "status": "QUEUED",
                "draft_id": draft_id,
                "base_revision_id": row.base_revision_id,
                "edl_revision": str(row.current_edl_revision),
                "operation_hash": row.current_operation_hash,
                "job_id": job_id,
                "created_at": _iso(now),
                "expires_at": _iso(preview.expires_at),
            }
            return {
                "preview": preview_data,
                "job": self._job(ctx, job_id, "CLEANING_PREVIEW", draft_id),
                "scope": _scope(ctx),
                "request_id": ctx.request_id,
                "contract_version": CONTRACT_VERSION,
            }

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="createCleaningPreview",
            request_value=command,
            target_type="cleaning_preview",
            target_id=preview_id,
            events=("cleaning.preview.requested",),
            action=action,
        )

    async def complete_preview(
        self, ctx: RequestContext, preview_id: str, output_revision_ids: Sequence[str]
    ) -> None:
        preview = await self.session.get(models.CleaningPreview, preview_id, with_for_update=True)
        if preview is None:
            raise NotFoundError(code="CLEANING_PREVIEW_NOT_FOUND")
        edl = await self.session.get(
            models.CleaningEdlRevision,
            {"draft_id": preview.draft_id, "edl_revision": preview.edl_revision},
        )
        operations = edl.canonical_document["operations"] if edl else []
        segments = build_time_mapping(
            0, max((int(op.get("end_ns", 0)) for op in operations), default=1), operations
        )
        if len(output_revision_ids) != 1 + max(
            segment.output_revision_index for segment in segments
        ):
            raise ValidationError(code="OUTPUT_REVISION_COUNT_MISMATCH")
        preview.source_to_output_map = {
            "mapping_version": "1",
            "segments": [
                {
                    "source_start_ns": str(segment.source_start_ns),
                    "source_end_ns": str(segment.source_end_ns),
                    "output_revision_id": output_revision_ids[segment.output_revision_index],
                    "output_start_ns": str(segment.output_start_ns),
                    "output_end_ns": str(segment.output_end_ns),
                }
                for segment in segments
            ],
        }
        preview.status = "READY"
        preview.validation_result = {"status": "PASSED", "issues": []}
        preview.summary = {"output_segment_count": str(len(output_revision_ids))}
        await emit_event(
            self.session,
            "cleaning.preview.completed",
            "cleaning_preview",
            preview_id,
            {"status": "READY"},
            ctx,
        )
        await write_audit(
            self.session,
            "cleaning.preview.completed",
            "cleaning_preview",
            preview_id,
            "SUCCEEDED",
            ctx,
        )
        await self.session.flush()

    async def commit(
        self,
        ctx: RequestContext,
        key: str,
        draft_id: str,
        request: Request,
        command: schemas.CommitCleaningDraftRequest,
    ) -> dict[str, Any]:
        commit_id = new_id("commit")
        job_id = new_id("job")

        async def action() -> dict[str, Any]:
            row = await self.repo.draft(ctx, draft_id, lock=True)
            check_if_match(request, _draft_etag(row))
            blocked = []
            if row.status != "EDITING":
                blocked.append({"code": "DRAFT_NOT_EDITABLE", "message": "Draft is not editable."})
            preview = await self.repo.preview(draft_id, command.preview_id, lock=True)
            if preview.status != "READY":
                blocked.append({"code": "PREVIEW_NOT_READY", "message": "Preview is not READY."})
            if (
                preview.edl_revision != int(command.edl_revision)
                or preview.operation_hash != command.operation_hash
            ):
                blocked.append(
                    {"code": "PREVIEW_STALE", "message": "Preview does not match saved EDL."}
                )
            composition = await self.session.get(models.ReviewSuccessorComposition, draft_id)
            if row.origin_type == "REVIEW_RETURN" and (
                not composition
                or command.successor_composition_hash != composition.composition_hash
            ):
                blocked.append(
                    {
                        "code": "SUCCESSOR_COMPOSITION_MISMATCH",
                        "message": "Successor composition hash is required and must match.",
                    }
                )
            if (
                row.origin_type == "ISSUE_DERIVED"
                and command.successor_composition_hash is not None
            ):
                blocked.append(
                    {
                        "code": "SUCCESSOR_COMPOSITION_NOT_ALLOWED",
                        "message": "Issue-derived Draft must send null composition hash.",
                    }
                )
            if not command.acknowledgement:
                blocked.append(
                    {
                        "code": "ACKNOWLEDGEMENT_REQUIRED",
                        "message": "Commit acknowledgement is required.",
                    }
                )
            if blocked:
                raise VersionConflictError(code="COMMIT_BLOCKED", blocked_reasons=blocked)
            now = _now()
            commit = models.CleaningCommit(
                commit_id=commit_id,
                draft_id=draft_id,
                preview_id=preview.preview_id,
                base_revision_id=row.base_revision_id,
                edl_revision=preview.edl_revision,
                operation_hash=preview.operation_hash,
                successor_composition_hash=command.successor_composition_hash,
                acknowledgement=True,
                status="QUEUED",
                job_id=job_id,
                created_at=now,
            )
            self.session.add(commit)
            row.active_commit_id = commit_id
            await self.session.flush()
            data = {
                "commit_id": commit_id,
                "status": "QUEUED",
                "draft_id": draft_id,
                "preview_id": preview.preview_id,
                "job_id": job_id,
                "created_at": _iso(now),
                "output_revisions": [],
                "output_version": None,
                "materialization_status": "NOT_STARTED",
                "successor_composition_hash": command.successor_composition_hash,
            }
            return {
                "commit": data,
                "job": self._job(ctx, job_id, "CLEANING_COMMIT", draft_id),
                "scope": _scope(ctx),
                "request_id": ctx.request_id,
                "contract_version": CONTRACT_VERSION,
            }

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="commitCleaningDraft",
            request_value=command,
            target_type="cleaning_commit",
            target_id=commit_id,
            events=("cleaning.submit.requested",),
            action=action,
        )

    def _job(self, ctx: RequestContext, job_id: str, kind: str, draft_id: str) -> dict[str, Any]:
        return {
            "job_id": job_id,
            "kind": kind,
            "status": "QUEUED",
            "stage": "QUEUED",
            "progress": {
                "completed_units": "0",
                "total_units": None,
                "unit": None,
                "percent": None,
                "message": None,
            },
            "result_ref": None,
            "error": None,
            "scope": _scope(ctx),
            "resource_ref": {
                "resource_type": "cleaning_draft",
                "resource_id": draft_id,
                "version": None,
                "etag": None,
            },
            "created_at": _iso(),
            "started_at": None,
            "finished_at": None,
            "updated_at": _iso(),
            "expires_at": None,
            "etag": compute_etag(1),
            "cancellable": True,
            "retry_of_job_id": None,
        }

    async def issue(self, ctx: RequestContext, issue_id: str) -> dict[str, Any]:
        return envelope(_issue_projection(await self.repo.issue(ctx, issue_id), ctx), ctx)

    async def draft(self, ctx: RequestContext, draft_id: str) -> dict[str, Any]:
        return envelope(_draft_projection(await self.repo.draft(ctx, draft_id)), ctx)
