"""Annotation-owned frozen schema targets in the caller's read transaction."""

from __future__ import annotations

import json
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5


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


def ensure_dataset_tag_schema_binding(
    cursor: Any,
    *,
    organization_id: str,
    project_id: str,
    storage_region_code: str,
    dataset_id: str,
    default_snapshot_id: str = "native-lerobot-v3-v1",
    auto_commit: bool = False,
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
    targets = tuple(str(row[0]) for row in cursor.fetchall())
    if targets:
        return targets

    # Fallback: auto-bind default LeRobot annotation schema
    cursor.execute(
        """
        SELECT schema_id, version
        FROM annotation.tag_schema_versions
        WHERE organization_id = %s AND project_id = %s AND status = 'PUBLISHED'
        ORDER BY (schema_id LIKE 'native-lerobot-labels%%') DESC, published_at DESC NULLS LAST, version DESC
        LIMIT 1
        """,
        (organization_id, project_id),
    )
    found = cursor.fetchone()
    if found:
        schema_id, schema_version = str(found[0]), int(found[1])
    else:
        # The version primary key is global; project IDs are only tenant-local.
        scope_key = json.dumps([organization_id, project_id], separators=(",", ":"))
        schema_id = f"native-lerobot-labels-{uuid5(NAMESPACE_URL, scope_key).hex}"
        schema_version = 1
        default_doc = json.dumps(
            {
                "nodes": [
                    {"code": "label-1", "tag_id": "label-1", "attributes": [], "display_name": "pick", "parent_tag_id": None},
                    {"code": "label-2", "tag_id": "label-2", "attributes": [], "display_name": "place", "parent_tag_id": None},
                ],
                "object_relations": [],
                "mutual_exclusions": [],
            }
        )
        content_hash = "72f1c25b5537889231d79d33982b1207c61480d2dd92fbaab6ece3d529e83cb3"
        cursor.execute(
            """
            INSERT INTO annotation.tag_schema_versions (
                schema_id, version, project_id, organization_id, name, status,
                document, content_hash, created_by, created_at, published_by, published_at, compatible_targets
            ) VALUES (
                %s, %s, %s, %s, %s, 'PUBLISHED',
                %s::jsonb, %s, 'system', clock_timestamp(), 'system', clock_timestamp(), '[]'::jsonb
            )
            ON CONFLICT (schema_id, version) DO NOTHING
            """,
            (
                schema_id,
                schema_version,
                project_id,
                organization_id,
                "LeRobot 原生数据人工标注",
                default_doc,
                content_hash,
            ),
        )

    cursor.execute(
        """
        INSERT INTO annotation.tag_schema_bindings (
            organization_id, project_id, region_code, dataset_id,
            dataset_schema_snapshot_id, task_kind,
            schema_id, schema_version, created_at
        ) VALUES (%s, %s, %s, %s, %s, 'TAGGING', %s, %s, clock_timestamp())
        ON CONFLICT (organization_id, project_id, region_code, dataset_id, dataset_schema_snapshot_id, task_kind)
        DO NOTHING
        """,
        (
            organization_id,
            project_id,
            storage_region_code,
            dataset_id,
            default_snapshot_id,
            schema_id,
            schema_version,
        ),
    )

    if auto_commit and hasattr(cursor, "connection") and cursor.connection:
        try:
            cursor.connection.commit()
        except Exception:
            pass

    return (default_snapshot_id,)


def dataset_schema_targets(
    cursor: Any,
    *,
    organization_id: str,
    project_id: str,
    storage_region_code: str,
    dataset_id: str,
) -> tuple[str, ...]:
    return ensure_dataset_tag_schema_binding(
        cursor,
        organization_id=organization_id,
        project_id=project_id,
        storage_region_code=storage_region_code,
        dataset_id=dataset_id,
        auto_commit=True,
    )
