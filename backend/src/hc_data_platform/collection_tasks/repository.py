from __future__ import annotations

from datetime import datetime
from threading import RLock

from hc_data_platform.core.errors import problem

from .models import (
    CollectionTaskRecord,
    CollectionTaskStatus,
    ProgressFacts,
    QcOutcome,
    utc_now,
)


def task_not_found() -> Exception:
    return problem(
        status=404,
        code="COLLECTION_TASK_NOT_FOUND",
        title="Collection task not found",
        detail="The collection task does not exist in this project scope.",
    )


def task_closed() -> Exception:
    return problem(
        status=409,
        code="COLLECTION_TASK_CLOSED",
        title="Collection task is closed",
        detail="A closed collection task cannot be changed or accept a new data package.",
    )


def version_conflict(current_version: int) -> Exception:
    return problem(
        status=412,
        code="ETAG_MISMATCH",
        title="Resource version mismatch",
        detail="The collection task changed after it was read.",
        details={"expected": f'"v{current_version}"'},
    )


class InMemoryCollectionTaskRepository:
    """Thread-safe reference repository, including close/package linearization."""

    def __init__(self) -> None:
        self._tasks: dict[tuple[str, str], CollectionTaskRecord] = {}
        self._packages: dict[
            tuple[str, str],
            dict[
                tuple[str, str],
                tuple[QcOutcome | None, tuple[str, ...], tuple[str, ...], tuple[str, ...]],
            ],
        ] = {}
        self._task_code = 0
        self._lock = RLock()

    def create(self, task: CollectionTaskRecord) -> CollectionTaskRecord:
        key = (task.project_id, task.collection_task_id)
        with self._lock:
            existing = self._tasks.get(key)
            if existing is not None:
                if existing.create_fingerprint != task.create_fingerprint:
                    raise problem(
                        status=409,
                        code="IDEMPOTENCY_KEY_REUSED",
                        title="Idempotency key reused",
                        detail="The create identity already exists with different content.",
                    )
                return existing
            self._task_code += 1
            if self._task_code > 99_999_999:
                raise RuntimeError("collection task code space exhausted")
            persisted = task.model_copy(update={"task_code": f"{self._task_code:08d}"})
            self._tasks[key] = persisted
            self._packages[key] = {}
            return persisted

    def list(
        self,
        *,
        project_id: str,
        status: CollectionTaskStatus | None,
        limit: int,
        after: tuple[datetime, str] | None,
    ) -> tuple[tuple[CollectionTaskRecord, ...], bool]:
        with self._lock:
            records = [
                item
                for (saved_project, _), item in self._tasks.items()
                if saved_project == project_id and (status is None or item.status is status)
            ]
            records.sort(key=lambda item: (item.created_at, item.collection_task_id), reverse=True)
            if after is not None:
                records = [
                    item for item in records if (item.created_at, item.collection_task_id) < after
                ]
            return tuple(records[:limit]), len(records) > limit

    def get(self, project_id: str, collection_task_id: str) -> CollectionTaskRecord | None:
        with self._lock:
            return self._tasks.get((project_id, collection_task_id))

    def update(
        self,
        *,
        project_id: str,
        collection_task_id: str,
        expected_version: int,
        changes: dict[str, object],
    ) -> CollectionTaskRecord:
        key = (project_id, collection_task_id)
        with self._lock:
            current = self._tasks.get(key)
            if current is None:
                raise task_not_found()
            if current.status is CollectionTaskStatus.CLOSED:
                raise task_closed()
            if current.version != expected_version:
                raise version_conflict(current.version)
            updated = CollectionTaskRecord.model_validate(
                {
                    **current.model_dump(),
                    **changes,
                    "version": current.version + 1,
                    "updated_at": utc_now(),
                }
            )
            self._tasks[key] = updated
            return updated

    def close(
        self,
        *,
        project_id: str,
        collection_task_id: str,
        expected_version: int,
    ) -> CollectionTaskRecord:
        key = (project_id, collection_task_id)
        with self._lock:
            current = self._tasks.get(key)
            if current is None:
                raise task_not_found()
            if current.status is CollectionTaskStatus.CLOSED:
                return current
            if current.version != expected_version:
                raise version_conflict(current.version)
            closed = current.model_copy(
                update={
                    "status": CollectionTaskStatus.CLOSED,
                    "version": current.version + 1,
                    "updated_at": utc_now(),
                }
            )
            self._tasks[key] = closed
            return closed

    def record_received_package(
        self,
        *,
        project_id: str,
        collection_task_id: str,
        package_id: str,
        qc_outcome: QcOutcome | None = None,
        region_code: str = "cn-test",
        device_ids: tuple[str, ...] = (),
        camera_ids: tuple[str, ...] = (),
        topic_names: tuple[str, ...] = (),
    ) -> None:
        """Test/reference equivalent of the PostgreSQL association trigger."""

        key = (project_id, collection_task_id)
        with self._lock:
            current = self._tasks.get(key)
            if current is None:
                raise task_not_found()
            packages = self._packages[key]
            package_key = (region_code, package_id)
            if package_key not in packages and current.status is CollectionTaskStatus.CLOSED:
                raise task_closed()
            existing = packages.get(package_key)
            if existing is None:
                packages[package_key] = (qc_outcome, device_ids, camera_ids, topic_names)
                return
            packages[package_key] = (
                qc_outcome,
                tuple(sorted(set(existing[1]).union(device_ids))),
                tuple(sorted(set(existing[2]).union(camera_ids))),
                tuple(sorted(set(existing[3]).union(topic_names))),
            )

    def progress(
        self,
        project_id: str,
        collection_task_id: str,
        region_code: str,
    ) -> ProgressFacts:
        key = (project_id, collection_task_id)
        with self._lock:
            if key not in self._tasks:
                raise task_not_found()
            package_facts = tuple(
                fact
                for (saved_region, _), fact in self._packages[key].items()
                if saved_region == region_code
            )
            outcomes = tuple(fact[0] for fact in package_facts)
            return ProgressFacts(
                as_of=utc_now(),
                received_package_count=len(outcomes),
                pass_count=outcomes.count(QcOutcome.PASS),
                risk_count=outcomes.count(QcOutcome.RISK),
                reject_count=outcomes.count(QcOutcome.REJECT),
                device_ids=tuple(sorted({item for fact in package_facts for item in fact[1]})),
                camera_ids=tuple(sorted({item for fact in package_facts for item in fact[2]})),
                topic_names=tuple(sorted({item for fact in package_facts for item in fact[3]})),
            )
