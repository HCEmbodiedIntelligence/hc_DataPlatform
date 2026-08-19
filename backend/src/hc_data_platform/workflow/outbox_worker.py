"""Long-running, exact-scope outbox dispatch loop for the Temporal Worker."""

from __future__ import annotations

import asyncio

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.security.outbox import OutboxDispatcher


async def serve_outbox(
    dispatcher: OutboxDispatcher,
    *,
    scopes: tuple[str, ...],
    poll_interval_seconds: float,
    batch_size: int,
) -> None:
    """Drain configured tenant scopes without ever opening an unscoped DB connection."""

    parsed = tuple(tuple(item.split("/", maxsplit=1)) for item in scopes)
    while True:
        dispatched = False
        for project_id, region_code in parsed:
            token = bind_request_context(
                RequestContext(
                    project_id=project_id,
                    region_code=region_code,
                    subject_id="outbox-dispatcher",
                    service_identity=True,
                )
            )
            try:
                for _ in range(batch_size):
                    if not await dispatcher.dispatch_one(
                        project_id=project_id,
                        region_code=region_code,
                    ):
                        break
                    dispatched = True
            finally:
                reset_request_context(token)
        if not dispatched:
            await asyncio.sleep(poll_interval_seconds)
