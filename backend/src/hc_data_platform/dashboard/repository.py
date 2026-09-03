from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Protocol

from hc_data_platform.core.errors import problem
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.capabilities import CAPABILITY_DASHBOARD_READ
from hc_data_platform.security.scope import ScopeGuard

from .models import (
    DashboardActivityEventType,
    DashboardPendingItemType,
    DashboardPendingSeverity,
    DashboardResourceType,
)

DASHBOARD_CAPABILITY = CAPABILITY_DASHBOARD_READ


@dataclass(frozen=True, slots=True)
class DashboardScope:
    principal_id: str
    project_id: str
    region_code: str

    def __post_init__(self) -> None:
        if not self.principal_id or not self.project_id or not self.region_code:
            raise ValueError("dashboard scope requires principal, project, and region")


@dataclass(frozen=True, slots=True)
class DashboardWindow:
    start: datetime
    end: datetime
    timezone: str


@dataclass(frozen=True, slots=True)
class CommittedObjectFact:
    project_id: str
    region_code: str
    rollout_id: str
    data_package_id: str
    file_size: int
    committed_at: datetime


@dataclass(frozen=True, slots=True)
class CollectionObservationFact:
    project_id: str
    region_code: str
    rollout_id: str
    task_id: str
    robot_id: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class DashboardFactPage:
    items: tuple[CommittedObjectFact | CollectionObservationFact, ...]
    has_more: bool


@dataclass(frozen=True, slots=True)
class DashboardBusinessEventFact:
    project_id: str
    region_code: str
    event_type: DashboardActivityEventType
    source_id: str
    occurred_at: datetime
    source_state: str
    target_resource_type: DashboardResourceType
    target_resource_id: str
    target_resource_version: str | None = None

    @property
    def event_id(self) -> str:
        return f"{self.event_type.value.lower()}:{self.source_id}"

    @property
    def deduplication_key(self) -> str:
        return self.event_id


@dataclass(frozen=True, slots=True)
class DashboardBusinessEventPage:
    items: tuple[DashboardBusinessEventFact, ...]
    has_more: bool
    unavailable_sources: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DashboardPendingFact:
    project_id: str
    region_code: str
    item_type: DashboardPendingItemType
    source_id: str
    source_state: str
    severity: DashboardPendingSeverity
    severity_rank: int
    opened_at: datetime
    target_resource_type: DashboardResourceType
    target_resource_id: str
    target_resource_version: str | None = None

    @property
    def deduplication_key(self) -> str:
        return f"{self.item_type.value.lower()}:{self.source_id}"

    @property
    def stable_sort(self) -> tuple[int, datetime, str, str]:
        return (-self.severity_rank, self.opened_at, self.item_type.value, self.source_id)


@dataclass(frozen=True, slots=True)
class DashboardPendingFactPage:
    items: tuple[DashboardPendingFact, ...]
    has_more: bool
    unavailable_sources: tuple[DashboardPendingItemType, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskStatusTaskFact:
    task_id: str
    task_code: str
    name: str
    lifecycle: str
    target_package_count: int | None = None
    target_duration_seconds: float | None = None
    registered_count: int = 0
    received_count: int = 0
    device_captured_count: int = 0
    device_saved_count: int = 0
    confirmed_duration_seconds: float = 0


@dataclass(frozen=True, slots=True)
class TaskStatusPackageFact:
    task_id: str
    rollout_id: str
    data_package_id: str
    rollout_status: str
    duplicate_of_rollout_id: str | None = None
    upload_status: str | None = None
    upload_failure_code: str | None = None
    raw_committed: bool = False
    verification_status: str | None = None
    verification_reason_code: str | None = None
    qc_status: str | None = None
    alignment_status: str | None = None
    lance_ready: bool = False
    annotation_task_id: str | None = None
    annotation_status: str | None = None
    review_decision: str | None = None
    published: bool = False
    workflow_status: str | None = None
    workflow_stage: str | None = None
    workflow_error_code: str | None = None
    technical_state_available: bool = True


@dataclass(frozen=True, slots=True)
class TaskStatusProjectionFacts:
    tasks: tuple[TaskStatusTaskFact, ...] = ()
    packages: tuple[TaskStatusPackageFact, ...] = ()
    unavailable_sources: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DashboardQueryAudit:
    principal_id: str
    project_id: str
    region_code: str
    endpoint: str
    range_start: datetime
    range_end: datetime
    timezone: str
    result_status: str
    occurred_at: datetime


class DashboardRepository(Protocol):
    def enforce_scope(self, auth: AuthContext, scope: DashboardScope) -> None: ...

    def record_query(self, auth: AuthContext, audit: DashboardQueryAudit) -> None: ...

    def business_events(
        self,
        *,
        auth: AuthContext,
        scope: DashboardScope,
        window: DashboardWindow,
        limit: int,
        after: tuple[datetime, str, str] | None = None,
    ) -> DashboardBusinessEventPage: ...

    def pending_facts(
        self,
        *,
        auth: AuthContext,
        scope: DashboardScope,
        window: DashboardWindow,
        allowed_types: tuple[DashboardPendingItemType, ...],
        limit: int,
        after: tuple[int, datetime, str, str] | None = None,
    ) -> DashboardPendingFactPage: ...

    def task_status_projection(
        self,
        *,
        auth: AuthContext,
        scope: DashboardScope,
        task_id: str | None = None,
    ) -> TaskStatusProjectionFacts: ...

    def committed_objects(
        self,
        *,
        auth: AuthContext,
        scope: DashboardScope,
        window: DashboardWindow,
        limit: int,
        after: tuple[datetime, str] | None = None,
    ) -> DashboardFactPage: ...

    def collection_observations(
        self,
        *,
        auth: AuthContext,
        scope: DashboardScope,
        window: DashboardWindow,
        limit: int,
        after: tuple[datetime, str] | None = None,
    ) -> DashboardFactPage: ...


def enforce_dashboard_scope(auth: AuthContext, scope: DashboardScope) -> None:
    if scope.principal_id != auth.subject_id:
        raise problem(
            status=403,
            code="DASHBOARD_PRINCIPAL_SCOPE_DENIED",
            title="Dashboard scope denied",
            detail="The requested dashboard scope is not available to this principal.",
        )
    ScopeGuard.require(auth, scope.project_id, scope.region_code)
    auth.require_capability(DASHBOARD_CAPABILITY, scope.project_id)


class InMemoryDashboardRepository:
    """Deterministic fake whose filtering mirrors the PostgreSQL adapter boundary."""

    def __init__(
        self,
        *,
        committed_objects: tuple[CommittedObjectFact, ...] = (),
        collection_observations: tuple[CollectionObservationFact, ...] = (),
        business_events: tuple[DashboardBusinessEventFact, ...] = (),
        pending_facts: tuple[DashboardPendingFact, ...] = (),
        task_status_projection: TaskStatusProjectionFacts | None = None,
        unavailable_activity_sources: tuple[str, ...] = (),
        unavailable_pending_sources: tuple[DashboardPendingItemType, ...] = (),
    ) -> None:
        self._committed_objects = committed_objects
        self._collection_observations = collection_observations
        self._business_events = business_events
        self._pending_facts = pending_facts
        self._task_status_projection = task_status_projection or TaskStatusProjectionFacts()
        self._unavailable_activity_sources = unavailable_activity_sources
        self._unavailable_pending_sources = unavailable_pending_sources
        self.audits: list[DashboardQueryAudit] = []
        self._lock = RLock()

    def enforce_scope(self, auth: AuthContext, scope: DashboardScope) -> None:
        enforce_dashboard_scope(auth, scope)

    def record_query(self, auth: AuthContext, audit: DashboardQueryAudit) -> None:
        scope = DashboardScope(audit.principal_id, audit.project_id, audit.region_code)
        self.enforce_scope(auth, scope)
        with self._lock:
            self.audits.append(audit)

    def business_events(
        self,
        *,
        auth: AuthContext,
        scope: DashboardScope,
        window: DashboardWindow,
        limit: int,
        after: tuple[datetime, str, str] | None = None,
    ) -> DashboardBusinessEventPage:
        self.enforce_scope(auth, scope)
        _validate_limit(limit)
        values = (
            item
            for item in self._business_events
            if item.project_id == scope.project_id
            and item.region_code == scope.region_code
            and window.start <= item.occurred_at < window.end
            and (after is None or (item.occurred_at, item.event_type.value, item.source_id) < after)
        )
        ordered = sorted(
            values,
            key=lambda item: (item.occurred_at, item.event_type.value, item.source_id),
            reverse=True,
        )
        return DashboardBusinessEventPage(
            tuple(ordered[:limit]),
            len(ordered) > limit,
            self._unavailable_activity_sources,
        )

    def pending_facts(
        self,
        *,
        auth: AuthContext,
        scope: DashboardScope,
        window: DashboardWindow,
        allowed_types: tuple[DashboardPendingItemType, ...],
        limit: int,
        after: tuple[int, datetime, str, str] | None = None,
    ) -> DashboardPendingFactPage:
        self.enforce_scope(auth, scope)
        _validate_limit(limit)
        allowed = set(allowed_types)
        after_sort = None
        if after is not None:
            rank, opened_at, item_type, source_id = after
            after_sort = (-rank, opened_at, item_type, source_id)
        deduplicated: dict[tuple[DashboardPendingItemType, str], DashboardPendingFact] = {}
        for item in self._pending_facts:
            if (
                item.project_id != scope.project_id
                or item.region_code != scope.region_code
                or item.item_type not in allowed
                or not _is_pending_state(item.item_type, item.source_state)
                or not (window.start <= item.opened_at < window.end)
                or (after_sort is not None and item.stable_sort <= after_sort)
            ):
                continue
            deduplicated[(item.item_type, item.source_id)] = item
        ordered = sorted(deduplicated.values(), key=lambda item: item.stable_sort)
        unavailable = tuple(
            source for source in self._unavailable_pending_sources if source in allowed
        )
        return DashboardPendingFactPage(
            tuple(ordered[:limit]),
            len(ordered) > limit,
            unavailable,
        )

    def task_status_projection(
        self,
        *,
        auth: AuthContext,
        scope: DashboardScope,
        task_id: str | None = None,
    ) -> TaskStatusProjectionFacts:
        self.enforce_scope(auth, scope)
        if task_id is None:
            return self._task_status_projection
        return TaskStatusProjectionFacts(
            tasks=self._task_status_projection.tasks,
            packages=tuple(
                item for item in self._task_status_projection.packages if item.task_id == task_id
            ),
            unavailable_sources=self._task_status_projection.unavailable_sources,
        )

    def committed_objects(
        self,
        *,
        auth: AuthContext,
        scope: DashboardScope,
        window: DashboardWindow,
        limit: int,
        after: tuple[datetime, str] | None = None,
    ) -> DashboardFactPage:
        self.enforce_scope(auth, scope)
        _validate_limit(limit)
        values = (
            item
            for item in self._committed_objects
            if item.project_id == scope.project_id
            and item.region_code == scope.region_code
            and window.start <= item.committed_at < window.end
            and (after is None or (item.committed_at, item.rollout_id) < after)
        )
        ordered = sorted(
            values,
            key=lambda item: (item.committed_at, item.rollout_id),
            reverse=True,
        )
        return DashboardFactPage(tuple(ordered[:limit]), len(ordered) > limit)

    def collection_observations(
        self,
        *,
        auth: AuthContext,
        scope: DashboardScope,
        window: DashboardWindow,
        limit: int,
        after: tuple[datetime, str] | None = None,
    ) -> DashboardFactPage:
        self.enforce_scope(auth, scope)
        _validate_limit(limit)
        values = (
            item
            for item in self._collection_observations
            if item.project_id == scope.project_id
            and item.region_code == scope.region_code
            and window.start <= item.observed_at < window.end
            and (after is None or (item.observed_at, item.rollout_id) < after)
        )
        ordered = sorted(values, key=lambda item: (item.observed_at, item.rollout_id), reverse=True)
        return DashboardFactPage(tuple(ordered[:limit]), len(ordered) > limit)


def _validate_limit(limit: int) -> None:
    if limit < 1 or limit > 100:
        raise ValueError("dashboard repository limit must be between 1 and 100")


def _is_pending_state(item_type: DashboardPendingItemType, source_state: str) -> bool:
    states = {
        DashboardPendingItemType.UPLOAD_FAILED: {"FAILED"},
        DashboardPendingItemType.QC_ANOMALY: {"RISK", "REJECT"},
        DashboardPendingItemType.TAG_REVIEW_PENDING: {"SUBMITTED"},
        DashboardPendingItemType.PUBLICATION_PENDING: {"APPROVED"},
    }
    return source_state in states[item_type]
