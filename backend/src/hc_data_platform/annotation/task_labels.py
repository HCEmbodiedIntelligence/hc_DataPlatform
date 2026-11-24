"""Read a collection task's vocabulary without depending on capture formats."""

from typing import Any

from .models import AnnotationTask


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


def scheme_tag_names(
    cursor: Any, task: AnnotationTask, *, lock_plan: bool = False
) -> tuple[str, ...]:
    """Follow the collection task's selected published version, including for older Episodes."""
    cursor.execute(
        """WITH source AS (
            SELECT NULLIF(v.input_document->>'task_id','unknown') AS task_id
            FROM episode_store.versions v
            WHERE v.organization_id=current_setting('app.organization_id',true)
              AND (v.project_id,v.region_code,v.dataset_id,v.data_version_id)=(%s,%s,%s,%s)
        ), owners AS (
            SELECT t.organization_id,t.project_id,t.collection_task_id
            FROM collection_tasks.collection_tasks t
            WHERE t.organization_id=current_setting('app.organization_id',true)
              AND (t.project_id,t.upload_region_code,t.dataset_id)=(%s,%s,%s)
              AND ((SELECT task_id FROM source) IS NULL
                   OR t.collection_task_id=(SELECT task_id FROM source))
        )
        SELECT v.document->'annotation_tags' AS names
        FROM owners JOIN collection_tasks.capture_plan_heads h
          USING (organization_id,project_id,collection_task_id)
        JOIN collection_tasks.capture_plans p
          USING (organization_id,project_id,collection_task_id,revision)
        JOIN ingest.capture_scheme_versions v ON (v.organization_id,v.scheme_id,v.version)
            =(p.organization_id,p.scheme_id,p.scheme_version)
        WHERE (SELECT count(*) FROM owners)=1 AND p.region_code=%s"""
        + (" FOR SHARE OF h" if lock_plan else ""),
        (
            task.project_id,
            task.region_code,
            task.dataset_id,
            task.data_ref.data_version_id if task.data_ref else None,
            task.project_id,
            task.region_code,
            task.dataset_id,
            task.region_code,
        ),
    )
    row = cursor.fetchone()
    if row is None:
        return ()
    names = row["names"] if isinstance(row, dict) else row[0]
    return tuple(names or ())
