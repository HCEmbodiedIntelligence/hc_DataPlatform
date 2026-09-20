"""Long-running, exact-scope outbox dispatch loop for the Temporal Worker."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    clear_request_context,
    reset_request_context,
)
from hc_data_platform.platform_control.maintenance_contract import MaintenanceContractError
from hc_data_platform.platform_ops.maintenance import (
    EnvironmentId,
    MaintenanceWriteGate,
    SafeActorId,
    writer_permit_scope,
)
from hc_data_platform.security.outbox import OutboxDispatcher
from hc_data_platform.storage.dispatch import StorageScheduleEnqueuer


def _parse_scope(value: str) -> tuple[str, str, str]:
    parts = tuple(part.strip() for part in value.split("/"))
    if len(parts) != 3 or not all(parts):
        raise ValueError("outbox scope must be organization_id/project_id/region_code")
    return parts[0], parts[1], parts[2]


async def serve_outbox(
    dispatcher: OutboxDispatcher,
    *,
    scopes: tuple[str, ...],
    poll_interval_seconds: float,
    batch_size: int,
    schedule_enqueuer: StorageScheduleEnqueuer | None = None,
    maintenance_gate: MaintenanceWriteGate | None = None,
    environment_id: EnvironmentId = "local",
    writer_id: SafeActorId = "outbox-worker",
    scope_provider: Callable[[], tuple[str, ...]] | None = None,
) -> None:
    """Discover local scopes if enabled, then dispatch under each exact tenant scope."""

    parsed = tuple(_parse_scope(item) for item in scopes)
    refreshed_at = float("-inf")
    while True:
        if scope_provider is not None and time.monotonic() - refreshed_at >= 5:
            discovered = await asyncio.to_thread(scope_provider)
            parsed = tuple(_parse_scope(item) for item in dict.fromkeys((*scopes, *discovered)))
            refreshed_at = time.monotonic()
        dispatched = False
        for organization_id, project_id, region_code in parsed:
            token = bind_request_context(
                RequestContext(
                    organization_id=organization_id,
                    project_id=project_id,
                    region_code=region_code,
                    subject_id="outbox-dispatcher",
                    service_identity=True,
                )
            )
            try:
                try:
                    with writer_permit_scope(
                        maintenance_gate,
                        environment_id=environment_id,
                        writer_id=writer_id,
                        writer_kind="outbox_claim",
                    ):
                        if schedule_enqueuer is not None:
                            scheduled = schedule_enqueuer.enqueue(
                                project_id=project_id,
                                region_code=region_code,
                                limit=batch_size,
                            )
                            dispatched = dispatched or scheduled > 0
                        for _ in range(batch_size):
                            if not await dispatcher.dispatch_one(
                                organization_id=organization_id,
                                project_id=project_id,
                                region_code=region_code,
                            ):
                                break
                            dispatched = True
                except MaintenanceContractError as exc:
                    if exc.code != "PLATFORM_MAINTENANCE":
                        raise
            finally:
                reset_request_context(token)
                # A long-lived worker must never inherit a request/test scope
                # after completing its explicit tenant claim.
                clear_request_context()
        if not dispatched:
            await asyncio.sleep(poll_interval_seconds)


def local_project_scopes(postgres_dsn: str) -> tuple[str, ...]:
    """Discover local project queues; each subsequent claim still binds an exact tenant.

    Only the explicit local development profile enables this directory read. Managed
    deployments continue to require the operator's fixed scope allowlist.
    """
    import psycopg

    with (
        psycopg.connect(
            postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
        ) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            SELECT DISTINCT project.organization_id, project.project_id, queue.region_code
            FROM registry.organization_projects project
            JOIN (
                SELECT organization_id, project_id, region_code FROM core.outbox_events
                WHERE published_at IS NULL AND region_code IS NOT NULL
                UNION
                SELECT organization_id, project_id, upload_region_code
                FROM collection_tasks.collection_tasks
                WHERE upload_region_code IS NOT NULL
            ) queue ON queue.organization_id = project.organization_id
                   AND queue.project_id = project.project_id
            ORDER BY 1, 2, 3
            """
        )
        return tuple("/".join(str(part) for part in row) for row in cursor.fetchall())
