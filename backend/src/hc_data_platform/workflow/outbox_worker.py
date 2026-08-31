"""Long-running, exact-scope outbox dispatch loop for the Temporal Worker."""

from __future__ import annotations

import asyncio

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
) -> None:
    """Drain configured tenant scopes without ever opening an unscoped DB connection."""

    parsed = tuple(_parse_scope(item) for item in scopes)
    while True:
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
