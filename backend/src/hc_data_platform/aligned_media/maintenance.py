from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypeVar

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.platform_control.maintenance_contract import MaintenanceContractError
from hc_data_platform.platform_ops.maintenance import (
    EnvironmentId,
    MaintenanceWriteGate,
    SafeActorId,
    writer_permit_scope,
)

_T = TypeVar("_T")


class MediaStagingSweeper:
    """Delete only expired direct children of explicit ephemeral staging roots."""

    def __init__(
        self,
        roots: tuple[Path, ...],
        *,
        ttl: timedelta,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._roots = tuple(root.resolve() for root in roots)
        if ttl <= timedelta(0):
            raise ValueError("media staging TTL must be positive")
        if any(root == Path("/") or len(root.parts) < 3 for root in self._roots):
            raise ValueError("media staging roots must be explicit non-root directories")
        self._ttl = ttl
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def run_once(self) -> int:
        cutoff = self._clock().timestamp() - self._ttl.total_seconds()
        deleted = 0
        for root in self._roots:
            if not root.exists():
                continue
            for candidate in root.iterdir():
                try:
                    if candidate.stat(follow_symlinks=False).st_mtime > cutoff:
                        continue
                    if candidate.is_symlink() or candidate.is_file():
                        candidate.unlink()
                    elif candidate.is_dir():
                        shutil.rmtree(candidate)
                    else:
                        continue
                    deleted += 1
                except FileNotFoundError:
                    continue
        return deleted


async def serve_media_maintenance(
    *,
    staging_sweeper: MediaStagingSweeper,
    scopes: tuple[str, ...],
    interval_seconds: float,
    interval_seconds_provider: Callable[[], float] | None = None,
    object_sweepers: tuple[Callable[[], int], ...] = (),
    scoped_object_sweepers: tuple[Callable[[], int], ...] = (),
    maintenance_gate: MaintenanceWriteGate | None = None,
    environment_id: EnvironmentId = "local",
    writer_id: SafeActorId = "media-maintenance-worker",
) -> None:
    if interval_seconds <= 0:
        raise ValueError("media maintenance interval must be positive")
    while True:
        await asyncio.to_thread(staging_sweeper.run_once)
        try:
            with writer_permit_scope(
                maintenance_gate,
                environment_id=environment_id,
                writer_id=writer_id,
                writer_kind="maintenance_controller",
            ):
                for sweep in object_sweepers:
                    with suppress(Exception):
                        await asyncio.to_thread(sweep)
                for scope in scopes:
                    for sweep in scoped_object_sweepers:
                        with suppress(Exception):
                            await asyncio.to_thread(_in_scope, scope, sweep)
        except MaintenanceContractError as exc:
            if exc.code != "PLATFORM_MAINTENANCE":
                raise
        delay = interval_seconds_provider() if interval_seconds_provider else interval_seconds
        if delay <= 0:
            raise ValueError("media maintenance interval must be positive")
        await asyncio.sleep(delay)


def _in_scope(scope: str, operation: Callable[[], _T]) -> _T:
    organization_id, project_id, region_code = scope.split("/", maxsplit=2)
    token = bind_request_context(
        RequestContext(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            service_identity=True,
        )
    )
    try:
        return operation()
    finally:
        reset_request_context(token)
