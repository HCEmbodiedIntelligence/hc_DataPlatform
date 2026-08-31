from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    current_request_context,
    reset_request_context,
)
from hc_data_platform.platform_control.maintenance_contract import MaintenanceContractError
from hc_data_platform.platform_ops.maintenance import (
    EnvironmentId,
    MaintenanceWriteGate,
    SafeActorId,
    writer_permit_scope,
)

from .models import AlignedMediaScopeV1
from .service import AlignedMediaLifecycleService

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


class PostgresAlignedMediaVersionRetirementCollector:
    """Lease deletion-triggered receipts and retire their exact MP4 manifests."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        lifecycle: AlignedMediaLifecycleService,
        *,
        batch_size: int = 100,
        lease_duration: timedelta = timedelta(minutes=30),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not 1 <= batch_size <= 1_000:
            raise ValueError("aligned-media retirement batch size must be between 1 and 1000")
        if lease_duration <= timedelta(0):
            raise ValueError("aligned-media retirement lease must be positive")
        self._connection_factory = connection_factory
        self._lifecycle = lifecycle
        self._batch_size = batch_size
        self._lease_duration = lease_duration
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def run_once(self) -> int:
        context = current_request_context()
        if (
            not context.service_identity
            or context.organization_id is None
            or context.project_id is None
            or context.region_code is None
        ):
            raise RuntimeError("aligned-media retirement requires a scoped service identity")
        now = self._now()
        lease_token = str(uuid4())
        candidates = self._claim(
            organization_id=context.organization_id,
            project_id=context.project_id,
            region_code=context.region_code,
            lease_token=lease_token,
            now=now,
        )
        scope = AlignedMediaScopeV1(
            organization_id=context.organization_id,
            project_id=context.project_id,
            region_code=context.region_code,
        )
        deleted_bytes = 0
        first_error: Exception | None = None
        for dataset_id, version_id, dataset_version in candidates:
            try:
                deleted_bytes += self._lifecycle.retire_dataset_version(
                    scope,
                    dataset_id=dataset_id,
                    dataset_version=dataset_version,
                )
                self._finish(
                    organization_id=context.organization_id,
                    project_id=context.project_id,
                    region_code=context.region_code,
                    dataset_id=dataset_id,
                    version_id=version_id,
                    lease_token=lease_token,
                    error=None,
                )
            except Exception as exc:
                self._finish(
                    organization_id=context.organization_id,
                    project_id=context.project_id,
                    region_code=context.region_code,
                    dataset_id=dataset_id,
                    version_id=version_id,
                    lease_token=lease_token,
                    error=exc,
                )
                first_error = first_error or exc
        if first_error is not None:
            raise first_error
        return deleted_bytes

    def _claim(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        lease_token: str,
        now: datetime,
    ) -> tuple[tuple[str, str, int], ...]:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    WITH candidates AS (
                        SELECT request.organization_id, request.project_id,
                               request.region_code, request.dataset_id, request.version_id
                          FROM aligned_media.dataset_version_retirement_requests AS request
                         WHERE request.organization_id = %s
                           AND request.project_id = %s
                           AND request.region_code = %s
                           AND (
                                request.status = 'PENDING'
                                OR (
                                    request.status = 'RUNNING'
                                    AND request.lease_expires_at <= %s
                                )
                           )
                         ORDER BY request.requested_at, request.dataset_id,
                                  request.dataset_version
                         FOR UPDATE SKIP LOCKED
                         LIMIT %s
                    )
                    UPDATE aligned_media.dataset_version_retirement_requests AS request
                       SET status = 'RUNNING', attempt = request.attempt + 1,
                           lease_token = %s::uuid, lease_expires_at = %s,
                           last_error = NULL, updated_at = %s
                      FROM candidates
                     WHERE request.organization_id = candidates.organization_id
                       AND request.project_id = candidates.project_id
                       AND request.region_code = candidates.region_code
                       AND request.dataset_id = candidates.dataset_id
                       AND request.version_id = candidates.version_id
                    RETURNING request.dataset_id, request.version_id,
                              request.dataset_version
                    """,
                    (
                        organization_id,
                        project_id,
                        region_code,
                        now,
                        self._batch_size,
                        lease_token,
                        now + self._lease_duration,
                        now,
                    ),
                )
                candidates = tuple(
                    (str(row[0]), str(row[1]), int(row[2])) for row in cursor.fetchall()
                )
            connection.commit()
            return candidates
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _finish(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        dataset_id: str,
        version_id: str,
        lease_token: str,
        error: Exception | None,
    ) -> None:
        now = self._now()
        status = "COMPLETED" if error is None else "PENDING"
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE aligned_media.dataset_version_retirement_requests
                       SET status = %s, lease_token = NULL, lease_expires_at = NULL,
                           last_error = %s, updated_at = %s,
                           completed_at = CASE WHEN %s = 'COMPLETED' THEN %s ELSE NULL END
                     WHERE organization_id = %s AND project_id = %s AND region_code = %s
                       AND dataset_id = %s AND version_id = %s
                       AND status = 'RUNNING' AND lease_token = %s::uuid
                    """,
                    (
                        status,
                        None if error is None else str(error)[:2_000],
                        now,
                        status,
                        now,
                        organization_id,
                        project_id,
                        region_code,
                        dataset_id,
                        version_id,
                        lease_token,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("aligned-media retirement lease was lost")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _now(self) -> datetime:
        value = self._clock()
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


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
