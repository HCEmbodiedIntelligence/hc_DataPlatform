from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
from typing import Literal, Protocol, cast
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from hc_data_platform.core.errors import problem
from hc_data_platform.core.pagination import CursorCodec, PageInfo
from hc_data_platform.ingest.processing_status import processing_interruption
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.capabilities import (
    CAPABILITY_ANNOTATION_REVIEW,
    CAPABILITY_DATASET_READ,
    CAPABILITY_DATASET_VERSION_PUBLISH,
    CAPABILITY_UPLOAD_MANAGE,
    CAPABILITY_UPLOAD_READ,
)

from .models import (
    PENDING_ITEM_TYPES,
    TASK_PROCESSING_STAGES,
    DashboardActivityEvent,
    DashboardActivityEventType,
    DashboardActivityPage,
    DashboardActivityResponse,
    DashboardCoverageResponse,
    DashboardPendingItem,
    DashboardPendingItemsPage,
    DashboardPendingItemsResponse,
    DashboardPendingItemType,
    DashboardResourceType,
    DashboardSectionError,
    DashboardSectionState,
    DashboardSectionStatus,
    DashboardTargetResource,
    DashboardTaskStatusResponse,
    SelectedTaskStatus,
    TaskAttainment,
    TaskDataIssue,
    TaskDeviceProgress,
    TaskLifecycle,
    TaskPackageMainState,
    TaskPipelineStatus,
    TaskProcessingStage,
    TaskQcCounts,
    TaskStageCounts,
    TaskStandardizationCounts,
    TaskStatusAction,
    TaskStatusBlocker,
    TaskStatusListItem,
    TaskStatusTarget,
)
from .repository import (
    DashboardBusinessEventFact,
    DashboardPendingFact,
    DashboardQueryAudit,
    DashboardRepository,
    DashboardScope,
    DashboardWindow,
    InMemoryDashboardRepository,
    TaskStatusPackageFact,
    TaskStatusProjectionFacts,
    TaskStatusTaskFact,
)

MAX_QUERY_WINDOW = timedelta(days=31)


class DashboardSectionKey(str, Enum):
    ACTIVITY = "activity"
    COVERAGE = "coverage"
    PENDING_ITEMS = "pending_items"


class FactSourceStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    EMPTY = "EMPTY"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class FactSourceProbe:
    source_id: str
    status: FactSourceStatus
    as_of: datetime | None = None

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("source_id must not be empty")
        if self.status is FactSourceStatus.AVAILABLE and self.as_of is None:
            raise ValueError("available sources require as_of")
        if self.as_of is not None and self.as_of.tzinfo is None:
            raise ValueError("source as_of must be timezone-aware")


@dataclass(frozen=True, slots=True)
class DashboardQuery:
    scope: DashboardScope
    window: DashboardWindow


class DashboardQueryAdmission(Protocol):
    def check(self, *, auth: AuthContext, endpoint: str, query: DashboardQuery) -> None: ...


class AllowDashboardQueries:
    """Extension point for deployment rate policy; numeric thresholds remain unconfirmed."""

    def check(self, *, auth: AuthContext, endpoint: str, query: DashboardQuery) -> None:
        del auth, endpoint, query


_BLOCKERS: dict[DashboardSectionKey, tuple[str, str]] = {
    DashboardSectionKey.COVERAGE: (
        "P01_COVERAGE_DENOMINATOR_MISSING",
        "缺少采集计划、机器人分组、任务目录或目标总量，因此暂时无法计算覆盖率。",
    ),
    DashboardSectionKey.ACTIVITY: (
        "DASHBOARD_ACTIVITY_SOURCE_PARTIAL",
        "One or more durable business-event sources are unavailable.",
    ),
    DashboardSectionKey.PENDING_ITEMS: (
        "DASHBOARD_PENDING_SOURCE_PARTIAL",
        "One or more authorized pending-item sources are unavailable.",
    ),
}

_PENDING_CAPABILITIES: dict[DashboardPendingItemType, tuple[str, ...]] = {
    DashboardPendingItemType.UPLOAD_FAILED: (CAPABILITY_UPLOAD_MANAGE,),
    DashboardPendingItemType.QC_ANOMALY: (
        CAPABILITY_DATASET_READ,
        CAPABILITY_UPLOAD_READ,
    ),
    DashboardPendingItemType.TAG_REVIEW_PENDING: (CAPABILITY_ANNOTATION_REVIEW,),
    DashboardPendingItemType.PUBLICATION_PENDING: (CAPABILITY_DATASET_VERSION_PUBLISH,),
}

_ACTIVITY_DISPLAY: dict[DashboardActivityEventType, tuple[str, str]] = {
    DashboardActivityEventType.UPLOAD_COMMITTED: (
        "Upload committed",
        "A Raw upload was committed to the scoped rollout.",
    ),
    DashboardActivityEventType.QC_COMPLETED: (
        "Quality check completed",
        "An immutable QC report was recorded for the scoped rollout.",
    ),
    DashboardActivityEventType.TAG_REVIEW_DECIDED: (
        "Tag review decided",
        "A reviewer decision was recorded for an annotation revision.",
    ),
    DashboardActivityEventType.DATASET_PUBLISHED: (
        "Dataset version published",
        "A dataset publication with certain rollout-region lineage was recorded.",
    ),
}


def blocked_state(section: DashboardSectionKey) -> DashboardSectionState:
    code, message = _BLOCKERS[section]
    return DashboardSectionState(
        status=DashboardSectionStatus.BLOCKED,
        error=DashboardSectionError(
            code=code,
            message=message,
            needs_product_confirmation=section is DashboardSectionKey.COVERAGE,
        ),
    )


def classify_section(
    probes: tuple[FactSourceProbe, ...],
    *,
    now: datetime,
    freshness: timedelta,
) -> DashboardSectionState:
    """Classify generic source health; this intentionally computes no P01 business metric."""

    if now.tzinfo is None:
        raise ValueError("classification time must be timezone-aware")
    if freshness <= timedelta(0):
        raise ValueError("freshness threshold must be positive")
    if not probes or all(probe.status is FactSourceStatus.EMPTY for probe in probes):
        observed = [probe.as_of for probe in probes if probe.as_of is not None]
        return DashboardSectionState(
            status=DashboardSectionStatus.EMPTY,
            as_of=max(observed, default=now),
        )

    errors = [probe for probe in probes if probe.status is FactSourceStatus.ERROR]
    healthy = [probe for probe in probes if probe.status is not FactSourceStatus.ERROR]
    if errors:
        status = DashboardSectionStatus.PARTIAL if healthy else DashboardSectionStatus.ERROR
        return DashboardSectionState(
            status=status,
            as_of=min(
                (probe.as_of for probe in healthy if probe.as_of is not None),
                default=None,
            ),
            error=DashboardSectionError(
                code="DASHBOARD_SOURCE_PARTIAL" if healthy else "DASHBOARD_SOURCE_UNAVAILABLE",
                message="One or more dashboard fact sources are unavailable.",
                retryable=True,
            ),
        )

    observed = [probe.as_of for probe in probes if probe.as_of is not None]
    source_as_of = min(observed, default=now)
    if source_as_of < now - freshness:
        return DashboardSectionState(
            status=DashboardSectionStatus.STALE,
            as_of=source_as_of,
            error=DashboardSectionError(
                code="DASHBOARD_SOURCE_STALE",
                message="Dashboard facts are older than the configured freshness threshold.",
                retryable=True,
            ),
        )
    return DashboardSectionState(status=DashboardSectionStatus.READY, as_of=source_as_of)


class DashboardCursorCodec:
    VERSION = 1

    def __init__(self, secret: str | bytes) -> None:
        self._codec = CursorCodec(secret)

    def encode(
        self,
        *,
        endpoint: str,
        query: DashboardQuery,
        sort_values: tuple[str | int, ...],
    ) -> str:
        if endpoint not in {"activity", "pending-items"} or not sort_values:
            raise ValueError("dashboard cursors require a pageable endpoint and stable sort tuple")
        return self._codec.encode(
            {
                "v": self.VERSION,
                "endpoint": endpoint,
                "binding": self._binding(query),
                "sort": list(sort_values),
            }
        )

    def decode(
        self,
        cursor: str,
        *,
        endpoint: str,
        query: DashboardQuery,
    ) -> tuple[str | int, ...]:
        payload = self._codec.decode(cursor)
        try:
            if (
                payload["v"] != self.VERSION
                or payload["endpoint"] != endpoint
                or payload["binding"] != self._binding(query)
                or not isinstance(payload["sort"], list)
                or not payload["sort"]
                or any(not isinstance(value, str | int) for value in payload["sort"])
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise problem(
                status=400,
                code="INVALID_CURSOR",
                title="Invalid pagination cursor",
                detail="The cursor does not belong to this dashboard query.",
            ) from exc
        return tuple(payload["sort"])

    @staticmethod
    def _binding(query: DashboardQuery) -> str:
        body = json.dumps(
            {
                "principal_id": query.scope.principal_id,
                "project_id": query.scope.project_id,
                "region_code": query.scope.region_code,
                "from": query.window.start.isoformat(),
                "to": query.window.end.isoformat(),
                "timezone": query.window.timezone,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return sha256(body).hexdigest()


class DashboardService:
    def __init__(
        self,
        repository: DashboardRepository | None = None,
        *,
        cursor_secret: str | bytes = "dashboard-memory-only",
        admission: DashboardQueryAdmission | None = None,
        clock: Callable[[], datetime] | None = None,
        max_query_window: timedelta = MAX_QUERY_WINDOW,
    ) -> None:
        if max_query_window <= timedelta(0):
            raise ValueError("dashboard max query window must be positive")
        self._repository = repository or InMemoryDashboardRepository()
        self._cursor = DashboardCursorCodec(cursor_secret)
        self._admission = admission or AllowDashboardQueries()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._max_query_window = max_query_window

    def task_status(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        task_id: str | None = None,
    ) -> DashboardTaskStatusResponse:
        scope = DashboardScope(auth.subject_id, project_id, region_code)
        self._repository.enforce_scope(auth, scope)
        now = self._clock()
        if now.tzinfo is None:
            raise RuntimeError("dashboard clock must return a timezone-aware instant")
        now = now.astimezone(timezone.utc)
        facts = self._repository.task_status_projection(
            auth=auth,
            scope=scope,
            task_id=task_id,
        )
        tasks = tuple(_task_list_item(item) for item in facts.tasks)
        task_ids = {item.task_id for item in tasks}
        if task_id is not None and task_id not in task_ids:
            raise problem(
                status=404,
                code="DASHBOARD_COLLECTION_TASK_NOT_FOUND",
                title="Collection task not found",
                detail="The requested collection task is not present in this project-region scope.",
            )
        selected_id = (
            task_id if task_id is not None else (tasks[0].task_id if len(tasks) == 1 else None)
        )
        selected = None
        if selected_id is not None:
            selected_task = next(item for item in tasks if item.task_id == selected_id)
            selected = _selected_task_status(selected_task, facts)
        pipeline = _task_pipeline_status(
            facts,
            task_count=1 if selected_id is not None else len(tasks),
        )
        if not tasks:
            section = DashboardSectionState(status=DashboardSectionStatus.EMPTY, as_of=now)
        elif facts.unavailable_sources:
            section = DashboardSectionState(
                status=DashboardSectionStatus.PARTIAL,
                as_of=now,
                error=DashboardSectionError(
                    code="DASHBOARD_TASK_STATUS_SOURCE_PARTIAL",
                    message=(
                        "One or more task-status fact sources are unavailable; "
                        "affected values are marked unavailable."
                    ),
                    retryable=True,
                ),
            )
        else:
            section = DashboardSectionState(status=DashboardSectionStatus.READY, as_of=now)
        self._repository.record_query(
            auth,
            DashboardQueryAudit(
                principal_id=scope.principal_id,
                project_id=project_id,
                region_code=region_code,
                endpoint="task-status",
                range_start=now,
                range_end=now,
                timezone="UTC",
                result_status=section.status.value,
                occurred_at=now,
            ),
        )
        return DashboardTaskStatusResponse(
            project_id=project_id,
            region_code=region_code,
            as_of=now,
            section=section,
            tasks=tasks,
            pipeline=pipeline,
            selected_task_id=selected_id,
            selected=selected,
        )

    def activity(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        range_start: datetime,
        range_end: datetime,
        timezone_name: str,
        cursor: str | None,
        limit: int,
    ) -> DashboardActivityResponse:
        self._validate_page_limit(limit)
        query = self._query(auth, project_id, region_code, range_start, range_end, timezone_name)
        now = self._begin(auth, "activity", query)
        after = self._decode_activity_cursor(cursor, query)
        facts = self._repository.business_events(
            auth=auth,
            scope=query.scope,
            window=query.window,
            limit=limit,
            after=after,
        )
        items = tuple(self._activity_item(fact) for fact in facts.items)
        page_info = self._activity_page_info(items, facts.has_more, cursor is not None, query)
        if facts.unavailable_sources:
            status = DashboardSectionStatus.PARTIAL
            error = DashboardSectionError(
                code="DASHBOARD_ACTIVITY_SOURCE_PARTIAL",
                message="Publication events are unavailable until region lineage is queryable.",
                retryable=True,
            )
        else:
            status = DashboardSectionStatus.READY if items else DashboardSectionStatus.EMPTY
            error = None
        page = DashboardActivityPage(
            status=status,
            as_of=now,
            error=error,
            page_info=page_info,
            items=items,
        )
        self._audit(auth, query, "activity", status, now)
        return DashboardActivityResponse.model_validate(
            {**self._base(query, now), "activity": page}
        )

    def coverage(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        range_start: datetime,
        range_end: datetime,
        timezone_name: str,
    ) -> DashboardCoverageResponse:
        query = self._query(auth, project_id, region_code, range_start, range_end, timezone_name)
        now = self._begin(auth, "coverage", query)
        state = blocked_state(DashboardSectionKey.COVERAGE)
        self._audit(auth, query, "coverage", state.status, now)
        return DashboardCoverageResponse.model_validate(
            {**self._base(query, now), "coverage": state}
        )

    def pending_items(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        range_start: datetime,
        range_end: datetime,
        timezone_name: str,
        cursor: str | None,
        limit: int,
    ) -> DashboardPendingItemsResponse:
        self._validate_page_limit(limit)
        query = self._query(auth, project_id, region_code, range_start, range_end, timezone_name)
        now = self._begin(auth, "pending-items", query)
        allowed = tuple(
            item_type
            for item_type in PENDING_ITEM_TYPES
            if all(
                auth.has_capability(capability, project_id)
                for capability in _PENDING_CAPABILITIES[item_type]
            )
        )
        after = self._decode_pending_cursor(cursor, query)
        facts = self._repository.pending_facts(
            auth=auth,
            scope=query.scope,
            window=query.window,
            allowed_types=allowed,
            limit=limit,
            after=after,
        )
        items = tuple(self._pending_item(fact) for fact in facts.items)
        page_info = self._pending_page_info(items, facts.has_more, cursor is not None, query)
        if facts.unavailable_sources:
            status = DashboardSectionStatus.PARTIAL
            error = DashboardSectionError(
                code="DASHBOARD_PENDING_SOURCE_PARTIAL",
                message=(
                    "Authorized pending-publication facts are unavailable until lineage exists."
                ),
                retryable=True,
            )
        else:
            status = DashboardSectionStatus.READY if items else DashboardSectionStatus.EMPTY
            error = None
        page = DashboardPendingItemsPage(
            status=status,
            as_of=now,
            error=error,
            page_info=page_info,
            authorized_source_types=allowed,
            items=items,
        )
        self._audit(auth, query, "pending-items", status, now)
        return DashboardPendingItemsResponse.model_validate(
            {**self._base(query, now), "pending_items": page}
        )

    def _query(
        self,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        range_start: datetime,
        range_end: datetime,
        timezone_name: str,
    ) -> DashboardQuery:
        if range_start.tzinfo is None or range_end.tzinfo is None:
            raise problem(
                status=422,
                code="DASHBOARD_TIMEZONE_OFFSET_REQUIRED",
                title="Timezone offset required",
                detail="The from and to timestamps must include UTC offsets.",
            )
        start = range_start.astimezone(timezone.utc)
        end = range_end.astimezone(timezone.utc)
        if start >= end:
            raise problem(
                status=422,
                code="DASHBOARD_TIME_RANGE_INVALID",
                title="Invalid dashboard time range",
                detail="The from timestamp must be earlier than the exclusive to timestamp.",
            )
        if end - start > self._max_query_window:
            raise problem(
                status=422,
                code="DASHBOARD_TIME_RANGE_TOO_LARGE",
                title="Dashboard time range is too large",
                detail="The requested range exceeds the configured synchronous query budget.",
            )
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise problem(
                status=422,
                code="DASHBOARD_TIMEZONE_INVALID",
                title="Invalid dashboard timezone",
                detail="timezone must name an installed IANA timezone.",
            ) from exc
        return DashboardQuery(
            scope=DashboardScope(auth.subject_id, project_id, region_code),
            window=DashboardWindow(start, end, timezone_name),
        )

    def _begin(self, auth: AuthContext, endpoint: str, query: DashboardQuery) -> datetime:
        self._repository.enforce_scope(auth, query.scope)
        self._admission.check(auth=auth, endpoint=endpoint, query=query)
        now = self._clock()
        if now.tzinfo is None:
            raise RuntimeError("dashboard clock must return a timezone-aware instant")
        return now.astimezone(timezone.utc)

    def _audit(
        self,
        auth: AuthContext,
        query: DashboardQuery,
        endpoint: str,
        status: DashboardSectionStatus,
        now: datetime,
    ) -> None:
        self._repository.record_query(
            auth,
            DashboardQueryAudit(
                principal_id=query.scope.principal_id,
                project_id=query.scope.project_id,
                region_code=query.scope.region_code,
                endpoint=endpoint,
                range_start=query.window.start,
                range_end=query.window.end,
                timezone=query.window.timezone,
                result_status=status.value,
                occurred_at=now,
            ),
        )

    def _decode_activity_cursor(
        self,
        cursor: str | None,
        query: DashboardQuery,
    ) -> tuple[datetime, str, str] | None:
        if cursor is None:
            return None
        values = self._cursor.decode(cursor, endpoint="activity", query=query)
        try:
            if len(values) != 3 or not all(isinstance(value, str) for value in values):
                raise ValueError
            occurred_at = datetime.fromisoformat(str(values[0]))
            if occurred_at.tzinfo is None:
                raise ValueError
            DashboardActivityEventType(str(values[1]))
            return occurred_at.astimezone(timezone.utc), str(values[1]), str(values[2])
        except (TypeError, ValueError) as exc:
            raise self._invalid_cursor() from exc

    def _decode_pending_cursor(
        self,
        cursor: str | None,
        query: DashboardQuery,
    ) -> tuple[int, datetime, str, str] | None:
        if cursor is None:
            return None
        values = self._cursor.decode(cursor, endpoint="pending-items", query=query)
        try:
            if (
                len(values) != 4
                or isinstance(values[0], bool)
                or not isinstance(values[0], int)
                or not all(isinstance(value, str) for value in values[1:])
            ):
                raise ValueError
            opened_at = datetime.fromisoformat(str(values[1]))
            if opened_at.tzinfo is None:
                raise ValueError
            DashboardPendingItemType(str(values[2]))
            return (
                int(values[0]),
                opened_at.astimezone(timezone.utc),
                str(values[2]),
                str(values[3]),
            )
        except (TypeError, ValueError) as exc:
            raise self._invalid_cursor() from exc

    @staticmethod
    def _invalid_cursor() -> Exception:
        return problem(
            status=400,
            code="INVALID_CURSOR",
            title="Invalid pagination cursor",
            detail="The cursor does not contain a valid stable dashboard sort key.",
        )

    def _activity_page_info(
        self,
        items: tuple[DashboardActivityEvent, ...],
        has_more: bool,
        has_previous: bool,
        query: DashboardQuery,
    ) -> PageInfo:
        cursors = (
            [
                self._cursor.encode(
                    endpoint="activity",
                    query=query,
                    sort_values=(
                        item.occurred_at.isoformat(),
                        item.event_type.value,
                        item.source_id,
                    ),
                )
                for item in (items[0], items[-1])
            ]
            if items
            else [None, None]
        )
        return PageInfo(
            has_next_page=has_more,
            has_previous_page=has_previous,
            start_cursor=cursors[0],
            end_cursor=cursors[1],
        )

    def _pending_page_info(
        self,
        items: tuple[DashboardPendingItem, ...],
        has_more: bool,
        has_previous: bool,
        query: DashboardQuery,
    ) -> PageInfo:
        def encode(item: DashboardPendingItem) -> str:
            rank = {
                "CRITICAL": 4,
                "HIGH": 3,
                "MEDIUM": 2,
                "LOW": 1,
            }[item.severity.value]
            return self._cursor.encode(
                endpoint="pending-items",
                query=query,
                sort_values=(
                    rank,
                    item.opened_at.isoformat(),
                    item.item_type.value,
                    item.source_id,
                ),
            )

        cursors = [encode(items[0]), encode(items[-1])] if items else [None, None]
        return PageInfo(
            has_next_page=has_more,
            has_previous_page=has_previous,
            start_cursor=cursors[0],
            end_cursor=cursors[1],
        )

    @staticmethod
    def _activity_item(fact: DashboardBusinessEventFact) -> DashboardActivityEvent:
        title, summary = _ACTIVITY_DISPLAY[fact.event_type]
        return DashboardActivityEvent(
            event_id=fact.event_id,
            event_type=fact.event_type,
            source_id=fact.source_id,
            deduplication_key=fact.deduplication_key,
            occurred_at=fact.occurred_at,
            source_state=fact.source_state,
            title=title,
            summary=summary,
            target=DashboardService._target(fact),
        )

    @staticmethod
    def _pending_item(fact: DashboardPendingFact) -> DashboardPendingItem:
        return DashboardPendingItem(
            item_type=fact.item_type,
            source_id=fact.source_id,
            deduplication_key=fact.deduplication_key,
            source_state=fact.source_state,
            severity=fact.severity,
            opened_at=fact.opened_at,
            target=DashboardService._target(fact, require_link=True),
        )

    @staticmethod
    def _target(
        fact: DashboardBusinessEventFact | DashboardPendingFact,
        *,
        require_link: bool = False,
    ) -> DashboardTargetResource:
        resource_id = quote(fact.target_resource_id, safe="")
        if fact.target_resource_type is DashboardResourceType.UPLOAD_SESSION:
            deep_link = f"/ingest/uploads/{resource_id}"
        elif fact.target_resource_type is DashboardResourceType.ANNOTATION_TASK:
            deep_link = f"/annotations/tasks/{resource_id}"
        elif fact.target_resource_type is DashboardResourceType.DATASET_VERSION:
            if fact.target_resource_version is None:
                raise RuntimeError("dataset-version targets require a version")
            version = quote(fact.target_resource_version, safe="")
            deep_link = f"/datasets/{resource_id}/versions/{version}"
        else:
            deep_link = None
        if require_link and deep_link is None:
            raise RuntimeError("pending fact has no allowlisted deep-link template")
        return DashboardTargetResource(
            resource_type=fact.target_resource_type,
            resource_id=fact.target_resource_id,
            resource_version=fact.target_resource_version,
            deep_link=deep_link,
        )

    @staticmethod
    def _base(query: DashboardQuery, now: datetime) -> dict[str, object]:
        return {
            "project_id": query.scope.project_id,
            "region_code": query.scope.region_code,
            "range_start": query.window.start,
            "range_end": query.window.end,
            "timezone": query.window.timezone,
            "as_of": now,
        }

    @staticmethod
    def _validate_page_limit(limit: int) -> None:
        if limit < 1 or limit > 100:
            raise problem(
                status=422,
                code="DASHBOARD_PAGE_LIMIT_INVALID",
                title="Invalid dashboard page limit",
                detail="limit must be between 1 and 100.",
            )

    @staticmethod
    def _combined_status(states: tuple[DashboardSectionState, ...]) -> DashboardSectionStatus:
        statuses = {state.status for state in states}
        if len(statuses) == 1:
            return next(iter(statuses))
        if DashboardSectionStatus.ERROR in statuses or DashboardSectionStatus.PARTIAL in statuses:
            return DashboardSectionStatus.PARTIAL
        if DashboardSectionStatus.BLOCKED in statuses:
            return DashboardSectionStatus.BLOCKED
        if DashboardSectionStatus.STALE in statuses:
            return DashboardSectionStatus.STALE
        return DashboardSectionStatus.READY


def _task_list_item(fact: TaskStatusTaskFact) -> TaskStatusListItem:
    target = (
        None
        if fact.target_package_count is None and fact.target_duration_seconds is None
        else TaskStatusTarget(
            package_count=fact.target_package_count,
            duration_seconds=fact.target_duration_seconds,
        )
    )
    return TaskStatusListItem(
        task_id=fact.task_id,
        task_code=fact.task_code.strip(),
        name=fact.name,
        lifecycle=TaskLifecycle(fact.lifecycle),
        target=target,
        registered_count=fact.registered_count,
        received_count=fact.received_count,
        device_progress=TaskDeviceProgress(
            source="DEVICE_ATTESTED_FACT",
            captured_count=fact.device_captured_count,
            saved_count=fact.device_saved_count,
            confirmed_duration_seconds=fact.confirmed_duration_seconds,
        ),
    )


def _processing_interruption(fact: TaskStatusPackageFact) -> str | None:
    return processing_interruption(
        qc_status=fact.qc_status,
        lance_ready=fact.lance_ready,
        workflow_status=fact.workflow_status,
        workflow_error_code=fact.workflow_error_code,
        alignment_status=fact.alignment_status,
        source_episode_status=fact.source_episode_status,
        source_processing_status=fact.source_processing_status,
        resolution_status=fact.resolution_status,
        duplicate_of_rollout_id=fact.duplicate_of_rollout_id,
    )


def _is_reprocessing_conflict(fact: TaskStatusPackageFact) -> bool:
    return _processing_interruption(fact) == "PROCESSING_CONFLICT"


def _package_main_state(fact: TaskStatusPackageFact) -> TaskPackageMainState:
    if fact.resolution_status == "DISCARDED":
        return TaskPackageMainState.DISCARDED
    if fact.duplicate_of_rollout_id is not None:
        return TaskPackageMainState.DUPLICATE
    if fact.published:
        return TaskPackageMainState.PUBLISHED
    if fact.annotation_task_id is not None:
        if fact.annotation_status == "APPROVED":
            return TaskPackageMainState.PUBLISH_PENDING
        if fact.annotation_status == "SUBMITTED":
            return TaskPackageMainState.REVIEW_PENDING
        return TaskPackageMainState.ANNOTATING
    if fact.lance_ready:
        return TaskPackageMainState.STANDARDIZED_READY
    if fact.qc_status == "RISK":
        return TaskPackageMainState.QC_RISK
    if fact.qc_status == "REJECT":
        return TaskPackageMainState.QC_REJECT
    if _is_reprocessing_conflict(fact):
        return TaskPackageMainState.REPROCESSING_CONFLICT
    if _processing_interruption(fact) == "RESUME_REQUIRED":
        return TaskPackageMainState.PROCESSING_RESUME_REQUIRED
    if fact.resolution_status in {"PENDING", "RUNNING"} or (
        fact.source_episode_status == "PENDING"
        and fact.source_processing_status in {"PENDING", "RUNNING"}
    ):
        return TaskPackageMainState.STANDARDIZATION_WAITING
    if not fact.technical_state_available and fact.qc_status == "PASS":
        return TaskPackageMainState.UNKNOWN
    if fact.workflow_status in {"PENDING", "RUNNING"}:
        stage = (fact.workflow_stage or "").lower()
        if "lance" in stage:
            return TaskPackageMainState.LANCE_WRITING
        if "align" in stage:
            return TaskPackageMainState.ALIGNING
    if fact.workflow_status == "TECHNICAL_FAILED":
        stage = (fact.workflow_stage or "").lower()
        error = (fact.workflow_error_code or "").upper()
        if "lance" in stage or any(
            token in error for token in ("LANCE", "CATALOG", "DATASET_WRITER")
        ):
            return TaskPackageMainState.LANCE_FAILED
        if "align" in stage or fact.qc_status == "PASS":
            return TaskPackageMainState.ALIGNMENT_FAILED
    if fact.alignment_status == "ABORTED":
        return TaskPackageMainState.ALIGNMENT_FAILED
    if fact.alignment_status == "WRITING":
        return TaskPackageMainState.ALIGNING
    if fact.alignment_status == "READY":
        return TaskPackageMainState.LANCE_WRITING
    if fact.qc_status == "PASS":
        return TaskPackageMainState.STANDARDIZATION_WAITING
    if fact.verification_status == "REJECTED":
        return TaskPackageMainState.VALIDATION_FAILED
    if fact.verification_status == "RAW_VERIFIED":
        return TaskPackageMainState.QC_WAITING
    if fact.raw_committed:
        if fact.workflow_status in {"PENDING", "RUNNING"} and fact.workflow_stage in {
            "manifest",
            "verification",
        }:
            return TaskPackageMainState.VALIDATION_RUNNING
        return TaskPackageMainState.RAW_RECEIVED
    state = fact.upload_status or fact.rollout_status
    return {
        "UPLOADING": TaskPackageMainState.UPLOADING,
        "PAUSED": TaskPackageMainState.UPLOAD_PAUSED,
        "FAILED": TaskPackageMainState.UPLOAD_FAILED,
        "CANCELLED": TaskPackageMainState.CANCELLED,
    }.get(state, TaskPackageMainState.REGISTERED)


def _task_attainment(task: TaskStatusListItem) -> TaskAttainment:
    target = task.target
    if target is None:
        return TaskAttainment.NOT_CONFIGURED
    progress: list[int] = []
    if target.package_count is not None:
        progress.append(
            1
            if task.received_count > target.package_count
            else 0
            if task.received_count == target.package_count
            else -1
        )
    if target.duration_seconds is not None:
        duration = task.device_progress.confirmed_duration_seconds
        progress.append(
            1
            if duration > target.duration_seconds
            else 0
            if duration == target.duration_seconds
            else -1
        )
    if not progress:
        return TaskAttainment.UNKNOWN
    if any(value < 0 for value in progress):
        return TaskAttainment.IN_PROGRESS
    return (
        TaskAttainment.EXCEEDED if any(value > 0 for value in progress) else TaskAttainment.ATTAINED
    )


def _increment(
    counts: dict[TaskProcessingStage, dict[str, int]],
    stage: TaskProcessingStage,
    bucket: str,
) -> None:
    counts[stage][bucket] += 1


def _stage_counts(packages: tuple[TaskStatusPackageFact, ...]) -> tuple[TaskStageCounts, ...]:
    buckets = (
        "waiting",
        "running",
        "succeeded",
        "risk",
        "isolated",
        "blocked",
        "failed",
        "unavailable",
    )
    counts = {stage: {bucket: 0 for bucket in buckets} for stage in TASK_PROCESSING_STAGES}
    for fact in packages:
        main = _package_main_state(fact)
        duplicate = fact.duplicate_of_rollout_id is not None
        downstream_isolated = (
            duplicate
            or _is_reprocessing_conflict(fact)
            or fact.qc_status in {"RISK", "REJECT"}
            or fact.resolution_status == "DISCARDED"
        )
        _increment(counts, TaskProcessingStage.TASK_EXECUTION, "succeeded")
        if fact.raw_committed:
            _increment(counts, TaskProcessingStage.PACKAGE_UPLOAD, "succeeded")
            _increment(counts, TaskProcessingStage.RAW_RECEIPT, "succeeded")
        else:
            upload_bucket = (
                "failed"
                if main is TaskPackageMainState.UPLOAD_FAILED
                else "blocked"
                if main in {TaskPackageMainState.UPLOAD_PAUSED, TaskPackageMainState.CANCELLED}
                else "running"
                if main is TaskPackageMainState.UPLOADING
                else "waiting"
            )
            _increment(counts, TaskProcessingStage.PACKAGE_UPLOAD, upload_bucket)
            _increment(counts, TaskProcessingStage.RAW_RECEIPT, "waiting")

        if fact.duplicate_of_rollout_id is not None:
            validation_bucket = "isolated"
        elif not fact.raw_committed:
            validation_bucket = "waiting"
        elif fact.verification_status == "REJECTED":
            validation_bucket = "failed"
        elif fact.qc_status in {"RISK", "REJECT"}:
            validation_bucket = "isolated"
        elif fact.qc_status == "PASS":
            validation_bucket = "succeeded"
        elif main is TaskPackageMainState.VALIDATION_RUNNING:
            validation_bucket = "running"
        else:
            validation_bucket = "waiting"
        _increment(counts, TaskProcessingStage.AUTOMATIC_VALIDATION, validation_bucket)

        if downstream_isolated:
            standard_bucket = "isolated"
        elif not fact.technical_state_available and fact.qc_status == "PASS":
            standard_bucket = "unavailable"
        elif (
            fact.verification_status == "REJECTED"
            or main is TaskPackageMainState.PROCESSING_RESUME_REQUIRED
        ):
            standard_bucket = "blocked"
        elif main in {TaskPackageMainState.ALIGNMENT_FAILED, TaskPackageMainState.LANCE_FAILED}:
            standard_bucket = "failed"
        elif main in {TaskPackageMainState.ALIGNING, TaskPackageMainState.LANCE_WRITING}:
            standard_bucket = "running"
        elif fact.lance_ready:
            standard_bucket = "succeeded"
        else:
            standard_bucket = "waiting"
        _increment(counts, TaskProcessingStage.STANDARDIZATION, standard_bucket)

        if downstream_isolated:
            annotation_bucket = "isolated"
        elif fact.annotation_task_id is None:
            annotation_bucket = "waiting"
        elif fact.annotation_status == "APPROVED" or fact.annotation_status == "SUBMITTED":
            annotation_bucket = "succeeded"
        else:
            annotation_bucket = "running"
        _increment(counts, TaskProcessingStage.ANNOTATION, annotation_bucket)
        if downstream_isolated:
            review_bucket = "isolated"
        elif fact.annotation_status == "SUBMITTED":
            review_bucket = "waiting"
        elif fact.annotation_status == "APPROVED":
            review_bucket = "succeeded"
        elif fact.review_decision in {"NEEDS_REVISION", "REJECT"}:
            review_bucket = "blocked"
        else:
            review_bucket = "waiting"
        _increment(counts, TaskProcessingStage.REVIEW, review_bucket)
        publication_bucket = (
            "isolated" if downstream_isolated else "succeeded" if fact.published else "waiting"
        )
        _increment(counts, TaskProcessingStage.PUBLICATION, publication_bucket)
    return tuple(TaskStageCounts(stage=stage, **counts[stage]) for stage in TASK_PROCESSING_STAGES)


def _qc_counts(
    packages: tuple[TaskStatusPackageFact, ...],
    unavailable_sources: tuple[str, ...],
) -> TaskQcCounts:
    received = tuple(item for item in packages if item.raw_committed)
    canonical = tuple(item for item in received if item.duplicate_of_rollout_id is None)
    qc_eligible = tuple(item for item in canonical if item.verification_status == "RAW_VERIFIED")
    return TaskQcCounts(
        waiting=sum(item.qc_status is None for item in qc_eligible),
        passed=sum(item.qc_status == "PASS" for item in canonical),
        risk=sum(item.qc_status == "RISK" for item in canonical),
        rejected=sum(item.qc_status == "REJECT" for item in canonical),
        duplicate=sum(item.duplicate_of_rollout_id is not None for item in received),
        reprocessing_conflicts=sum(_is_reprocessing_conflict(item) for item in packages),
        discarded=sum(item.resolution_status == "DISCARDED" for item in packages),
        unavailable=(len(qc_eligible) if "quality_rollout_summaries" in unavailable_sources else 0),
    )


def _task_pipeline_status(
    facts: TaskStatusProjectionFacts,
    *,
    task_count: int,
) -> TaskPipelineStatus:
    return TaskPipelineStatus(
        task_count=task_count,
        package_count=len(facts.packages),
        qc=_qc_counts(facts.packages, facts.unavailable_sources),
        stages=_stage_counts(facts.packages),
        issues=_data_issues(facts.packages),
        unavailable_sources=facts.unavailable_sources,
    )


def _blockers(
    packages: tuple[TaskStatusPackageFact, ...], task_id: str
) -> tuple[TaskStatusBlocker, ...]:
    grouped: dict[tuple[str, str, str, bool], int] = {}
    for fact in packages:
        if fact.resolution_status in {"DISCARDED", "PENDING", "RUNNING", "FAILED"}:
            continue
        if fact.duplicate_of_rollout_id is not None:
            continue
        main = _package_main_state(fact)
        value: tuple[str, str, str, bool] | None = None
        if main is TaskPackageMainState.UPLOAD_FAILED:
            value = (fact.upload_failure_code or "UPLOAD_FAILED", "上传失败", "UPLOAD", True)
        elif main is TaskPackageMainState.VALIDATION_FAILED:
            value = (
                fact.verification_reason_code or "RAW_STRUCTURE_REJECTED",
                "Raw结构验证失败",
                "RAW_VALIDATION",
                False,
            )
        elif main is TaskPackageMainState.ALIGNMENT_FAILED:
            value = (
                fact.workflow_error_code or "ALIGNMENT_TECHNICAL_FAILED",
                "30 Hz对齐技术失败",
                "TECHNICAL",
                True,
            )
        elif main is TaskPackageMainState.LANCE_FAILED:
            value = (
                fact.workflow_error_code or "LANCE_WRITE_FAILED",
                "Lance写入技术失败",
                "TECHNICAL",
                True,
            )
        elif main is TaskPackageMainState.PROCESSING_RESUME_REQUIRED:
            value = ("PROCESSING_RESUME_REQUIRED", "质检已通过，待继续处理", "TECHNICAL", True)
        if value is not None:
            grouped[value] = grouped.get(value, 0) + 1
    task_query = quote(task_id, safe="")
    links = {
        "UPLOAD": f"/ingest/uploads/records?task_id={task_query}&status=FAILED",
        "RAW_VALIDATION": f"/ingest/uploads/records?task_id={task_query}",
        "QUALITY": f"/ingest/uploads/records?task_id={task_query}",
        "TECHNICAL": f"/ingest/uploads/records?task_id={task_query}",
        "REVIEW": "/annotations/tag-review",
    }
    return tuple(
        TaskStatusBlocker(
            reason_code=code,
            label=label,
            category=cast(
                Literal["UPLOAD", "RAW_VALIDATION", "QUALITY", "TECHNICAL", "REVIEW"],
                category,
            ),
            count=count,
            retryable=retryable,
            deep_link=links[category],
        )
        for (code, label, category, retryable), count in sorted(
            grouped.items(), key=lambda item: (-item[1], item[0][0])
        )
    )


def _data_issues(packages: tuple[TaskStatusPackageFact, ...]) -> tuple[TaskDataIssue, ...]:
    issues: list[TaskDataIssue] = []
    for fact in packages:
        if fact.resolution_status == "DISCARDED":
            continue
        category: Literal[
            "DUPLICATE", "QUALITY", "TECHNICAL", "PROCESSING_CONFLICT", "RESUME_REQUIRED"
        ]
        stage = TaskProcessingStage.AUTOMATIC_VALIDATION
        if fact.duplicate_of_rollout_id is not None:
            category, code, label = "DUPLICATE", "DUPLICATE_SOURCE_EPISODE", "重复上传"
            description = "此数据包与已有数据重复，已保留原数据，不重复入库。"
        elif _is_reprocessing_conflict(fact):
            category, code, label = (
                "PROCESSING_CONFLICT",
                "ALIGNMENT_ATTEMPT_IMMUTABLE",
                "处理结果冲突",
            )
            stage = TaskProcessingStage.STANDARDIZATION
            description = (
                "同一原始数据已有对齐结果，本次重复处理生成的结果与历史结果不一致，"
                "为保护历史数据已停止写入。此数据包尚未入库，需核对处理版本后重新处理。"
            )
        elif _processing_interruption(fact) == "RESUME_REQUIRED":
            category, code, label = (
                "RESUME_REQUIRED",
                "PROCESSING_RESUME_REQUIRED",
                "质检已通过，待继续处理",
            )
            stage = TaskProcessingStage.STANDARDIZATION
            description = (
                "此前处理已停止，当前质检结果已通过，但尚未完成标准化入库。"
                "质检结果更新不会自动恢复已停止的任务，请重试未完成处理。"
            )
        elif fact.raw_committed and fact.qc_status in {"RISK", "REJECT"}:
            category, code = "QUALITY", f"QC_{fact.qc_status}"
            label = "质量风险" if fact.qc_status == "RISK" else "质检拒绝"
            description = "此数据包未通过自动质检，已暂停后续处理，可查看检测结果和原始数据。"
        else:
            blockers = _blockers((fact,), fact.task_id)
            if not blockers:
                continue
            blocker = blockers[0]
            category, code, label = "TECHNICAL", blocker.reason_code, blocker.label
            stage = {
                "UPLOAD": TaskProcessingStage.PACKAGE_UPLOAD,
                "RAW_VALIDATION": TaskProcessingStage.AUTOMATIC_VALIDATION,
                "TECHNICAL": TaskProcessingStage.STANDARDIZATION,
            }[blocker.category]
            description = f"此数据包在{label}处停止，请核对原始数据及错误码。"
        issues.append(
            TaskDataIssue(
                task_id=fact.task_id,
                rollout_id=fact.rollout_id,
                data_package_id=fact.data_package_id,
                category=category,
                stage=stage,
                reason_code=code,
                label=label,
                description=description,
                duplicate_of_rollout_id=fact.duplicate_of_rollout_id,
                source_episode_index=fact.source_episode_index,
                source_import_id=fact.source_import_id,
                qc_status=fact.qc_status,
                lance_ready=fact.lance_ready,
                alignment_attempt_id=fact.alignment_attempt_id,
                findings=tuple(
                    finding
                    for finding in fact.quality_findings
                    if finding.severity.upper() not in {"INFO", "PASS"}
                )
                if category == "QUALITY"
                else (),
            )
        )
    return tuple(issues)


def _actions(
    task: TaskStatusListItem,
    blockers: tuple[TaskStatusBlocker, ...],
    packages: tuple[TaskStatusPackageFact, ...],
    attainment: TaskAttainment,
) -> tuple[TaskStatusAction, ...]:
    task_query = quote(task.task_id, safe="")
    actions: list[TaskStatusAction] = []
    categories = {item.category for item in blockers}
    quality_problem_count = sum(
        item.duplicate_of_rollout_id is not None
        or _is_reprocessing_conflict(item)
        or item.qc_status in {"RISK", "REJECT"}
        for item in packages
    )
    if "UPLOAD" in categories:
        actions.append(
            TaskStatusAction(
                action="VIEW_UPLOAD_FAILURES",
                label="查看上传失败",
                deep_link=f"/ingest/uploads/records?task_id={task_query}&status=FAILED",
            )
        )
    if "RAW_VALIDATION" in categories:
        actions.append(
            TaskStatusAction(
                action="VIEW_RAW_DIAGNOSTICS",
                label="查看Raw诊断",
                deep_link=f"/ingest/uploads/records?task_id={task_query}",
            )
        )
    if quality_problem_count:
        actions.append(
            TaskStatusAction(
                action="VIEW_QC_ANOMALIES",
                label="查看问题数据",
                deep_link="/manual/issues?source=AUTO_QC",
            )
        )
    if any(item.category == "TECHNICAL" and item.retryable for item in blockers):
        actions.append(
            TaskStatusAction(
                action="RETRY_TECHNICAL_PROCESSING",
                label="重试技术处理",
                deep_link=f"/ingest/uploads/records?task_id={task_query}",
            )
        )
    annotation = next(
        (
            item.annotation_task_id
            for item in packages
            if item.annotation_task_id and item.annotation_status != "APPROVED"
        ),
        None,
    )
    if annotation is not None:
        actions.append(
            TaskStatusAction(
                action="ENTER_ANNOTATION",
                label="进入标注",
                deep_link=f"/annotations/tasks/{quote(annotation, safe='')}",
            )
        )
    if any(item.annotation_status == "SUBMITTED" for item in packages):
        actions.append(
            TaskStatusAction(
                action="VIEW_PENDING_REVIEW",
                label="查看待审核",
                deep_link="/annotations/tag-review",
            )
        )
    if (
        attainment in {TaskAttainment.ATTAINED, TaskAttainment.EXCEEDED}
        and task.lifecycle is TaskLifecycle.ACTIVE
    ):
        actions.append(
            TaskStatusAction(
                action="CLOSE_TASK",
                label="查看任务并关闭",
                deep_link=f"/collection-tasks?task_id={task_query}",
            )
        )
    if not actions:
        actions.append(
            TaskStatusAction(
                action="VIEW_TASK",
                label="查看采集任务",
                deep_link=f"/collection-tasks?task_id={task_query}",
            )
        )
    return tuple(actions[:3])


def _selected_task_status(
    task: TaskStatusListItem,
    facts: TaskStatusProjectionFacts,
) -> SelectedTaskStatus:
    packages = tuple(item for item in facts.packages if item.task_id == task.task_id)
    canonical_packages = tuple(item for item in packages if item.duplicate_of_rollout_id is None)
    main_counts: dict[TaskPackageMainState, int] = {}
    for fact in packages:
        state = _package_main_state(fact)
        main_counts[state] = main_counts.get(state, 0) + 1
    qc = _qc_counts(packages, facts.unavailable_sources)
    standardization = TaskStandardizationCounts(
        resume_required=sum(
            _package_main_state(item) is TaskPackageMainState.PROCESSING_RESUME_REQUIRED
            for item in canonical_packages
        ),
        waiting=sum(
            _package_main_state(item) is TaskPackageMainState.STANDARDIZATION_WAITING
            for item in canonical_packages
        ),
        aligning=sum(
            _package_main_state(item) is TaskPackageMainState.ALIGNING
            for item in canonical_packages
        ),
        alignment_failed=sum(
            _package_main_state(item) is TaskPackageMainState.ALIGNMENT_FAILED
            for item in canonical_packages
        ),
        lance_writing=sum(
            _package_main_state(item) is TaskPackageMainState.LANCE_WRITING
            for item in canonical_packages
        ),
        lance_failed=sum(
            _package_main_state(item) is TaskPackageMainState.LANCE_FAILED
            for item in canonical_packages
        ),
        ready=sum(item.lance_ready for item in canonical_packages),
        isolated_by_quality=sum(
            item.duplicate_of_rollout_id is not None or item.qc_status in {"RISK", "REJECT"}
            for item in packages
        ),
        unavailable=len(canonical_packages)
        if any(
            source in facts.unavailable_sources
            for source in (
                "aligned_fragment_attempts",
                "lance_rollout_lineage",
                "workflow_execution_state",
            )
        )
        else 0,
    )
    blockers = _blockers(packages, task.task_id)
    attainment = _task_attainment(task)
    stages = _stage_counts(packages)
    active_stage = TaskProcessingStage.TASK_EXECUTION
    for stage in TASK_PROCESSING_STAGES:
        item = next(value for value in stages if value.stage is stage)
        if item.waiting or item.running or item.blocked or item.failed:
            active_stage = stage
            break
    labels = {
        TaskProcessingStage.TASK_EXECUTION: "任务执行",
        TaskProcessingStage.PACKAGE_UPLOAD: "数据包登记与上传",
        TaskProcessingStage.RAW_RECEIPT: "Raw接收",
        TaskProcessingStage.AUTOMATIC_VALIDATION: "自动校验",
        TaskProcessingStage.STANDARDIZATION: "标准化入库",
        TaskProcessingStage.ANNOTATION: "标注",
        TaskProcessingStage.REVIEW: "审核",
        TaskProcessingStage.PUBLICATION: "发布",
    }
    next_step = (
        blockers[0].label
        if blockers
        else (
            "任务已达标，生命周期仍为ACTIVE，请确认后关闭任务。"
            if attainment in {TaskAttainment.ATTAINED, TaskAttainment.EXCEEDED}
            and task.lifecycle is TaskLifecycle.ACTIVE
            else f"继续处理“{labels[active_stage]}”阶段的等待项。"
        )
    )
    return SelectedTaskStatus(
        task=task,
        attainment=attainment,
        current_stage=active_stage,
        current_stage_label=labels[active_stage],
        next_step=next_step,
        qc=qc,
        standardization=standardization,
        stages=stages,
        main_state_counts=main_counts,
        blocker_count=sum(item.count for item in blockers),
        blockers=blockers,
        actions=_actions(task, blockers, packages, attainment),
        unavailable_sources=facts.unavailable_sources,
    )
