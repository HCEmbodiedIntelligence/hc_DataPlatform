"""Selectable annotation work with exact upload lineage and current draft tags."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from hc_data_platform.dataset_registry.display_labels import annotation_task_label
from hc_data_platform.episode_store.upload_candidates import ADMITTED_UPLOADS

from .queue_sql import QUEUE_TASKS
from .task_labels import SCHEME_TAG_NAMES_SQL


class ModelEpisodeUpload(BaseModel):
    batch_number: int
    uploaded_at: datetime


class ModelEpisode(BaseModel):
    task_id: str
    label: str
    episode_id: str
    collection_task_id: str | None
    collection_task_code: str | None
    status: Literal["DRAFT", "NEEDS_REVISION"]
    assignee_id: str | None
    tag_count: int
    allowed_tags: tuple[str, ...]
    uploads: tuple[ModelEpisodeUpload, ...]


_QUEUE = QUEUE_TASKS.format(
    columns="a.task_id,a.project_id,a.region_code,a.dataset_id,a.data_ref,a.status,"
    "a.assignee_id,a.current_revision,a.updated_at",
    predicate="a.project_id=%(project)s AND a.region_code=%(region)s AND a.data_ref IS NOT NULL "
    "AND a.status IN ('DRAFT','NEEDS_REVISION')",
)
_LABELS = SCHEME_TAG_NAMES_SQL.format(
    project="q.project_id",
    region="q.region_code",
    dataset="q.dataset_id",
    version="q.data_ref->>'data_version_id'",
)
_EPISODES = (
    ADMITTED_UPLOADS
    + f"""
, queue AS ({_QUEUE}), batch_rows AS (
    SELECT DISTINCT a.data_ref, b.batch_number, b.created_at
    FROM admitted a JOIN ingest.upload_batch_members m
        ON (m.organization_id,m.project_id,m.region_code,m.upload_id)
         = (%(org)s,%(project)s,%(region)s,a.upload_id)
    JOIN ingest.upload_batches b USING (organization_id,project_id,region_code,batch_key)
), uploads AS (
    SELECT data_ref, jsonb_agg(jsonb_build_object('batch_number',batch_number,
                 'uploaded_at',created_at) ORDER BY created_at DESC,batch_number DESC) AS records
    FROM batch_rows GROUP BY data_ref
)
SELECT q.task_id,q.data_ref #>> '{{identity,episode_id}}',q.queue_ordinal,q.queue_task_code,
       q.collection_task_id,q.status,q.assignee_id,coalesce(jsonb_array_length(r.tags),0),
       labels.names,u.records
FROM queue q
LEFT JOIN uploads u ON u.data_ref=q.data_ref
LEFT JOIN annotation.annotation_revisions r ON r.task_id=q.task_id AND r.revision=q.current_revision
LEFT JOIN LATERAL ({_LABELS}) labels ON true
ORDER BY q.updated_at DESC,q.task_id
"""
)


def model_episodes(repository, organization, project, region):
    with repository.transaction() as cursor:
        cursor.execute(_EPISODES, dict(org=organization, project=project, region=region))
        return [
            ModelEpisode(
                task_id=task,
                episode_id=episode,
                label=annotation_task_label(ordinal, code) if ordinal is not None else episode,
                collection_task_id=collection,
                collection_task_code=code,
                status=status,
                assignee_id=assignee,
                tag_count=count,
                allowed_tags=names or (),
                uploads=uploads or (),
            )
            for (
                task, episode, ordinal, code, collection, status, assignee, count, names, uploads
            ) in cursor.fetchall()
        ]
