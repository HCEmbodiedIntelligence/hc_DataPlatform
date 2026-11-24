"""Aggregate current RAW Episode projections and current QC reports in the authorized region."""

from hc_data_platform.continuous_recordings.current_qc import CURRENT_EPISODE_QC

# Read one snapshot per dataset. Later versions can repeat all prior Episodes.
VOLUME = """
WITH bounds AS (
    SELECT %s::text AS organization_id, %s::text AS project_id, %s::text AS region_code,
           %s::timestamptz AS lo, %s::timestamptz AS hi, %s::text AS task_id, %s::interval AS stride
), latest AS (
    SELECT DISTINCT ON (dataset_id) v.organization_id, v.project_id, v.region_code,
           dataset_id, version_id
    FROM dataset_registry.dataset_versions v
    JOIN bounds USING (organization_id,project_id,region_code)
    WHERE version_kind='RAW'
      AND (bounds.task_id IS NULL OR EXISTS (
          SELECT 1 FROM collection_tasks.collection_tasks t
          WHERE (t.organization_id,t.project_id,t.dataset_id) =
                (v.organization_id,v.project_id,v.dataset_id)
            AND t.collection_task_id=bounds.task_id
      ))
    ORDER BY dataset_id,created_at DESC,version_id DESC
), volumes AS (
    SELECT date_bin(stride,e.started_at,lo) AS start,
           count(*) AS episodes, sum(episode.frames) AS frames,
           sum((r.revision_document->>'duration_ns')::numeric)/1000000000 AS seconds
    FROM latest
    JOIN dataset_registry.dataset_version_episodes e
        USING (organization_id,project_id,region_code,dataset_id,version_id)
    JOIN dataset_registry.dataset_version_episode_revisions r
        USING (organization_id,project_id,region_code,dataset_id,version_id,episode_id,revision_id)
    JOIN bounds USING (organization_id,project_id,region_code)
    CROSS JOIN LATERAL (
        -- Synchronized channels describe the same Episode timeline.
        SELECT max(COALESCE((binding->>'end_step')::bigint - (binding->>'start_step')::bigint,
                            (binding#>>'{data,frame_count}')::bigint)) AS frames
        FROM jsonb_array_elements(r.revision_document->'streams') stream
        CROSS JOIN LATERAL (SELECT COALESCE(NULLIF(stream->'package_binding','null'::jsonb),
                                            NULLIF(stream->'data_binding','null'::jsonb),
                                            stream->'aligned_media_binding') AS binding) selected
    ) episode
    WHERE e.included AND e.started_at >= lo AND e.started_at < hi
    GROUP BY 1
)
SELECT bucket AS start, COALESCE(episodes,0) AS episodes, COALESCE(frames,0) AS frames, COALESCE(seconds,0) AS seconds
FROM bounds CROSS JOIN LATERAL generate_series(lo,hi,stride) bucket
LEFT JOIN volumes ON start=bucket
WHERE bucket < hi ORDER BY bucket
"""

# Select current reports in the window, then collapse layers and repeated codes per package.
QUALITY = """
WITH bounds AS (
    SELECT %s::text AS organization_id, %s::text AS project_id, %s::text AS region_code,
           %s::timestamptz AS lo, %s::timestamptz AS hi, %s::text AS task_id
), recordings AS (
    SELECT r.*
    FROM ingest.continuous_recordings r JOIN bounds b USING (organization_id,project_id,region_code)
    WHERE r.superseded_by_processing_id IS NULL
      AND (b.task_id IS NULL OR r.collection_task_id=b.task_id)
), reports AS (
    SELECT 'rollout' AS stage, j.task_id, rr.data_package_id AS package_id,
           q.status, q.report_json->'findings' AS findings, '[]'::jsonb AS codes
    FROM bounds b JOIN quality_rollout_summaries s USING (organization_id,project_id,region_code)
    JOIN qc_reports q USING (organization_id,project_id,region_code,rollout_id,report_sha256)
    JOIN ingest.rollouts rr USING (organization_id,project_id,region_code,rollout_id)
    JOIN ingest.collection_jobs j ON (j.organization_id,j.project_id,j.region_code,j.collection_job_id)
       = (rr.organization_id,rr.project_id,rr.region_code,rr.collection_job_id)
    WHERE q.created_at >= lo AND q.created_at < hi
      AND (b.task_id IS NULL OR j.task_id=b.task_id)
      AND NOT EXISTS (
          SELECT 1 FROM ingest.continuous_recordings old
          WHERE (old.organization_id,old.project_id,old.region_code,old.rollout_id) =
                (rr.organization_id,rr.project_id,rr.region_code,rr.rollout_id)
            AND old.superseded_by_processing_id IS NOT NULL
      )
    UNION ALL
    SELECT 'episode', r.collection_task_id, r.data_package_id,
           q.status, q.report_document#>'{quality_report,findings}', q.report_document->'finding_codes'
    FROM recordings r
    JOIN ingest.recording_episode_processing p USING (organization_id,project_id,region_code,recording_id,finalized_revision)
    JOIN """ + CURRENT_EPISODE_QC + """ q ON true
    CROSS JOIN bounds b
    WHERE q.created_at >= b.lo AND q.created_at < b.hi
    UNION ALL
    SELECT 'recording', r.collection_task_id, r.data_package_id,
           q.report_document->>'status', q.report_document#>'{quality_report,findings}',
           q.report_document->'finding_codes'
    FROM recordings r JOIN ingest.recording_quality_reports q USING (organization_id,project_id,region_code,recording_id)
    CROSS JOIN bounds b
    WHERE q.report_id=r.recording_document#>>'{quality_summary,report_id}'
      AND (r.recording_document#>>'{quality_summary,checked_at}')::timestamptz >= b.lo
      AND (r.recording_document#>>'{quality_summary,checked_at}')::timestamptz < b.hi
), packages AS (
    SELECT task_id,package_id,
           CASE WHEN bool_or(status='REJECT') THEN 'REJECT'
                WHEN bool_or(status='RISK') THEN 'RISK' ELSE 'PASS' END AS status
    FROM reports GROUP BY task_id,package_id
), package_issues AS (
    SELECT DISTINCT r.task_id,r.package_id,issue.code
    FROM reports r CROSS JOIN LATERAL (
        SELECT f->>'code' AS code FROM jsonb_array_elements(r.findings) f
        WHERE lower(f->>'severity') != 'info'
        UNION ALL
        SELECT c FROM jsonb_array_elements_text(r.codes) c
        WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(r.findings) f WHERE f->>'code'=c)
    ) issue
    WHERE issue.code <> '' AND issue.code <> 'QUALITY_REJECT'
)
SELECT 'outcome' AS kind,status AS key,count(*) AS n FROM packages GROUP BY status
UNION ALL
SELECT 'report',stage,count(*) FROM reports GROUP BY stage
UNION ALL
SELECT 'affected','packages',count(DISTINCT (task_id,package_id)) FROM package_issues
UNION ALL
SELECT 'issue',code,count(*) FROM package_issues GROUP BY code
"""
