from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import (
    CollectionTaskPackage,
    CollectionTaskRecord,
    CollectionTaskStatus,
    ProgressFacts,
)


class CollectionTaskRepositoryPort(Protocol):
    def is_dataset_assignable(
        self, organization_id: str, project_id: str, dataset_id: str
    ) -> bool: ...

    def has_received_packages(
        self, organization_id: str, project_id: str, collection_task_id: str
    ) -> bool: ...

    def create(self, task: CollectionTaskRecord) -> CollectionTaskRecord: ...

    def list(
        self,
        *,
        organization_id: str,
        project_id: str,
        status: CollectionTaskStatus | None,
        limit: int,
        after: tuple[datetime, str] | None,
    ) -> tuple[tuple[CollectionTaskRecord, ...], bool]: ...

    def get(
        self,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
    ) -> CollectionTaskRecord | None: ...

    def update(
        self,
        *,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
        expected_version: int,
        changes: dict[str, object],
    ) -> CollectionTaskRecord: ...

    def close(
        self,
        *,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
        expected_version: int,
    ) -> CollectionTaskRecord: ...

    def cancel(
        self,
        *,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
        expected_version: int,
    ) -> CollectionTaskRecord: ...

    def reopen(
        self,
        *,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
        expected_version: int,
    ) -> CollectionTaskRecord: ...

    def progress(
        self,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
        region_code: str,
    ) -> ProgressFacts: ...

    def packages(
        self,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
        region_code: str,
        assigned_dataset_id: str,
    ) -> tuple[CollectionTaskPackage, ...]: ...
