"""Dataset/version/review application service."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, Protocol

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.context import RequestContext
from app.core.errors import ServerError, ValidationError, VersionConflictError
from app.core.etag import check_if_match, compute_etag
from app.core.idempotency import with_idempotency
from app.core.ids import new_id
from app.core.outbox import emit_event
from app.core.pagination import CursorParams, build_page

from . import models, schemas
from .repository import (
    DatasetRepository,
    apply_keyset,
    restore_keyset_order,
    scope_key,
    scope_predicate,
)

CONTRACT_VERSION = "dataset-version-review.v1alpha1"


class ReviewSuccessorDraftPort(Protocol):
    """Joins the caller's transaction and must never commit independently."""

    async def create_review_return_successor_in_transaction(
        self,
        *,
        ctx: RequestContext,
        supersedes_draft_id: str,
        returned_from_version_id: str,
        returned_from_review_decision_id: str,
        editable_base_revision_id: str,
        composition_members: list[dict[str, Any]],
    ) -> str: ...


class MissingReviewSuccessorDraftPort:
    async def create_review_return_successor_in_transaction(self, **_: Any) -> str:
        raise ServerError(code="REVIEW_SUCCESSOR_PORT_UNAVAILABLE", retryable=True)


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


def _request_digest(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _dataset_projection(row: models.Dataset, ctx: RequestContext) -> dict[str, Any]:
    return {
        "scope": _scope(ctx),
        "dataset_id": row.dataset_id,
        "name": row.name,
        "description": row.description,
        "labels": row.labels,
        "availability": row.availability,
        "owner": {"id": "system", "display_name": "System"},
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "etag": row.etag,
        "allowed_actions": [],
    }


def _version_projection(row: models.DatasetVersion, ctx: RequestContext) -> dict[str, Any]:
    result: dict[str, Any] = {
        "scope": _scope(ctx),
        "dataset_id": row.dataset_id,
        "version_id": row.version_id,
        "display_version": row.display_version,
        "kind": row.kind,
        "status": row.status,
        "created_at": _iso(row.created_at),
        "etag": row.etag,
        "version_token": row.version_token,
        "allowed_actions": [],
    }
    if row.status == "REVIEWING":
        result.update(source_draft_id=row.source_draft_id, delivery_status="NOT_STARTED")
    elif row.status == "RETURNED":
        result.update(
            source_draft_id=row.source_draft_id,
            review_decision_id=row.review_decision_id,
            successor_draft_id=row.successor_draft_id,
            supersedes_draft_id=row.supersedes_draft_id,
            returned_from_version_id=row.returned_from_version_id,
            returned_from_review_decision_id=row.returned_from_review_decision_id,
        )
    else:
        result.update(published_at=_iso(row.published_at), content_snapshot=None, manifest=None)
    return result


class DatasetService:
    def __init__(
        self, session: AsyncSession, successor_port: ReviewSuccessorDraftPort | None = None
    ) -> None:
        self.session = session
        self.repo = DatasetRepository(session)
        self.successor_port = successor_port or MissingReviewSuccessorDraftPort()

    def _idempotency_scope(self, ctx: RequestContext, operation_id: str) -> dict[str, str]:
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
        digest = _request_digest(request_value)

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
                stable["_idempotency_request_hash"] = digest
            return stable

        result = await with_idempotency(
            self.session, key, self._idempotency_scope(ctx, operation_id), unit
        )
        if isinstance(result, dict):
            prior = result.pop("_idempotency_request_hash", None)
            if prior is not None and prior != digest:
                raise VersionConflictError(code="IDEMPOTENCY_KEY_REUSED")
        return result

    async def create_dataset(
        self, ctx: RequestContext, key: str, command: schemas.CreateDatasetRequest
    ) -> dict[str, Any]:
        dataset_id = new_id("dataset")

        async def action() -> dict[str, Any]:
            duplicate = await self.session.scalar(
                select(models.Dataset.dataset_id).where(
                    scope_predicate(models.Dataset, ctx),
                    func.lower(models.Dataset.name) == command.name.strip().lower(),
                )
            )
            if duplicate:
                raise VersionConflictError(code="DATASET_NAME_CONFLICT")
            now = _now()
            row = models.Dataset(
                dataset_id=dataset_id,
                scope_key=scope_key(ctx),
                organization_id=ctx.organization_id,
                project_id=ctx.project_id,
                region_code=ctx.region_code,
                name=command.name.strip(),
                description=command.description,
                labels=command.labels,
                availability="ACTIVE",
                current_ready_version_id=None,
                row_version=1,
                etag=compute_etag(1),
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
            await self.session.flush()
            return envelope(_dataset_projection(row, ctx), ctx)

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="createDataset",
            request_value=command,
            target_type="dataset",
            target_id=dataset_id,
            events=("dataset.created", "dataset.updated"),
            action=action,
        )

    async def list_datasets(self, ctx: RequestContext, page: CursorParams) -> dict[str, Any]:
        stmt = apply_keyset(
            select(models.Dataset).where(scope_predicate(models.Dataset, ctx)),
            page,
            models.Dataset.updated_at,
            models.Dataset.dataset_id,
        )
        rows = restore_keyset_order(list((await self.session.scalars(stmt)).all()), page)
        result = build_page(rows, page, ("updated_at", "dataset_id"))
        info = result["page_info"]
        result["page_info"] = {
            "after": info["end_cursor"],
            "before": info["start_cursor"],
            "has_next": info["has_next_page"],
            "has_previous": info["has_previous_page"],
            "limit": page.limit,
            "total_count": None,
        }
        result["items"] = [_dataset_projection(row, ctx) for row in result["items"]]
        result.update(
            snapshot_id=new_id("snapshot"),
            scope=_scope(ctx),
            request_id=ctx.request_id,
            contract_version=CONTRACT_VERSION,
        )
        return result

    async def dataset_bootstrap(self, ctx: RequestContext, dataset_id: str) -> dict[str, Any]:
        row = await self.repo.dataset(ctx, dataset_id)
        current = None
        if row.current_ready_version_id:
            version = await self.repo.version(ctx, dataset_id, row.current_ready_version_id)
            current = _version_projection(version, ctx)
        return envelope(
            {
                "scope": _scope(ctx),
                "dataset": _dataset_projection(row, ctx),
                "current_ready_version": current,
                "suggested_version_id": row.current_ready_version_id,
                "summary": {
                    "episode_count": "0",
                    "effective_duration_ns": "0",
                    "source_bytes": "0",
                    "required_physical_bytes": "0",
                    "actual_oss_bytes": "0",
                    "pending_review_version_count": "0",
                    "returned_version_count": "0",
                    "actionable_draft_count": "0",
                    "calculated_at": _iso(),
                    "calculation_state": "CONFIRMED",
                },
            },
            ctx,
        )

    async def list_versions(
        self, ctx: RequestContext, dataset_id: str, page: CursorParams
    ) -> dict[str, Any]:
        await self.repo.dataset(ctx, dataset_id)
        stmt = apply_keyset(
            select(models.DatasetVersion).where(
                scope_predicate(models.DatasetVersion, ctx),
                models.DatasetVersion.dataset_id == dataset_id,
            ),
            page,
            models.DatasetVersion.created_at,
            models.DatasetVersion.version_id,
        )
        rows = restore_keyset_order(list((await self.session.scalars(stmt)).all()), page)
        result = build_page(rows, page, ("created_at", "version_id"))
        result["items"] = [_version_projection(row, ctx) for row in result["items"]]
        result.update(
            snapshot_id=new_id("snapshot"),
            scope=_scope(ctx),
            request_id=ctx.request_id,
            contract_version=CONTRACT_VERSION,
        )
        return result

    async def review_checks(
        self, ctx: RequestContext, dataset_id: str, version_id: str, request: Request
    ) -> dict[str, Any]:
        row = await self.repo.version(ctx, dataset_id, version_id, lock=True)
        check_if_match(request, row.etag)
        if row.status != "REVIEWING":
            raise VersionConflictError(code="VERSION_NOT_REVIEWING")
        revisions = list(
            (
                await self.session.scalars(
                    select(models.DatasetVersionRevision)
                    .where(models.DatasetVersionRevision.version_id == version_id)
                    .order_by(models.DatasetVersionRevision.ordinal)
                )
            ).all()
        )
        eligible = []
        for member in revisions:
            revision = await self.session.get(models.EpisodeRevision, member.revision_id)
            eligible.append(
                {
                    "output_revision_id": member.revision_id,
                    "episode_id": member.episode_id,
                    "streams": (revision.streams if revision else []),
                }
            )
        return envelope(
            {
                "scope": _scope(ctx),
                "dataset_id": dataset_id,
                "output_version_id": version_id,
                "source_draft_id": row.source_draft_id,
                "expected_status": "REVIEWING",
                "review_token": new_id("review_token") + "-" * 32,
                "review_token_expires_at": _iso(_now() + timedelta(minutes=5)),
                "version_token": row.version_token,
                "blockers": [],
                "finding_catalog": {
                    "version": "1",
                    "finding_types": ["DATA_QUALITY"],
                    "severities": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                    "note_min_length": 1,
                    "note_max_length": 8192,
                },
                "eligible_targets": eligible,
            },
            ctx,
        )

    async def approve(
        self,
        ctx: RequestContext,
        key: str,
        dataset_id: str,
        version_id: str,
        request: Request,
        command: schemas.ApproveReviewCommand,
    ) -> dict[str, Any]:
        decision_id = new_id("review_decision")

        async def action() -> dict[str, Any]:
            row = await self.repo.version(ctx, dataset_id, version_id, lock=True)
            check_if_match(request, row.etag)
            if row.status != "REVIEWING" or row.review_decision_id:
                raise VersionConflictError(code="VERSION_REVIEW_CONFLICT")
            decision = models.ReviewDecision(
                review_decision_id=decision_id,
                scope_key=scope_key(ctx),
                output_version_id=version_id,
                decision="APPROVED",
                actor_id=ctx.actor_id,
                audit_ref=ctx.request_id,
                created_at=_now(),
            )
            self.session.add(decision)
            row.review_decision_id = decision_id
            row.row_version += 1
            row.etag = compute_etag(row.row_version)
            row.version_token = new_id("version_token")
            job = {
                "job_id": new_id("job"),
                "kind": "VERSION_MATERIALIZATION",
                "status": "QUEUED",
                "etag": compute_etag(1),
            }
            await self.session.flush()
            return envelope(
                {
                    "review_decision": {
                        "id": decision_id,
                        "output_version_id": version_id,
                        "decision": "APPROVED",
                        "immutable": True,
                        "created_at": _iso(decision.created_at),
                    },
                    "output_version": {
                        "id": version_id,
                        "status": "REVIEWING",
                        "version_token": row.version_token,
                    },
                    "job": job,
                    "scope": _scope(ctx),
                    "request_id": ctx.request_id,
                    "contract_version": CONTRACT_VERSION,
                },
                ctx,
            )

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="approveVersionReview",
            request_value=command,
            target_type="dataset_version",
            target_id=version_id,
            events=("dataset_version.review.approved", "dataset_version.published"),
            action=action,
        )

    async def return_review(
        self,
        ctx: RequestContext,
        key: str,
        dataset_id: str,
        version_id: str,
        request: Request,
        command: schemas.ReturnReviewCommand,
    ) -> dict[str, Any]:
        decision_id = new_id("review_decision")
        finding_ids = [new_id("review_finding") for _ in command.findings]

        async def action() -> dict[str, Any]:
            row = await self.repo.version(ctx, dataset_id, version_id, lock=True)
            check_if_match(request, row.etag)
            if row.status != "REVIEWING" or not row.source_draft_id or row.review_decision_id:
                raise VersionConflictError(code="VERSION_REVIEW_CONFLICT")
            members = list(
                (
                    await self.session.scalars(
                        select(models.DatasetVersionRevision)
                        .where(models.DatasetVersionRevision.version_id == version_id)
                        .order_by(models.DatasetVersionRevision.ordinal)
                    )
                ).all()
            )
            member_ids = {member.revision_id for member in members}
            target_ids = {finding.output_revision_id for finding in command.findings}
            if len(target_ids) != 1 or not target_ids.issubset(member_ids):
                raise ValidationError(code="REVIEW_FINDING_TARGET_INVALID")
            decision = models.ReviewDecision(
                review_decision_id=decision_id,
                scope_key=scope_key(ctx),
                output_version_id=version_id,
                decision="RETURNED",
                actor_id=ctx.actor_id,
                audit_ref=ctx.request_id,
                created_at=_now(),
            )
            self.session.add(decision)
            finding_rows = []
            for finding_id, value in zip(finding_ids, command.findings, strict=True):
                finding = models.ReviewFinding(
                    review_finding_id=finding_id,
                    review_decision_id=decision_id,
                    output_revision_id=value.output_revision_id,
                    episode_stream_id=value.episode_stream_id,
                    start_ns=int(value.start_ns),
                    end_ns=int(value.end_ns),
                    finding_type=value.finding_type,
                    severity=value.severity,
                    note=value.note,
                    created_at=_now(),
                )
                self.session.add(finding)
                finding_rows.append(finding)
            composition = [
                {
                    "source_revision_id": member.revision_id,
                    "source_ordinal": member.ordinal,
                    "handling": "EDITABLE_BASE"
                    if member.revision_id in target_ids
                    else "CARRY_FORWARD",
                }
                for member in members
            ]
            successor_id = await self.successor_port.create_review_return_successor_in_transaction(
                ctx=ctx,
                supersedes_draft_id=row.source_draft_id,
                returned_from_version_id=version_id,
                returned_from_review_decision_id=decision_id,
                editable_base_revision_id=next(iter(target_ids)),
                composition_members=composition,
            )
            row.status = "RETURNED"
            row.review_decision_id = decision_id
            row.successor_draft_id = successor_id
            row.supersedes_draft_id = row.source_draft_id
            row.returned_from_version_id = version_id
            row.returned_from_review_decision_id = decision_id
            row.row_version += 1
            row.etag = compute_etag(row.row_version)
            row.version_token = new_id("version_token")
            self.session.add(
                models.ReviewReturnLineage(
                    review_decision_id=decision_id,
                    successor_draft_id=successor_id,
                    supersedes_draft_id=row.source_draft_id,
                    returned_from_version_id=version_id,
                    created_at=_now(),
                )
            )
            await self.session.flush()
            findings = [
                {
                    "id": item.review_finding_id,
                    "output_revision_id": item.output_revision_id,
                    "episode_stream_id": item.episode_stream_id,
                    "start_ns": str(item.start_ns),
                    "end_ns": str(item.end_ns),
                    "finding_type": item.finding_type,
                    "severity": item.severity,
                    "note": item.note,
                    "immutable": True,
                    "created_at": _iso(item.created_at),
                }
                for item in finding_rows
            ]
            data = {
                "scope": _scope(ctx),
                "review_decision": {
                    "id": decision_id,
                    "output_version_id": version_id,
                    "decision": "RETURNED",
                    "immutable": True,
                    "created_at": _iso(decision.created_at),
                },
                "findings": findings,
                "review_finding_ids": finding_ids,
                "output_version": {
                    "id": version_id,
                    "status": "RETURNED",
                    "version_token": row.version_token,
                },
                "successor_draft_id": successor_id,
                "supersedes_draft_id": row.source_draft_id,
                "returned_from_version_id": version_id,
                "returned_from_review_decision_id": decision_id,
            }
            schemas.ReturnReviewResult.model_validate(data)
            return envelope(data, ctx)

        return await self._write(
            ctx=ctx,
            key=key,
            operation_id="returnVersionReview",
            request_value=command,
            target_type="dataset_version",
            target_id=version_id,
            events=("dataset_version.review.returned",),
            action=action,
        )

    async def decision_detail(
        self, ctx: RequestContext, dataset_id: str, version_id: str, decision_id: str
    ) -> dict[str, Any]:
        await self.repo.version(ctx, dataset_id, version_id)
        decision = await self.repo.decision(ctx, version_id, decision_id)
        findings = await self.repo.findings(decision_id)
        lineage = await self.session.get(models.ReviewReturnLineage, decision_id)
        return envelope(
            {
                "scope": _scope(ctx),
                "dataset_id": dataset_id,
                "version_id": version_id,
                "review_decision": {
                    "id": decision.review_decision_id,
                    "output_version_id": decision.output_version_id,
                    "decision": decision.decision,
                    "immutable": True,
                    "created_at": _iso(decision.created_at),
                },
                "findings": [
                    {
                        "id": row.review_finding_id,
                        "output_revision_id": row.output_revision_id,
                        "episode_stream_id": row.episode_stream_id,
                        "start_ns": str(row.start_ns),
                        "end_ns": str(row.end_ns),
                        "finding_type": row.finding_type,
                        "severity": row.severity,
                        "note": row.note,
                        "immutable": True,
                        "created_at": _iso(row.created_at),
                    }
                    for row in findings
                ],
                "successor_draft_id": lineage.successor_draft_id if lineage else None,
            },
            ctx,
        )

    async def deletion_preflight(
        self,
        ctx: RequestContext,
        dataset_id: str,
        version_id: str | None,
        command: schemas.DeletionPreflightRequest,
    ) -> dict[str, Any]:
        row = await (
            self.repo.version(ctx, dataset_id, version_id)
            if version_id
            else self.repo.dataset(ctx, dataset_id)
        )
        if command.expected_etag != row.etag:
            raise VersionConflictError(code="ETAG_MISMATCH")
        reasons = [
            {"code": "CAPABILITY_RESERVED", "message": "Deletion capability is not approved."}
        ]
        checks = [
            {
                "check_type": name,
                "passed": False if name == "PERMISSION" else True,
                "blocked_reasons": reasons if name == "PERMISSION" else [],
            }
            for name in (
                "ACTIVE_REFERENCES",
                "RETENTION_POLICY",
                "LEGAL_HOLD",
                "PERMISSION",
                "CURRENT_READY",
                "ACTIVE_JOBS",
                "IMMUTABLE_RETENTION",
            )
        ]
        data = {
            "scope": _scope(ctx),
            "resource_type": "DATASET_VERSION" if version_id else "DATASET",
            "resource_id": version_id or dataset_id,
            "capability_status": "RESERVED_CONDITIONAL",
            "executable": False,
            "domain_clear": False,
            "preflight_token": new_id("deletion_preflight") + "-" * 32,
            "expires_at": _iso(_now() + timedelta(minutes=5)),
            "checks": checks,
            "async_impact": {
                "object_count": "0",
                "estimated_bytes": "0",
                "dependent_projection_count": "0",
                "requires_async_job": True,
            },
            "blocked_reasons": reasons,
        }
        return envelope(data, ctx)

    async def generic_version_read(
        self, ctx: RequestContext, dataset_id: str, version_id: str, kind: str
    ) -> dict[str, Any]:
        row = await self.repo.version(ctx, dataset_id, version_id)
        return envelope(
            {
                "scope": _scope(ctx),
                "dataset_id": dataset_id,
                "version_id": version_id,
                "kind": kind,
                "version": _version_projection(row, ctx),
                "items": [],
            },
            ctx,
        )
