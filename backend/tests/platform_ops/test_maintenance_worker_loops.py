from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from threading import Event
from typing import Any
from uuid import UUID

import pytest

from hc_data_platform.aligned_media import maintenance as media_maintenance
from hc_data_platform.core.context import current_platform_task_lease
from hc_data_platform.platform_ops import maintenance
from hc_data_platform.platform_ops.maintenance import (
    InMemoryMaintenanceWriteGate,
    WriterPermit,
    writer_permit_scope,
)
from hc_data_platform.platform_ops.task_leases import PlatformTaskLease
from hc_data_platform.storage import inventory_worker


class _StopLoop(Exception):
    pass


class _StagingSweeper:
    def __init__(self) -> None:
        self.calls = 0

    def run_once(self) -> int:
        self.calls += 1
        return 0


class _RenewalRecordingGate(InMemoryMaintenanceWriteGate):
    def __init__(self) -> None:
        super().__init__()
        self.renewed = Event()

    def renew_writer_permit(self, permit_id: UUID) -> WriterPermit:
        renewed = super().renew_writer_permit(permit_id)
        self.renewed.set()
        return renewed


class _RecordingTaskLeaseRepository:
    def __init__(self, lease: PlatformTaskLease) -> None:
        self.lease = lease
        self.acquisitions: list[tuple[str, str, UUID]] = []
        self.releases: list[UUID] = []

    def try_acquire(
        self,
        *,
        environment_id: str,
        task_id: str,
        owner_instance_id: UUID,
    ) -> PlatformTaskLease:
        self.acquisitions.append((environment_id, task_id, owner_instance_id))
        return self.lease

    def renew(self, lease_id: UUID) -> PlatformTaskLease:
        assert lease_id == self.lease.lease_id
        return self.lease

    def release(self, lease_id: UUID) -> None:
        self.releases.append(lease_id)


def test_long_running_writer_scope_renews_and_releases_its_permit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _RenewalRecordingGate()
    monkeypatch.setattr(maintenance, "RENEWAL_INTERVAL_SECONDS", 0.01)

    with writer_permit_scope(
        gate,
        environment_id="worker-renewal-test",
        writer_id="long-running-worker",
        writer_kind="temporal_activity",
    ):
        assert gate.renewed.wait(timeout=1)

    assert gate.writer_inventory("worker-renewal-test") == ()


@pytest.mark.asyncio
async def test_read_only_stops_storage_inventory_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = InMemoryMaintenanceWriteGate()
    gate.set_mode("worker-loop-test", "READ_ONLY_MAINTENANCE")
    calls: list[object] = []

    def record_cycle(*args: object, **kwargs: object) -> tuple[()]:
        calls.append((args, kwargs))
        return ()

    async def stop_after_cycle(_seconds: float) -> None:
        raise _StopLoop

    monkeypatch.setattr(inventory_worker, "run_inventory_cycle", record_cycle)
    monkeypatch.setattr(inventory_worker.asyncio, "sleep", stop_after_cycle)
    runtime = inventory_worker.StorageInventoryRuntime(
        producer=Any,  # type: ignore[arg-type]
        scopes=("organization-a/project-a/cn-east",),
        interval_seconds=1,
    )

    with pytest.raises(_StopLoop):
        await inventory_worker.serve_inventory(
            runtime,
            maintenance_gate=gate,
            environment_id="worker-loop-test",
            writer_id="inventory-test-worker",
        )
    assert calls == []


@pytest.mark.asyncio
async def test_storage_inventory_binds_scope_singleton_lease_through_commit_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = UUID("00000000-0000-4000-8000-000000000103")
    lease_id = UUID("00000000-0000-4000-8000-000000000203")
    now = datetime.now(timezone.utc)
    repository = _RecordingTaskLeaseRepository(
        PlatformTaskLease(
            environment_id="worker-singleton-test",
            task_id=f"storage-inventory:sha256:{'1' * 64}",
            lease_id=lease_id,
            owner_instance_id=owner_id,
            fencing_token=7,
            lease_until=now + timedelta(seconds=30),
            lease_version=1,
            acquired_at=now,
            updated_at=now,
        )
    )
    observed_leases: list[UUID | None] = []

    def stop_during_cycle(*args: object, **kwargs: object) -> tuple[()]:
        observed_leases.append(current_platform_task_lease())
        raise asyncio.CancelledError

    monkeypatch.setattr(inventory_worker, "run_inventory_cycle", stop_during_cycle)
    runtime = inventory_worker.StorageInventoryRuntime(
        producer=Any,  # type: ignore[arg-type]
        scopes=("organization-a/project-a/cn-east",),
        interval_seconds=30,
    )

    with pytest.raises(asyncio.CancelledError):
        await inventory_worker.serve_inventory(
            runtime,
            environment_id="worker-singleton-test",
            task_lease_repository=repository,
            owner_instance_id=owner_id,
        )

    assert repository.acquisitions == [
        (
            "worker-singleton-test",
            inventory_worker._inventory_task_id("organization-a/project-a/cn-east"),
            owner_id,
        )
    ]
    assert observed_leases == [lease_id]
    assert repository.releases == [lease_id]


@pytest.mark.asyncio
async def test_read_only_keeps_local_staging_cleanup_but_stops_object_gc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = InMemoryMaintenanceWriteGate()
    gate.set_mode("worker-loop-test", "READ_ONLY_MAINTENANCE")
    staging = _StagingSweeper()
    object_calls: list[str] = []

    async def stop_after_cycle(_seconds: float) -> None:
        raise _StopLoop

    monkeypatch.setattr(media_maintenance.asyncio, "sleep", stop_after_cycle)
    with pytest.raises(_StopLoop):
        await media_maintenance.serve_media_maintenance(
            scopes=(),
            staging_sweeper=staging,  # type: ignore[arg-type]
            interval_seconds=1,
            object_sweepers=(lambda: object_calls.append("object") or 0,),
            maintenance_gate=gate,
            environment_id="worker-loop-test",
            writer_id="media-test-worker",
        )
    assert staging.calls == 1
    assert object_calls == []
