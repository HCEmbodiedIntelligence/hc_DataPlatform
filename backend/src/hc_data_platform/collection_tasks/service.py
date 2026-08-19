from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid5

from hc_data_platform.core.errors import problem
from hc_data_platform.core.pagination import CursorCodec
from hc_data_platform.security.idempotency import (
    IdempotencyStore,
    InMemoryIdempotencyStore,
    request_fingerprint,
)
from hc_data_platform.security.versioning import ResourceVersion

from .models import (
    CollectionTaskPage,
    CollectionTaskProgress,
    CollectionTaskQcProgress,
    CollectionTaskRatio,
    CollectionTaskRecord,
    CollectionTaskStatus,
    CreateCollectionTask,
    ManifestObservedSources,
    UpdateCollectionTask,
)
from .ports import CollectionTaskRepositoryPort
from .repository import InMemoryCollectionTaskRepository, task_not_found

_TASK_ID_NAMESPACE = UUID("e076395c-8aa8-44cd-87e0-10e9b25a910d")
_CURSOR_VERSION = 1


@dataclass(frozen=True, slots=True)
class CommandResult:
    record: CollectionTaskRecord
    replayed: bool


class CollectionTaskService:
    def __init__(
        self,
        repository: CollectionTaskRepositoryPort | None = None,
        idempotency: IdempotencyStore | None = None,
        *,
        cursor_secret: str = "collection-task-local-cursor-secret",
    ) -> None:
        self.repository = repository or InMemoryCollectionTaskRepository()
        self.idempotency = idempotency or InMemoryIdempotencyStore()
        self._cursor = CursorCodec(cursor_secret)

    @staticmethod
    def etag(record: CollectionTaskRecord) -> str:
        return ResourceVersion(record.version).etag

    def create(
        self,
        *,
        project_id: str,
        command: CreateCollectionTask,
        idempotency_key: str,
    ) -> CommandResult:
        payload = command.model_dump(mode="json")
        fingerprint = request_fingerprint(payload)
        task_id = str(uuid5(_TASK_ID_NAMESPACE, f"{project_id}\0{idempotency_key}"))

        def create_once() -> CollectionTaskRecord:
            return self.repository.create(
                CollectionTaskRecord(
                    collection_task_id=task_id,
                    project_id=project_id,
                    name=command.name,
                    type=command.type,
                    scenario=command.scenario,
                    description=command.description,
                    target=command.target,
                    quality_threshold=command.quality_threshold,
                    create_fingerprint=fingerprint,
                )
            )

        result = self.idempotency.execute(
            scope=project_id,
            key=f"collection-task:create:{idempotency_key}",
            payload=payload,
            action=create_once,
        )
        return CommandResult(record=result.value, replayed=result.replayed)

    def list(
        self,
        *,
        project_id: str,
        status: CollectionTaskStatus | None,
        limit: int,
        cursor: str | None,
    ) -> CollectionTaskPage:
        after = self._decode_cursor(cursor, project_id=project_id, status=status)
        records, has_more = self.repository.list(
            project_id=project_id,
            status=status,
            limit=limit,
            after=after,
        )
        next_cursor = None
        if has_more and records:
            last = records[-1]
            next_cursor = self._cursor.encode(
                {
                    "v": _CURSOR_VERSION,
                    "project_id": project_id,
                    "status": None if status is None else status.value,
                    "created_at": last.created_at.isoformat(),
                    "collection_task_id": last.collection_task_id,
                }
            )
        return CollectionTaskPage(
            items=tuple(record.public() for record in records),
            next_cursor=next_cursor,
        )

    def detail(self, project_id: str, collection_task_id: str) -> CollectionTaskRecord:
        record = self.repository.get(project_id, collection_task_id)
        if record is None:
            raise task_not_found()
        return record

    def update(
        self,
        *,
        project_id: str,
        collection_task_id: str,
        command: UpdateCollectionTask,
        if_match: str,
    ) -> CollectionTaskRecord:
        changes = command.model_dump(exclude_unset=True)
        if not changes:
            raise problem(
                status=422,
                code="COLLECTION_TASK_UPDATE_EMPTY",
                title="Collection task update is empty",
                detail="At least one editable task field must be supplied.",
            )
        expected_version = ResourceVersion.from_etag(if_match).value
        return self.repository.update(
            project_id=project_id,
            collection_task_id=collection_task_id,
            expected_version=expected_version,
            changes=changes,
        )

    def close(
        self,
        *,
        project_id: str,
        collection_task_id: str,
        if_match: str,
        idempotency_key: str,
    ) -> CommandResult:
        expected_version = ResourceVersion.from_etag(if_match).value
        payload = {
            "collection_task_id": collection_task_id,
            "if_match": if_match,
        }

        def close_once() -> CollectionTaskRecord:
            return self.repository.close(
                project_id=project_id,
                collection_task_id=collection_task_id,
                expected_version=expected_version,
            )

        result = self.idempotency.execute(
            scope=project_id,
            key=f"collection-task:close:{collection_task_id}:{idempotency_key}",
            payload=payload,
            action=close_once,
        )
        return CommandResult(record=result.value, replayed=result.replayed)

    def progress(
        self,
        project_id: str,
        collection_task_id: str,
        region_code: str,
    ) -> CollectionTaskProgress:
        task = self.detail(project_id, collection_task_id)
        facts = self.repository.progress(project_id, collection_task_id, region_code)
        evaluated = facts.evaluated_count
        if facts.pending_count < 0:
            raise RuntimeError("QC facts cannot exceed received package facts")
        return CollectionTaskProgress(
            collection_task_id=collection_task_id,
            project_id=project_id,
            status=task.status,
            as_of=facts.as_of,
            received_package_count=facts.received_package_count,
            qc=CollectionTaskQcProgress(
                evaluated_count=evaluated,
                pass_count=facts.pass_count,
                risk_count=facts.risk_count,
                reject_count=facts.reject_count,
                pending_count=facts.pending_count,
                pass_rate=CollectionTaskRatio(
                    numerator=facts.pass_count,
                    denominator=evaluated,
                    value=None if evaluated == 0 else facts.pass_count / evaluated,
                ),
            ),
            observed_sources=ManifestObservedSources(
                device_ids=facts.device_ids,
                camera_ids=facts.camera_ids,
                topic_names=facts.topic_names,
            ),
        )

    def _decode_cursor(
        self,
        cursor: str | None,
        *,
        project_id: str,
        status: CollectionTaskStatus | None,
    ) -> tuple[datetime, str] | None:
        if cursor is None:
            return None
        payload = self._cursor.decode(cursor)
        expected_status = None if status is None else status.value
        try:
            if (
                payload["v"] != _CURSOR_VERSION
                or payload["project_id"] != project_id
                or payload["status"] != expected_status
            ):
                raise ValueError
            created_at = datetime.fromisoformat(str(payload["created_at"]))
            task_id = str(payload["collection_task_id"])
            if created_at.tzinfo is None or not task_id:
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise problem(
                status=400,
                code="INVALID_CURSOR",
                title="Invalid pagination cursor",
                detail="The cursor does not belong to this collection-task query.",
            ) from exc
        return created_at, task_id
