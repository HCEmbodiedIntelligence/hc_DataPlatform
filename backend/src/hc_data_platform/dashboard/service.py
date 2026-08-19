from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
from typing import Protocol
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from hc_data_platform.core.errors import problem
from hc_data_platform.core.pagination import CursorCodec, PageInfo
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.capabilities import (
    CAPABILITY_ANNOTATION_REVIEW,
    CAPABILITY_DATASETS_PUBLISH,
    CAPABILITY_DATASETS_READ,
    CAPABILITY_INGEST_UPLOAD,
)

from .models import (
    PENDING_ITEM_TYPES,
    DashboardActivityEvent,
    DashboardActivityEventType,
    DashboardActivityPage,
    DashboardActivityResponse,
    DashboardCoverageResponse,
    DashboardPendingItem,
    DashboardPendingItemsPage,
    DashboardPendingItemsResponse,
    DashboardPendingItemType,
    DashboardPublishedRegionState,
    DashboardResourceType,
    DashboardSectionError,
    DashboardSectionState,
    DashboardSectionStatus,
    DashboardSignalPipelineState,
    DashboardSnapshotResponse,
    DashboardSnapshotSections,
    DashboardTargetResource,
)
from .repository import (
    DashboardBusinessEventFact,
    DashboardPendingFact,
    DashboardQueryAudit,
    DashboardRepository,
    DashboardScope,
    DashboardWindow,
    InMemoryDashboardRepository,
    PublicationLineageSummary,
)

MAX_QUERY_WINDOW = timedelta(days=31)


class DashboardSectionKey(str, Enum):
    SIGNAL_PIPELINE = "signal_pipeline"
    EPISODES = "episodes"
    WORK = "work"
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
    DashboardSectionKey.SIGNAL_PIPELINE: (
        "P01_SIGNAL_FORMULA_UNCONFIRMED",
        "Signal-stage counting identities and qualifying states are not fully defined.",
    ),
    DashboardSectionKey.EPISODES: (
        "P01_EPISODE_DEFINITION_UNCONFIRMED",
        "Uploaded, validated, and viewable episode identities need product confirmation.",
    ),
    DashboardSectionKey.WORK: (
        "P01_WORK_CATALOG_UNCONFIRMED",
        "The dashboard work catalog and counted states remain unconfirmed.",
    ),
    DashboardSectionKey.COVERAGE: (
        "P01_COVERAGE_DENOMINATOR_MISSING",
        "No versioned collection plan, robot group, task catalog, or denominator exists.",
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
    DashboardPendingItemType.UPLOAD_FAILED: (CAPABILITY_INGEST_UPLOAD,),
    DashboardPendingItemType.QC_ANOMALY: (
        CAPABILITY_DATASETS_READ,
        CAPABILITY_INGEST_UPLOAD,
    ),
    DashboardPendingItemType.TAG_REVIEW_PENDING: (CAPABILITY_ANNOTATION_REVIEW,),
    DashboardPendingItemType.PUBLICATION_PENDING: (CAPABILITY_DATASETS_PUBLISH,),
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
            needs_product_confirmation=section
            in {
                DashboardSectionKey.SIGNAL_PIPELINE,
                DashboardSectionKey.EPISODES,
                DashboardSectionKey.WORK,
                DashboardSectionKey.COVERAGE,
            },
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

    def snapshot(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        range_start: datetime,
        range_end: datetime,
        timezone_name: str,
    ) -> DashboardSnapshotResponse:
        query = self._query(auth, project_id, region_code, range_start, range_end, timezone_name)
        now = self._begin(auth, "snapshot", query)
        published = self._published_region_state(
            self._repository.publication_lineage_summary(
                auth=auth,
                scope=query.scope,
                window=query.window,
            ),
            now,
        )
        signal = blocked_state(DashboardSectionKey.SIGNAL_PIPELINE)
        episodes = blocked_state(DashboardSectionKey.EPISODES)
        work = blocked_state(DashboardSectionKey.WORK)
        response = DashboardSnapshotResponse.model_validate(
            {
                **self._base(query, now),
                "sections": DashboardSnapshotSections(
                    signal_pipeline=DashboardSignalPipelineState(
                        **signal.model_dump(),
                        published_region=published,
                    ),
                    episodes=episodes,
                    work=work,
                ),
            }
        )
        self._audit(
            auth,
            query,
            "snapshot",
            self._combined_status((signal, episodes, work, published)),
            now,
        )
        return response

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
    def _published_region_state(
        summary: PublicationLineageSummary,
        now: datetime,
    ) -> DashboardPublishedRegionState:
        if not summary.available:
            return DashboardPublishedRegionState(
                status=DashboardSectionStatus.BLOCKED,
                error=DashboardSectionError(
                    code="P01_PUBLICATION_REGION_LINEAGE_MISSING",
                    message="Rollout-to-publication region lineage is not queryable.",
                    retryable=True,
                ),
            )
        if summary.unresolved_history_count and not summary.lineage_count:
            return DashboardPublishedRegionState(
                status=DashboardSectionStatus.BLOCKED,
                as_of=summary.latest_published_at,
                error=DashboardSectionError(
                    code="P01_PUBLICATION_REGION_HISTORY_UNRESOLVED",
                    message="Historical publications cannot be assigned to a certain region.",
                ),
                unresolved_history_count=summary.unresolved_history_count,
            )
        if summary.unresolved_history_count:
            return DashboardPublishedRegionState(
                status=DashboardSectionStatus.PARTIAL,
                as_of=summary.latest_published_at or now,
                error=DashboardSectionError(
                    code="P01_PUBLICATION_REGION_HISTORY_PARTIAL",
                    message="Counts include only certain lineage; some history is unassignable.",
                ),
                lineage_count=summary.lineage_count,
                publication_count=summary.publication_count,
                unresolved_history_count=summary.unresolved_history_count,
            )
        status = (
            DashboardSectionStatus.READY if summary.lineage_count else DashboardSectionStatus.EMPTY
        )
        return DashboardPublishedRegionState(
            status=status,
            as_of=summary.latest_published_at or now,
            lineage_count=summary.lineage_count,
            publication_count=summary.publication_count,
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
