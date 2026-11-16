"""Freeze task notes against exact source uploads, using Shanghai calendar dates."""

import json
from datetime import date

from .models import FrozenModel
from .upload_candidates import ADMITTED_UPLOADS


class UploadDayRecord(FrozenModel):
    uploaded_on: date
    batch_numbers: tuple[int, ...]
    note: str


class CollectionRecord(FrozenModel):
    collection_task_id: str | None
    collection_description: str
    uploads: tuple[UploadDayRecord, ...]


# Sources supplies item_id, task_id and upload_id for just the selected versions or raw sources.
_RECORDS = """
SELECT s.item_id, t.collection_task_id, t.collection_description,
       b.upload_day, array_agg(DISTINCT b.batch_number ORDER BY b.batch_number)
           FILTER (WHERE b.batch_number IS NOT NULL),
       t.upload_day_notes->>to_char(b.upload_day,'YYYY-MM-DD')
FROM sources s
LEFT JOIN collection_tasks.collection_tasks t
  ON (t.organization_id,t.project_id,t.collection_task_id)=(%(org)s,%(project)s,s.task_id)
LEFT JOIN LATERAL (
    SELECT b.batch_number,(b.created_at AT TIME ZONE 'Asia/Shanghai')::date AS upload_day
    FROM ingest.upload_batch_members m JOIN ingest.upload_batches b
      USING (organization_id,project_id,region_code,batch_key)
    WHERE (m.organization_id,m.project_id,m.region_code,m.upload_id)
          =(%(org)s,%(project)s,%(region)s,s.upload_id)
      AND (%(batches)s::bigint[] IS NULL OR b.batch_number=ANY(%(batches)s))
      AND (%(start)s::timestamptz IS NULL OR b.created_at >= %(start)s)
      AND (%(end)s::timestamptz IS NULL OR b.created_at < %(end)s)
) b ON true
GROUP BY s.item_id,t.collection_task_id,t.collection_description,b.upload_day,t.upload_day_notes
ORDER BY s.item_id,b.upload_day
"""


def _read(cursor, sources, scope, ids, selection=None):
    batches = []
    if selection:
        batches = selection.batch_numbers or (
            [selection.batch_number] if selection.batch_number else []
        )
    cursor.execute(
        sources + _RECORDS,
        dict(
            org=scope[0],
            project=scope[1],
            region=scope[2],
            ids=list(ids),
            batches=list(batches) or None,
            start=selection.uploaded_from if selection else None,
            end=selection.uploaded_before if selection else None,
        ),
    )
    grouped = {}
    for item, task, description, day, numbers, note in cursor.fetchall():
        if task is None and day is None:
            continue
        record = grouped.setdefault(
            item,
            dict(collection_task_id=task, collection_description=description or "", uploads=[]),
        )
        if day is not None:
            record["uploads"].append(
                UploadDayRecord(uploaded_on=day, batch_numbers=numbers, note=note or "")
            )
    return {item: CollectionRecord(**record) for item, record in grouped.items()}


def episode_records(repository, scope, refs, selection=None):
    if any(ref.identity.scope != scope for ref in refs):
        raise PermissionError("data outside export scope")
    sources = (
        ADMITTED_UPLOADS
        + """, sources AS (
        SELECT v.data_version_id AS item_id,v.input_document->>'task_id' AS task_id,a.upload_id
        FROM episode_store.versions v LEFT JOIN admitted a ON a.data_ref=v.receipt_document->'data'
        WHERE (v.organization_id,v.project_id,v.region_code)=(%(org)s,%(project)s,%(region)s)
          AND v.data_version_id=ANY(%(ids)s)
    )"""
    )
    with repository.transaction() as cursor:
        return _read(cursor, sources, scope, [ref.data_version_id for ref in refs], selection)


def raw_records(cursor, scope, ids, selection=None):
    sources = """WITH sources AS (
        SELECT raw_source_id AS item_id,collection_task_id AS task_id,upload_id
        FROM ingest.raw_sources
        WHERE (organization_id,project_id,region_code)=(%(org)s,%(project)s,%(region)s)
          AND raw_source_id=ANY(%(ids)s)
    )"""
    return _read(cursor, sources, scope, ids, selection)


def record_document(records):
    return json.dumps(
        {
            "time_zone": "Asia/Shanghai",
            "records": {key: record.model_dump(mode="json") for key, record in records.items()},
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
