"""Capabilities for ingest planning, committed sample projection and receipt recovery."""

from __future__ import annotations

from typing import Protocol

from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot, StepWindow

from .catalog_models import CollectionVersion
from .models import CommitReceipt, DataVersionRef


class NativeSchemaRegistry(Protocol):
    def register_schema(self, snapshot: DatasetSchemaSnapshot) -> None: ...

    def schema_for(self, project_id: str, dataset_id: str) -> DatasetSchemaSnapshot | None: ...


class NativeSampleCatalog(Protocol):
    def read_steps(
        self,
        dataset_id: str,
        rollout_id: str,
        start_step: int,
        end_step: int,
        *,
        project_id: str | None = None,
        version: int | None = None,
    ) -> StepWindow: ...


class PublishedPackageReceipts(Protocol):
    def published_receipt(
        self, data: DataVersionRef, collection: CollectionVersion
    ) -> CommitReceipt: ...
