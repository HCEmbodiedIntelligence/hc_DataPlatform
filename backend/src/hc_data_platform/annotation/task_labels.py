"""Read a collection task's vocabulary without depending on capture formats."""

from typing import Any


def collection_task_label_binding(
    cursor: Any,
    *,
    project_id: str,
    region_code: str,
    dataset_id: str,
    task_kind: str = "TAGGING",
) -> tuple[str, int] | None:
    cursor.execute(
        """SELECT b.schema_id,b.schema_version
        FROM annotation.collection_task_tag_schema_bindings b
        JOIN collection_tasks.collection_tasks t
          ON (t.organization_id,t.project_id,t.collection_task_id)=
             (b.organization_id,b.project_id,b.collection_task_id)
          AND t.dataset_id=b.dataset_id AND t.upload_region_code=b.region_code
        JOIN annotation.tag_schema_versions s
          ON (s.organization_id,s.project_id,s.schema_id,s.version)=
             (b.organization_id,b.project_id,b.schema_id,b.schema_version)
        WHERE b.organization_id=NULLIF(current_setting('app.organization_id',true),'')
          AND b.project_id=NULLIF(current_setting('app.project_id',true),'')
          AND b.region_code=NULLIF(current_setting('app.region_code',true),'')
          AND b.project_id=%s AND b.region_code=%s AND b.dataset_id=%s
          AND b.task_kind=%s AND s.status='PUBLISHED'
          AND 1=(SELECT count(*) FROM collection_tasks.collection_tasks owner
                 WHERE (owner.organization_id,owner.project_id,owner.upload_region_code,
                        owner.dataset_id)=(b.organization_id,b.project_id,b.region_code,b.dataset_id))
        """,
        (project_id, region_code, dataset_id, task_kind),
    )
    rows = cursor.fetchall()
    return (str(rows[0][0]), int(rows[0][1])) if len(rows) == 1 else None
