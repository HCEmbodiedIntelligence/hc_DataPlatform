"""Long-running and one-shot execution helpers for storage inventory."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    clear_request_context,
    reset_request_context,
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


async def serve_inventory(runtime: StorageInventoryRuntime) -> None:
    """Publish immediately on worker start and then at the configured interval."""

    while True:
        await asyncio.to_thread(
            run_inventory_cycle,
            runtime.producer,
            scopes=runtime.scopes,
        )
        await asyncio.sleep(runtime.interval_seconds)
