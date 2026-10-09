"""Compose owner queries in one read transaction; processors never receive SQL cursors."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hc_data_platform.annotation.ingest_queries import DatasetSchemaTargetsQuery
from hc_data_platform.collection_tasks.ingest_queries import TaskDatasetQuery

from .orchestration import LeRobotEpisodeTaskV1


class PostgresNativePlanningQuery:
    def __init__(
        self,
        connections: Callable[[], Any],
        *,
        task_datasets: TaskDatasetQuery,
        schema_targets: DatasetSchemaTargetsQuery,
    ) -> None:
        self.connections = connections
        self.task_datasets = task_datasets
        self.schema_targets = schema_targets

    def targets(self, task: LeRobotEpisodeTaskV1) -> tuple[str, tuple[str, ...]]:
        with self.connections() as connection, connection.cursor() as cursor:
            dataset = self.task_datasets(
                cursor,
                organization_id=task.organization_id,
                project_id=task.project_id,
                collection_task_id=task.collection_task_id,
            )
            targets = self.schema_targets(
                cursor,
                organization_id=task.organization_id,
                project_id=task.project_id,
                storage_region_code=task.region_code,
                dataset_id=dataset,
            )
            return dataset, targets
