from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import CollectionTaskRecord, CollectionTaskStatus, ProgressFacts


class CollectionTaskRepositoryPort(Protocol):
    def create(self, task: CollectionTaskRecord) -> CollectionTaskRecord: ...

    def list(
        self,
        *,
        project_id: str,
        status: CollectionTaskStatus | None,
        limit: int,
        after: tuple[datetime, str] | None,
    ) -> tuple[tuple[CollectionTaskRecord, ...], bool]: ...

    def get(self, project_id: str, collection_task_id: str) -> CollectionTaskRecord | None: ...

    def update(
        self,
        *,
        project_id: str,
        collection_task_id: str,
        expected_version: int,
        changes: dict[str, object],
    ) -> CollectionTaskRecord: ...

    def close(
        self,
        *,
        project_id: str,
        collection_task_id: str,
        expected_version: int,
    ) -> CollectionTaskRecord: ...

    def progress(
        self,
        project_id: str,
        collection_task_id: str,
        region_code: str,
    ) -> ProgressFacts: ...
