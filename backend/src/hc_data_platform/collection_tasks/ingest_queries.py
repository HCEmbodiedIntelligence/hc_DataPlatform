"""Collection-task-owned dataset lookup in the caller's read transaction."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import TypeAdapter, ValidationError

from hc_data_platform.dataset_registry.models import DatasetId
from hc_data_platform.ingest.planning_errors import IngestPlanBlocked

_DATASET_ID_ADAPTER = TypeAdapter(DatasetId)


class TaskDatasetQuery(Protocol):
    def __call__(
        self, cursor: Any, *, organization_id: str, project_id: str, collection_task_id: str
    ) -> str: ...


def task_dataset(
    cursor: Any,
    *,
    organization_id: str,
    project_id: str,
    collection_task_id: str,
) -> str:
    cursor.execute(
        """
        SELECT dataset_id
        FROM collection_tasks.collection_tasks
        WHERE organization_id = %s AND project_id = %s
          AND collection_task_id = %s
        """,
        (organization_id, project_id, collection_task_id),
    )
    raw = cursor.fetchone()
    if raw is None:
        raise IngestPlanBlocked(
            "the Manifest task must reference one collection task in the project"
        )
    dataset_id = str(raw[0])
    try:
        _DATASET_ID_ADAPTER.validate_python(dataset_id)
    except ValidationError as exc:
        raise IngestPlanBlocked("the collection task has an invalid Dataset identity") from exc
    return dataset_id
