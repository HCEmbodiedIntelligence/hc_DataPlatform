"""Read exact-version upload provenance for the data traceback page."""

from fastapi import APIRouter, Query, Response

from hc_data_platform.core.context import current_request_context
from hc_data_platform.security.http import VerifiedAuth, authorize_scope

from .delivery_repository import scope_key
from .delivery_router import delivery_service
from .dependencies import PackageServices
from .models import DataVersionRef, FrozenModel
from .upload_candidates import ADMITTED_UPLOADS, ExportUploadBatch

router = APIRouter(tags=["episode_data"])
BASE = "/projects/{project_id}/regions/{region_code}/data-trace"


class TraceSample(FrozenModel):
    data: DataVersionRef | None
    viewer_version_id: str | None


class TraceOrigin(TraceSample):
    identifier: str
    batches: tuple[ExportUploadBatch, ...]


class TraceMember(TraceSample):
    upload_id: str
    raw_source_ids: tuple[str, ...]
    collection_description: str
    upload_note: str


class TracePage(FrozenModel):
    items: tuple[TraceMember, ...]
    total: int
    upload_count: int


_RECEIPTS = (
    ADMITTED_UPLOADS
    + """
, receipts AS (
    SELECT DISTINCT a.upload_id, v.data_version_id, a.data_ref AS data,
           v.input_document->>'task_id' AS task_id, 'version_'
               || pub.collection_version_id AS viewer
    FROM admitted a
    JOIN episode_store.versions v ON (v.organization_id,v.project_id,
        v.region_code,v.data_version_id)
        =(%(org)s,%(project)s,%(region)s,a.data_ref->>'data_version_id')
        AND v.status='COMMITTED' AND v.receipt_document->'data'=a.data_ref
    LEFT JOIN episode_store.collection_publications pub
        USING (organization_id,project_id,region_code,dataset_id,data_version_id)
    WHERE NOT EXISTS (SELECT FROM episode_store.qc_retired_versions retired
        WHERE (retired.organization_id,retired.project_id,retired.region_code,
            retired.data_version_id)
             =(v.organization_id,v.project_id,v.region_code,v.data_version_id))
)
"""
)


def origins(repository, scope, identifier):
    args = dict(org=scope[0], project=scope[1], region=scope[2], identifier=identifier)
    with repository.transaction() as cursor:
        cursor.execute(
            _RECEIPTS
            + """
            , seeds AS (
                SELECT v.data_version_id AS identifier, v.receipt_document->'data' AS data,
                       'version_' || pub.collection_version_id AS viewer, r.upload_id
                FROM episode_store.versions v
                LEFT JOIN episode_store.collection_publications pub
                    USING (organization_id,project_id,region_code,dataset_id,data_version_id)
                LEFT JOIN receipts r ON r.data_version_id=v.data_version_id
                WHERE (v.organization_id,v.project_id,v.region_code)=(%(org)s,
                    %(project)s,%(region)s)
                  AND NOT EXISTS (SELECT FROM episode_store.qc_retired_versions retired
                      WHERE (retired.organization_id,retired.project_id,
                          retired.region_code,retired.data_version_id)
                           =(v.organization_id,v.project_id,v.region_code,v.data_version_id))
                  AND v.status='COMMITTED' AND (%(identifier)s IN
                      (v.episode_id,v.data_version_id,v.receipt_document#>>'{data,identity,rollout_id}')
                      OR EXISTS (SELECT FROM episode_store.export_file_names f WHERE
                        (f.organization_id,f.project_id,f.region_code,f.data_version_id)=
                        (v.organization_id,v.project_id,v.region_code,v.data_version_id)
                        AND lower(f.filename)=lower(%(identifier)s)))
                UNION ALL
                SELECT DISTINCT m.upload_id,NULL::jsonb,NULL::text,
                    m.upload_id FROM ingest.upload_batch_members m
                WHERE (m.organization_id,m.project_id,m.region_code,m.upload_id)
                     =(%(org)s,%(project)s,%(region)s,%(identifier)s)
                UNION ALL
                SELECT s.upload_id,NULL::jsonb,NULL::text,s.upload_id FROM ingest.raw_sources s
                WHERE (s.organization_id,s.project_id,s.region_code,s.raw_source_id)
                     =(%(org)s,%(project)s,%(region)s,%(identifier)s)
            )
            SELECT DISTINCT s.identifier,s.data,s.viewer,b.batch_number,b.channel,b.created_at
            FROM seeds s LEFT JOIN ingest.upload_batch_members m
              ON (m.organization_id,m.project_id,m.region_code,m.upload_id)
                 =(%(org)s,%(project)s,%(region)s,s.upload_id)
            LEFT JOIN ingest.upload_batches b USING (organization_id,
                project_id,region_code,batch_key)
            ORDER BY s.identifier,b.batch_number""",
            args,
        )
        result = {}
        for key, data, viewer, number, channel, created in cursor.fetchall():
            seed = result.setdefault(
                (key, data is None),
                dict(identifier=key, data=data, viewer_version_id=viewer, batches=[]),
            )
            if number is not None:
                seed["batches"].append(
                    ExportUploadBatch(batch_number=number, channel=channel, created_at=created)
                )
    return [TraceOrigin(**seed) for seed in result.values()]


def members(repository, scope, number, offset, limit):
    args = dict(
        org=scope[0], project=scope[1], region=scope[2], number=number, offset=offset, limit=limit
    )
    with repository.transaction() as cursor:
        cursor.execute(
            _RECEIPTS
            + """
            , uploads AS (
                SELECT m.upload_id,b.created_at FROM ingest.upload_batches b
                JOIN ingest.upload_batch_members m USING (organization_id,
                    project_id,region_code,batch_key)
                WHERE (b.organization_id,b.project_id,b.region_code,b.batch_number)
                      =(%(org)s,%(project)s,%(region)s,%(number)s)
            ), items AS (
                SELECT u.*,r.data_version_id,r.data,r.viewer,r.task_id FROM uploads u
                LEFT JOIN receipts r USING (upload_id)
            )
            SELECT counts.total,counts.uploads,p.upload_id,p.data,p.viewer,
                raw.ids,t.collection_description,
                   t.upload_day_notes->>to_char(p.created_at
                       AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD')
            FROM (SELECT (SELECT count(*) FROM items) AS total,(SELECT count(*)
                FROM uploads) AS uploads) counts
            LEFT JOIN LATERAL (
                SELECT * FROM items ORDER BY upload_id,data_version_id
                    OFFSET %(offset)s LIMIT %(limit)s
            ) p ON true
            LEFT JOIN LATERAL (
                SELECT array_agg(s.raw_source_id ORDER BY s.raw_source_id) AS ids,
                       CASE WHEN count(DISTINCT s.collection_task_id)=1
                            THEN min(s.collection_task_id) END AS task_id
                FROM ingest.raw_sources s
                WHERE (s.organization_id,s.project_id,s.region_code,s.upload_id)
                     =(%(org)s,%(project)s,%(region)s,p.upload_id)
            ) raw ON true
            LEFT JOIN collection_tasks.collection_tasks t
              ON (t.organization_id,t.project_id,t.collection_task_id)=(%(org)s,
                  %(project)s,coalesce(p.task_id,raw.task_id))
            ORDER BY p.upload_id,p.data_version_id""",
            args,
        )
        rows = cursor.fetchall()
    return TracePage(
        total=rows[0][0],
        upload_count=rows[0][1],
        items=tuple(
            TraceMember(
                upload_id=upload,
                raw_source_ids=raw_ids or (),
                data=data,
                viewer_version_id=viewer,
                collection_description=description or "",
                upload_note=note or "",
            )
            for _, _, upload, data, viewer, raw_ids, description, note in rows
            if upload is not None
        ),
    )


def trace_service(packages, auth, project_id, region_code, response):
    organization = current_request_context().organization_id
    for capability in ("episode.read", "upload.read"):
        authorize_scope(auth, project_id, capability, region_code, organization)
    response.headers["Cache-Control"] = "no-store"
    return delivery_service(packages, auth, project_id, region_code)



def suggestions(repository, scope, query, limit=20):
    if not query.strip():
        return []
    args = dict(org=scope[0], project=scope[1], region=scope[2], query=query.strip(), limit=limit)
    with repository.transaction() as cursor:
        cursor.execute(
            """WITH versions AS (
                SELECT v.data_version_id, v.episode_id, v.input_document#>>'{identity,rollout_id}' AS rollout
                FROM episode_store.versions v
                WHERE (organization_id,project_id,region_code)=(%(org)s,%(project)s,%(region)s)
                  AND status='COMMITTED' AND NOT EXISTS (
                    SELECT FROM episode_store.qc_retired_versions r WHERE
                    (r.organization_id,r.project_id,r.region_code,r.data_version_id)=
                    (v.organization_id,v.project_id,v.region_code,v.data_version_id))
            ), candidates AS (
                SELECT f.filename AS value,0 AS priority FROM episode_store.export_file_names f
                JOIN versions USING (data_version_id)
                WHERE (organization_id,project_id,region_code)=(%(org)s,%(project)s,%(region)s)
                UNION ALL
                SELECT unnest(ARRAY[episode_id,data_version_id,rollout]),1 FROM versions
                UNION ALL
                SELECT upload_id,1 FROM ingest.upload_batch_members
                WHERE (organization_id,project_id,region_code)=(%(org)s,%(project)s,%(region)s)
                UNION ALL
                SELECT raw_source_id,1 FROM ingest.raw_sources
                WHERE (organization_id,project_id,region_code)=(%(org)s,%(project)s,%(region)s)
            )
            SELECT value FROM candidates WHERE strpos(lower(value),lower(%(query)s))>0
            GROUP BY value ORDER BY lower(value)=lower(%(query)s) DESC,min(priority),
                starts_with(lower(value),lower(%(query)s)) DESC, length(value),value LIMIT %(limit)s""",
            args,
        )
        return [row[0] for row in cursor.fetchall()]


@router.get(BASE + "/suggestions", response_model=list[str])
def trace_suggestions(
    project_id: str, region_code: str, auth: VerifiedAuth, packages: PackageServices,
    response: Response, query: str = Query(min_length=1, max_length=256),
):
    with trace_service(packages, auth, project_id, region_code, response) as service:
        return suggestions(service.repository, scope_key(), query)


@router.get(BASE + "/origins", response_model=list[TraceOrigin])
def trace_origins(
    project_id: str,
    region_code: str,
    auth: VerifiedAuth,
    packages: PackageServices,
    response: Response,
    identifier: str = Query(min_length=1, max_length=256),
):
    with trace_service(packages, auth, project_id, region_code, response) as service:
        return origins(service.repository, scope_key(), identifier)


@router.get(BASE + "/batches/{number}", response_model=TracePage)
def trace_members(
    project_id: str,
    region_code: str,
    number: int,
    auth: VerifiedAuth,
    packages: PackageServices,
    response: Response,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
):
    with trace_service(packages, auth, project_id, region_code, response) as service:
        return members(service.repository, scope_key(), number, offset, limit)
