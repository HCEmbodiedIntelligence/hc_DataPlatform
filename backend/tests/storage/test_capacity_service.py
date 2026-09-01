from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.storage.models import (
    BusinessCapacityCategory,
    CapacityInventoryFact,
    InventoryDisposition,
    ObjectRole,
)
from hc_data_platform.storage.repository import InMemoryStorageRepository
from hc_data_platform.storage.service import StorageGovernanceService

OBSERVED_AT = datetime(2026, 8, 17, 2, 30, tzinfo=timezone.utc)
ORGANIZATION_ID = "organization-a"


def reader(project_id: str = "project-a") -> AuthContext:
    return AuthContext(
        subject_id="capacity-reader",
        organization_ids=frozenset({ORGANIZATION_ID}),
        project_ids=frozenset({project_id}),
        region_codes=frozenset(),
        capabilities=frozenset({"storage.overview.read"}),
        scope_pairs=frozenset({(project_id, None)}),
        organization_scope_triples=frozenset({(ORGANIZATION_ID, project_id, None)}),
        organization_scoped_capabilities=frozenset(
            {(ORGANIZATION_ID, project_id, "storage.overview.read")}
        ),
    )


def capability_reader(
    *capabilities: str,
    project_id: str = "project-a",
) -> AuthContext:
    return AuthContext(
        subject_id="capacity-capability-reader",
        organization_ids=frozenset({ORGANIZATION_ID}),
        project_ids=frozenset({project_id}),
        region_codes=frozenset(),
        capabilities=frozenset(capabilities),
        scope_pairs=frozenset({(project_id, None)}),
        organization_scope_triples=frozenset({(ORGANIZATION_ID, project_id, None)}),
        organization_scoped_capabilities=frozenset(
            (ORGANIZATION_ID, project_id, capability) for capability in capabilities
        ),
    )


def fact(
    physical_id: str,
    logical_id: str | None,
    size: int,
    *,
    category: BusinessCapacityCategory | None,
    disposition: InventoryDisposition = InventoryDisposition.PRIMARY,
    role: ObjectRole = ObjectRole.OTHER,
    project_id: str = "project-a",
    snapshot_id: str = "snapshot-1",
) -> CapacityInventoryFact:
    return CapacityInventoryFact(
        snapshot_id=snapshot_id,
        project_id=project_id,
        physical_instance_id=physical_id,
        logical_object_id=logical_id,
        physical_bytes=str(size),
        disposition=disposition,
        business_category=category,
        object_role=role,
        observed_at=OBSERVED_AT,
    )


def reconciliation_sample() -> tuple[CapacityInventoryFact, ...]:
    raw = fact(
        "physical/raw-primary",
        "logical/raw",
        100,
        category=BusinessCapacityCategory.RAW,
        role=ObjectRole.RAW,
    )
    return (
        raw,
        raw,  # duplicated provider row: same physical instance, ignored
        fact(
            "physical/raw-replica",
            "logical/raw",
            100,
            category=BusinessCapacityCategory.RAW,
            disposition=InventoryDisposition.REPLICA,
            role=ObjectRole.RAW,
        ),
        fact(
            "physical/annotated",
            "logical/annotated",
            50,
            category=BusinessCapacityCategory.ANNOTATION_COMPLETE,
        ),
        fact(
            "physical/pending",
            "logical/pending",
            30,
            category=BusinessCapacityCategory.PENDING_ANNOTATION,
        ),
        fact(
            "physical/issue",
            "logical/issue",
            20,
            category=BusinessCapacityCategory.ISSUE_DATA,
        ),
        fact(
            "physical/tmp",
            None,
            10,
            category=None,
            disposition=InventoryDisposition.TEMPORARY,
        ),
    )


def snapshot_facts(
    *,
    snapshot_id: str,
    observed_at: datetime,
    annotated_bytes: int,
) -> tuple[CapacityInventoryFact, ...]:
    """Create one internally reconciled immutable snapshot for a trend day."""

    return tuple(
        item.model_copy(
            update={
                "snapshot_id": snapshot_id,
                "observed_at": observed_at,
                **(
                    {"physical_bytes": str(annotated_bytes)}
                    if item.physical_instance_id == "physical/annotated"
                    else {}
                ),
            }
        )
        for item in reconciliation_sample()
    )


def test_capacity_reports_physical_and_candidate_mutually_exclusive_totals() -> None:
    service = StorageGovernanceService(InMemoryStorageRepository())

    snapshot = service.record_inventory_snapshot(
        project_id="project-a",
        snapshot_id="snapshot-1",
        facts=reconciliation_sample(),
    )

    assert snapshot.physical_total_bytes == "310"
    assert snapshot.physical_instance_count == 6
    assert snapshot.candidate_business_total_bytes == "200"
    assert snapshot.candidate_logical_object_count == 4
    assert [item.category for item in snapshot.categories] == list(BusinessCapacityCategory)
    assert [item.candidate_bytes for item in snapshot.categories] == ["100", "50", "30", "20"]
    assert snapshot.reconciliation.replica_overhead_bytes == "100"
    assert snapshot.reconciliation.replica_instance_count == 1
    assert snapshot.reconciliation.temporary_bytes == "10"
    assert snapshot.reconciliation.temporary_instance_count == 1
    assert snapshot.reconciliation.duplicate_inventory_rows_ignored == 1
    assert snapshot.reconciliation.balanced is True
    assert 310 == 200 + 100 + 10

    persisted = service.capacity_snapshot(project_id="project-a", actor=reader())
    assert persisted.reconciliation.duplicate_inventory_rows_ignored == 1


def test_capacity_without_snapshot_remains_not_found_and_unknown() -> None:
    service = StorageGovernanceService(InMemoryStorageRepository())

    with pytest.raises(ProblemException) as snapshot_missing:
        service.capacity_snapshot(project_id="project-a", actor=reader())
    with pytest.raises(ProblemException) as history_missing:
        service.capacity_history(project_id="project-a", actor=reader())

    assert snapshot_missing.value.problem.code == "CAPACITY_SNAPSHOT_NOT_FOUND"
    assert history_missing.value.problem.code == "CAPACITY_SNAPSHOT_NOT_FOUND"


def test_capacity_rejects_conflicting_duplicate_physical_and_logical_facts() -> None:
    service = StorageGovernanceService(InMemoryStorageRepository())
    physical_conflict = (
        fact("same", "logical-a", 10, category=BusinessCapacityCategory.RAW),
        fact("same", "logical-a", 11, category=BusinessCapacityCategory.RAW),
    )
    with pytest.raises(ProblemException) as physical_error:
        service.record_inventory_snapshot(
            project_id="project-a",
            snapshot_id="snapshot-1",
            facts=physical_conflict,
        )
    assert physical_error.value.problem.code == "CAPACITY_PHYSICAL_ID_CONFLICT"

    logical_conflict = (
        fact("one", "logical-a", 10, category=BusinessCapacityCategory.RAW),
        fact(
            "two",
            "logical-a",
            10,
            category=BusinessCapacityCategory.ISSUE_DATA,
            disposition=InventoryDisposition.REPLICA,
        ),
    )
    with pytest.raises(ProblemException) as logical_error:
        service.record_inventory_snapshot(
            project_id="project-a",
            snapshot_id="snapshot-1",
            facts=logical_conflict,
        )
    assert logical_error.value.problem.code == "CAPACITY_LOGICAL_OBJECT_CONFLICT"


def test_capacity_snapshot_replay_is_exact_and_immutable_in_memory() -> None:
    service = StorageGovernanceService(InMemoryStorageRepository())
    original = reconciliation_sample()
    first = service.record_inventory_snapshot(
        project_id="project-a", snapshot_id="snapshot-1", facts=original
    )
    assert (
        service.record_inventory_snapshot(
            project_id="project-a", snapshot_id="snapshot-1", facts=original
        )
        == first
    )

    changed = tuple(
        fact.model_copy(update={"physical_bytes": "11"})
        if fact.physical_instance_id == "physical/tmp"
        else fact
        for fact in original
    )
    with pytest.raises(ProblemException) as immutable:
        service.record_inventory_snapshot(
            project_id="project-a", snapshot_id="snapshot-1", facts=changed
        )
    assert immutable.value.problem.code == "CAPACITY_SNAPSHOT_IMMUTABLE"


def test_capacity_scope_and_cursor_pagination_are_enforced() -> None:
    service = StorageGovernanceService(InMemoryStorageRepository(), cursor_secret="test-secret")
    service.record_inventory_snapshot(
        project_id="project-a",
        snapshot_id="snapshot-1",
        facts=reconciliation_sample(),
    )

    first = service.inventory_page(
        project_id="project-a",
        actor=reader(),
        limit=2,
    )
    assert len(first.items) == 2
    assert len({item.physical_instance_id for item in first.items}) == 2
    assert first.page_info.has_next_page is True
    second = service.inventory_page(
        project_id="project-a",
        actor=reader(),
        cursor=first.page_info.end_cursor,
        limit=2,
    )
    assert len(second.items) == 2
    assert second.page_info.has_previous_page is True
    assert set(item.physical_instance_id for item in first.items).isdisjoint(
        item.physical_instance_id for item in second.items
    )
    previous = service.inventory_page(
        project_id="project-a",
        actor=reader(),
        cursor=second.page_info.start_cursor,
        limit=2,
    )
    assert [item.physical_instance_id for item in previous.items] == [
        item.physical_instance_id for item in first.items
    ]
    assert previous.page_info.has_previous_page is False

    with pytest.raises(ProblemException) as denied:
        service.capacity_snapshot(project_id="project-a", actor=reader("project-b"))
    assert denied.value.problem.code == "ORGANIZATION_SCOPE_REQUIRED"

    with pytest.raises(ProblemException) as cursor_denied:
        service.inventory_page(
            project_id="project-b",
            actor=reader("project-b"),
            cursor=first.page_info.end_cursor,
            limit=2,
        )
    assert cursor_denied.value.problem.code == "CAPACITY_SNAPSHOT_NOT_FOUND"


def test_capacity_requires_its_documented_capability_with_legacy_read_compatibility() -> None:
    service = StorageGovernanceService(InMemoryStorageRepository(), cursor_secret="test-secret")
    service.record_inventory_snapshot(
        project_id="project-a",
        snapshot_id="snapshot-1",
        facts=reconciliation_sample(),
    )

    exact = service.capacity_snapshot(
        project_id="project-a",
        actor=capability_reader("storage.overview.read"),
    )
    assert exact.project_id == "project-a"

    with pytest.raises(ProblemException) as denied:
        service.capacity_snapshot(
            project_id="project-a",
            actor=capability_reader("dashboard.read"),
        )
    assert denied.value.problem.code == "CAPABILITY_REQUIRED"


def test_capacity_history_uses_latest_utc_fact_per_day_and_exact_growth() -> None:
    now = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
    service = StorageGovernanceService(
        InMemoryStorageRepository(),
        clock=lambda: now,
    )
    first_at = now - timedelta(days=3)
    same_day_early = now - timedelta(days=2, hours=2)
    same_day_late = now - timedelta(days=2)
    final_at = now - timedelta(days=1)
    for snapshot_id, observed_at, annotated_bytes in (
        ("history-first", first_at, 50),
        ("history-middle-early", same_day_early, 60),
        ("history-middle-late", same_day_late, 80),
        ("history-final", final_at, 110),
    ):
        service.record_inventory_snapshot(
            project_id="project-a",
            snapshot_id=snapshot_id,
            facts=snapshot_facts(
                snapshot_id=snapshot_id,
                observed_at=observed_at,
                annotated_bytes=annotated_bytes,
            ),
        )

    history = service.capacity_history(project_id="project-a", actor=reader())

    assert [item.snapshot_id for item in history.items] == [
        "history-first",
        "history-middle-late",
        "history-final",
    ]
    assert [item.candidate_business_total_bytes for item in history.items] == [
        "200",
        "230",
        "260",
    ]
    assert history.growth is not None
    assert history.growth.candidate_change_bytes == "60"
    assert history.growth.elapsed_seconds == 2 * 86_400
    assert history.growth.candidate_bytes_per_day == "30"


def test_capacity_history_keeps_current_snapshot_chronological_and_rejects_bad_ranges() -> None:
    now = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
    service = StorageGovernanceService(InMemoryStorageRepository(), clock=lambda: now)
    # Deliberately insert the newer fact first: in-memory behavior must match
    # PostgreSQL's observed_at ordering rather than insertion order.
    for snapshot_id, observed_at in (
        ("history-newer", now - timedelta(days=1)),
        ("history-older", now - timedelta(days=2)),
    ):
        service.record_inventory_snapshot(
            project_id="project-a",
            snapshot_id=snapshot_id,
            facts=snapshot_facts(
                snapshot_id=snapshot_id,
                observed_at=observed_at,
                annotated_bytes=50,
            ),
        )

    assert (
        service.capacity_snapshot(project_id="project-a", actor=reader()).snapshot_id
        == "history-newer"
    )
    with pytest.raises(ProblemException) as invalid:
        service.capacity_history(
            project_id="project-a",
            actor=reader(),
            window_start=now - timedelta(days=32),
            window_end=now,
        )
    assert invalid.value.problem.code == "CAPACITY_HISTORY_RANGE_INVALID"
    with pytest.raises(ProblemException) as denied:
        service.capacity_history(project_id="project-a", actor=reader("project-b"))
    assert denied.value.problem.code == "ORGANIZATION_SCOPE_REQUIRED"
