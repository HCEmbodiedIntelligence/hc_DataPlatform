"""Annotation-owned frozen schema targets in the caller's read transaction."""

from __future__ import annotations

from typing import Any, Protocol


class DatasetSchemaTargetsQuery(Protocol):
    def __call__(
        self,
        cursor: Any,
        *,
        organization_id: str,
        project_id: str,
        storage_region_code: str,
        dataset_id: str,
    ) -> tuple[str, ...]: ...


def dataset_schema_targets(
    cursor: Any,
    *,
    organization_id: str,
    project_id: str,
    storage_region_code: str,
    dataset_id: str,
) -> tuple[str, ...]:
    cursor.execute(
        """
        SELECT dataset_schema_snapshot_id
        FROM annotation.tag_schema_bindings
        WHERE project_id = %s AND region_code = %s AND task_kind = 'TAGGING'
          AND dataset_id = %s AND organization_id = %s
        ORDER BY dataset_schema_snapshot_id
        """,
        (
            project_id,
            storage_region_code,
            dataset_id,
            organization_id,
        ),
    )
    return tuple(str(row[0]) for row in cursor.fetchall())
