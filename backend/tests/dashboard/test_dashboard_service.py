from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.dashboard.models import (
    DashboardActivityEventType,
    DashboardPendingItemType,
    DashboardPendingSeverity,
    DashboardResourceType,
    DashboardSectionError,
    DashboardSectionState,
    DashboardSectionStatus,
)
from hc_data_platform.dashboard.repository import (
    CollectionObservationFact,
    CommittedObjectFact,
    DashboardBusinessEventFact,
    DashboardPendingFact,
    DashboardScope,
    DashboardWindow,
    InMemoryDashboardRepository,
)
from hc_data_platform.dashboard.service import (
    DashboardCursorCodec,
    DashboardQuery,
    DashboardService,
    FactSourceProbe,
    FactSourceStatus,
    classify_section,
)
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.capabilities import (
    CAPABILITY_ANNOTATION_REVIEW,
    CAPABILITY_DASHBOARD_READ,
    CAPABILITY_DATASET_READ,
    CAPABILITY_DATASET_VERSION_PUBLISH,
    CAPABILITY_UPLOAD_MANAGE,
    CAPABILITY_UPLOAD_READ,
)

NOW = datetime(2026, 8, 18, 8, tzinfo=timezone.utc)
START = NOW - timedelta(hours=24)
END = NOW


def auth(
    principal: str = "user-a",
    *,
    project: str = "project-a",
    region: str = "cn-east",
    capabilities: tuple[str, ...] = (),
) -> AuthContext:
    return AuthContext(
        subject_id=principal,
        project_ids=frozenset({project}),
        region_codes=frozenset({region}),
        scope_pairs=frozenset({(project, region)}),
        scoped_capabilities=frozenset(
            (project, capability) for capability in (CAPABILITY_DASHBOARD_READ, *capabilities)
        ),
    )


def query(
    principal: str = "user-a",
    *,
    project: str = "project-a",
    region: str = "cn-east",
    start: datetime = START,
    timezone_name: str = "Asia/Shanghai",
) -> DashboardQuery:
    return DashboardQuery(
        DashboardScope(principal, project, region),
        DashboardWindow(start, END, timezone_name),
    )


def common(actor: AuthContext) -> dict[str, object]:
    return {
        "auth": actor,
        "project_id": "project-a",
        "region_code": "cn-east",
        "range_start": START,
        "range_end": END,
        "timezone_name": "Asia/Shanghai",
    }


def event(
    event_type: DashboardActivityEventType,
    source_id: str,
    seconds_ago: int,
    target_type: DashboardResourceType,
    target_id: str,
    target_version: str | None = None,
) -> DashboardBusinessEventFact:
    return DashboardBusinessEventFact(
        project_id="project-a",
        region_code="cn-east",
        event_type=event_type,
        source_id=source_id,
        occurred_at=NOW - timedelta(seconds=seconds_ago),
        source_state=(
            "PASS" if event_type is DashboardActivityEventType.QC_COMPLETED else "PUBLISHED"
        ),
        target_resource_type=target_type,
        target_resource_id=target_id,
        target_resource_version=target_version,
    )


def test_activity_is_stable_deduplicated_business_event_feed_with_cursor() -> None:
    facts = (
        event(
            DashboardActivityEventType.UPLOAD_COMMITTED,
            "package-a",
            1,
            DashboardResourceType.UPLOAD_SESSION,
            "session-a",
        ),
        event(
            DashboardActivityEventType.QC_COMPLETED,
            "report-a",
            2,
            DashboardResourceType.UPLOAD_SESSION,
            "session-a",
        ),
        event(
            DashboardActivityEventType.TAG_REVIEW_DECIDED,
            "review-a",
            3,
            DashboardResourceType.ANNOTATION_TASK,
            "task-a",
            "2",
        ),
    )
    service = DashboardService(
        InMemoryDashboardRepository(business_events=facts),
        cursor_secret="activity-test",
        clock=lambda: NOW,
    )
    actor = auth()
    first = service.activity(**common(actor), cursor=None, limit=2)
    assert first.activity.status is DashboardSectionStatus.READY
    assert [item.event_type for item in first.activity.items] == [
        DashboardActivityEventType.UPLOAD_COMMITTED,
        DashboardActivityEventType.QC_COMPLETED,
    ]
    assert all(item.event_id == item.deduplication_key for item in first.activity.items)
    assert first.activity.page_info is not None
    assert first.activity.page_info.has_next_page is True
    assert first.activity.page_info.end_cursor is not None

    second = service.activity(
        **common(actor),
        cursor=first.activity.page_info.end_cursor,
        limit=2,
    )
    assert [item.source_id for item in second.activity.items] == ["review-a"]
    assert second.activity.page_info is not None
    assert second.activity.page_info.has_previous_page is True
    assert {item.event_id for item in first.activity.items}.isdisjoint(
        item.event_id for item in second.activity.items
    )


def pending(
    item_type: DashboardPendingItemType,
    source_id: str,
    state: str,
    severity: DashboardPendingSeverity,
    rank: int,
    minutes_ago: int,
    target_type: DashboardResourceType,
    target_id: str,
    target_version: str | None = None,
) -> DashboardPendingFact:
    return DashboardPendingFact(
        project_id="project-a",
        region_code="cn-east",
        item_type=item_type,
        source_id=source_id,
        source_state=state,
        severity=severity,
        severity_rank=rank,
        opened_at=NOW - timedelta(minutes=minutes_ago),
        target_resource_type=target_type,
        target_resource_id=target_id,
        target_resource_version=target_version,
    )


def test_pending_four_sources_capability_intersection_sort_links_and_close_projection() -> None:
    facts = (
        pending(
            DashboardPendingItemType.UPLOAD_FAILED,
            "upload-open",
            "FAILED",
            DashboardPendingSeverity.HIGH,
            3,
            20,
            DashboardResourceType.UPLOAD_SESSION,
            "upload-open",
        ),
        pending(
            DashboardPendingItemType.UPLOAD_FAILED,
            "upload-closed",
            "RAW_COMMITTED",
            DashboardPendingSeverity.HIGH,
            3,
            30,
            DashboardResourceType.UPLOAD_SESSION,
            "upload-closed",
        ),
        pending(
            DashboardPendingItemType.QC_ANOMALY,
            "qc-reject",
            "REJECT",
            DashboardPendingSeverity.CRITICAL,
            4,
            5,
            DashboardResourceType.UPLOAD_SESSION,
            "upload-qc",
        ),
        pending(
            DashboardPendingItemType.TAG_REVIEW_PENDING,
            "task-review",
            "SUBMITTED",
            DashboardPendingSeverity.MEDIUM,
            2,
            40,
            DashboardResourceType.ANNOTATION_TASK,
            "task-review",
        ),
        pending(
            DashboardPendingItemType.PUBLICATION_PENDING,
            "task-publish",
            "APPROVED",
            DashboardPendingSeverity.LOW,
            1,
            60,
            DashboardResourceType.DATASET_VERSION,
            "dataset-a",
            "7",
        ),
    )
    repository = InMemoryDashboardRepository(pending_facts=facts)
    service = DashboardService(repository, cursor_secret="pending-test", clock=lambda: NOW)

    upload_only = service.pending_items(
        **common(auth(capabilities=(CAPABILITY_UPLOAD_MANAGE,))),
        cursor=None,
        limit=50,
    ).pending_items
    assert upload_only.authorized_source_types == (DashboardPendingItemType.UPLOAD_FAILED,)
    assert [item.source_id for item in upload_only.items] == ["upload-open"]

    all_sources = service.pending_items(
        **common(
            auth(
                capabilities=(
                    CAPABILITY_UPLOAD_MANAGE,
                    CAPABILITY_UPLOAD_READ,
                    CAPABILITY_DATASET_READ,
                    CAPABILITY_ANNOTATION_REVIEW,
                    CAPABILITY_DATASET_VERSION_PUBLISH,
                )
            )
        ),
        cursor=None,
        limit=50,
    ).pending_items
    assert [item.source_id for item in all_sources.items] == [
        "qc-reject",
        "upload-open",
        "task-review",
        "task-publish",
    ]
    assert all(item.target.deep_link is not None for item in all_sources.items)
    assert all(".." not in str(item.target.deep_link) for item in all_sources.items)
    assert "upload-closed" not in {item.source_id for item in all_sources.items}


def test_coverage_is_always_blocked_without_versioned_denominator() -> None:
    repository = InMemoryDashboardRepository(
        committed_objects=(
            CommittedObjectFact(
                project_id="project-a",
                region_code="cn-east",
                rollout_id="rollout-a",
                data_package_id="package-a",
                file_size=1_024,
                committed_at=NOW - timedelta(minutes=1),
            ),
        ),
        collection_observations=(
            CollectionObservationFact(
                project_id="project-a",
                region_code="cn-east",
                rollout_id="rollout-a",
                task_id="task-a",
                robot_id="robot-a",
                observed_at=NOW - timedelta(minutes=1),
            ),
        ),
    )
    coverage = DashboardService(repository, clock=lambda: NOW).coverage(**common(auth())).coverage
    assert coverage.status is DashboardSectionStatus.BLOCKED
    assert coverage.error is not None
    assert coverage.error.code == "P01_COVERAGE_DENOMINATOR_MISSING"
    assert coverage.error.message == (
        "缺少采集计划、机器人分组、任务目录或目标总量，因此暂时无法计算覆盖率。"
    )
    assert not hasattr(coverage, "percentage")


def test_generic_status_formula_covers_empty_partial_stale_error_and_ready() -> None:
    empty = classify_section((), now=NOW, freshness=timedelta(minutes=5))
    assert empty.status is DashboardSectionStatus.EMPTY
    partial = classify_section(
        (
            FactSourceProbe("ingest", FactSourceStatus.AVAILABLE, NOW),
            FactSourceProbe("quality", FactSourceStatus.ERROR),
        ),
        now=NOW,
        freshness=timedelta(minutes=5),
    )
    assert partial.status is DashboardSectionStatus.PARTIAL
    failed = classify_section(
        (FactSourceProbe("ingest", FactSourceStatus.ERROR),),
        now=NOW,
        freshness=timedelta(minutes=5),
    )
    assert failed.status is DashboardSectionStatus.ERROR
    stale = classify_section(
        (FactSourceProbe("ingest", FactSourceStatus.AVAILABLE, NOW - timedelta(minutes=6)),),
        now=NOW,
        freshness=timedelta(minutes=5),
    )
    assert stale.status is DashboardSectionStatus.STALE
    ready = classify_section(
        (FactSourceProbe("ingest", FactSourceStatus.AVAILABLE, NOW - timedelta(minutes=1)),),
        now=NOW,
        freshness=timedelta(minutes=5),
    )
    assert ready.status is DashboardSectionStatus.READY


def test_cursor_is_tamper_evident_and_bound_to_principal_scope_and_query() -> None:
    codec = DashboardCursorCodec("cursor-secret")
    original = query()
    cursor = codec.encode(
        endpoint="activity",
        query=original,
        sort_values=(NOW.isoformat(), "QC_COMPLETED", "report-9"),
    )
    assert codec.decode(cursor, endpoint="activity", query=original) == (
        NOW.isoformat(),
        "QC_COMPLETED",
        "report-9",
    )
    for variant in (
        query(principal="user-b"),
        query(project="project-b"),
        query(region="cn-west"),
        query(start=START + timedelta(seconds=1)),
        query(timezone_name="UTC"),
    ):
        with pytest.raises(ProblemException):
            codec.decode(cursor, endpoint="activity", query=variant)
    with pytest.raises(ProblemException):
        codec.decode(cursor, endpoint="pending-items", query=original)
    replacement = "A" if cursor[-1] != "A" else "B"
    with pytest.raises(ProblemException):
        codec.decode(cursor[:-1] + replacement, endpoint="activity", query=original)


def test_time_validation_supports_24h_7d_30d_dst_and_technical_cap() -> None:
    service = DashboardService(clock=lambda: NOW)
    actor = auth()
    for duration in (timedelta(hours=24), timedelta(days=7), timedelta(days=30)):
        response = service.coverage(
            auth=actor,
            project_id="project-a",
            region_code="cn-east",
            range_start=NOW - duration,
            range_end=NOW,
            timezone_name="Asia/Shanghai",
        )
        assert response.range_end - response.range_start == duration

    repeated_hour = service.coverage(
        auth=actor,
        project_id="project-a",
        region_code="cn-east",
        range_start=datetime.fromisoformat("2026-11-01T01:30:00-04:00"),
        range_end=datetime.fromisoformat("2026-11-01T01:30:00-05:00"),
        timezone_name="America/New_York",
    )
    assert repeated_hour.range_end - repeated_hour.range_start == timedelta(hours=1)
    with pytest.raises(ProblemException) as too_long:
        service.coverage(
            auth=actor,
            project_id="project-a",
            region_code="cn-east",
            range_start=NOW - timedelta(days=32),
            range_end=NOW,
            timezone_name="UTC",
        )
    assert too_long.value.problem.code == "DASHBOARD_TIME_RANGE_TOO_LARGE"


def test_section_invariants_reject_ambiguous_states() -> None:
    with pytest.raises(ValidationError):
        DashboardSectionState(status=DashboardSectionStatus.BLOCKED)
    with pytest.raises(ValidationError):
        DashboardSectionState(
            status=DashboardSectionStatus.EMPTY,
            as_of=NOW,
            error=DashboardSectionError(code="BAD", message="must not be attached"),
        )
