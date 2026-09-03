"""Long-running and one-shot execution helpers for storage inventory."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from uuid import UUID

from hc_data_platform.core.context import (
    RequestContext,
    bind_platform_task_lease,
    bind_request_context,
    clear_request_context,
    reset_platform_task_lease,
    reset_request_context,
)
from hc_data_platform.platform_control.maintenance_contract import MaintenanceContractError
from hc_data_platform.platform_ops.maintenance import (
    EnvironmentId,
    MaintenanceWriteGate,
    SafeActorId,
    writer_permit_scope,
)
from hc_data_platform.platform_ops.task_leases import (
    TASK_LEASE_RENEWAL_INTERVAL_SECONDS,
    PlatformTaskLeaseRepository,
)

from .inventory import InventoryUnavailable, StorageInventorySnapshotProducer
from .models import CapacitySnapshot

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InventoryScope:
    organization_id: str
    project_id: str
    region_code: str


@dataclass(frozen=True, slots=True)
class StorageInventoryRuntime:
    producer: StorageInventorySnapshotProducer
    scopes: tuple[str, ...]
    interval_seconds: float


def parse_inventory_scope(value: str) -> InventoryScope:
    parts = tuple(part.strip() for part in value.split("/"))
    if len(parts) != 3 or not all(parts):
        raise ValueError("inventory scope must be organization_id/project_id/region_code")
    return InventoryScope(*parts)


def run_inventory_cycle(
    producer: StorageInventorySnapshotProducer,
    *,
    scopes: tuple[str, ...],
    strict: bool = False,
) -> tuple[CapacitySnapshot, ...]:
    """Run one exact-scope cycle; deterministic snapshot IDs make retries idempotent."""

    published: list[CapacitySnapshot] = []
    for raw_scope in scopes:
        scope = parse_inventory_scope(raw_scope)
        token = bind_request_context(
            RequestContext(
                organization_id=scope.organization_id,
                project_id=scope.project_id,
                region_code=scope.region_code,
                subject_id="storage-inventory-worker",
                service_identity=True,
            )
        )
        try:
            snapshot = producer.run(project_id=scope.project_id)
            published.append(snapshot)
            logger.info(
                "storage inventory snapshot published",
                extra={
                    "project_id": scope.project_id,
                    "snapshot_id": snapshot.snapshot_id,
                    "physical_total_bytes": snapshot.physical_total_bytes,
                },
            )
        except InventoryUnavailable:
            if strict:
                raise
            logger.warning(
                "storage inventory remains unknown because complete evidence is unavailable",
                extra={"project_id": scope.project_id},
                exc_info=True,
            )
        except Exception:
            if strict:
                raise
            logger.exception(
                "storage inventory production failed",
                extra={"project_id": scope.project_id},
            )
        finally:
            reset_request_context(token)
            clear_request_context()
    return tuple(published)


async def serve_inventory(
    runtime: StorageInventoryRuntime,
    *,
    interval_seconds_provider: Callable[[], float] | None = None,
    maintenance_gate: MaintenanceWriteGate | None = None,
    environment_id: EnvironmentId = "local",
    writer_id: SafeActorId = "storage-inventory-worker",
    task_lease_repository: PlatformTaskLeaseRepository | None = None,
    owner_instance_id: UUID | None = None,
) -> None:
    """Publish immediately on worker start and then at the configured interval."""

    if task_lease_repository is not None:
        if owner_instance_id is None:
            raise ValueError("owner_instance_id is required with a platform task lease repository")
        await asyncio.gather(
            *(
                _serve_singleton_scope(
                    runtime,
                    raw_scope=raw_scope,
                    interval_seconds_provider=interval_seconds_provider,
                    maintenance_gate=maintenance_gate,
                    environment_id=environment_id,
                    writer_id=writer_id,
                    task_lease_repository=task_lease_repository,
                    owner_instance_id=owner_instance_id,
                )
                for raw_scope in runtime.scopes
            )
        )
        return

    while True:
        try:
            with writer_permit_scope(
                maintenance_gate,
                environment_id=environment_id,
                writer_id=writer_id,
                writer_kind="maintenance_controller",
            ):
                await asyncio.to_thread(
                    run_inventory_cycle,
                    runtime.producer,
                    scopes=runtime.scopes,
                )
        except MaintenanceContractError as exc:
            if exc.code != "PLATFORM_MAINTENANCE":
                raise
        next_interval = (
            interval_seconds_provider()
            if interval_seconds_provider is not None
            else runtime.interval_seconds
        )
        if next_interval <= 0:
            raise ValueError("storage inventory interval must be positive")
        await asyncio.sleep(next_interval)


def _inventory_task_id(raw_scope: str) -> str:
    scope = parse_inventory_scope(raw_scope)
    canonical_scope = f"{scope.organization_id}/{scope.project_id}/{scope.region_code}"
    return f"storage-inventory:sha256:{hashlib.sha256(canonical_scope.encode()).hexdigest()}"


async def _renew_task_lease(
    repository: PlatformTaskLeaseRepository,
    lease_id: UUID,
) -> None:
    while True:
        await asyncio.sleep(TASK_LEASE_RENEWAL_INTERVAL_SECONDS)
        await asyncio.to_thread(repository.renew, lease_id)


async def _serve_singleton_scope(
    runtime: StorageInventoryRuntime,
    *,
    raw_scope: str,
    interval_seconds_provider: Callable[[], float] | None,
    maintenance_gate: MaintenanceWriteGate | None,
    environment_id: EnvironmentId,
    writer_id: SafeActorId,
    task_lease_repository: PlatformTaskLeaseRepository,
    owner_instance_id: UUID,
) -> None:
    task_id = _inventory_task_id(raw_scope)
    while True:
        next_interval = (
            interval_seconds_provider()
            if interval_seconds_provider is not None
            else runtime.interval_seconds
        )
        if next_interval <= 0:
            raise ValueError("storage inventory interval must be positive")
        try:
            lease = await asyncio.to_thread(
                task_lease_repository.try_acquire,
                environment_id=environment_id,
                task_id=task_id,
                owner_instance_id=owner_instance_id,
            )
        except MaintenanceContractError as exc:
            if exc.code != "PLATFORM_MAINTENANCE":
                raise
            await asyncio.sleep(min(next_interval, TASK_LEASE_RENEWAL_INTERVAL_SECONDS))
            continue
        except Exception:
            logger.exception("storage inventory singleton lease acquisition failed")
            await asyncio.sleep(min(next_interval, TASK_LEASE_RENEWAL_INTERVAL_SECONDS))
            continue
        if lease is None:
            await asyncio.sleep(min(next_interval, TASK_LEASE_RENEWAL_INTERVAL_SECONDS))
            continue

        lease_token = bind_platform_task_lease(lease.lease_id)
        renew_task = asyncio.create_task(
            _renew_task_lease(task_lease_repository, lease.lease_id),
            name=f"storage-inventory-lease-{str(lease.lease_id)[:8]}",
        )
        try:
            while True:
                with writer_permit_scope(
                    maintenance_gate,
                    environment_id=environment_id,
                    writer_id=writer_id,
                    writer_kind="maintenance_controller",
                ):
                    await asyncio.to_thread(
                        run_inventory_cycle,
                        runtime.producer,
                        scopes=(raw_scope,),
                    )
                delay = asyncio.create_task(asyncio.sleep(next_interval))
                done, _pending = await asyncio.wait(
                    {delay, renew_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if renew_task in done:
                    delay.cancel()
                    await asyncio.gather(delay, return_exceptions=True)
                    renew_task.result()
                next_interval = (
                    interval_seconds_provider()
                    if interval_seconds_provider is not None
                    else runtime.interval_seconds
                )
                if next_interval <= 0:
                    raise ValueError("storage inventory interval must be positive")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("storage inventory singleton ownership was lost")
        finally:
            reset_platform_task_lease(lease_token)
            renew_task.cancel()
            await asyncio.gather(renew_task, return_exceptions=True)
            with suppress(Exception):
                await asyncio.to_thread(task_lease_repository.release, lease.lease_id)
