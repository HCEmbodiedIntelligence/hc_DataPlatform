from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import TypeVar

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)

from .metrics import (
    PREVIEW_GC_DELETED_BYTES,
    PREVIEW_GC_FAILURES,
    PREVIEW_STORAGE_ARTIFACTS,
    PREVIEW_STORAGE_BYTES,
)
from .models import PreviewArtifactV1, PreviewGcResultV1
from .ports import PreviewArtifactStorePort, PreviewRepositoryPort

T = TypeVar("T")


class PreviewGarbageCollector:
    """Exact-manifest TTL/LRU collector for rebuildable preview derivatives only."""

    def __init__(
        self,
        repository: PreviewRepositoryPort,
        store: PreviewArtifactStorePort,
        *,
        project_quota_bytes: int,
        global_quota_bytes: int,
        high_watermark_percent: int = 85,
        low_watermark_percent: int = 70,
        batch_size: int = 100,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if project_quota_bytes < 1 or global_quota_bytes < 1:
            raise ValueError("preview quotas must be positive")
        if not 1 <= low_watermark_percent < high_watermark_percent <= 100:
            raise ValueError("preview watermarks must satisfy 1 <= low < high <= 100")
        self._repository = repository
        self._store = store
        self._project_quota_bytes = project_quota_bytes
        self._global_quota_bytes = global_quota_bytes
        self._high_watermark_percent = high_watermark_percent
        self._low_watermark_percent = low_watermark_percent
        self._batch_size = batch_size
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def global_quota_bytes(self) -> int:
        return self._global_quota_bytes

    @property
    def high_watermark_percent(self) -> int:
        return self._high_watermark_percent

    @property
    def low_watermark_percent(self) -> int:
        return self._low_watermark_percent

    @property
    def batch_size(self) -> int:
        return self._batch_size

    def now(self) -> datetime:
        value = self._clock()
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    def storage_totals(self) -> tuple[int, int]:
        """Return READY totals visible through the currently bound RLS scope."""

        return self._repository.storage_totals()

    def oldest_reclaimable(self, *, now: datetime) -> PreviewArtifactV1 | None:
        """Return one unprotected LRU candidate in the current RLS scope."""

        candidates = self._repository.gc_candidates(
            now=now,
            # A one-byte quota deliberately makes every READY artifact eligible;
            # the cross-scope coordinator owns the aggregate watermark decision.
            project_quota_bytes=1,
            global_quota_bytes=1,
            high_watermark_percent=1,
            low_watermark_percent=1,
            limit=1,
        )
        return candidates[0] if candidates else None

    def reclaim_candidate(self, artifact: PreviewArtifactV1) -> tuple[bool, int]:
        """Remove one selected artifact by its immutable manifest.

        The boolean records that the artifact left READY accounting.  A zero byte
        result with ``True`` means object/metadata cleanup must be retried from its
        durable DELETING state.
        """

        if not self._repository.mark_deleting(
            artifact.scope,
            artifact.artifact_id,
            expected_version=artifact.version,
        ):
            return False, 0
        try:
            released = self._store.delete_exact(artifact.objects)
            self._repository.delete_artifact_metadata(
                artifact.scope, artifact.artifact_id
            )
            return True, released
        except Exception:
            PREVIEW_GC_FAILURES.inc()
            return True, 0

    def run_once(self, *, enforce_global_quota: bool = True) -> PreviewGcResultV1:
        now = self.now()
        self._repository.cleanup_expired_metadata(now=now)
        deleted_count = 0
        deleted_bytes = 0
        failures = 0
        details: list[dict[str, object]] = []

        # Finish interrupted exact-object deletions before selecting new victims.
        pending_deletes = self._repository.deleting_artifacts(limit=self._batch_size)
        for artifact in pending_deletes:
            try:
                released = self._store.delete_exact(artifact.objects)
                self._repository.delete_artifact_metadata(
                    artifact.scope, artifact.artifact_id
                )
                deleted_count += 1
                deleted_bytes += released
            except Exception as exc:
                failures += 1
                PREVIEW_GC_FAILURES.inc()
                details.append(
                    {
                        "artifact_id": artifact.artifact_id,
                        "outcome": "RETRY_REQUIRED",
                        "error_code": type(exc).__name__,
                    }
                )

        total_bytes, _ = self._repository.storage_totals()
        effective_global_quota = (
            self._global_quota_bytes
            if enforce_global_quota
            else self._project_quota_bytes
        )
        quota = min(self._project_quota_bytes, effective_global_quota)
        low_target = quota * self._low_watermark_percent // 100
        candidates = self._repository.gc_candidates(
            now=now,
            project_quota_bytes=self._project_quota_bytes,
            global_quota_bytes=effective_global_quota,
            high_watermark_percent=self._high_watermark_percent,
            low_watermark_percent=self._low_watermark_percent,
            limit=self._batch_size,
        )
        for artifact in candidates:
            # Expired artifacts are always reclaimed. Quota-driven LRU stops once
            # the low watermark is reached, preventing high-watermark thrashing.
            if artifact.expires_at > now and total_bytes <= low_target:
                break
            if not self._repository.mark_deleting(
                artifact.scope,
                artifact.artifact_id,
                expected_version=artifact.version,
            ):
                continue
            try:
                released = self._store.delete_exact(artifact.objects)
                self._repository.delete_artifact_metadata(
                    artifact.scope, artifact.artifact_id
                )
                deleted_count += 1
                deleted_bytes += released
                total_bytes = max(0, total_bytes - artifact.total_bytes)
            except Exception as exc:
                failures += 1
                PREVIEW_GC_FAILURES.inc()
                details.append(
                    {
                        "artifact_id": artifact.artifact_id,
                        "outcome": "RETRY_REQUIRED",
                        "error_code": type(exc).__name__,
                    }
                )
        PREVIEW_GC_DELETED_BYTES.inc(deleted_bytes)
        remaining_bytes, remaining_artifacts = self._repository.storage_totals()
        PREVIEW_STORAGE_BYTES.set(remaining_bytes)
        PREVIEW_STORAGE_ARTIFACTS.set(remaining_artifacts)
        return PreviewGcResultV1(
            deleted_artifacts=deleted_count,
            deleted_bytes=deleted_bytes,
            failed_artifacts=failures,
            details=tuple(details),
        )


class PreviewStagingSweeper:
    """Delete only expired direct children of explicit worker staging roots."""

    def __init__(
        self,
        roots: tuple[Path, ...],
        *,
        ttl: timedelta,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        resolved = tuple(root.resolve() for root in roots)
        if ttl <= timedelta(0):
            raise ValueError("preview staging TTL must be positive")
        if any(root == Path("/") or len(root.parts) < 3 for root in resolved):
            raise ValueError("preview staging roots must be explicit non-root directories")
        self._roots = resolved
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
                except OSError:
                    PREVIEW_GC_FAILURES.inc()
        return deleted


async def serve_preview_maintenance(
    *,
    collector: PreviewGarbageCollector | None,
    scopes: tuple[str, ...],
    staging_sweeper: PreviewStagingSweeper,
    interval_seconds: float,
    object_staging_sweepers: tuple[Callable[[], int], ...] = (),
) -> None:
    """Run exact-object GC and local staging cleanup immediately and periodically."""

    if interval_seconds <= 0:
        raise ValueError("preview maintenance interval must be positive")
    while True:
        await asyncio.to_thread(staging_sweeper.run_once)
        for sweep in object_staging_sweepers:
            try:
                await asyncio.to_thread(sweep)
            except Exception:
                PREVIEW_GC_FAILURES.inc()
        if collector is not None:
            for scope in scopes:
                await asyncio.to_thread(_run_scoped_gc, collector, scope)
            await asyncio.to_thread(_run_global_gc, collector, scopes)
        await asyncio.sleep(interval_seconds)


def _run_scoped_gc(collector: PreviewGarbageCollector, scope: str) -> None:
    try:
        _in_scope(scope, lambda: collector.run_once(enforce_global_quota=False))
    except Exception:
        PREVIEW_GC_FAILURES.inc()


def _run_global_gc(
    collector: PreviewGarbageCollector, scopes: tuple[str, ...]
) -> None:
    """Coordinate one true aggregate quota across configured, RLS-bound scopes."""

    unique_scopes = tuple(dict.fromkeys(scopes))
    totals: dict[str, tuple[int, int]] = {}
    for scope in unique_scopes:
        try:
            totals[scope] = _in_scope(scope, collector.storage_totals)
        except Exception:
            PREVIEW_GC_FAILURES.inc()

    total_bytes = sum(item[0] for item in totals.values())
    total_artifacts = sum(item[1] for item in totals.values())
    PREVIEW_STORAGE_BYTES.set(total_bytes)
    PREVIEW_STORAGE_ARTIFACTS.set(total_artifacts)
    if (
        not totals
        or total_bytes * 100
        < collector.global_quota_bytes * collector.high_watermark_percent
    ):
        return

    now = collector.now()
    low_target = (
        collector.global_quota_bytes * collector.low_watermark_percent // 100
    )
    attempts = 0
    while total_bytes > low_target and attempts < collector.batch_size:
        candidates: list[tuple[str, PreviewArtifactV1]] = []
        for scope in totals:
            try:
                candidate = _in_scope(
                    scope, lambda: collector.oldest_reclaimable(now=now)
                )
            except Exception:
                PREVIEW_GC_FAILURES.inc()
                continue
            if candidate is not None:
                candidates.append((scope, candidate))
        if not candidates:
            break
        scope, candidate = min(
            candidates,
            key=lambda item: (
                item[1].expires_at > now,
                item[1].last_accessed_at,
                item[1].artifact_id,
            ),
        )
        try:
            removed, released = _in_scope(
                scope,
                partial(collector.reclaim_candidate, candidate),
            )
        except Exception:
            PREVIEW_GC_FAILURES.inc()
            removed, released = False, 0
        attempts += 1
        if not removed:
            continue
        total_bytes = max(0, total_bytes - candidate.total_bytes)
        total_artifacts = max(0, total_artifacts - 1)
        PREVIEW_GC_DELETED_BYTES.inc(released)

    PREVIEW_STORAGE_BYTES.set(total_bytes)
    PREVIEW_STORAGE_ARTIFACTS.set(total_artifacts)


def _in_scope(scope: str, operation: Callable[[], T]) -> T:
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
        with suppress(Exception):
            reset_request_context(token)
