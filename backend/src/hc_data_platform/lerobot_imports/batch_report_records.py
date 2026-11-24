"""Read terminal failures for the batch from current recording and Episode reports."""

from collections import defaultdict

from hc_data_platform.continuous_recordings.current_qc import CURRENT_EPISODE_QC

from .batch_report_analysis import failure_items


MEMBERS = """
WITH members AS (
    SELECT u.batch_key,u.upload_id, src.raw_source_id, src.manifest_key, src.processing_status,
           sess.rollout_id, sess.object_key, sess.status AS session_status, sess.failure_code
    FROM ingest.upload_batch_members u
    LEFT JOIN ingest.raw_sources src USING (organization_id,project_id,region_code,upload_id)
    LEFT JOIN ingest.upload_sessions sess ON
      (sess.organization_id,sess.project_id,sess.region_code,sess.session_id::text)=
      (u.organization_id,u.project_id,u.region_code,u.upload_id)
    WHERE (u.organization_id,u.project_id,u.region_code)=(%(org)s,%(project)s,%(region)s) AND u.batch_key=ANY(%(batches)s)
), sources AS (
    SELECT u.*, m.source_path, m.configuration, m.status AS processing, m.error,
           r.recording_id,r.status AS recording_status,r.finalized_revision,r.recording_document,
           COALESCE(m.source_path,u.object_key,r.recording_id,u.manifest_key,u.upload_id) AS name
    FROM members u
    LEFT JOIN LATERAL (
        SELECT DISTINCT ON (source_path) * FROM ingest.mcap_processing_files m
        WHERE (m.organization_id,m.project_id,m.region_code,m.raw_source_id)=
              (%(org)s,%(project)s,%(region)s,u.raw_source_id)
        ORDER BY source_path,created_at DESC,processing_id
    ) m ON true
    LEFT JOIN ingest.continuous_recordings r ON
      (r.organization_id,r.project_id,r.region_code)=(%(org)s,%(project)s,%(region)s)
      AND (r.recording_id=m.recording_id OR (m.processing_id IS NULL AND
           u.upload_id IN (r.upload_session_id::text,r.recording_upload_id::text)))
    WHERE m.duplicate_of IS NULL AND r.recording_document->>'quality_discarded_at' IS NULL
      AND NOT EXISTS (SELECT FROM ingest.recording_source_links l WHERE
          (l.organization_id,l.project_id,l.region_code,l.raw_source_id)=
          (%(org)s,%(project)s,%(region)s,u.raw_source_id))
)
"""

RECORDING_RESULTS = """
SELECT s.batch_key,s.upload_id,s.name,'' AS episode,s.configuration,
       CASE WHEN s.recording_status='QC_FAILED' THEN 'QC_FAILED' ELSE 'FAILED' END AS stage,
       q.report_document,COALESCE(s.recording_document->>'quality_error_code',s.error,j.last_error_code)
FROM sources s
LEFT JOIN ingest.recording_quality_reports q ON
  (q.organization_id,q.project_id,q.region_code,q.recording_id,q.report_id)=
  (%(org)s,%(project)s,%(region)s,s.recording_id,s.recording_document->'quality_summary'->>'report_id')
LEFT JOIN ingest.raw_ingest_jobs j ON (j.organization_id,j.project_id,j.region_code,j.raw_source_id)=
  (%(org)s,%(project)s,%(region)s,s.raw_source_id) AND s.processing IS NULL
WHERE s.recording_status IN ('QC_FAILED','QC_ERROR') OR s.processing='FAILED'
   OR (s.processing IS NULL AND s.recording_id IS NULL AND s.rollout_id IS NULL AND
       (j.status='FAILED' OR s.processing_status='FAILED'))
"""

EPISODE_RESULTS = f"""
SELECT s.batch_key,s.upload_id,s.name,p.episode_id,s.configuration,
       CASE WHEN p.status='QC_FAILED' OR q.status='REJECT' THEN 'QC_FAILED' ELSE 'FAILED' END,
       q.report_document,p.failure_code
FROM sources s
JOIN ingest.recording_episode_processing p ON
  (p.organization_id,p.project_id,p.region_code,p.recording_id,p.finalized_revision)=
  (%(org)s,%(project)s,%(region)s,s.recording_id,s.finalized_revision)
LEFT JOIN {CURRENT_EPISODE_QC} q ON true
WHERE p.status <> 'DISCARDED' AND (p.status IN ('QC_FAILED','FAILED') OR q.status='REJECT')
"""

RAW_EPISODE_RESULTS = """
SELECT s.batch_key,s.upload_id,s.name,e.episode_id,s.configuration,
       CASE WHEN COALESCE(summary.status,e.quality_status) IN ('REJECT','RISK') THEN 'QC_FAILED' ELSE 'FAILED' END,
       q.report_json,j.last_error_code
FROM sources s JOIN ingest.raw_source_episodes e ON
  (e.organization_id,e.project_id,e.region_code,e.raw_source_id)=
  (%(org)s,%(project)s,%(region)s,s.raw_source_id)
LEFT JOIN quality_rollout_summaries summary ON
  (summary.organization_id,summary.project_id,summary.region_code,summary.rollout_id)=
  (e.organization_id,e.project_id,e.region_code,COALESCE(e.data_ref#>>'{identity,rollout_id}',e.episode_id))
LEFT JOIN qc_reports q ON q.report_sha256=summary.report_sha256
LEFT JOIN ingest.raw_ingest_jobs j ON (j.organization_id,j.project_id,j.region_code,j.raw_source_id)=
  (e.organization_id,e.project_id,e.region_code,e.raw_source_id)
WHERE s.recording_id IS NULL AND s.processing IS NULL AND s.rollout_id IS NULL AND e.status <> 'DISCARDED'
  AND (e.status='FAILED' OR COALESCE(summary.status,e.quality_status)='REJECT')
"""

LEGACY_RESULTS = """
SELECT s.batch_key,s.upload_id,s.name,'',s.configuration,
       CASE WHEN summary.status='REJECT' THEN 'QC_FAILED' ELSE 'FAILED' END,
       q.report_json,COALESCE(job.error_code,s.failure_code)
FROM sources s LEFT JOIN quality_rollout_summaries summary ON
  (summary.organization_id,summary.project_id,summary.region_code,summary.rollout_id)=
  (%(org)s,%(project)s,%(region)s,s.rollout_id)
LEFT JOIN qc_reports q ON q.report_sha256=summary.report_sha256
LEFT JOIN LATERAL (
    SELECT status,error_code FROM workflow.jobs
    WHERE (organization_id,project_id,resource_id)=(%(org)s,%(project)s,s.rollout_id)
      AND job_type='IngestRolloutWorkflow' ORDER BY updated_at DESC,job_id DESC LIMIT 1
) job ON true
WHERE s.rollout_id IS NOT NULL AND s.recording_id IS NULL AND s.processing IS NULL
  AND (summary.status='REJECT' OR job.status IN ('TECHNICAL_FAILED','FAILED') OR s.session_status='FAILED')
"""


def failed_rows(db, scope, batches):
    params = dict(zip(("org", "project", "region"), scope, strict=True), batches=list(batches))
    query = MEMBERS + " UNION ALL ".join((RECORDING_RESULTS, EPISODE_RESULTS, RAW_EPISODE_RESULTS, LEGACY_RESULTS))
    grouped = defaultdict(list)
    for key, upload, name, episode, cfg, stage, report, error in db.execute(query + " ORDER BY 1,2,3,4,6", params):
        for text, topic, note in failure_items(stage, report, error, cfg):
            label = f"{episode} · {text}" if episode else text
            grouped[key, upload, name].append((label, topic, note))
    rows = {number: [] for number in batches.values()}
    for (key, _, name), issues in grouped.items():
        issues = list(dict.fromkeys(issues))
        rows[batches[key]].append([name, "\n".join(v[0] for v in issues),
                     "\n".join(v[1] for v in issues).rstrip(),
                     "\n".join(dict.fromkeys(v[2] for v in issues if v[2]))])
    return {n: sorted(rows[n]) for n in sorted(rows)}
