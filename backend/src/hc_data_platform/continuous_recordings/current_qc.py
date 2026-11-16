"""Current QC for display. The original package continues to reference its admission report."""

CURRENT_EPISODE_QC = """
LATERAL (
    SELECT report.*
    FROM ingest.recording_episode_qc_reports report
    WHERE (report.organization_id,report.project_id,report.region_code,report.recording_id,report.episode_id)
        = (p.organization_id,p.project_id,p.region_code,p.recording_id,p.episode_id)
      AND (report.report_id=p.qc_report_id OR report.report_document->>'rechecked_report_id'=p.qc_report_id)
    ORDER BY (report.report_id=p.qc_report_id), report.created_at DESC, report.report_id DESC
    LIMIT 1
)
"""
