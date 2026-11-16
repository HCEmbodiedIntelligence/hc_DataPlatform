"""Export candidates from the exact ingested version and its upload submission."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from hc_data_platform.continuous_recordings.current_qc import CURRENT_EPISODE_QC

from .models import DataVersionRef, FrozenModel


# "unannotated" is the original wire value used by previously loaded clients.
UploadExportStage = Literal["qc_passed", "annotated", "dataset", "unannotated"]


class UploadExportFilter(FrozenModel):
    stage: UploadExportStage
    batch_number: int | None = Field(None, ge=1)
    batch_numbers: tuple[Annotated[int, Field(ge=1)], ...] = Field(default=(), exclude_if=lambda v: not v)
    all_batches: bool = Field(default=False, exclude_if=lambda v: not v)


class ExportUploadBatch(FrozenModel):
    batch_number: int
    channel: Literal["web", "oss", "historical"]
    created_at: datetime


def upload_batches(repository, scope, before, limit):
    with repository.transaction() as cursor:
        cursor.execute(
            """SELECT batch_number,channel,created_at FROM ingest.upload_batches
            WHERE (organization_id,project_id,region_code)=(%s,%s,%s)
              AND (%s::bigint IS NULL OR batch_number<%s)
            ORDER BY batch_number DESC LIMIT %s""",
            (*scope, before, before, limit),
        )
        return [
            ExportUploadBatch(batch_number=n, channel=c, created_at=t)
            for n, c, t in cursor.fetchall()
        ]


class UploadCandidate(FrozenModel):
    data: DataVersionRef
    batch_numbers: tuple[int, ...]


class UploadCandidatePage(FrozenModel):
    items: tuple[UploadCandidate, ...]
    next_after: str | None
    total: int


# recording_episode_processing and raw_source_episodes bind admission to a fixed data_ref.
# Formal single-rollout uploads can instead have their exact package receipt on the workflow.
ADMISSIONS = f"""
WITH admitted AS (
    SELECT p.data_ref, coalesce(src.upload_id, r.upload_session_id::text,
           r.recording_upload_id::text) AS upload_id, q.status AS qc
    FROM ingest.recording_episode_processing p
    JOIN ingest.continuous_recordings r USING (organization_id,project_id,region_code,recording_id)
    LEFT JOIN ingest.mcap_processing_files m USING (organization_id,project_id,region_code,recording_id)
    LEFT JOIN ingest.raw_sources src ON (src.organization_id,src.project_id,src.region_code,src.raw_source_id)
        = (m.organization_id,m.project_id,m.region_code,m.raw_source_id)
    LEFT JOIN {CURRENT_EPISODE_QC} q ON true
    WHERE (p.organization_id,p.project_id,p.region_code)=(%(org)s,%(project)s,%(region)s)
      AND p.status='READY' AND p.data_ref IS NOT NULL
    UNION ALL
    SELECT coalesce(e.data_ref, w.result#>'{{derived,data}}'), src.upload_id,
           CASE WHEN qc.source_sha256=v.input_document->>'source_sha256' THEN qc.status
                WHEN qc.rollout_id IS NULL AND e.qc_report_id IS NOT NULL THEN e.quality_status END
    FROM ingest.raw_source_episodes e
    JOIN ingest.raw_sources src USING (organization_id,project_id,region_code,raw_source_id)
    LEFT JOIN ingest.raw_ingest_jobs j USING (organization_id,project_id,region_code,raw_source_id)
    LEFT JOIN workflow.jobs w ON e.data_ref IS NULL AND w.organization_id=j.organization_id
        AND w.project_id=j.project_id AND w.workflow_id=j.workflow_id AND w.status='SUCCEEDED'
        AND w.resource_id=e.episode_id
    JOIN episode_store.versions v ON (v.organization_id,v.project_id,v.region_code,v.data_version_id)
        =(e.organization_id,e.project_id,e.region_code,
          coalesce(e.data_ref,w.result#>'{{derived,data}}')->>'data_version_id')
    LEFT JOIN quality_rollout_summaries qc ON (qc.organization_id,qc.project_id,qc.region_code,qc.rollout_id)
        =(e.organization_id,e.project_id,e.region_code,e.episode_id)
    WHERE (e.organization_id,e.project_id,e.region_code)=(%(org)s,%(project)s,%(region)s)
      AND src.raw_status='COMMITTED' AND (e.status='READY' OR
          (e.status<>'DISCARDED' AND e.data_ref IS NULL AND w.result#>'{{derived,data}}'=v.receipt_document->'data'))
), eligible AS (
    SELECT v.data_version_id, a.data_ref,
           a.upload_id
    FROM admitted a JOIN episode_store.versions v
        ON (v.organization_id,v.project_id,v.region_code)=(%(org)s,%(project)s,%(region)s)
        AND v.data_version_id=a.data_ref->>'data_version_id'
        AND v.status='COMMITTED' AND v.receipt_document->'data'=a.data_ref
    JOIN episode_store.bindings bind USING (organization_id,project_id,region_code,dataset_id,episode_id)
    WHERE bind.current_data_version_id=v.data_version_id
      AND EXISTS (SELECT FROM episode_store.collection_publications pub
          WHERE (pub.organization_id,pub.project_id,pub.region_code,pub.dataset_id,pub.data_version_id)
               =(v.organization_id,v.project_id,v.region_code,v.dataset_id,v.data_version_id))
      AND NOT EXISTS (SELECT FROM episode_store.qc_retired_versions retired
          WHERE (retired.organization_id,retired.project_id,retired.region_code,retired.data_version_id)
               =(v.organization_id,v.project_id,v.region_code,v.data_version_id))
      AND (%(datasets)s::text[] IS NULL OR v.dataset_id=ANY(%(datasets)s))
      AND (%(tasks)s::text[] IS NULL OR v.input_document->>'task_id'=ANY(%(tasks)s))
      AND (%(ids)s::text[] IS NULL OR v.data_version_id=ANY(%(ids)s))
      AND (%(stage)s NOT IN ('qc_passed', 'unannotated') OR a.qc='PASS')
      AND (%(stage)s<>'annotated' OR EXISTS (
          SELECT FROM annotation.annotation_tasks t
          WHERE (t.organization_id,t.project_id,t.region_code,t.dataset_id)
               =(v.organization_id,v.project_id,v.region_code,v.dataset_id)
            AND t.data_ref=a.data_ref AND t.status='APPROVED'
            AND t.approved_revision=t.current_revision AND t.approved_review_id IS NOT NULL))
), filtered AS (
    SELECT e.data_version_id,e.data_ref,
           coalesce(array_agg(DISTINCT b.batch_number ORDER BY b.batch_number)
                    FILTER (WHERE b.batch_number IS NOT NULL), ARRAY[]::bigint[]) AS batches
    FROM eligible e LEFT JOIN ingest.upload_batch_members m
      ON (m.organization_id,m.project_id,m.region_code,m.upload_id)
         =(%(org)s,%(project)s,%(region)s,e.upload_id)
    LEFT JOIN ingest.upload_batches b USING (organization_id,project_id,region_code,batch_key)
    GROUP BY e.data_version_id,e.data_ref
    HAVING (%(batch_list)s::bigint[] IS NULL OR bool_or(b.batch_number=ANY(%(batch_list)s)))
       AND (NOT %(all_batches)s OR count(b.batch_number)>0)
)
"""


def candidates(
    repository, scope, selection, *, datasets=(), tasks=(), ids=None, after=None, limit=25
):
    args = dict(
        org=scope[0],
        project=scope[1],
        region=scope[2],
        stage=selection.stage,
        batch_list=list(selection.batch_numbers) or ([selection.batch_number] if selection.batch_number else None),
        all_batches=selection.all_batches,
        datasets=list(datasets) or None,
        tasks=list(tasks) or None,
        ids=ids,
        after=after,
        limit=limit + 1,
    )
    with repository.transaction() as cursor:
        cursor.execute(
            ADMISSIONS
            + """
            SELECT (SELECT count(*) FROM filtered), page.data_ref,page.batches
            FROM (SELECT 1) anchor LEFT JOIN LATERAL (
                SELECT data_ref,batches FROM filtered
                WHERE %(after)s::text IS NULL OR data_version_id>%(after)s
                ORDER BY data_version_id LIMIT %(limit)s
            ) page ON true ORDER BY page.data_ref->>'data_version_id'""",
            args,
        )
        rows = cursor.fetchall()
    items = tuple(
        UploadCandidate(data=ref, batch_numbers=batches)
        for _, ref, batches in rows[:limit]
        if ref is not None
    )
    return UploadCandidatePage(
        total=rows[0][0],
        items=items,
        next_after=items[-1].data.data_version_id if len(rows) > limit else None,
    )
