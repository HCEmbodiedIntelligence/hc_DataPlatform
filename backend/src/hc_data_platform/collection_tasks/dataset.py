"""Register the task-owned empty Dataset before any Episode has been admitted."""

from typing import Any
from uuid import uuid4

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import problem
from hc_data_platform.dataset_registry.models import (
    DatasetPageActor,
    DatasetPageMetadata,
    DatasetPageRecord,
    DatasetPageScope,
)
from hc_data_platform.security.audit import canonical_hash

from .models import CollectionTaskRecord, dataset_id_for_collection_task


def task_upload_region(cursor: Any, task: CollectionTaskRecord, *, region_code: str | None) -> str:
    """An idempotent task keeps its Region; an explicit Dataset supplies its actual Region."""
    cursor.execute(
        """SELECT upload_region_code FROM collection_tasks.collection_tasks
        WHERE organization_id=%s AND project_id=%s AND collection_task_id=%s""",
        (task.organization_id, task.project_id, task.collection_task_id),
    )
    existing = cursor.fetchone()
    if existing:
        return str(existing[0])
    if task.dataset_id is None or task.dataset_id == dataset_id_for_collection_task(
        task.organization_id, task.project_id, task.collection_task_id
    ):
        return region_code or "global"
    cursor.execute(
        """SELECT region_code FROM dataset_registry.datasets
        WHERE organization_id=%s AND project_id=%s AND dataset_id=%s
        AND availability='ACTIVE' AND (%s::text IS NULL OR region_code=%s)""",
        (task.organization_id, task.project_id, task.dataset_id, region_code, region_code),
    )
    regions = {row[0] for row in cursor.fetchall()}
    if len(regions) != 1:
        raise problem(
            status=422,
            code=(
                "COLLECTION_TASK_REGION_REQUIRED"
                if regions
                else "COLLECTION_TASK_DATASET_NOT_ASSIGNABLE"
            ),
            title="任务数据集区域不明确",
            detail="请选择当前区域内的有效数据集；跨区域同名数据集需要明确指定 X-Region-Code。",
        )
    return str(next(iter(regions)))


def ensure_task_dataset(
    cursor: Any, task: CollectionTaskRecord, *, region_code: str | None = None
) -> bool:
    # An explicitly selected Dataset already exists and keeps its own metadata.
    if task.dataset_id != dataset_id_for_collection_task(
        task.organization_id, task.project_id, task.collection_task_id
    ):
        return False
    cursor.execute(
        """SELECT upload_region_code, current_setting('app.region_code', true)
        FROM collection_tasks.collection_tasks
        WHERE organization_id=%s AND project_id=%s AND collection_task_id=%s""",
        (task.organization_id, task.project_id, task.collection_task_id),
    )
    upload_region, previous_region = cursor.fetchone()
    region = region_code or upload_region
    # Project-scoped task creation still needs an exact Region for the Dataset's
    # RLS policy. Narrow this transaction to the task's server-owned upload Region
    # and restore the caller's scope after registration. On failure the caller
    # rolls back both the task and Dataset, including this transaction-local setting.
    if previous_region != region:
        cursor.execute("SELECT set_config('app.region_code', %s, true)", (region,))
    inserted = _register_task_dataset(cursor, task, region=region)
    if previous_region != region:
        cursor.execute("SELECT set_config('app.region_code', %s, true)", (previous_region or "",))
    return inserted


def _register_task_dataset(cursor: Any, task: CollectionTaskRecord, *, region: str) -> bool:
    cursor.execute(
        """SELECT 1 FROM dataset_registry.datasets
        WHERE organization_id=%s AND project_id=%s AND dataset_id=%s LIMIT 1""",
        (task.organization_id, task.project_id, task.dataset_id),
    )
    if cursor.fetchone() is not None:
        return False
    name = f"{task.name} · {task.task_code}"
    cursor.execute(
        """SELECT 1 FROM dataset_registry.datasets
        WHERE organization_id=%s AND project_id=%s AND region_code=%s
          AND folder_path='{}'::text[] AND lower(name)=lower(%s)""",
        (task.organization_id, task.project_id, region, name),
    )
    if cursor.fetchone() is not None:
        name = f"Dataset {task.dataset_id}"
    record = DatasetPageRecord(
        scope=DatasetPageScope(
            organization_id=task.organization_id, project_id=task.project_id, region_code=region
        ),
        dataset_id=task.dataset_id,
        name=name,
        description=task.description[:4096],
        availability="ACTIVE",
        owner=DatasetPageActor(
            id=task.created_by or "hc-data-worker", display_name=task.created_by or "数据处理服务"
        ),
        created_at=task.created_at,
        updated_at=task.created_at,
        activity_at=task.created_at,
        etag='"v1"',
        metadata=DatasetPageMetadata(
            collection_task_id=task.collection_task_id,
            task=task.collection_task_id,
            scene=task.scenario,
            asset_state="EMPTY",
            storage_class="STANDARD",
        ),
    )
    cursor.execute(
        """INSERT INTO dataset_registry.datasets
        (organization_id,project_id,region_code,dataset_id,name,description,availability,
         owner_id,owner_display_name,collection_task_id,task,scene,asset_state,storage_class,
         version,dataset_document,created_at,updated_at,activity_at)
        VALUES (%s,%s,%s,%s,%s,%s,'ACTIVE',%s,%s,%s,%s,%s,'EMPTY','STANDARD',1,%s::jsonb,%s,%s,%s)
        ON CONFLICT (organization_id,project_id,region_code,dataset_id) DO NOTHING""",
        (
            task.organization_id,
            task.project_id,
            region,
            task.dataset_id,
            record.name,
            record.description,
            record.owner.id,
            record.owner.display_name,
            task.collection_task_id,
            task.collection_task_id,
            task.scenario,
            record.model_dump_json(),
            task.created_at,
            task.created_at,
            task.created_at,
        ),
    )
    inserted = cursor.rowcount == 1
    if inserted:
        context = current_request_context()
        cursor.execute(
            """INSERT INTO core.audit_events
            (audit_id,organization_id,project_id,region_code,actor_id,action,resource_type,
             resource_id,request_id,after_hash,details,occurred_at)
            VALUES (%s,%s,%s,%s,%s,'dataset.task_registered','DATASET',%s,%s,%s,
                    '{"version_created":false}'::jsonb,clock_timestamp())""",
            (
                str(uuid4()),
                task.organization_id,
                task.project_id,
                region,
                context.subject_id or record.owner.id,
                task.dataset_id,
                context.request_id,
                canonical_hash(record.model_dump(mode="json")),
            ),
        )
        from hc_data_platform.annotation.ingest_queries import ensure_dataset_tag_schema_binding

        ensure_dataset_tag_schema_binding(
            cursor,
            organization_id=task.organization_id,
            project_id=task.project_id,
            storage_region_code=region,
            dataset_id=task.dataset_id,
            auto_commit=False,
        )
    return inserted
