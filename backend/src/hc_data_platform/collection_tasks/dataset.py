"""Register the task-owned empty Dataset before any Episode has been admitted."""

from typing import Any
from uuid import uuid4

from hc_data_platform.core.context import current_request_context
from hc_data_platform.dataset_registry.models import (
    DatasetPageActor,
    DatasetPageMetadata,
    DatasetPageRecord,
    DatasetPageScope,
)
from hc_data_platform.security.audit import canonical_hash

from .models import CollectionTaskRecord, dataset_id_for_collection_task


def ensure_task_dataset(
    cursor: Any, task: CollectionTaskRecord, *, region_code: str | None = None
) -> bool:
    # An explicitly selected Dataset already exists and keeps its own metadata.
    if task.dataset_id != dataset_id_for_collection_task(
        task.organization_id, task.project_id, task.collection_task_id
    ):
        return False
    cursor.execute(
        """SELECT upload_region_code FROM collection_tasks.collection_tasks
        WHERE organization_id=%s AND project_id=%s AND collection_task_id=%s""",
        (task.organization_id, task.project_id, task.collection_task_id),
    )
    upload_region = cursor.fetchone()[0]
    region = region_code or upload_region
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
    return inserted
