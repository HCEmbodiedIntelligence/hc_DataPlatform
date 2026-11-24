"""List current, failed MCAP preparations and recording quality checks for a task."""

from hc_data_platform.continuous_recordings.current_qc import CURRENT_EPISODE_QC

from . import plans
from .common import reject


def candidates(db, scope, task, raw_id=None):
    raw_filter = "AND s.raw_source_id=%s" if raw_id is not None else ""
    params = (*scope, task, raw_id) if raw_id is not None else (*scope, task)
    rows = db.execute(
        f"""WITH latest AS (
            SELECT DISTINCT ON (f.raw_source_id,f.source_path)
                f.raw_source_id,f.source_path,f.processing_id,f.recording_id,f.status,f.duplicate_of
            FROM ingest.raw_sources s JOIN ingest.mcap_processing_files f
                USING (organization_id,project_id,region_code,raw_source_id)
            WHERE s.organization_id=%s AND s.project_id=%s AND s.region_code=%s
                AND s.collection_task_id=%s AND s.source_format='MCAP' AND s.raw_status='COMMITTED'
                {raw_filter}
                AND f.error IS DISTINCT FROM 'MCAP_REPROCESS_CANCELLED'
                AND NOT EXISTS (SELECT 1 FROM ingest.recording_source_links l WHERE
                    (l.organization_id,l.project_id,l.region_code,l.raw_source_id)=
                    (s.organization_id,s.project_id,s.region_code,s.raw_source_id))
            ORDER BY f.raw_source_id,f.source_path,f.created_at DESC,f.processing_id
        )
        SELECT f.raw_source_id,f.source_path,f.processing_id,f.recording_id,
            CASE WHEN f.status='FAILED' THEN 'PREPARATION'
                 WHEN c.status IN ('QC_FAILED','QC_ERROR') THEN 'QUALITY'
                 WHEN episodes.quality_failed THEN 'EPISODE_QUALITY' ELSE 'INGEST' END
        FROM latest f LEFT JOIN ingest.continuous_recordings c ON c.organization_id=%s
            AND c.project_id=%s AND c.region_code=%s AND c.recording_id=f.recording_id
        LEFT JOIN LATERAL (
            SELECT bool_or(p.status='QC_FAILED' OR latest_qc.status='REJECT') AS quality_failed,
                   bool_or(p.status='FAILED') AS ingest_failed
            FROM ingest.recording_episode_processing p
            LEFT JOIN {CURRENT_EPISODE_QC} latest_qc ON true
            WHERE (p.organization_id,p.project_id,p.region_code,p.recording_id,p.finalized_revision)=
                  (c.organization_id,c.project_id,c.region_code,c.recording_id,c.finalized_revision)
              AND p.status<>'DISCARDED'
        ) episodes ON true
        WHERE f.duplicate_of IS NULL AND (f.status='FAILED' OR (f.status='READY' AND c.superseded_by_processing_id IS NULL
              AND c.recording_document->>'quality_discarded_at' IS NULL
              AND (c.status IN ('QC_FAILED','QC_ERROR') OR episodes.quality_failed OR episodes.ingest_failed)))
            AND NOT EXISTS (SELECT 1 FROM latest busy WHERE busy.raw_source_id=f.raw_source_id
                            AND busy.status IN ('PENDING','RUNNING'))
        ORDER BY f.source_path,f.raw_source_id""", (*params, *scope),
    ).fetchall()
    return [dict(zip(("raw_source_id", "source_path", "processing_id", "recording_id", "stage"), row, strict=True))
            for row in rows]


def selected_paths(db, scope, task, raw_id, command, manifest):
    selection = command.failed_selection
    if not selection:
        return None
    head = plans.get_plan(db, scope, task)
    if task != selection.collection_task_id or not head or head["revision"] != command.plan_revision:
        reject("CAPTURE_VERSION_MISMATCH", "任务方案已变更，请刷新失败列表")
    ids = sorted(set(selection.processing_ids))
    records = db.execute(
        """SELECT recording_id FROM ingest.mcap_processing_files WHERE organization_id=%s
        AND project_id=%s AND region_code=%s AND raw_source_id=%s AND processing_id=ANY(%s)
        ORDER BY processing_id FOR UPDATE""", (*scope, raw_id, ids),
    ).fetchall()
    db.execute(
        """SELECT recording_id FROM ingest.continuous_recordings WHERE organization_id=%s AND
        project_id=%s AND region_code=%s AND recording_id=ANY(%s) ORDER BY recording_id FOR UPDATE""",
        (*scope, [row[0] for row in records if row[0]]),
    ).fetchall()
    wanted = set(ids)
    paths = {row["source_path"] for row in candidates(db, scope, task, raw_id)
             if row["processing_id"] in wanted}
    if len(paths) != len(wanted) or not paths.issubset({f["path"] for f in manifest["files"]}):
        reject("CAPTURE_REPROCESS_CONFLICT", "所选记录的处理状态已变更，请刷新失败列表")
    return paths
