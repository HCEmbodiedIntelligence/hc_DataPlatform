"""Application service for the P10 read-only CleaningDraft projection."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone

from hc_data_platform.core.errors import problem
from hc_data_platform.core.pagination import CursorCodec
from hc_data_platform.security.audit import canonical_hash
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.scope import ScopeGuard

from .models import (
    CleaningDraftAuditEvent,
    CleaningDraftDetailData,
    CleaningDraftDetailEnvelope,
    CleaningDraftEvent,
    CleaningDraftEventsEnvelope,
    CleaningDraftIssueDerivedOrigin,
    CleaningDraftJobs,
    CleaningDraftListEnvelope,
    CleaningDraftListItem,
    CleaningDraftMetrics,
    CleaningDraftPageInfo,
    CleaningDraftProjection,
    CleaningDraftScope,
    CleaningDraftScopeCounts,
    CleaningDraftSort,
    CleaningDraftSummaryData,
    CleaningDraftSummaryEnvelope,
)
from .repository import (
    CleaningDraftFilters,
    CleaningDraftProjectionIntegrityError,
    CleaningDraftRepository,
    InMemoryCleaningDraftRepository,
)

Clock = Callable[[], datetime]


class CleaningDraftService:
    def __init__(
        self,
        repository: CleaningDraftRepository,
        *,
        cursor_secret: str = "cleaning-draft-local-cursor-secret",
        clock: Clock = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._repository = repository
        self._cursor = CursorCodec(cursor_secret)
        self._clock = clock

    @classmethod
    def in_memory(cls) -> CleaningDraftService:
        return cls(InMemoryCleaningDraftRepository())

    def list_drafts(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        filters: CleaningDraftFilters,
        sort: CleaningDraftSort,
        after: str | None,
        before: str | None,
        limit: int,
        request_id: str,
    ) -> CleaningDraftListEnvelope:
        scope = self._authorize(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        normalized = _validated_filters(filters)
        if after is not None and before is not None:
            raise problem(
                status=422,
                code="CURSOR_DIRECTION_CONFLICT",
                title="Invalid pagination request",
                detail="Use either after or before, not both.",
            )
        if limit not in {20, 50, 100}:
            raise problem(
                status=422,
                code="PAGE_LIMIT_INVALID",
                title="Invalid page size",
                detail="Page size must be one of 20, 50, or 100.",
            )
        records = self._sorted(
            self._filtered(
                self._present_records(self._records(scope), auth=auth),
                filters=normalized,
                actor_id=auth.subject_id,
            ),
            sort,
        )
        visible, page_info = self._page(
            records=records,
            scope=scope,
            filters=normalized,
            sort=sort,
            actor_id=auth.subject_id,
            after=after,
            before=before,
            limit=limit,
        )
        now = self._clock()
        self._audit(
            auth=auth,
            scope=scope,
            action="cleaning.draft.listed",
            resource_id=project_id,
            request_id=request_id,
            details={"result_count": len(visible)},
        )
        return CleaningDraftListEnvelope(
            items=tuple(_list_item(record) for record in visible),
            page_info=page_info,
            snapshot_at=now,
            query_signature=f"sha256:{canonical_hash(_query_document(normalized, sort=sort))}",
            scope=scope,
            request_id=request_id,
        )

    def summary(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        filters: CleaningDraftFilters,
        request_id: str,
    ) -> CleaningDraftSummaryEnvelope:
        scope = self._authorize(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        normalized = _validated_filters(filters)
        records = self._filtered(
            self._present_records(self._records(scope), auth=auth),
            filters=normalized,
            actor_id=auth.subject_id,
        )
        statuses = Counter(record.draft.status for record in records)
        output_statuses = Counter(
            record.draft.output_version_status
            for record in records
            if record.draft.output_version_status is not None
        )
        now = self._clock()
        self._audit(
            auth=auth,
            scope=scope,
            action="cleaning.draft.summary_viewed",
            resource_id=project_id,
            request_id=request_id,
            details={"result_count": len(records)},
        )
        return CleaningDraftSummaryEnvelope(
            data=CleaningDraftSummaryData(
                scope_counts=CleaningDraftScopeCounts(
                    EDITING=str(statuses["EDITING"]),
                    COMMITTED=str(statuses["COMMITTED"]),
                    RETURNED=str(output_statuses["RETURNED"]),
                    REVIEWING=str(output_statuses["REVIEWING"]),
                ),
                metrics=CleaningDraftMetrics(
                    active_draft_count=str(
                        sum(1 for record in records if record.draft.status == "EDITING")
                    ),
                    manual_issue_derived_count=str(
                        sum(
                            1
                            for record in records
                            if isinstance(record.draft.origin, CleaningDraftIssueDerivedOrigin)
                        )
                    ),
                    review_return_count=str(
                        sum(
                            1
                            for record in records
                            if record.draft.output_version_status == "RETURNED"
                        )
                    ),
                ),
                jobs=CleaningDraftJobs(
                    preview_queued=str(
                        sum(1 for record in records if record.draft.preview_status == "QUEUED")
                    ),
                    preview_running=str(
                        sum(1 for record in records if record.draft.preview_status == "RUNNING")
                    ),
                    commit_queued=str(
                        sum(1 for record in records if record.draft.commit_status == "QUEUED")
                    ),
                ),
                as_of=now,
            ),
            scope=scope,
            request_id=request_id,
        )

    def detail(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        draft_id: str,
        request_id: str,
    ) -> CleaningDraftDetailEnvelope:
        scope = self._authorize(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        record = self._present_record(
            self._required_record(scope=scope, draft_id=draft_id),
            auth=auth,
        )
        self._audit(
            auth=auth,
            scope=scope,
            action="cleaning.draft.viewed",
            resource_id=draft_id,
            request_id=request_id,
        )
        return CleaningDraftDetailEnvelope(
            data=CleaningDraftDetailData(
                draft=record.draft,
                origin=record.draft.origin,
                review_summary=record.draft.review_summary,
                relationships=record.relationships,
            ),
            scope=scope,
            request_id=request_id,
        )

    def events(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        draft_id: str,
        after: str | None,
        before: str | None,
        limit: int,
        request_id: str,
    ) -> CleaningDraftEventsEnvelope:
        scope = self._authorize(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        self._required_record(scope=scope, draft_id=draft_id)
        if after is not None and before is not None:
            raise problem(
                status=422,
                code="CURSOR_DIRECTION_CONFLICT",
                title="Invalid pagination request",
                detail="Use either after or before, not both.",
            )
        if not 1 <= limit <= 100:
            raise problem(
                status=422,
                code="PAGE_LIMIT_INVALID",
                title="Invalid page size",
                detail="Event page size must be between 1 and 100.",
            )
        try:
            records = tuple(
                sorted(
                    self._repository.list_events(scope=scope, draft_id=draft_id),
                    key=lambda event: (event.occurred_at, event.event_id),
                    reverse=True,
                )
            )
        except CleaningDraftProjectionIntegrityError as exc:
            raise problem(
                status=409,
                code="CLEANING_DRAFT_PROJECTION_INCOMPLETE",
                title="CleaningDraft projection is incomplete",
                detail="A durable Draft cannot yet be represented safely by the read contract.",
            ) from exc
        visible, page_info = self._event_page(
            events=records,
            scope=scope,
            draft_id=draft_id,
            actor_id=auth.subject_id,
            after=after,
            before=before,
            limit=limit,
        )
        now = self._clock()
        self._audit(
            auth=auth,
            scope=scope,
            action="cleaning.draft.events_viewed",
            resource_id=draft_id,
            request_id=request_id,
            details={"result_count": len(visible)},
        )
        return CleaningDraftEventsEnvelope(
            items=visible,
            page_info=page_info,
            snapshot_at=now,
            scope=scope,
            request_id=request_id,
        )

    def _authorize(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
    ) -> CleaningDraftScope:
        ScopeGuard.require(auth, project_id, region_code)
        auth.require_capability("cleaning.read", project_id)
        if not self._repository.has_organization_project(
            organization_id=organization_id,
            project_id=project_id,
        ):
            raise problem(
                status=403,
                code="ORGANIZATION_SCOPE_DENIED",
                title="Organization access denied",
                detail="The selected organization is not bound to this project.",
            )
        return CleaningDraftScope(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )

    def _records(self, scope: CleaningDraftScope) -> tuple[CleaningDraftProjection, ...]:
        try:
            return self._repository.list_drafts(scope=scope)
        except CleaningDraftProjectionIntegrityError as exc:
            raise problem(
                status=409,
                code="CLEANING_DRAFT_PROJECTION_INCOMPLETE",
                title="CleaningDraft projection is incomplete",
                detail="A durable Draft cannot yet be represented safely by the read contract.",
            ) from exc

    def _present_records(
        self, records: tuple[CleaningDraftProjection, ...], *, auth: AuthContext
    ) -> tuple[CleaningDraftProjection, ...]:
        return tuple(self._present_record(record, auth=auth) for record in records)

    @staticmethod
    def _present_record(
        record: CleaningDraftProjection, *, auth: AuthContext
    ) -> CleaningDraftProjection:
        """Add only capability-authorized navigation actions to a read projection."""

        draft = record.draft
        actions: list[str] = ["VIEW", "VIEW_EVENTS"]
        project_id = record.scope.project_id
        if draft.status == "EDITING" and auth.has_capability("cleaning.edit", project_id):
            actions.append("EDIT")
        if draft.output_version_status == "REVIEWING" and auth.has_capability(
            "dataset_version.review", project_id
        ):
            actions.append("OPEN_REVIEW")
        if draft.output_version_status == "RETURNED" and draft.successor_draft_id is not None:
            actions.append("OPEN_SUCCESSOR")
        return record.model_copy(
            update={"draft": draft.model_copy(update={"allowed_actions": tuple(actions)})}
        )

    def _required_record(
        self, *, scope: CleaningDraftScope, draft_id: str
    ) -> CleaningDraftProjection:
        try:
            record = self._repository.get_draft(scope=scope, draft_id=draft_id)
        except CleaningDraftProjectionIntegrityError as exc:
            raise problem(
                status=409,
                code="CLEANING_DRAFT_PROJECTION_INCOMPLETE",
                title="CleaningDraft projection is incomplete",
                detail="A durable Draft cannot yet be represented safely by the read contract.",
            ) from exc
        if record is None:
            raise problem(
                status=404,
                code="CLEANING_DRAFT_NOT_FOUND",
                title="CleaningDraft not found",
                detail="The CleaningDraft does not exist in the selected scope.",
            )
        return record

    def _filtered(
        self,
        records: tuple[CleaningDraftProjection, ...],
        *,
        filters: CleaningDraftFilters,
        actor_id: str,
    ) -> tuple[CleaningDraftProjection, ...]:
        return tuple(
            record for record in records if _matches(record, filters=filters, actor_id=actor_id)
        )

    @staticmethod
    def _sorted(
        records: tuple[CleaningDraftProjection, ...], sort: CleaningDraftSort
    ) -> tuple[CleaningDraftProjection, ...]:
        if sort == "updated_at:asc,id:asc":
            return tuple(
                sorted(records, key=lambda item: (item.draft.updated_at, item.draft.draft_id))
            )
        if sort == "created_at:desc,id:desc":
            return tuple(
                sorted(
                    records,
                    key=lambda item: (item.draft.created_at, item.draft.draft_id),
                    reverse=True,
                )
            )
        if sort == "estimated_effective_duration_ns:desc,id:desc":
            # This compact queue projection intentionally omits the P11 EDL
            # calculation details, so duration is unknown for every row.
            return tuple(sorted(records, key=lambda item: item.draft.draft_id, reverse=True))
        # P10 does not duplicate P11's detailed preview/reuse calculation. Its
        # requested primary key is unknown for every row, so use the stable tie-breaker.
        return tuple(
            sorted(
                records,
                key=lambda item: (item.draft.updated_at, item.draft.draft_id),
                reverse=True,
            )
        )

    def _page(
        self,
        *,
        records: tuple[CleaningDraftProjection, ...],
        scope: CleaningDraftScope,
        filters: CleaningDraftFilters,
        sort: CleaningDraftSort,
        actor_id: str,
        after: str | None,
        before: str | None,
        limit: int,
    ) -> tuple[tuple[CleaningDraftProjection, ...], CleaningDraftPageInfo]:
        snapshot = _snapshot(records)
        cursor = after or before
        position: tuple[str, str] | None = None
        if cursor is not None:
            payload = self._cursor.decode(cursor)
            expected = {
                "kind": "cleaning-draft-page",
                "organization_id": scope.organization_id,
                "project_id": scope.project_id,
                "region_code": scope.region_code,
                "subject_id": actor_id,
                "filters": _filter_document(filters),
                "sort": sort,
                "snapshot": snapshot,
            }
            if any(payload.get(key) != value for key, value in expected.items()):
                raise problem(
                    status=409,
                    code="CURSOR_SNAPSHOT_STALE",
                    title="CleaningDraft page changed",
                    detail="Refresh the CleaningDraft list before continuing pagination.",
                )
            position = _cursor_position_from_payload(payload, kind="CleaningDraft")
        keys = [self._cursor_position(record, sort) for record in records]
        if after is not None:
            start = _after_index(keys, position, resource="CleaningDraft")
            visible = records[start : start + limit]
            has_previous = start > 0
            has_next = start + len(visible) < len(records)
        elif before is not None:
            end = _before_index(keys, position, resource="CleaningDraft")
            start = max(0, end - limit)
            visible = records[start:end]
            has_previous = start > 0
            has_next = end < len(records)
        else:
            visible = records[:limit]
            has_previous = False
            has_next = len(visible) < len(records)
        next_after = (
            self._encode_page_cursor(
                scope=scope,
                filters=filters,
                sort=sort,
                actor_id=actor_id,
                snapshot=snapshot,
                position=self._cursor_position(visible[-1], sort),
            )
            if visible and has_next
            else None
        )
        next_before = (
            self._encode_page_cursor(
                scope=scope,
                filters=filters,
                sort=sort,
                actor_id=actor_id,
                snapshot=snapshot,
                position=self._cursor_position(visible[0], sort),
            )
            if visible and has_previous
            else None
        )
        return tuple(visible), CleaningDraftPageInfo(
            after=next_after,
            before=next_before,
            has_next=has_next,
            has_previous=has_previous,
        )

    @staticmethod
    def _cursor_position(
        record: CleaningDraftProjection, sort: CleaningDraftSort
    ) -> tuple[str, str]:
        if sort == "created_at:desc,id:desc":
            return (record.draft.created_at.isoformat(), record.draft.draft_id)
        if sort == "estimated_effective_duration_ns:desc,id:desc":
            return ("unknown", record.draft.draft_id)
        return (record.draft.updated_at.isoformat(), record.draft.draft_id)

    def _encode_page_cursor(
        self,
        *,
        scope: CleaningDraftScope,
        filters: CleaningDraftFilters,
        sort: CleaningDraftSort,
        actor_id: str,
        snapshot: str,
        position: tuple[str, str],
    ) -> str:
        return self._cursor.encode(
            {
                "kind": "cleaning-draft-page",
                "organization_id": scope.organization_id,
                "project_id": scope.project_id,
                "region_code": scope.region_code,
                "subject_id": actor_id,
                "filters": _filter_document(filters),
                "sort": sort,
                "snapshot": snapshot,
                "position": list(position),
            }
        )

    def _event_page(
        self,
        *,
        events: tuple[CleaningDraftEvent, ...],
        scope: CleaningDraftScope,
        draft_id: str,
        actor_id: str,
        after: str | None,
        before: str | None,
        limit: int,
    ) -> tuple[tuple[CleaningDraftEvent, ...], CleaningDraftPageInfo]:
        snapshot = canonical_hash(
            [(event.event_id, event.occurred_at.isoformat(), event.request_id) for event in events]
        )
        cursor = after or before
        position: tuple[str, str] | None = None
        if cursor is not None:
            payload = self._cursor.decode(cursor)
            expected = {
                "kind": "cleaning-draft-event-page",
                "organization_id": scope.organization_id,
                "project_id": scope.project_id,
                "region_code": scope.region_code,
                "subject_id": actor_id,
                "draft_id": draft_id,
                "snapshot": snapshot,
            }
            if any(payload.get(key) != value for key, value in expected.items()):
                raise problem(
                    status=409,
                    code="CURSOR_SNAPSHOT_STALE",
                    title="CleaningDraft events changed",
                    detail="Refresh the CleaningDraft events before continuing pagination.",
                )
            position = _cursor_position_from_payload(payload, kind="CleaningDraft event")
        keys = [(event.occurred_at.isoformat(), event.event_id) for event in events]
        if after is not None:
            start = _after_index(keys, position, resource="CleaningDraft event")
            visible = events[start : start + limit]
            has_previous = start > 0
            has_next = start + len(visible) < len(events)
        elif before is not None:
            end = _before_index(keys, position, resource="CleaningDraft event")
            start = max(0, end - limit)
            visible = events[start:end]
            has_previous = start > 0
            has_next = end < len(events)
        else:
            visible = events[:limit]
            has_previous = False
            has_next = len(visible) < len(events)
        next_after = (
            self._encode_event_cursor(
                scope=scope,
                draft_id=draft_id,
                actor_id=actor_id,
                snapshot=snapshot,
                position=(visible[-1].occurred_at.isoformat(), visible[-1].event_id),
            )
            if visible and has_next
            else None
        )
        next_before = (
            self._encode_event_cursor(
                scope=scope,
                draft_id=draft_id,
                actor_id=actor_id,
                snapshot=snapshot,
                position=(visible[0].occurred_at.isoformat(), visible[0].event_id),
            )
            if visible and has_previous
            else None
        )
        return tuple(visible), CleaningDraftPageInfo(
            after=next_after,
            before=next_before,
            has_next=has_next,
            has_previous=has_previous,
        )

    def _encode_event_cursor(
        self,
        *,
        scope: CleaningDraftScope,
        draft_id: str,
        actor_id: str,
        snapshot: str,
        position: tuple[str, str],
    ) -> str:
        return self._cursor.encode(
            {
                "kind": "cleaning-draft-event-page",
                "organization_id": scope.organization_id,
                "project_id": scope.project_id,
                "region_code": scope.region_code,
                "subject_id": actor_id,
                "draft_id": draft_id,
                "snapshot": snapshot,
                "position": list(position),
            }
        )

    def _audit(
        self,
        *,
        auth: AuthContext,
        scope: CleaningDraftScope,
        action: str,
        resource_id: str,
        request_id: str,
        details: dict[str, object] | None = None,
    ) -> None:
        self._repository.append_audit(
            CleaningDraftAuditEvent(
                project_id=scope.project_id,
                region_code=scope.region_code,
                actor_id=auth.subject_id,
                action=action,
                resource_id=resource_id,
                request_id=request_id,
                occurred_at=self._clock(),
                details=details,
            )
        )


def _list_item(record: CleaningDraftProjection) -> CleaningDraftListItem:
    draft = record.draft
    return CleaningDraftListItem(
        draft_id=draft.draft_id,
        etag=draft.etag,
        status=draft.status,
        origin=draft.origin,
        base_version_id=draft.base_version_id,
        base_revision_id=draft.base_revision_id,
        episode_id=draft.episode_id,
        manual_issue_count=draft.manual_issue_count,
        preview_status=draft.preview_status,
        commit_status=draft.commit_status,
        output_version_status=draft.output_version_status,
        review_decision_id=draft.review_decision_id,
        successor_draft_id=draft.successor_draft_id,
        review_finding_count=draft.review_finding_count,
        review_summary=draft.review_summary,
        allowed_actions=draft.allowed_actions,
        updated_at=draft.updated_at,
    )


def _matches(
    record: CleaningDraftProjection, *, filters: CleaningDraftFilters, actor_id: str
) -> bool:
    draft = record.draft
    if filters.scope == "mine" and record.creator_id != actor_id:
        return False
    if filters.scope == "actionable" and draft.status != "EDITING":
        return False
    if filters.scope == "review" and draft.output_version_status != "REVIEWING":
        return False
    if filters.scope == "returned" and draft.output_version_status != "RETURNED":
        return False
    if filters.scope == "submitted" and draft.status != "COMMITTED":
        return False
    if filters.status == "submitted" and draft.status != "COMMITTED":
        return False
    if filters.status == "failed" and draft.commit_status != "FAILED":
        return False
    if filters.status == "archived":
        # P10 has no archival mutation or archival fact.  An empty result is more
        # truthful than inferring archival from age or a browser-only state.
        return False
    if filters.query:
        issue_id = (
            draft.origin.manual_issue_context.manual_issue_ids[0]
            if isinstance(draft.origin, CleaningDraftIssueDerivedOrigin)
            else ""
        )
        values = (
            draft.draft_id,
            record.dataset_id or "",
            draft.base_version_id,
            draft.episode_id,
            issue_id,
            draft.review_decision_id or "",
        )
        if not any(filters.query.casefold() in value.casefold() for value in values):
            return False
    if filters.dataset_id is not None and record.dataset_id != filters.dataset_id:
        return False
    if filters.base_version_id is not None and draft.base_version_id != filters.base_version_id:
        return False
    if filters.episode_id is not None and draft.episode_id != filters.episode_id:
        return False
    if filters.robot_id is not None and record.robot_id != filters.robot_id:
        return False
    if filters.creator_id is not None and record.creator_id != filters.creator_id:
        return False
    if filters.updated_from is not None and draft.updated_at < filters.updated_from:
        return False
    if filters.updated_to is not None and draft.updated_at >= filters.updated_to:
        return False
    if filters.preview_statuses and draft.preview_status not in filters.preview_statuses:
        return False
    if filters.commit_statuses and draft.commit_status not in filters.commit_statuses:
        return False
    if (
        filters.version_review_statuses
        and draft.output_version_status not in filters.version_review_statuses
    ):
        return False
    if filters.finding_types and not set(filters.finding_types).intersection(
        record.review_finding_types
    ):
        return False
    return not filters.finding_severities or bool(
        set(filters.finding_severities).intersection(record.review_finding_severities)
    )


def _validated_filters(filters: CleaningDraftFilters) -> CleaningDraftFilters:
    def text(value: str | None, *, maximum: int = 256) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if (
            not normalized
            or len(normalized) > maximum
            or any(ord(char) < 32 for char in normalized)
        ):
            raise problem(
                status=422,
                code="CLEANING_DRAFT_FILTER_INVALID",
                title="Invalid CleaningDraft filter",
                detail="CleaningDraft filters must be non-empty and within their size limit.",
            )
        return normalized

    def codes(values: tuple[str, ...], *, maximum: int = 64) -> tuple[str, ...]:
        normalized = tuple(sorted({value.strip() for value in values}))
        if any(not value or len(value) > maximum for value in normalized):
            raise problem(
                status=422,
                code="CLEANING_DRAFT_FILTER_INVALID",
                title="Invalid CleaningDraft filter",
                detail="CleaningDraft code filters must be non-empty and within their size limit.",
            )
        return normalized

    if filters.scope not in {"mine", "actionable", "review", "returned", "submitted", "all"}:
        raise _filter_problem()
    if filters.status not in {"active", "submitted", "failed", "archived"}:
        raise _filter_problem()
    updated_from = _datetime_filter(filters.updated_from)
    updated_to = _datetime_filter(filters.updated_to)
    if updated_from is not None and updated_to is not None and updated_from >= updated_to:
        raise _filter_problem()
    return CleaningDraftFilters(
        scope=filters.scope,
        status=filters.status,
        query=text(filters.query),
        dataset_id=text(filters.dataset_id),
        base_version_id=text(filters.base_version_id),
        episode_id=text(filters.episode_id),
        robot_id=text(filters.robot_id),
        creator_id=text(filters.creator_id),
        updated_from=updated_from,
        updated_to=updated_to,
        preview_statuses=codes(filters.preview_statuses),
        commit_statuses=codes(filters.commit_statuses),
        version_review_statuses=codes(filters.version_review_statuses),
        finding_types=codes(filters.finding_types, maximum=96),
        finding_severities=codes(filters.finding_severities),
    )


def _datetime_filter(value: object | None) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise _filter_problem()
    return value.astimezone(timezone.utc)


def _filter_problem() -> Exception:
    return problem(
        status=422,
        code="CLEANING_DRAFT_FILTER_INVALID",
        title="Invalid CleaningDraft filter",
        detail="CleaningDraft filters are inconsistent or unsupported.",
    )


def _filter_document(filters: CleaningDraftFilters) -> dict[str, object]:
    return {
        "scope": filters.scope,
        "status": filters.status,
        "q": filters.query,
        "dataset_id": filters.dataset_id,
        "base_version_id": filters.base_version_id,
        "episode_id": filters.episode_id,
        "robot_id": filters.robot_id,
        "creator_id": filters.creator_id,
        "updated_from": _date_value(filters.updated_from),
        "updated_to": _date_value(filters.updated_to),
        "preview_status": list(filters.preview_statuses),
        "commit_status": list(filters.commit_statuses),
        "version_review_status": list(filters.version_review_statuses),
        "finding_type": list(filters.finding_types),
        "finding_severity": list(filters.finding_severities),
    }


def _query_document(filters: CleaningDraftFilters, *, sort: CleaningDraftSort) -> dict[str, object]:
    return {"filters": _filter_document(filters), "sort": sort}


def _date_value(value: object | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _snapshot(records: tuple[CleaningDraftProjection, ...]) -> str:
    return canonical_hash(
        [
            (
                record.draft.draft_id,
                record.draft.etag,
                record.draft.updated_at.isoformat(),
                record.draft.output_version_status,
                record.draft.review_decision_id,
            )
            for record in records
        ]
    )


def _cursor_position_from_payload(payload: dict[str, object], *, kind: str) -> tuple[str, str]:
    raw = payload.get("position")
    if not isinstance(raw, list) or len(raw) != 2:
        raise problem(
            status=400,
            code="INVALID_CURSOR",
            title="Invalid pagination cursor",
            detail=f"The cursor does not contain a valid {kind} position.",
        )
    return (str(raw[0]), str(raw[1]))


def _after_index(
    keys: list[tuple[str, str]], position: tuple[str, str] | None, *, resource: str
) -> int:
    if position is None:
        raise problem(
            status=400,
            code="INVALID_CURSOR",
            title="Invalid pagination cursor",
            detail=f"The cursor does not include an ordered {resource} position.",
        )
    try:
        return keys.index(position) + 1
    except ValueError as exc:
        raise problem(
            status=409,
            code="CURSOR_SNAPSHOT_STALE",
            title=f"{resource} page changed",
            detail=f"Refresh the {resource} page before continuing pagination.",
        ) from exc


def _before_index(
    keys: list[tuple[str, str]], position: tuple[str, str] | None, *, resource: str
) -> int:
    if position is None:
        raise problem(
            status=400,
            code="INVALID_CURSOR",
            title="Invalid pagination cursor",
            detail=f"The cursor does not include an ordered {resource} position.",
        )
    try:
        return keys.index(position)
    except ValueError as exc:
        raise problem(
            status=409,
            code="CURSOR_SNAPSHOT_STALE",
            title=f"{resource} page changed",
            detail=f"Refresh the {resource} page before continuing pagination.",
        ) from exc
