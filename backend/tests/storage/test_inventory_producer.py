from __future__ import annotations

from datetime import datetime, timezone

import pytest

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.storage.inventory import (
    InventoryCatalogEntry,
    InventoryUnavailable,
    PostgresStorageInventoryCatalog,
    ProviderInventoryObject,
    StorageInventorySnapshotProducer,
)
from hc_data_platform.storage.inventory_worker import run_inventory_cycle
from hc_data_platform.storage.models import (
    BusinessCapacityCategory,
    InventoryDisposition,
    ObjectRole,
)
from hc_data_platform.storage.repository import InMemoryStorageRepository
from hc_data_platform.storage.service import StorageGovernanceService

OBSERVED_AT = datetime(2026, 8, 24, 4, 0, tzinfo=timezone.utc)


class Catalog:
    def entries(self, *, project_id: str) -> tuple[InventoryCatalogEntry, ...]:
        if project_id != "project-inventory":
            return ()
        return (
            InventoryCatalogEntry(
                identity="ingest:rollout-1:raw",
                object_key="raw/recording.mcap",
                expected_bytes=100,
                business_category=BusinessCapacityCategory.RAW,
                object_role=ObjectRole.RAW,
                observed_at=OBSERVED_AT,
                priority=60,
            ),
            InventoryCatalogEntry(
                identity="lance:dataset-1",
                object_key="lance/project-inventory/dataset-1/",
                business_category=BusinessCapacityCategory.PENDING_ANNOTATION,
                object_role=ObjectRole.REBUILDABLE_DERIVATIVE,
                observed_at=OBSERVED_AT,
                prefix=True,
                priority=40,
            ),
        )


class Provider:
    objects = {
        "raw/recording.mcap": ProviderInventoryObject(
            object_key="raw/recording.mcap",
            physical_bytes=100,
            observed_at=OBSERVED_AT,
        ),
        "lance/project-inventory/dataset-1/data/part.lance": ProviderInventoryObject(
            object_key="lance/project-inventory/dataset-1/data/part.lance",
            physical_bytes=25,
            observed_at=OBSERVED_AT,
        ),
        "lance/project-inventory/dataset-1/_versions/1.manifest": ProviderInventoryObject(
            object_key="lance/project-inventory/dataset-1/_versions/1.manifest",
            physical_bytes=5,
            observed_at=OBSERVED_AT,
        ),
    }

    def head(self, object_key: str) -> ProviderInventoryObject | None:
        return self.objects.get(object_key)

    def list_prefix(self, prefix: str) -> tuple[ProviderInventoryObject, ...]:
        return tuple(value for key, value in sorted(self.objects.items()) if key.startswith(prefix))


class CatalogCursor:
    description = (("unused",),)

    def __init__(self) -> None:
        self._rows: tuple[dict[str, object], ...] = ()

    def execute(self, query: str, parameters: tuple[str, ...]) -> None:
        assert parameters == ("project-inventory",)
        if "FROM publishing.published_exports" in query:
            self._rows = (
                {
                    "dataset_id": "dataset-1",
                    "dataset_version": "version-1",
                    "export_format": "lerobot_v3",
                    "artifact_uri": "exports/project-inventory/dataset-1/artifact.zip",
                    "published_at": OBSERVED_AT,
                },
            )
        else:
            self._rows = ()

    def fetchall(self) -> tuple[dict[str, object], ...]:
        return self._rows

    def close(self) -> None:
        pass


class CatalogConnection:
    def __init__(self) -> None:
        self.catalog_cursor = CatalogCursor()

    def cursor(self) -> CatalogCursor:
        return self.catalog_cursor

    def close(self) -> None:
        pass


def reader(*project_ids: str) -> AuthContext:
    return AuthContext(
        subject_id="inventory-reader",
        project_ids=frozenset(project_ids),
        region_codes=frozenset(),
        roles=frozenset(),
        capabilities=frozenset({"storage.overview.read"}),
        scope_pairs=frozenset((project_id, None) for project_id in project_ids),
    )


def producer() -> tuple[StorageInventorySnapshotProducer, StorageGovernanceService]:
    service = StorageGovernanceService(
        InMemoryStorageRepository(), cursor_secret="inventory-producer-test"
    )
    return (
        StorageInventorySnapshotProducer(Catalog(), Provider(), service, clock=lambda: OBSERVED_AT),
        service,
    )


def test_producer_publishes_snapshot_and_facts_idempotently() -> None:
    target, service = producer()

    first = target.run(project_id="project-inventory")
    replay = target.run(project_id="project-inventory")

    assert replay == first
    assert first.snapshot_id.startswith("inventory-")
    assert first.physical_total_bytes == "130"
    assert first.candidate_business_total_bytes == "130"
    inventory = service.inventory_page(
        project_id="project-inventory",
        actor=reader("project-inventory"),
        snapshot_id=first.snapshot_id,
        limit=10,
    )
    assert len(inventory.items) == 3
    assert {item.business_category for item in inventory.items} == {
        BusinessCapacityCategory.RAW,
        BusinessCapacityCategory.PENDING_ANNOTATION,
    }
    history = service.capacity_history(
        project_id="project-inventory", actor=reader("project-inventory")
    )
    assert [item.snapshot_id for item in history.items] == [first.snapshot_id]


def test_producer_counts_leftover_lance_attempts_as_temporary_physical_bytes() -> None:
    class AttemptCatalog:
        def entries(self, *, project_id: str) -> tuple[InventoryCatalogEntry, ...]:
            assert project_id == "project-inventory"
            return (
                InventoryCatalogEntry(
                    identity="raw:one",
                    object_key="raw/recording.mcap",
                    expected_bytes=100,
                    business_category=BusinessCapacityCategory.RAW,
                    object_role=ObjectRole.RAW,
                    observed_at=OBSERVED_AT,
                ),
                InventoryCatalogEntry(
                    identity="lance-attempt:dataset-1",
                    object_key="lance/_attempts/project-inventory/dataset-1/",
                    business_category=None,
                    object_role=ObjectRole.REBUILDABLE_DERIVATIVE,
                    observed_at=OBSERVED_AT,
                    prefix=True,
                    disposition=InventoryDisposition.TEMPORARY,
                    required=False,
                ),
            )

    class AttemptProvider(Provider):
        objects = {
            "raw/recording.mcap": Provider.objects["raw/recording.mcap"],
            "lance/_attempts/project-inventory/dataset-1/a.lance/data/part.arrow": (
                ProviderInventoryObject(
                    object_key=(
                        "lance/_attempts/project-inventory/dataset-1/"
                        "a.lance/data/part.arrow"
                    ),
                    physical_bytes=50,
                    observed_at=OBSERVED_AT,
                )
            ),
        }

    service = StorageGovernanceService(
        InMemoryStorageRepository(), cursor_secret="inventory-attempt-test"
    )
    snapshot = StorageInventorySnapshotProducer(
        AttemptCatalog(), AttemptProvider(), service, clock=lambda: OBSERVED_AT
    ).run(project_id="project-inventory")

    assert snapshot.physical_total_bytes == "150"
    assert snapshot.candidate_business_total_bytes == "100"
    assert snapshot.reconciliation.temporary_bytes == "50"
    assert snapshot.reconciliation.temporary_instance_count == 1


def test_postgres_catalog_includes_immutable_published_exports() -> None:
    connection = CatalogConnection()
    catalog = PostgresStorageInventoryCatalog(
        lambda: connection,
        object_store_bucket="inventory-bucket",
        artifact_prefix="artifacts",
    )

    entries = catalog.entries(project_id="project-inventory")

    assert len(entries) == 1
    assert entries[0].identity == "published-export:dataset-1:version-1:lerobot_v3"
    assert entries[0].object_key == "artifacts/exports/project-inventory/dataset-1/artifact.zip"
    assert entries[0].business_category is BusinessCapacityCategory.ANNOTATION_COMPLETE
    assert entries[0].object_role is ObjectRole.REBUILDABLE_DERIVATIVE


def test_producer_keeps_empty_catalog_unknown_instead_of_publishing_zero_bytes() -> None:
    target, service = producer()

    with pytest.raises(InventoryUnavailable, match="capacity remains unknown"):
        target.run(project_id="project-without-objects")

    with pytest.raises(ProblemException) as missing:
        service.capacity_snapshot(
            project_id="project-without-objects",
            actor=reader("project-without-objects"),
        )
    assert missing.value.problem.code == "CAPACITY_SNAPSHOT_NOT_FOUND"


def test_worker_cycle_binds_exact_scope_and_replays_the_same_sealed_snapshot() -> None:
    target, _ = producer()
    scope = "organization-inventory/project-inventory/cn-east"

    first = run_inventory_cycle(target, scopes=(scope,), strict=True)
    replay = run_inventory_cycle(target, scopes=(scope,), strict=True)

    assert replay == first
    assert len(first) == 1
    with pytest.raises(RuntimeError, match="no RequestContext"):
        current_request_context()
