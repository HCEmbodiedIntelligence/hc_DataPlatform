from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.dashboard.models import (
    DashboardActivityEventType,
    DashboardPendingItemType,
    DashboardPendingSeverity,
    DashboardResourceType,
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
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.capabilities import CAPABILITY_DASHBOARD_READ

NOW = datetime(2026, 8, 18, 8, tzinfo=timezone.utc)
WINDOW = DashboardWindow(NOW - timedelta(hours=1), NOW, "UTC")


def auth(
    *,
    principal: str = "principal-a",
    project: str = "project-a",
    region: str = "cn-east",
    capability: bool = True,
) -> AuthContext:
    return AuthContext(
        subject_id=principal,
        project_ids=frozenset({project}),
        region_codes=frozenset({region}),
        scope_pairs=frozenset({(project, region)}),
        scoped_capabilities=(
            frozenset({(project, CAPABILITY_DASHBOARD_READ)}) if capability else frozenset()
        ),
    )


def committed(project: str, region: str, rollout: str, seconds_ago: int) -> CommittedObjectFact:
    return CommittedObjectFact(
        project_id=project,
        region_code=region,
        rollout_id=rollout,
        data_package_id=f"package-{rollout}",
        file_size=100,
        committed_at=NOW - timedelta(seconds=seconds_ago),
    )


def observation(project: str, region: str, rollout: str) -> CollectionObservationFact:
    return CollectionObservationFact(
        project_id=project,
        region_code=region,
        rollout_id=rollout,
        task_id="task-a",
        robot_id="robot-a",
        observed_at=NOW - timedelta(minutes=1),
    )


def test_repository_filters_exact_principal_project_region_and_half_open_time() -> None:
    rows = (
        committed("project-a", "cn-east", "r-end", 0),
        committed("project-a", "cn-east", "r-1", 1),
        committed("project-a", "cn-east", "r-start", 3600),
        committed("project-a", "cn-west", "r-west", 1),
        committed("project-b", "cn-east", "r-other", 1),
    )
    repository = InMemoryDashboardRepository(committed_objects=rows)
    actor = auth()
    scope = DashboardScope(actor.subject_id, "project-a", "cn-east")
    page = repository.committed_objects(
        auth=actor,
        scope=scope,
        window=WINDOW,
        limit=10,
    )
    # from is inclusive and to is exclusive.
    assert [item.rollout_id for item in page.items] == ["r-1", "r-start"]

    wrong_principal = DashboardScope("principal-b", "project-a", "cn-east")
    with pytest.raises(ProblemException) as principal_denied:
        repository.committed_objects(
            auth=actor,
            scope=wrong_principal,
            window=WINDOW,
            limit=10,
        )
    assert principal_denied.value.problem.code == "DASHBOARD_PRINCIPAL_SCOPE_DENIED"
    with pytest.raises(ProblemException) as wrong_region:
        repository.committed_objects(
            auth=actor,
            scope=DashboardScope(actor.subject_id, "project-a", "cn-west"),
            window=WINDOW,
            limit=10,
        )
    assert wrong_region.value.problem.code == "REGION_SCOPE_DENIED"
    with pytest.raises(ProblemException) as missing_capability:
        repository.committed_objects(
            auth=auth(capability=False),
            scope=scope,
            window=WINDOW,
            limit=10,
        )
    assert missing_capability.value.problem.code == "CAPABILITY_REQUIRED"


def test_repository_stable_sort_and_keyset_boundary_do_not_repeat_items() -> None:
    timestamp = NOW - timedelta(minutes=1)
    rows = tuple(
        CommittedObjectFact(
            project_id="project-a",
            region_code="cn-east",
            rollout_id=f"r-{index}",
            data_package_id=f"p-{index}",
            file_size=100,
            committed_at=timestamp,
        )
        for index in range(5)
    )
    repository = InMemoryDashboardRepository(committed_objects=rows)
    actor = auth()
    scope = DashboardScope(actor.subject_id, "project-a", "cn-east")
    first = repository.committed_objects(
        auth=actor,
        scope=scope,
        window=WINDOW,
        limit=2,
    )
    assert [item.rollout_id for item in first.items] == ["r-4", "r-3"]
    boundary = first.items[-1]
    second = repository.committed_objects(
        auth=actor,
        scope=scope,
        window=WINDOW,
        limit=2,
        after=(boundary.committed_at, boundary.rollout_id),
    )
    assert [item.rollout_id for item in second.items] == ["r-2", "r-1"]
    assert set(first.items).isdisjoint(second.items)


def test_coverage_observations_are_raw_facts_not_a_denominator() -> None:
    repository = InMemoryDashboardRepository(
        collection_observations=(
            observation("project-a", "cn-east", "r-a"),
            observation("project-a", "cn-west", "r-west"),
            observation("project-b", "cn-east", "r-other"),
        )
    )
    actor = auth()
    page = repository.collection_observations(
        auth=actor,
        scope=DashboardScope(actor.subject_id, "project-a", "cn-east"),
        window=WINDOW,
        limit=100,
    )
    assert [item.rollout_id for item in page.items] == ["r-a"]
    assert not hasattr(page, "coverage")


def test_business_events_use_exact_scope_half_open_range_and_stable_source_tuple() -> None:
    timestamp = NOW - timedelta(minutes=1)
    rows = tuple(
        DashboardBusinessEventFact(
            project_id=project,
            region_code=region,
            event_type=DashboardActivityEventType.QC_COMPLETED,
            source_id=source_id,
            occurred_at=occurred_at,
            source_state="PASS",
            target_resource_type=DashboardResourceType.ROLLOUT,
            target_resource_id=f"rollout-{source_id}",
        )
        for project, region, source_id, occurred_at in (
            ("project-a", "cn-east", "report-b", timestamp),
            ("project-a", "cn-east", "report-a", timestamp),
            ("project-a", "cn-east", "at-end", NOW),
            ("project-a", "cn-west", "wrong-region", timestamp),
            ("project-b", "cn-east", "wrong-project", timestamp),
        )
    )
    repository = InMemoryDashboardRepository(business_events=rows)
    actor = auth()
    scope = DashboardScope(actor.subject_id, "project-a", "cn-east")
    first = repository.business_events(auth=actor, scope=scope, window=WINDOW, limit=1)
    assert [item.source_id for item in first.items] == ["report-b"]
    boundary = first.items[-1]
    second = repository.business_events(
        auth=actor,
        scope=scope,
        window=WINDOW,
        limit=2,
        after=(boundary.occurred_at, boundary.event_type.value, boundary.source_id),
    )
    assert [item.source_id for item in second.items] == ["report-a"]


def test_pending_repository_deduplicates_filters_closed_states_and_orders_domain_severity() -> None:
    def item(
        item_type: DashboardPendingItemType,
        source_id: str,
        source_state: str,
        severity: DashboardPendingSeverity,
        rank: int,
        minutes_ago: int,
    ) -> DashboardPendingFact:
        target_type = (
            DashboardResourceType.DATASET_VERSION
            if item_type is DashboardPendingItemType.PUBLICATION_PENDING
            else DashboardResourceType.UPLOAD_SESSION
        )
        return DashboardPendingFact(
            project_id="project-a",
            region_code="cn-east",
            item_type=item_type,
            source_id=source_id,
            source_state=source_state,
            severity=severity,
            severity_rank=rank,
            opened_at=NOW - timedelta(minutes=minutes_ago),
            target_resource_type=target_type,
            target_resource_id="dataset-a" if rank == 1 else source_id,
            target_resource_version="1" if rank == 1 else None,
        )

    repository = InMemoryDashboardRepository(
        pending_facts=(
            item(
                DashboardPendingItemType.UPLOAD_FAILED,
                "upload-newer",
                "FAILED",
                DashboardPendingSeverity.HIGH,
                3,
                2,
            ),
            item(
                DashboardPendingItemType.UPLOAD_FAILED,
                "upload-older",
                "FAILED",
                DashboardPendingSeverity.HIGH,
                3,
                10,
            ),
            item(
                DashboardPendingItemType.QC_ANOMALY,
                "qc-critical",
                "REJECT",
                DashboardPendingSeverity.CRITICAL,
                4,
                1,
            ),
            item(
                DashboardPendingItemType.QC_ANOMALY,
                "qc-closed",
                "PASS",
                DashboardPendingSeverity.HIGH,
                3,
                20,
            ),
            item(
                DashboardPendingItemType.PUBLICATION_PENDING,
                "publish",
                "APPROVED",
                DashboardPendingSeverity.LOW,
                1,
                30,
            ),
        )
    )
    actor = auth()
    page = repository.pending_facts(
        auth=actor,
        scope=DashboardScope(actor.subject_id, "project-a", "cn-east"),
        window=WINDOW,
        allowed_types=(
            DashboardPendingItemType.UPLOAD_FAILED,
            DashboardPendingItemType.QC_ANOMALY,
        ),
        limit=10,
    )
    assert [fact.source_id for fact in page.items] == [
        "qc-critical",
        "upload-older",
        "upload-newer",
    ]
    assert "qc-closed" not in {fact.source_id for fact in page.items}
    assert "publish" not in {fact.source_id for fact in page.items}
