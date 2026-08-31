"""Quota-bound, exact-object lifecycle for frame-selection manifests."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, cast
from uuid import uuid4

from prometheus_client import Counter, Gauge

FRAME_SELECTION_GC_OBJECTS = Counter(
    "frame_selection_gc_objects_total",
    "Frame-selection objects deleted after a fenced lifecycle transition.",
)
FRAME_SELECTION_GC_BYTES = Counter(
    "frame_selection_gc_bytes_total",
    "Frame-selection bytes released from project quota.",
)
FRAME_SELECTION_GC_FAILURES = Counter(
    "frame_selection_gc_failures_total",
    "Frame-selection exact-object deletions requiring retry.",
)
FRAME_SELECTION_QUOTA_BYTES = Gauge(
    "frame_selection_quota_used_bytes",
    "Frame-selection bytes charged to the current project scope.",
)


@dataclass(frozen=True, slots=True)
class FrameSelectionDeletionReceipt:
    task_id: str
    object_key: str
    size_bytes: int
    deletion_token: str


class FrameSelectionLifecycleRepositoryPort(Protocol):
    def claim_expired(self, *, now: datetime) -> FrameSelectionDeletionReceipt | None: ...

    def finish_deletion(self, receipt: FrameSelectionDeletionReceipt) -> bool: ...

    def record_failure(self, receipt: FrameSelectionDeletionReceipt, error_code: str) -> None: ...

    def used_bytes(self) -> int: ...


class ExactObjectDeletePort(Protocol):
    def delete(self, key: str) -> None: ...


class PostgresFrameSelectionLifecycleRepository:
    """Fence deletion in PostgreSQL before touching one exact object key."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._factory = connection_factory

    def claim_expired(self, *, now: datetime) -> FrameSelectionDeletionReceipt | None:
        connection = self._factory()
        cursor = connection.cursor()
        token = str(uuid4())
        try:
            cursor.execute(
                """
                WITH candidate AS (
                    SELECT manifest.task_id
                    FROM annotation.frame_selection_manifests AS manifest
                    WHERE manifest.lifecycle_status = 'ACTIVE'
                      AND NOT manifest.legal_hold
                      AND NOT manifest.governance_hold
                      AND manifest.retention_until <= %s
                      AND manifest.expires_at <= %s
                      AND NOT EXISTS (
                          SELECT 1
                          FROM annotation.auto_annotation_jobs AS job
                          WHERE job.task_id = manifest.task_id
                            AND job.status IN ('QUEUED', 'RUNNING')
                            AND job.sampling_reference->>'object_key' = manifest.object_key
                      )
                    ORDER BY manifest.expires_at, manifest.task_id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE annotation.frame_selection_manifests AS manifest
                SET lifecycle_status = 'DELETING',
                    deletion_token = %s::uuid,
                    deletion_started_at = %s,
                    deletion_error_code = NULL
                FROM candidate
                WHERE manifest.task_id = candidate.task_id
                  AND manifest.lifecycle_status = 'ACTIVE'
                RETURNING manifest.task_id, manifest.object_key,
                          manifest.size_bytes, manifest.deletion_token::text
                """,
                (now, now, token, now),
            )
            row = cursor.fetchone()
            connection.commit()
            return None if row is None else _receipt(cursor, row)
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def finish_deletion(self, receipt: FrameSelectionDeletionReceipt) -> bool:
        connection = self._factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                DELETE FROM annotation.frame_selection_manifests
                WHERE task_id = %s
                  AND lifecycle_status = 'DELETING'
                  AND deletion_token = %s::uuid
                  AND object_key = %s
                """,
                (receipt.task_id, receipt.deletion_token, receipt.object_key),
            )
            removed = bool(cursor.rowcount == 1)
            connection.commit()
            return removed
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def record_failure(self, receipt: FrameSelectionDeletionReceipt, error_code: str) -> None:
        connection = self._factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE annotation.frame_selection_manifests
                SET deletion_error_code = %s
                WHERE task_id = %s
                  AND lifecycle_status = 'DELETING'
                  AND deletion_token = %s::uuid
                """,
                (error_code[:128], receipt.task_id, receipt.deletion_token),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def used_bytes(self) -> int:
        connection = self._factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT COALESCE(sum(used_bytes), 0)
                FROM annotation.frame_selection_project_quotas
                """
            )
            row = cursor.fetchone()
            if row is None:
                return 0
            value = (
                next(iter(row.values()))
                if isinstance(row, Mapping)
                else cast(Sequence[object], row)[0]
            )
            if not isinstance(value, int):
                raise TypeError("frame-selection quota total must be an integer")
            return value
        finally:
            cursor.close()
            connection.close()


class FrameSelectionLifecycleCollector:
    """Retry-safe collector: claim, delete one exact key, then release quota."""

    def __init__(
        self,
        repository: FrameSelectionLifecycleRepositoryPort,
        objects: ExactObjectDeletePort,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        batch_size: int = 32,
    ) -> None:
        if batch_size < 1 or batch_size > 1_000:
            raise ValueError("frame-selection GC batch size must be between 1 and 1000")
        self._repository = repository
        self._objects = objects
        self._clock = clock
        self._batch_size = batch_size

    def run_once(self) -> int:
        deleted = 0
        for _ in range(self._batch_size):
            receipt = self._repository.claim_expired(now=self._now())
            if receipt is None:
                break
            try:
                # ``object_key`` comes from the immutable, scope-bound manifest.
                # No prefix or recursive deletion is exposed by this port.
                self._objects.delete(receipt.object_key)
                if self._repository.finish_deletion(receipt):
                    deleted += 1
                    FRAME_SELECTION_GC_OBJECTS.inc()
                    FRAME_SELECTION_GC_BYTES.inc(receipt.size_bytes)
            except Exception as exc:
                FRAME_SELECTION_GC_FAILURES.inc()
                self._repository.record_failure(receipt, type(exc).__name__)
        FRAME_SELECTION_QUOTA_BYTES.set(self._repository.used_bytes())
        return deleted

    def _now(self) -> datetime:
        now = self._clock()
        return now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)


def _receipt(cursor: Any, row: object) -> FrameSelectionDeletionReceipt:
    if isinstance(row, Mapping):
        values = row
    else:
        names = tuple(str(column[0]) for column in cursor.description)
        values = dict(zip(names, cast(Sequence[object], row), strict=True))
    return FrameSelectionDeletionReceipt(
        task_id=str(values["task_id"]),
        object_key=str(values["object_key"]),
        size_bytes=int(str(values["size_bytes"])),
        deletion_token=str(values["deletion_token"]),
    )
